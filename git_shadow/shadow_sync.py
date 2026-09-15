"""Layered .gitshadow synchronization state.

The Git lane owns tracked source files.  This module only builds and consumes
the manifest for the private shadow lane; it never persists secret contents.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import pathlib
from typing import Any, Dict, Iterable, List, Optional


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def project_key(project_root: str) -> str:
    """Return a stable, non-secret identifier for a local project path."""
    return sha256_bytes(str(pathlib.Path(project_root).resolve()).encode("utf-8"))[:32]


class ShadowManifestStore:
    """Local acknowledgement state for the .gitshadow lane.

    Only hashes and relative paths are stored.  Secret file bytes remain in
    the working tree and are sent in the current job request over SSH.
    """

    def __init__(self, project_root: str, state_root: Optional[str] = None):
        self.project_root = pathlib.Path(project_root).resolve()
        default_root = pathlib.Path.home() / ".local/state/git-shadow"
        self.state_root = pathlib.Path(
            state_root or os.environ.get("GIT_SHADOW_LOCAL_STATE_DIR", str(default_root))
        ).expanduser()
        self.path = self.state_root / "shadows" / (project_key(str(self.project_root)) + ".json")
        self._data = self._load()

    def _load(self) -> Dict[str, Any]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            value = {}
        if not isinstance(value, dict):
            value = {}
        files = value.get("files")
        if not isinstance(files, dict):
            files = {}
        return {"version": 1, "project_root": str(self.project_root), "files": files}

    def base_hash(self, relative_path: str) -> Optional[str]:
        record = self._data["files"].get(relative_path)
        if not isinstance(record, dict):
            return None
        value = record.get("synced_hash")
        return str(value) if value else None

    def build_entries(self, shadow_files: Iterable[str]) -> List[Dict[str, Any]]:
        entries: List[Dict[str, Any]] = []
        for relative_path in sorted(set(shadow_files)):
            path = self.project_root / relative_path
            if not path.is_file():
                continue
            payload = path.read_bytes()
            entries.append(
                {
                    "path": relative_path,
                    "base_hash": self.base_hash(relative_path),
                    "local_hash": sha256_bytes(payload),
                    "content_b64": base64.b64encode(payload).decode("ascii"),
                    "deleted": False,
                }
            )
        return entries

    def consume_event(self, event: Dict[str, Any]) -> None:
        """Advance local acknowledgement state only after remote CAS success."""
        if event.get("event") != "shadow.applied":
            return
        relative_path = str(event.get("path") or "")
        local_hash = str(event.get("local_hash") or "")
        if not relative_path or not local_hash:
            return
        self._data["files"][relative_path] = {
            "synced_hash": local_hash,
            "updated_at": event.get("at"),
        }
        self._save()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(self._data, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(str(temporary), str(self.path))
