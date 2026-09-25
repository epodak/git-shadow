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


def auto_fast_forward_pull(root: str, remote: str = "origin", branch: str = "") -> Dict[str, Any]:
    """Fetch and fast-forward a clean local branch from its Git remote.

    The result has a stable ``status`` value:

    ``updated``
        One or more remote commits were fast-forwarded locally.
    ``up-to-date``
        The local branch already points at the remote branch.
    ``blocked``
        Local tracked or untracked work exists, so merge is not run.
        Includes ``has_remote_updates``: True if remote actually has commits ahead.
    ``local_ahead``
        Local commits exist that are ahead of the remote.
    ``diverged``
        Both local and remote have commits that diverged.
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

    # Fetch remote refs safely (read-only to working directory, does not touch working files)
    fetched = _run_git(root, ["fetch", "--prune", remote, branch])
    if fetched.returncode != 0:
        return {"status": "error", "reason": "fetch-failed", "message": fetched.stderr.strip()}

    remote_ref = "%s/%s" % (remote, branch)
    before = _run_git(root, ["rev-parse", "HEAD"])
    target = _run_git(root, ["rev-parse", remote_ref])
    if before.returncode != 0 or target.returncode != 0:
        return {"status": "error", "reason": "rev-parse-failed", "message": (target.stderr or before.stderr).strip()}

    before_head = before.stdout.strip()
    target_head = target.stdout.strip()

    # Check worktree dirty status (staged, unstaged, or untracked)
    dirty = _run_git(root, ["status", "--porcelain", "--untracked-files=all"])
    is_dirty = bool(dirty.returncode == 0 and dirty.stdout.strip())

    if before_head == target_head:
        if is_dirty:
            return {"status": "blocked", "reason": "local-worktree-dirty", "has_remote_updates": False}
        return {"status": "up-to-date", "commit": before_head[:7]}

    # Check ancestry: is remote ahead of local?
    is_remote_ahead = _run_git(root, ["merge-base", "--is-ancestor", before_head, target_head]).returncode == 0
    if is_remote_ahead:
        # Remote has new commits!
        if is_dirty:
            # Rule 3(b): Local has staged or unstaged changes, defer merge and report
            return {
                "status": "blocked",
                "reason": "local-worktree-dirty",
                "has_remote_updates": True,
                "remote_commit": target_head[:7],
                "local_commit": before_head[:7],
            }

        # Rule 3(a): Local is clean, fast-forward merge!
        merged = _run_git(root, ["merge", "--ff-only", remote_ref])
        if merged.returncode != 0:
            return {
                "status": "error",
                "reason": "non-fast-forward",
                "message": (merged.stderr or merged.stdout).strip(),
            }
        return {
            "status": "updated",
            "remote_commit": target_head[:7],
            "local_commit": before_head[:7],
            "message": (merged.stdout or merged.stderr).strip(),
        }

    is_local_ahead = _run_git(root, ["merge-base", "--is-ancestor", target_head, before_head]).returncode == 0
    if is_local_ahead:
        # Rule 2: Local has committed changes ahead of remote!
        return {
            "status": "local_ahead",
            "local_commit": before_head[:7],
            "remote_commit": target_head[:7],
        }

    return {
        "status": "diverged",
        "local_commit": before_head[:7],
        "remote_commit": target_head[:7],
    }

