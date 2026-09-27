"""
git_shadow.workspace_identity
Deterministic identity and routing rules for remote workspaces.

A Git workspace is not identified by repository name alone.  Its stable identity
is (repository, branch).  Branch slashes are intentionally preserved as
directory boundaries, so branch "foo/bar" maps to "repo/foo/bar".
"""

from __future__ import annotations

import re
from typing import List, Optional


_UNSAFE_COMPONENT = re.compile(r"[\\\x00-\x1f\x7f]")


def _validate_component(value: str, label: str) -> str:
    value = str(value or "")
    if not value or value in (".", ".."):
        raise ValueError("%s contains an empty or traversal component" % label)
    if "/" in value or _UNSAFE_COMPONENT.search(value):
        raise ValueError("%s contains an unsafe path component: %r" % (label, value))
    return value


def branch_parts(branch: str) -> List[str]:
    """Return a branch name as safe POSIX path components.

    Git branch namespaces already use '/', and git-shadow deliberately preserves
    that hierarchy in the remote workspace route.
    """
    value = str(branch or "").strip()
    if value.startswith("refs/heads/"):
        value = value[len("refs/heads/"):]
    if not value or value.startswith("/") or value.endswith("/") or "//" in value:
        raise ValueError("branch is not routable: %r" % branch)

    parts = value.split("/")
    return [_validate_component(part, "branch") for part in parts]


def branch_route(branch: str) -> str:
    return "/".join(branch_parts(branch))


def workspace_relative_path(
    repo_name: str,
    branch: Optional[str] = None,
    is_git: bool = True,
) -> str:
    """Return the path below the chosen workspace root.

    Git:
        AI + foo/bar -> AI/foo/bar

    Plain folder:
        notes -> notes

    Plain folders intentionally do not inherit RepoState's synthetic "main"
    placeholder because they have no Git branch identity.
    """
    repo = _validate_component(str(repo_name or ""), "repository")
    if not is_git:
        return repo
    if not branch:
        raise ValueError("Git workspace requires a branch identity")
    return repo + "/" + branch_route(branch)


def workspace_display_identity(
    repo_name: str,
    branch: Optional[str] = None,
    is_git: bool = True,
) -> str:
    if not is_git:
        return _validate_component(str(repo_name or ""), "repository")
    return "%s@%s" % (
        _validate_component(str(repo_name or ""), "repository"),
        branch_route(str(branch or "")),
    )
