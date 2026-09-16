"""Safe automatic Git pulls for the local personal development workflow.

Git-tracked files remain owned by Git.  This module only performs a
fetch followed by a fast-forward-only merge when the local worktree is clean;
it never copies or overwrites tracked files directly.
"""

from __future__ import annotations

import subprocess
from typing import Dict, List

from .utils import NO_WINDOW_FLAG


def _run_git(root: str, args: List[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        creationflags=NO_WINDOW_FLAG,
    )


def auto_fast_forward_pull(root: str, remote: str = "origin", branch: str = "") -> Dict[str, str]:
    """Fetch and fast-forward a clean local branch from its Git remote.

    The result has a stable ``status`` value:

    ``updated``
        One or more remote commits were fast-forwarded locally.
    ``up-to-date``
        The local branch already points at the remote branch.
    ``blocked``
        Local tracked or untracked work exists, so no fetch/merge is run.
    ``skipped``
        The repository has no usable remote/branch.
    ``error``
        Fetch or fast-forward failed; local files are not force-overwritten.
    """

    if not branch:
        branch_result = _run_git(root, ["branch", "--show-current"])
        branch = branch_result.stdout.strip()
        if branch_result.returncode != 0 or not branch:
            return {"status": "skipped", "reason": "no-current-branch"}

    remote_result = _run_git(root, ["config", "--get", "remote.%s.url" % remote])
    if remote_result.returncode != 0 or not remote_result.stdout.strip():
        return {"status": "skipped", "reason": "no-remote"}

    dirty = _run_git(root, ["status", "--porcelain", "--untracked-files=all"])
    if dirty.returncode != 0:
        return {"status": "error", "reason": "git-status-failed", "message": dirty.stderr.strip()}
    if dirty.stdout.strip():
        return {"status": "blocked", "reason": "local-worktree-dirty"}

    fetched = _run_git(root, ["fetch", "--prune", remote, branch])
    if fetched.returncode != 0:
        return {"status": "error", "reason": "fetch-failed", "message": fetched.stderr.strip()}

    remote_ref = "%s/%s" % (remote, branch)
    before = _run_git(root, ["rev-parse", "HEAD"])
    target = _run_git(root, ["rev-parse", remote_ref])
    if before.returncode != 0 or target.returncode != 0:
        return {"status": "error", "reason": "rev-parse-failed", "message": (target.stderr or before.stderr).strip()}
    if before.stdout.strip() == target.stdout.strip():
        return {"status": "up-to-date"}

    merged = _run_git(root, ["merge", "--ff-only", remote_ref])
    if merged.returncode != 0:
        return {
            "status": "error",
            "reason": "non-fast-forward",
            "message": (merged.stderr or merged.stdout).strip(),
        }
    return {"status": "updated", "message": (merged.stdout or merged.stderr).strip()}
