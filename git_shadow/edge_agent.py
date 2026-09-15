#!/usr/bin/env python3
"""git-shadow VPS edge executor.

This file is deliberately standalone. ``git shadow edge install`` uploads it
to the VPS, where it can run without installing the local package. The wire
protocol is newline-delimited JSON (JSONL) over a clean SSH channel.
"""

from __future__ import annotations

import argparse
import base64
import datetime as _datetime
import errno
import fcntl
import fnmatch
import hashlib
import json
import os
import pathlib
import re
import signal
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import quote, urlsplit
from typing import Any, Dict, Iterable, List, Optional, Tuple


JOB_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
DEFAULT_TTL = 600
DEFAULT_EVENT_LOG_MAX_BYTES = 4 * 1024 * 1024
DEFAULT_MAX_COMPLETED_RUNS = 100


class EdgeError(RuntimeError):
    """A user-visible, safe-to-serialize executor error."""


def now_iso() -> str:
    return _datetime.datetime.now(_datetime.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def safe_job_id(value: Any) -> str:
    job_id = str(value or "")
    if not JOB_ID_RE.fullmatch(job_id):
        raise EdgeError("invalid job_id")
    return job_id


def redact(value: Any) -> Any:
    """Remove secret-bearing request fields before writing durable state."""
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if lowered.endswith("_b64") or "token" in lowered or "secret" in lowered:
                result[key] = "<redacted>"
            else:
                result[key] = redact(item)
        return result
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def scrub_text(value: str) -> str:
    """Remove configured credential values from live and durable diagnostics."""
    result = str(value)
    for name in ("GIT_SHADOW_CLOUDCLI_TOKEN", "GIT_SHADOW_CLOUDCLI_API_KEY"):
        secret = os.environ.get(name, "")
        if secret:
            result = result.replace(secret, "<redacted>")
    return result


def ensure_inside(path_value: str, root: pathlib.Path) -> pathlib.Path:
    """Resolve a path and prevent the executor escaping the user's home."""
    candidate = pathlib.Path(path_value).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise EdgeError("path is outside the remote home directory") from exc
    return resolved


def run_checked(
    argv: List[str],
    cwd: Optional[pathlib.Path] = None,
    input_data: Optional[bytes] = None,
    timeout: int = 900,
) -> Tuple[str, str]:
    try:
        completed = subprocess.run(
            argv,
            cwd=str(cwd) if cwd else None,
            input=input_data,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise EdgeError("executable not found: %s" % argv[0]) from exc
    except subprocess.TimeoutExpired as exc:
        raise EdgeError("command timed out: %s" % argv[0]) from exc

    stdout = completed.stdout.decode("utf-8", "replace") if isinstance(completed.stdout, bytes) else (completed.stdout or "")
    stderr = completed.stderr.decode("utf-8", "replace") if isinstance(completed.stderr, bytes) else (completed.stderr or "")
    if completed.returncode != 0:
        detail = (stderr.strip() or stdout.strip() or "exit code %s" % completed.returncode)[-4000:]
        raise EdgeError("%s failed: %s" % (argv[0], detail))
    return stdout, stderr


class EventJournal:
    def __init__(
        self,
        state_root: pathlib.Path,
        job_id: str,
        max_bytes: int = DEFAULT_EVENT_LOG_MAX_BYTES,
    ):
        self.directory = state_root / "runs" / job_id
        self.events_path = self.directory / "events.ndjson"
        self.state_path = self.directory / "state.json"
        self.request_path = self.directory / "request.json"
        self.max_bytes = max(256, int(max_bytes))
        self.directory.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.directory, 0o700)
        except OSError:
            pass
        self._lock = threading.Lock()
        self._seq = self._read_last_seq()

    def _read_last_seq(self) -> int:
        if not self.events_path.exists():
            return 0
        last = 0
        try:
            with self.events_path.open("r", encoding="utf-8") as stream:
                for line in stream:
                    try:
                        last = max(last, int(json.loads(line).get("seq", 0)))
                    except (ValueError, TypeError, json.JSONDecodeError):
                        continue
        except OSError:
            return 0
        return last

    def save_request(self, request: Dict[str, Any]) -> None:
        self.request_path.write_text(json_dump(redact(request)) + "\n", encoding="utf-8")
        try:
            os.chmod(self.request_path, 0o600)
        except OSError:
            pass

    def save_state(self, state: Dict[str, Any]) -> None:
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json_dump(redact(state)) + "\n", encoding="utf-8")
        os.replace(str(temporary), str(self.state_path))
        try:
            os.chmod(self.state_path, 0o600)
        except OSError:
            pass

    def append(self, event: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            self._seq += 1
            payload = {"seq": self._seq, "at": now_iso(), **event}
            persisted = redact(payload)
            line = self._bounded_line(persisted)
            existing = self.events_path.read_bytes() if self.events_path.exists() else b""
            if len(existing) + len(line) <= self.max_bytes:
                with self.events_path.open("ab") as stream:
                    stream.write(line)
                    stream.flush()
                    os.fsync(stream.fileno())
            else:
                retained = []
                remaining = self.max_bytes - len(line)
                for old_line in reversed(existing.splitlines(keepends=True)):
                    if len(old_line) <= remaining:
                        retained.append(old_line)
                        remaining -= len(old_line)
                retained.reverse()
                temporary = self.events_path.with_suffix(".tmp")
                with temporary.open("wb") as stream:
                    stream.writelines(retained)
                    stream.write(line)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(str(temporary), str(self.events_path))
            try:
                os.chmod(self.events_path, 0o600)
            except OSError:
                pass
            return payload

    def _bounded_line(self, persisted: Dict[str, Any]) -> bytes:
        """Serialize one event without allowing a single output line to evade the cap."""
        line = (json_dump(persisted) + "\n").encode("utf-8")
        if len(line) <= self.max_bytes:
            return line

        compact = dict(persisted)
        for key in ("message", "error", "detail"):
            value = compact.get(key)
            if isinstance(value, str):
                compact[key] = value[:512] + "... <truncated>"
        line = (json_dump(compact) + "\n").encode("utf-8")
        if len(line) <= self.max_bytes:
            return line

        # Keep the fields needed to identify and order an event if arbitrary
        # future event data is still larger than the configured journal cap.
        minimal = {
            key: compact[key]
            for key in ("seq", "at", "type", "job_id", "event")
            if key in compact
        }
        minimal["detail"] = "<event payload truncated>"
        line = (json_dump(minimal) + "\n").encode("utf-8")
        if len(line) <= self.max_bytes:
            return line
        return (json_dump({"seq": compact.get("seq"), "event": compact.get("event")}) + "\n").encode("utf-8")

    def first_seq(self) -> int:
        if not self.events_path.exists():
            return 0
        try:
            with self.events_path.open("r", encoding="utf-8") as stream:
                for line in stream:
                    try:
                        return int(json.loads(line).get("seq", 0))
                    except (ValueError, TypeError, json.JSONDecodeError):
                        continue
        except OSError:
            return 0
        return 0

    def replay(self, after_seq: int) -> Iterable[Dict[str, Any]]:
        if not self.events_path.exists():
            return []
        output = []
        with self.events_path.open("r", encoding="utf-8") as stream:
            for line in stream:
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if int(payload.get("seq", 0)) > after_seq:
                    if payload.get("content_b64") == "<redacted>":
                        payload["replay_redacted"] = True
                    payload["replay"] = True
                    output.append(payload)
        return output


class EdgeExecutor:
    def __init__(
        self,
        state_root: Optional[str] = None,
        max_event_log_bytes: Optional[int] = None,
        max_completed_runs: Optional[int] = None,
    ):
        self.home = pathlib.Path.home().resolve()
        self.state_root = ensure_inside(
            state_root or os.environ.get("GIT_SHADOW_STATE_DIR", str(self.home / ".local/share/git-shadow")),
            self.home,
        )
        self.state_root.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.state_root, 0o700)
        except OSError:
            pass
        self.max_event_log_bytes = self._positive_setting(
            max_event_log_bytes,
            "GIT_SHADOW_EVENT_LOG_MAX_BYTES",
            DEFAULT_EVENT_LOG_MAX_BYTES,
        )
        self.max_completed_runs = self._nonnegative_setting(
            max_completed_runs,
            "GIT_SHADOW_MAX_COMPLETED_RUNS",
            DEFAULT_MAX_COMPLETED_RUNS,
        )
        self._cleanup_terminal_runs()
        self._write_lock = threading.Lock()
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._stop = threading.Event()
        self._lease_thread = threading.Thread(target=self._lease_loop, daemon=True)
        self._lease_thread.start()

    @staticmethod
    def _positive_setting(value: Optional[int], name: str, default: int) -> int:
        if value is None:
            try:
                value = int(os.environ.get(name, default))
            except (TypeError, ValueError):
                value = default
        return max(256, int(value))

    @staticmethod
    def _nonnegative_setting(value: Optional[int], name: str, default: int) -> int:
        if value is None:
            try:
                value = int(os.environ.get(name, default))
            except (TypeError, ValueError):
                value = default
        return max(0, int(value))

    def _cleanup_terminal_runs(self) -> None:
        """Retain recent terminal jobs while never deleting active work."""
        runs_root = self.state_root / "runs"
        if not runs_root.exists() or self.max_completed_runs < 0:
            return
        terminal = []
        for run_dir in runs_root.iterdir():
            if not run_dir.is_dir():
                continue
            try:
                state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if state.get("status") not in ("completed", "failed", "interrupted"):
                continue
            try:
                updated_at = state.get("updated_at", 0)
                try:
                    updated_order = int(updated_at)
                except (TypeError, ValueError):
                    # Edge job states use ISO-8601 while the service state
                    # uses epoch seconds; filesystem mtime remains a stable
                    # fallback for the former.
                    updated_order = 0
                order = (updated_order, run_dir.stat().st_mtime, run_dir.name)
            except OSError:
                continue
            terminal.append((order, run_dir))
        terminal.sort(key=lambda item: item[0], reverse=True)
        for _order, run_dir in terminal[self.max_completed_runs:]:
            try:
                shutil.rmtree(str(run_dir))
            except OSError:
                continue

    def _lease_loop(self) -> None:
        while not self._stop.wait(1.0):
            now = time.time()
            for job_id, record in list(self._jobs.items()):
                if record.get("status") not in ("accepted", "running"):
                    continue
                lease_path = record.get("lease_path")
                ttl = int(record.get("lease_ttl", DEFAULT_TTL))
                if not lease_path or not os.path.exists(lease_path):
                    continue
                try:
                    age = now - os.path.getmtime(lease_path)
                except OSError:
                    continue
                if age > ttl and not record["cancel_event"].is_set():
                    record["cancel_event"].set()
                    self.emit_event(job_id, "lease.expired", ttl=ttl, age=int(age))

    def emit_control(self, payload: Dict[str, Any]) -> None:
        with self._write_lock:
            sys.stdout.write(json_dump(payload) + "\n")
            sys.stdout.flush()

    def emit_event(self, job_id: str, event: str, **data: Any) -> Dict[str, Any]:
        for field in ("message", "error", "detail"):
            if field in data and data[field] is not None:
                data[field] = scrub_text(str(data[field]))
        record = self._jobs.get(job_id)
        if record is None:
            journal = EventJournal(self.state_root, job_id, self.max_event_log_bytes)
        else:
            journal = record["journal"]
        payload = journal.append({"type": "event", "job_id": job_id, "event": event, **data})
        with self._write_lock:
            sys.stdout.write(json_dump(payload) + "\n")
            sys.stdout.flush()
        return payload

    def _state(self, job_id: str, status: str, **data: Any) -> Dict[str, Any]:
        record = self._jobs.get(job_id)
        state = {"job_id": job_id, "status": status, "updated_at": now_iso(), **data}
        if record:
            record["journal"].save_state(state)
        else:
            EventJournal(self.state_root, job_id, self.max_event_log_bytes).save_state(state)
        return state

    def _validate_cwd(self, value: Optional[str]) -> Optional[pathlib.Path]:
        if not value:
            return None
        return ensure_inside(value, self.home)

    def _run_streaming(self, job_id: str, step_id: str, step: Dict[str, Any]) -> None:
        argv = step.get("argv")
        if not isinstance(argv, list) or not argv or not all(isinstance(item, str) for item in argv):
            raise EdgeError("exec step requires a non-empty argv list")
        cwd = self._validate_cwd(step.get("cwd"))
        if cwd and not cwd.exists():
            raise EdgeError("working directory does not exist: %s" % cwd)
        timeout = int(step.get("timeout", 900))
        environment = os.environ.copy()
        supplied_env = step.get("env", {})
        if supplied_env:
            if not isinstance(supplied_env, dict):
                raise EdgeError("step env must be an object")
            for key, value in supplied_env.items():
                if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(key)):
                    raise EdgeError("invalid environment variable name")
                environment[str(key)] = str(value)

        try:
            process = subprocess.Popen(
                argv,
                cwd=str(cwd) if cwd else None,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                start_new_session=True,
            )
        except FileNotFoundError as exc:
            raise EdgeError("executable not found: %s" % argv[0]) from exc

        def forward(stream: Any, channel: str) -> None:
            for line in iter(stream.readline, ""):
                message = line.rstrip("\n")
                if message:
                    self.emit_event(job_id, "output", step=step_id, channel=channel, message=message)
            stream.close()

        stdout_thread = threading.Thread(target=forward, args=(process.stdout, "stdout"), daemon=True)
        stderr_thread = threading.Thread(target=forward, args=(process.stderr, "stderr"), daemon=True)
        stdout_thread.start()
        stderr_thread.start()

        def terminate_and_reap() -> None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except OSError:
                try:
                    process.kill()
                except OSError:
                    pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    process.kill()
                except OSError:
                    pass
                process.wait(timeout=5)

        try:
            deadline = time.time() + timeout
            while process.poll() is None:
                record = self._jobs.get(job_id)
                if record and record["cancel_event"].is_set():
                    terminate_and_reap()
                    raise EdgeError("step cancelled because the lease expired")
                if time.time() > deadline:
                    raise subprocess.TimeoutExpired(argv, timeout)
                time.sleep(0.1)
            return_code = process.returncode
        except subprocess.TimeoutExpired as exc:
            terminate_and_reap()
            raise EdgeError("step timed out after %ss" % timeout) from exc
        stdout_thread.join(timeout=2)
        stderr_thread.join(timeout=2)
        if return_code != 0:
            raise EdgeError("command exited with code %s" % return_code)

    def _safe_extract(self, payload: bytes, target: pathlib.Path) -> None:
        target.mkdir(parents=True, exist_ok=True)
        with tarfile.open(fileobj=__import__("io").BytesIO(payload), mode="r:gz") as archive:
            for member in archive.getmembers():
                if member.issym() or member.islnk():
                    raise EdgeError("archive links are not allowed")
                member_path = (target / member.name).resolve()
                try:
                    member_path.relative_to(target.resolve())
                except ValueError as exc:
                    raise EdgeError("archive contains an unsafe path") from exc
            archive.extractall(str(target))

    def _workspace_prepare(self, step: Dict[str, Any]) -> None:
        target = ensure_inside(str(step.get("target", "")), self.home)
        remote_url = str(step.get("remote_url") or "").strip()
        branch = str(step.get("branch") or "main")
        pull = bool(step.get("pull"))
        git_enabled = bool(step.get("git_enabled", True))
        target.parent.mkdir(parents=True, exist_ok=True)
        preexisting = self._snapshot_workspace(target) if target.exists() and not (target / ".git").exists() else []

        if remote_url:
            if not target.exists():
                run_checked(["git", "clone", "--", remote_url, str(target)], timeout=1800)
            elif not (target / ".git").exists():
                if any(target.iterdir()):
                    try:
                        run_checked(["git", "init", "-b", branch], cwd=target)
                    except EdgeError:
                        run_checked(["git", "init"], cwd=target)
                    run_checked(["git", "remote", "add", "origin", remote_url], cwd=target)
                    run_checked(["git", "fetch", "origin"], cwd=target, timeout=900)
                    self._clear_snapshot_paths(target, preexisting)
                    try:
                        run_checked(["git", "checkout", "-B", branch, "origin/" + branch], cwd=target, timeout=900)
                    except Exception:
                        self._restore_workspace(target, preexisting)
                        raise
                    self._restore_workspace(target, preexisting)
                else:
                    run_checked(["git", "clone", "--", remote_url, str(target)], timeout=1800)
            else:
                run_checked(["git", "fetch", "origin"], cwd=target, timeout=900)
                run_checked(["git", "checkout", branch], cwd=target, timeout=900)
            if (target / ".git").exists() and not (target / ".git" / "config").exists():
                raise EdgeError("Git workspace was not initialized: %s" % target)
            if pull:
                run_checked(["git", "pull", "origin", branch], cwd=target, timeout=900)
            self._restore_workspace(target, preexisting)
            return

        target.mkdir(parents=True, exist_ok=True)
        if not git_enabled:
            self._restore_workspace(target, preexisting)
            return
        if not (target / ".git").exists():
            try:
                run_checked(["git", "init", "-b", branch], cwd=target)
            except EdgeError:
                run_checked(["git", "init"], cwd=target)
        commit = str(step.get("commit") or "")
        archive_b64 = step.get("archive_b64")
        if archive_b64:
            self._safe_extract(base64.b64decode(str(archive_b64)), target)
        if commit:
            (target / ".git/SHADOW_COMMIT").write_text(commit + "\n", encoding="utf-8")
        self._restore_workspace(target, preexisting)

    @staticmethod
    def _clear_snapshot_paths(target: pathlib.Path, snapshot: List[Tuple[str, bytes, int]]) -> None:
        for relative, _payload, _mode in snapshot:
            destination = target / pathlib.Path(*relative.split("/"))
            try:
                if destination.is_file() or destination.is_symlink():
                    destination.unlink()
            except OSError:
                continue

    @staticmethod
    def _snapshot_workspace(target: pathlib.Path) -> List[Tuple[str, bytes, int]]:
        """Keep optimistic pre-clone files in memory until Git is ready."""
        snapshot: List[Tuple[str, bytes, int]] = []
        try:
            candidates = target.rglob("*")
        except OSError:
            return snapshot
        for candidate in candidates:
            if candidate.is_symlink() or not candidate.is_file():
                continue
            try:
                relative = candidate.relative_to(target).as_posix()
                mode = stat.S_IMODE(candidate.stat().st_mode)
                snapshot.append((relative, candidate.read_bytes(), mode))
            except OSError:
                continue
        return snapshot

    @staticmethod
    def _restore_workspace(target: pathlib.Path, snapshot: List[Tuple[str, bytes, int]]) -> None:
        """Overlay files written before clone/init without persisting plaintext."""
        for relative, payload, mode in snapshot:
            destination = target / pathlib.Path(*relative.split("/"))
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.is_dir() and not destination.is_symlink():
                for child in sorted(destination.rglob("*"), reverse=True):
                    if child.is_file() or child.is_symlink():
                        child.unlink()
                    elif child.is_dir():
                        child.rmdir()
                destination.rmdir()
            destination.write_bytes(payload)
            os.chmod(destination, mode)

    def _workspace_create(self, step: Dict[str, Any]) -> None:
        target = ensure_inside(str(step.get("target", "")), self.home)
        if target.exists() and not target.is_dir():
            raise EdgeError("workspace target is not a directory: %s" % target)
        target.mkdir(parents=True, exist_ok=True)

    def _apply_patch(self, step: Dict[str, Any]) -> None:
        cwd = self._validate_cwd(str(step.get("cwd") or ""))
        if not cwd or not cwd.exists():
            raise EdgeError("patch working directory does not exist")
        patch_b64 = step.get("patch_b64")
        if not patch_b64:
            return
        run_checked(["git", "apply", "-"], cwd=cwd, input_data=base64.b64decode(str(patch_b64)), timeout=900)

    @staticmethod
    def _file_hash(path: pathlib.Path) -> Optional[str]:
        if not path.exists():
            return None
        if not path.is_file():
            raise EdgeError("shadow target is not a regular file: %s" % path)
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _shadow_manifest_path(self, target: pathlib.Path) -> pathlib.Path:
        digest = hashlib.sha256(str(target).encode("utf-8")).hexdigest()[:32]
        return self.state_root / "shadows" / (digest + ".json")

    def _load_shadow_manifest(self, target: pathlib.Path) -> Dict[str, Any]:
        path = self._shadow_manifest_path(target)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            value = {}
        files = value.get("files") if isinstance(value, dict) else None
        return {"version": 1, "target": str(target), "files": files if isinstance(files, dict) else {}}

    def _save_shadow_manifest(self, target: pathlib.Path, manifest: Dict[str, Any]) -> None:
        path = self._shadow_manifest_path(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json_dump(manifest) + "\n", encoding="utf-8")
        os.replace(str(temporary), str(path))

    def _shadow_relative_path(self, target: pathlib.Path, value: Any) -> Tuple[str, pathlib.Path]:
        relative = str(value or "")
        candidate = pathlib.PurePosixPath(relative)
        if not relative or candidate.is_absolute() or ".." in candidate.parts:
            raise EdgeError("shadow entry has an unsafe relative path")
        destination = (target / pathlib.Path(*candidate.parts)).resolve(strict=False)
        try:
            destination.relative_to(target.resolve())
        except ValueError as exc:
            raise EdgeError("shadow entry escapes its target") from exc
        return "/".join(candidate.parts), destination

    @staticmethod
    def _atomic_write(path: pathlib.Path, payload: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(".%s.git-shadow-%s.tmp" % (path.name, os.getpid()))
        try:
            with temporary.open("wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(str(temporary), str(path))
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def _shadow_sync_unlocked(self, job_id: str, step: Dict[str, Any]) -> Dict[str, Any]:
        """Apply .gitshadow entries with compare-and-swap conflict protection."""
        target = ensure_inside(str(step.get("target") or ""), self.home)
        entries = step.get("entries")
        if not isinstance(entries, list):
            raise EdgeError("shadow.sync requires an entries array")
        target.mkdir(parents=True, exist_ok=True)
        manifest = self._load_shadow_manifest(target)
        applied = 0
        conflicts: List[str] = []

        for raw_entry in entries:
            if not isinstance(raw_entry, dict):
                raise EdgeError("shadow entry must be an object")
            relative, destination = self._shadow_relative_path(target, raw_entry.get("path"))
            base_hash = raw_entry.get("base_hash") or None
            local_hash = str(raw_entry.get("local_hash") or "")
            deleted = bool(raw_entry.get("deleted"))
            payload = b""
            if not deleted:
                encoded = raw_entry.get("content_b64")
                if not isinstance(encoded, str):
                    raise EdgeError("shadow entry requires content_b64")
                try:
                    payload = base64.b64decode(encoded, validate=True)
                except (ValueError, TypeError) as exc:
                    raise EdgeError("shadow entry contains invalid base64") from exc
                if hashlib.sha256(payload).hexdigest() != local_hash:
                    raise EdgeError("shadow entry hash does not match content: %s" % relative)

            remote_hash = self._file_hash(destination)
            if remote_hash == local_hash and not deleted:
                manifest["files"][relative] = {"synced_hash": local_hash, "updated_at": now_iso()}
                self.emit_event(job_id, "shadow.applied", path=relative, local_hash=local_hash, idempotent=True)
                applied += 1
                continue
            if deleted and remote_hash is None:
                manifest["files"][relative] = {"synced_hash": None, "updated_at": now_iso()}
                self.emit_event(job_id, "shadow.applied", path=relative, local_hash="", deleted=True, idempotent=True)
                applied += 1
                continue

            if remote_hash != base_hash:
                target_key = hashlib.sha256(str(target).encode("utf-8")).hexdigest()[:32]
                conflict_path = self.state_root / "conflicts" / target_key / safe_job_id(job_id) / pathlib.Path(*relative.split("/"))
                if not deleted:
                    self._atomic_write(conflict_path, payload)
                else:
                    self._atomic_write(conflict_path, b"git-shadow deletion conflict\n")
                conflicts.append(relative)
                self.emit_event(
                    job_id,
                    "shadow.conflict",
                    path=relative,
                    base_hash=base_hash,
                    local_hash=local_hash,
                    remote_hash=remote_hash,
                    conflict_path=str(conflict_path),
                )
                continue

            if deleted:
                try:
                    destination.unlink()
                except FileNotFoundError:
                    pass
            else:
                self._atomic_write(destination, payload)
            manifest["files"][relative] = {"synced_hash": None if deleted else local_hash, "updated_at": now_iso()}
            self.emit_event(job_id, "shadow.applied", path=relative, local_hash=local_hash, deleted=deleted)
            applied += 1

        self._save_shadow_manifest(target, manifest)
        if conflicts:
            raise EdgeError("shadow CAS conflict: %s" % ", ".join(conflicts))
        return {"applied": applied, "conflicts": 0}

    def _shadow_sync(self, job_id: str, step: Dict[str, Any]) -> Dict[str, Any]:
        """Serialize concurrent jobs targeting the same shadow workspace."""
        target = ensure_inside(str(step.get("target") or ""), self.home)
        lock_path = self._shadow_manifest_path(target).with_suffix(".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+", encoding="utf-8") as lock_stream:
            fcntl.flock(lock_stream.fileno(), fcntl.LOCK_EX)
            try:
                return self._shadow_sync_unlocked(job_id, step)
            finally:
                fcntl.flock(lock_stream.fileno(), fcntl.LOCK_UN)

    def _shadow_pull(self, job_id: str, step: Dict[str, Any]) -> Dict[str, Any]:
        """Return remote Shadow changes without overwriting local state blindly."""
        target = ensure_inside(str(step.get("target") or ""), self.home)
        entries = step.get("entries")
        if not isinstance(entries, list):
            raise EdgeError("shadow.pull requires an entries array")
        entries = list(entries)
        known_paths = {str(item.get("path")) for item in entries if isinstance(item, dict)}
        patterns = step.get("patterns") or []
        if not isinstance(patterns, list) or not all(isinstance(pattern, str) for pattern in patterns):
            raise EdgeError("shadow.pull patterns must be an array of strings")
        if patterns:
            for candidate in target.rglob("*"):
                if not candidate.is_file() or candidate.is_symlink():
                    continue
                relative = candidate.relative_to(target).as_posix()
                if ".git" in pathlib.PurePosixPath(relative).parts:
                    continue
                if any(
                    relative.startswith(pattern.rstrip("/") + "/")
                    if pattern.endswith("/")
                    else fnmatch.fnmatch(relative, pattern) or fnmatch.fnmatch(candidate.name, pattern)
                    for pattern in patterns
                ) and relative not in known_paths:
                    entries.append({"path": relative, "base_hash": None, "local_hash": None})
                    known_paths.add(relative)
        lock_path = self._shadow_manifest_path(target).with_suffix(".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        changed = 0
        conflicts: List[str] = []
        with lock_path.open("a+", encoding="utf-8") as lock_stream:
            fcntl.flock(lock_stream.fileno(), fcntl.LOCK_EX)
            try:
                for raw_entry in entries:
                    if not isinstance(raw_entry, dict):
                        raise EdgeError("shadow pull entry must be an object")
                    relative, destination = self._shadow_relative_path(target, raw_entry.get("path"))
                    base_hash = raw_entry.get("base_hash") or None
                    local_hash = raw_entry.get("local_hash") or None
                    remote_hash = self._file_hash(destination)
                    if remote_hash == local_hash or remote_hash == base_hash:
                        continue

                    payload = destination.read_bytes() if remote_hash is not None else b""
                    if local_hash == base_hash:
                        self.emit_event(
                            job_id,
                            "shadow.remote",
                            path=relative,
                            remote_hash=remote_hash or "",
                            deleted=remote_hash is None,
                            content_b64="" if remote_hash is None else base64.b64encode(payload).decode("ascii"),
                        )
                        changed += 1
                    else:
                        target_key = hashlib.sha256(str(target).encode("utf-8")).hexdigest()[:32]
                        conflict_path = self.state_root / "conflicts" / target_key / safe_job_id(job_id) / pathlib.Path(*relative.split("/"))
                        if remote_hash is not None:
                            self._atomic_write(conflict_path, payload)
                        else:
                            self._atomic_write(conflict_path, b"git-shadow remote deletion conflict\n")
                        self.emit_event(
                            job_id,
                            "shadow.conflict",
                            path=relative,
                            base_hash=base_hash,
                            local_hash=local_hash or "",
                            remote_hash=remote_hash or "",
                            deleted=remote_hash is None,
                            content_b64="" if remote_hash is None else base64.b64encode(payload).decode("ascii"),
                            conflict_path=str(conflict_path),
                            direction="remote-to-local",
                        )
                        conflicts.append(relative)
            finally:
                fcntl.flock(lock_stream.fileno(), fcntl.LOCK_UN)
        if conflicts:
            raise EdgeError("shadow pull conflict: %s" % ", ".join(conflicts))
        return {"changed": changed, "conflicts": 0}

    def _headers(
        self,
        bearer_override: Optional[str] = None,
        api_key_override: Optional[str] = None,
    ) -> Dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        bearer = (bearer_override if bearer_override is not None else os.environ.get("GIT_SHADOW_CLOUDCLI_TOKEN", "")).strip()
        api_key = (api_key_override if api_key_override is not None else os.environ.get("GIT_SHADOW_CLOUDCLI_API_KEY", "")).strip()
        if bearer:
            headers["Authorization"] = "Bearer " + bearer
        if api_key:
            headers["X-API-KEY"] = api_key
        return headers

    def _http_json(
        self,
        url: str,
        payload: Dict[str, Any],
        bearer_override: Optional[str] = None,
        api_key_override: Optional[str] = None,
    ) -> Dict[str, Any]:
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=self._headers(bearer_override, api_key_override),
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                body = response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            detail = scrub_text(exc.read().decode("utf-8", "replace")[-1000:])
            raise EdgeError("CloudCLI API returned HTTP %s: %s" % (exc.code, detail)) from exc
        except urllib.error.URLError as exc:
            raise EdgeError("CloudCLI API unavailable: %s" % scrub_text(str(exc.reason))) from exc
        try:
            parsed = json.loads(body) if body else {}
        except json.JSONDecodeError as exc:
            raise EdgeError("CloudCLI API returned invalid JSON") from exc
        if not isinstance(parsed, dict):
            raise EdgeError("CloudCLI API returned an invalid object")
        return parsed

    def _cloudcli_session(self, job_id: str, step: Dict[str, Any]) -> Dict[str, Any]:
        base_url = str(
            step.get("base_url")
            or os.environ.get("GIT_SHADOW_CLOUDCLI_BASE_URL")
            or "http://127.0.0.1:3001"
        ).rstrip("/")
        public_url = str(step.get("public_url") or base_url).rstrip("/")
        project_path_value = str(step.get("project_path") or "")
        provider = str(step.get("provider") or "").strip()
        project_path = ensure_inside(project_path_value, self.home) if project_path_value else None
        if project_path is None or not provider:
            raise EdgeError("CloudCLI session requires project_path and provider")
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,63}", provider):
            raise EdgeError("CloudCLI provider is invalid")

        for name, value in (("base_url", base_url), ("public_url", public_url)):
            parsed = urlsplit(value)
            if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.username or parsed.password:
                raise EdgeError("CloudCLI %s must be an absolute HTTP(S) URL" % name)
        project_path_text = str(project_path)
        bearer_token = str(step.get("token") or os.environ.get("GIT_SHADOW_CLOUDCLI_TOKEN", "")).strip() or None
        api_key = str(step.get("api_key") or os.environ.get("GIT_SHADOW_CLOUDCLI_API_KEY", "")).strip() or None

        self._http_json(
            base_url + "/api/projects/create-project",
            {"path": project_path_text},
            bearer_override=bearer_token,
            api_key_override=api_key,
        )
        session_response = self._http_json(
            base_url + "/api/providers/sessions",
            {
                "provider": provider,
                "projectPath": project_path_text,
                "initialMessage": str(step.get("initial_message") or ""),
            },
            bearer_override=bearer_token,
            api_key_override=api_key,
        )
        session_id = session_response.get("sessionId")
        if not session_id and isinstance(session_response.get("data"), dict):
            session_id = session_response["data"].get("sessionId")
        if not session_id:
            raise EdgeError("CloudCLI API did not return sessionId")
        session_id_text = str(session_id).strip()
        if not session_id_text or len(session_id_text) > 256:
            raise EdgeError("CloudCLI API returned an invalid sessionId")
        url = public_url + "/session/" + quote(session_id_text, safe="")
        self.emit_event(job_id, "session.ready", session_id=session_id_text, provider=provider, project_path=project_path_text, url=url)
        return {"session_id": session_id_text, "url": url, "provider": provider, "project_path": project_path_text}

    def _execute_step(self, job_id: str, step: Dict[str, Any]) -> Dict[str, Any]:
        action = str(step.get("action") or "")
        if action == "exec":
            self._run_streaming(job_id, str(step.get("id") or "step"), step)
            return {}
        if action == "workspace.prepare":
            self._workspace_prepare(step)
            return {}
        if action == "workspace.create":
            self._workspace_create(step)
            return {}
        if action == "shadow.sync":
            return self._shadow_sync(job_id, step)
        if action == "shadow.pull":
            return self._shadow_pull(job_id, step)
        if action == "patch.apply":
            self._apply_patch(step)
            return {}
        if action == "cloudcli.session":
            return self._cloudcli_session(job_id, step)
        raise EdgeError("unsupported action: %s" % action)

    def _run_job(self, job_id: str, request: Dict[str, Any]) -> None:
        record = self._jobs[job_id]
        journal = record["journal"]
        steps = request.get("steps")
        if not isinstance(steps, list):
            self.emit_event(job_id, "job.failed", error="steps must be an array")
            self._state(job_id, "failed", error="steps must be an array")
            return
        self._state(job_id, "running", step_count=len(steps))
        record["status"] = "running"
        self.emit_event(job_id, "job.started", step_count=len(steps))
        try:
            for index, raw_step in enumerate(steps, start=1):
                if record["cancel_event"].is_set():
                    raise EdgeError("job cancelled because the lease expired")
                if not isinstance(raw_step, dict):
                    raise EdgeError("step %s is not an object" % index)
                step = dict(raw_step)
                step_id = str(step.get("id") or "step-%s" % index)
                retries = max(0, min(3, int(step.get("retries", 0))))
                attempt = 0
                while True:
                    self.emit_event(job_id, "step.started", step=step_id, action=step.get("action"), index=index, attempt=attempt + 1)
                    try:
                        result = self._execute_step(job_id, step)
                        break
                    except Exception as exc:
                        if attempt >= retries:
                            raise
                        attempt += 1
                        self.emit_event(
                            job_id,
                            "step.retry",
                            step=step_id,
                            action=step.get("action"),
                            attempt=attempt + 1,
                            error=str(exc)[-2000:],
                        )
                        time.sleep(min(2, 0.25 * attempt))
                if step.get("action") == "cloudcli.session" and isinstance(result, dict):
                    record["session"] = {
                        key: result[key]
                        for key in ("session_id", "url", "provider", "project_path")
                        if key in result
                    }
                self.emit_event(job_id, "step.succeeded", step=step_id, result=result)
            self._state(job_id, "completed")
            record["status"] = "completed"
            try:
                os.unlink(record["lease_path"])
            except OSError:
                pass
            self.emit_event(job_id, "job.completed")
        except Exception as exc:
            message = str(exc)[-4000:]
            failure_data: Dict[str, Any] = {"error": message}
            session = record.get("session")
            if session:
                failure_data.update({"partial": True, "session": session})
                self.emit_event(job_id, "job.partial", phase="cloudcli.session", session=session, error=message)
            self._state(job_id, "failed", **failure_data)
            record["status"] = "failed"
            try:
                os.unlink(record["lease_path"])
            except OSError:
                pass
            self.emit_event(job_id, "job.failed", **failure_data)

    def submit(self, request: Dict[str, Any]) -> None:
        job_id = safe_job_id(request.get("job_id"))
        if job_id in self._jobs:
            self.emit_control({"type": "accepted", "job_id": job_id, "reused": True})
            return
        journal = EventJournal(self.state_root, job_id, self.max_event_log_bytes)
        if journal.state_path.exists():
            try:
                previous_state = json.loads(journal.state_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                previous_state = {}
            if previous_state.get("status") in ("completed", "failed", "interrupted"):
                self.emit_control(
                    {
                        "type": "accepted",
                        "job_id": job_id,
                        "reused": True,
                        "status": previous_state.get("status"),
                    }
                )
                return
        journal.save_request(request)
        lease_ttl = max(1, int(request.get("lease_ttl", DEFAULT_TTL)))
        lease_path = journal.directory / "lease"
        lease_path.touch()
        record = {
            "journal": journal,
            "request": request,
            "started": time.time(),
            "status": "accepted",
            "lease_ttl": lease_ttl,
            "lease_path": str(lease_path),
            "cancel_event": threading.Event(),
        }
        self._jobs[job_id] = record
        journal.save_state({"job_id": job_id, "status": "accepted", "created_at": now_iso()})
        self.emit_control({"type": "accepted", "job_id": job_id})
        worker = threading.Thread(target=self._run_job, args=(job_id, request), daemon=True)
        record["worker"] = worker
        worker.start()

    def resume(self, message: Dict[str, Any]) -> None:
        job_id = safe_job_id(message.get("job_id"))
        after_seq = int(message.get("after_seq", 0))
        journal = EventJournal(self.state_root, job_id, self.max_event_log_bytes)
        first_seq = journal.first_seq()
        self.emit_control(
            {
                "type": "replay.begin",
                "job_id": job_id,
                "after_seq": after_seq,
                "first_seq": first_seq,
                "truncated": bool(first_seq and after_seq < first_seq - 1),
            }
        )
        for event in journal.replay(after_seq):
            self.emit_control(event)
        self.emit_control({"type": "replay.end", "job_id": job_id, "last_seq": journal._seq})

    def status(self, message: Dict[str, Any]) -> None:
        job_id = safe_job_id(message.get("job_id"))
        state_path = self.state_root / "runs" / job_id / "state.json"
        if not state_path.exists():
            self.emit_control({"type": "status", "job_id": job_id, "found": False})
            return
        self.emit_control({"type": "status", "job_id": job_id, "found": True, "state": json.loads(state_path.read_text(encoding="utf-8"))})

    def serve(self) -> int:
        self.emit_control({"type": "ready", "protocol": 1, "pid": os.getpid()})
        active_threads: List[threading.Thread] = []
        try:
            for line in sys.stdin:
                if not line.strip():
                    continue
                try:
                    message = json.loads(line)
                    if not isinstance(message, dict):
                        raise EdgeError("message must be an object")
                    kind = message.get("type")
                    if kind == "submit":
                        self.submit(message)
                    elif kind == "resume":
                        self.resume(message)
                    elif kind == "status":
                        self.status(message)
                    elif kind == "heartbeat":
                        job_id = message.get("job_id")
                        record = self._jobs.get(str(job_id)) if job_id else None
                        if record and record.get("lease_path"):
                            pathlib.Path(record["lease_path"]).touch()
                        self.emit_control({"type": "heartbeat.ack", "job_id": job_id, "at": now_iso()})
                    elif kind == "shutdown":
                        break
                    else:
                        self.emit_control({"type": "error", "error": "unsupported message type"})
                except Exception as exc:
                    self.emit_control({"type": "error", "error": str(exc)[-4000:]})
        finally:
            active_threads = [record.get("worker") for record in self._jobs.values() if record.get("worker")]
            for worker in active_threads:
                if worker:
                    worker.join()
            self._stop.set()
            self._lease_thread.join(timeout=2)
        return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="git-shadow VPS edge executor")
    parser.add_argument("--rpc", action="store_true", help="serve JSONL RPC on stdin/stdout")
    parser.add_argument("--state-dir", default=None)
    parser.add_argument("--max-event-log-bytes", type=int, default=None)
    parser.add_argument("--max-completed-runs", type=int, default=None)
    options = parser.parse_args(argv)
    if not options.rpc:
        parser.error("--rpc is required")
    return EdgeExecutor(
        options.state_dir,
        max_event_log_bytes=options.max_event_log_bytes,
        max_completed_runs=options.max_completed_runs,
    ).serve()


if __name__ == "__main__":
    raise SystemExit(main())
