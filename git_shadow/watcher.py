"""Low-overhead local filesystem notifications with a polling fallback."""

from __future__ import annotations

import ctypes
import errno
import os
import pathlib
import select
import struct
import sys
import time
from typing import Dict, Iterable, Optional


IN_ACCESS = 0x00000001
IN_MODIFY = 0x00000002
IN_ATTRIB = 0x00000004
IN_CLOSE_WRITE = 0x00000008
IN_MOVED_FROM = 0x00000040
IN_MOVED_TO = 0x00000080
IN_CREATE = 0x00000100
IN_DELETE = 0x00000200
IN_DELETE_SELF = 0x00000400
IN_MOVE_SELF = 0x00000800
IN_ISDIR = 0x40000000
IN_IGNORED = 0x00008000

WATCH_MASK = (
    IN_MODIFY
    | IN_ATTRIB
    | IN_CLOSE_WRITE
    | IN_MOVED_FROM
    | IN_MOVED_TO
    | IN_CREATE
    | IN_DELETE
    | IN_DELETE_SELF
    | IN_MOVE_SELF
)
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".next"}


class LocalChangeWatcher:
    """Notify on local changes without requiring third-party dependencies."""

    def __init__(self, root: str):
        self.root = pathlib.Path(root).resolve()
        self.fd: Optional[int] = None
        self.native = False
        self._watches: Dict[int, pathlib.Path] = {}
        self._libc = None

    def __enter__(self) -> "LocalChangeWatcher":
        if sys.platform != "linux":
            return self
        try:
            libc = ctypes.CDLL(None, use_errno=True)
            init1 = libc.inotify_init1
            init1.argtypes = [ctypes.c_int]
            init1.restype = ctypes.c_int
            fd = init1(os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0))
            if fd < 0:
                raise OSError(ctypes.get_errno(), os.strerror(ctypes.get_errno()))
            self.fd = fd
            self._libc = libc
            self._add_tree(self.root)
            self.native = bool(self._watches)
        except (AttributeError, OSError, OverflowError):
            self.close()
        return self

    def _iter_dirs(self, root: pathlib.Path) -> Iterable[pathlib.Path]:
        if root.is_dir() and root.name not in SKIP_DIRS:
            yield root
        try:
            for candidate in root.rglob("*"):
                if candidate.is_dir() and not any(part in SKIP_DIRS for part in candidate.relative_to(root).parts):
                    yield candidate
        except OSError:
            return

    def _add_watch(self, directory: pathlib.Path) -> None:
        if self.fd is None or self._libc is None or directory in self._watches.values():
            return
        add_watch = self._libc.inotify_add_watch
        add_watch.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
        add_watch.restype = ctypes.c_int
        watch_descriptor = add_watch(self.fd, os.fsencode(str(directory)), WATCH_MASK)
        if watch_descriptor >= 0:
            self._watches[watch_descriptor] = directory

    def _add_tree(self, root: pathlib.Path) -> None:
        for directory in self._iter_dirs(root):
            self._add_watch(directory)

    def _consume_events(self, payload: bytes) -> bool:
        changed = False
        header_size = struct.calcsize("iIII")
        offset = 0
        while offset + header_size <= len(payload):
            descriptor, mask, _cookie, name_length = struct.unpack_from("iIII", payload, offset)
            offset += header_size
            raw_name = payload[offset : offset + name_length]
            offset += name_length
            name = raw_name.split(b"\0", 1)[0]
            directory = self._watches.get(descriptor)
            if directory is None:
                continue
            changed = changed or bool(mask & WATCH_MASK)
            if mask & IN_IGNORED:
                self._watches.pop(descriptor, None)
                continue
            if name and mask & IN_ISDIR and mask & (IN_CREATE | IN_MOVED_TO):
                self._add_tree(directory / os.fsdecode(name))
        return changed

    def wait(self, timeout: float) -> bool:
        """Return whether the filesystem emitted an event during the wait."""
        if not self.native or self.fd is None:
            time.sleep(max(0.0, timeout))
            return False
        try:
            readable, _, _ = select.select([self.fd], [], [], max(0.0, timeout))
            if not readable:
                return False
            return self._consume_events(os.read(self.fd, 1024 * 1024))
        except (OSError, ValueError):
            self.native = False
            return False

    def wait_for_quiet(self, quiet_period: float) -> bool:
        """Wait for one event and then a quiet window, coalescing save bursts."""
        if not self.native:
            return False
        changed = self.wait(quiet_period)
        if not changed:
            return False
        while self.wait(quiet_period):
            pass
        return True

    def close(self) -> None:
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
        self.fd = None
        self._watches.clear()
        self.native = False

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()
