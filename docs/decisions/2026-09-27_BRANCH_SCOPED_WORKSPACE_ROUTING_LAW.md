# Branch-Scoped Workspace Routing Law

Date: 2026-09-27

## Context

Older git-shadow versions treated the repository name as the complete remote
workspace identity:

~~~text
~/wkspace/<repo>
~~~

That identity is insufficient once one local checkout moves between named Git
branches. AI/main and AI/foo/bar would otherwise share Git checkout state,
Shadow CAS acknowledgement, CloudCLI project paths and local binding memory.

## Decision

For Git repositories:

~~~text
WorkspaceIdentity = Repository + Branch
WorkspaceRoute    = <workspace-root>/<repo>/<branch segments>
~~~

Example:

~~~text
repository: AI
branch:     foo/bar

remote:
~/wkspace/AI/foo/bar
~~~

Branch slashes are intentionally preserved as directory hierarchy.

For plain non-Git folders:

~~~text
WorkspaceIdentity = Project
WorkspaceRoute    = <workspace-root>/<project>
~~~

RepoState's synthetic main placeholder must never create a fake branch directory
for a plain folder.

## Canonical routing function

All automatic routing uses the same canonical function:

~~~text
workspace_relative_path(repo_name, branch, is_git)
~~~

Examples:

~~~text
AI + main       -> AI/main
AI + foo/bar    -> AI/foo/bar
notes + no Git  -> notes
~~~

Traversal components and unsafe control/path characters are rejected.

## Binding identity

Bindings use the same branch identity.

Old:

~~~text
binding key = local project root
~~~

New:

~~~text
binding key = local project root + named branch
~~~

Therefore one local checkout can move between main and foo/bar without reusing
the other branch's remote_dir or launch preference.

Legacy path-only bindings are not silently reused for a branch-specific request.

## Git transport

A branch-scoped workspace is also branch-scoped at the Git layer.

New workspaces use:

~~~bash
git clone --branch <branch> --single-branch ...
~~~

Existing workspaces fetch the explicit branch refspec:

~~~text
+refs/heads/<branch>:refs/remotes/origin/<branch>
~~~

When workspace.create has already created an empty target for an early CloudCLI
session, workspace.prepare initializes Git in place and persists the same
single-branch remote.origin.fetch rule.

The filesystem route and the Git ref namespace therefore describe the same
branch scope.

## Legacy repo-root migration

A previous git-shadow version may already have:

~~~text
~/wkspace/AI/.git
~~~

Creating ~/wkspace/AI/foo/bar directly in that state would put one workspace
inside another Git worktree. That is invalid.

The Edge plan therefore inserts a typed migration action before workspace.create:

~~~text
workspace.route
→ workspace.create
→ cloudcli.session (optional)
→ workspace.prepare
→ shadow.sync
~~~

workspace.route performs the following operation only for automatically routed
Git workspaces:

1. Check whether <workspace>/<repo> itself is a Git worktree.
2. Read the actual current branch of that legacy worktree.
3. Rename the whole worktree to a temporary sibling path.
4. Recreate <workspace>/<repo> as a container directory.
5. Move the untouched old worktree into its own branch route.
6. Continue preparing the requested branch route.

Example:

~~~text
before:
~/wkspace/AI/.git                  # actual branch = main

after migration:
~/wkspace/AI/main/.git

requested later:
~/wkspace/AI/foo/bar/.git
~~~

The migration moves the complete worktree. It does not clean, reset, stash, or
copy it, so dirty and untracked files move with the workspace.

If migration fails, git-shadow removes only empty directories created by that
migration and restores the staged legacy worktree whenever rollback is possible.
It never deletes concurrent user content during rollback.

Explicit -d/--dest remains user-owned routing and is not automatically migrated.

## Shadow implication

Shadow acknowledgement already scopes state by:

~~~text
remote host + resolved remote target
~~~

Because the target now includes the branch route, the existing Shadow manifest
store naturally separates:

~~~text
host-a + ~/wkspace/AI/main
host-a + ~/wkspace/AI/foo/bar
~~~

No second branch field is required inside ShadowManifestStore.

## CloudCLI implication

CloudCLI receives the exact same branch-scoped project_path. AI/main and
AI/foo/bar therefore become distinct project/session paths instead of two
sessions pointing at one mutable checkout.

## Discovery

Remote discovery searches the exact branch route:

~~~text
~/wkspace/AI/foo/bar
~/workspace/AI/foo/bar
~/projects/AI/foo/bar
~~~

If none exists, git-shadow chooses the same deterministic route for creation.
The next run converges on that same path.

## Invariants

1. One named Git branch maps to one deterministic automatic remote directory.
2. Two different branches of one repository never share a remote checkout.
3. Branch slash hierarchy is preserved in the filesystem route.
4. A branch-scoped checkout fetches only its own branch refspec.
5. Plain folders do not gain a synthetic main directory.
6. Binding memory is branch-aware.
7. A branch workspace is never created inside a legacy repo-root Git worktree.
8. Legacy migration preserves dirty and untracked files.
9. Shadow, CloudCLI, Git and binding layers observe the same resolved workspace.
10. Explicit custom destinations remain user-owned and are not rewritten.

## Regression coverage

Tests cover:

- foo/bar -> AI/foo/bar routing;
- plain-folder routing without /main;
- path traversal rejection;
- branch-aware RemoteProbe routing;
- same local root with independent main and foo/bar bindings;
- legacy path-only binding not reused for a named branch;
- workspace.route appearing before workspace.create;
- legacy repo-root migration preserving dirty/untracked files;
- idempotent second workspace.route;
- single-branch clone/fetch refspec behavior.
