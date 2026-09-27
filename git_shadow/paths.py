"""Central filesystem layout for git-shadow.

All git-shadow-owned local state now lives under one visible, user-owned root:

    ~/.git-shadow/

The root can be overridden with GIT_SHADOW_HOME. Legacy local state/runtime
under ~/.local/state/git-shadow and ~/.local/share/git-shadow is migrated
conservatively: existing new-path files are never overwritten.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import stat
import time
from typing import Optional


ENV_HOME = "GIT_SHADOW_HOME"
LEGACY_LOCAL_STATE = pathlib.Path.home() / ".local" / "state" / "git-shadow"
LEGACY_LOCAL_SHARE = pathlib.Path.home() / ".local" / "share" / "git-shadow"


def git_shadow_home(create: bool = True) -> pathlib.Path:
    root = pathlib.Path(
        os.environ.get(ENV_HOME, str(pathlib.Path.home() / ".git-shadow"))
    ).expanduser()
    if create:
        root.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(root, 0o700)
        except OSError:
            pass
    return root


def _move_if_absent(source: pathlib.Path, destination: pathlib.Path) -> bool:
    if not source.exists() or destination.exists():
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(destination))
    return True


def migrate_legacy_local_state(root: Optional[pathlib.Path] = None) -> pathlib.Path:
    """Move known legacy local-state entries into ~/.git-shadow.

    Migration is intentionally conservative:
    - existing destination entries win;
    - no recursive merge overwrites existing data;
    - unrecognized legacy entries are left untouched.
    """
    target = pathlib.Path(root or git_shadow_home()).expanduser()
    target.mkdir(parents=True, exist_ok=True)

    legacy = LEGACY_LOCAL_STATE
    if legacy.exists() and legacy.resolve() != target.resolve():
        for name in (
            "bindings.json",
            "shadows",
            "conflicts",
            "daemon",
            "cloudcli.pid",
            "logs",
        ):
            _move_if_absent(legacy / name, target / name)
        try:
            legacy.rmdir()
        except OSError:
            pass

    legacy_share = LEGACY_LOCAL_SHARE
    if legacy_share.exists() and legacy_share.resolve() != target.resolve():
        for name in ("bin", "apps", "runtime", "runs", "services"):
            _move_if_absent(legacy_share / name, target / name)
        try:
            legacy_share.rmdir()
        except OSError:
            pass
    return target


def local_state_root() -> pathlib.Path:
    configured = os.environ.get("GIT_SHADOW_LOCAL_STATE_DIR", "").strip()
    if configured:
        path = pathlib.Path(configured).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        return path
    return migrate_legacy_local_state()


def cleanup_test_tree(
    path: pathlib.Path,
    attempts: int = 6,
    strict: bool = True,
) -> bool:
    """Windows-friendly cleanup for test workspaces.

    rmtree(ignore_errors=True) used to silently leak directories into Home.
    This helper clears read-only bits, retries transient sharing violations,
    and by default raises if the directory still exists after all retries.
    """
    target = pathlib.Path(path)
    if not target.exists():
        return True

    def _onerror(func, failed_path, _exc_info):
        try:
            os.chmod(failed_path, stat.S_IWRITE | stat.S_IREAD)
            func(failed_path)
        except OSError:
            pass

    total_attempts = max(1, int(attempts))
    for attempt in range(total_attempts):
        try:
            shutil.rmtree(target, onerror=_onerror)
        except FileNotFoundError:
            return True
        except OSError:
            pass

        if not target.exists():
            return True
        if attempt < total_attempts - 1:
            time.sleep(0.05 * (attempt + 1))
    removed = not target.exists()
    if not removed and strict:
        raise OSError(
            "git-shadow test workspace cleanup failed after %s attempts: %s"
            % (total_attempts, target)
        )
    return removed


def test_temp_root() -> pathlib.Path:
    root = git_shadow_home() / "tmp" / "tests"
    root.mkdir(parents=True, exist_ok=True)
    return root
