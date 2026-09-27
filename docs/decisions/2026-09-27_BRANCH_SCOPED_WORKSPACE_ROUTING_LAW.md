# Branch-Scoped Workspace Routing Law

Date: 2026-09-27

## Context

git-shadow previously treated the remote workspace as a repository-scoped path:

~~~
~/wkspace/<repo>
~~~

That is not a sufficient identity once the local checkout represents a named Git
branch. A local checkout of repository AI on branch foo/bar would collide with
main or any other branch because all of them resolved to the same remote path.

The local binding store had the same defect: its key was only the local project
root, so switching branches could recover a remote_dir that belonged to the
previous branch.

## Decision

A named Git workspace is identified by the pair:

~~~
(repository, branch)
~~~

The branch namespace is preserved as a directory namespace.

Example:

~~~
repository: AI
branch:     foo/bar

remote workspace:
~/wkspace/AI/foo/bar
~~~

A plain non-Git folder has no branch identity and therefore remains:

~~~
~/wkspace/<project>
~~~

RepoState's synthetic plain-folder main value must never create a fake /main
directory.

## Routing function

All automatic routing must use one canonical function:

~~~python
workspace_relative_path(repo_name, branch, is_git)
~~~

Examples:

~~~text
AI + main       -> AI/main
AI + foo/bar    -> AI/foo/bar
notes + no Git  -> notes
~~~

Traversal components and unsafe path separators are rejected.

## Binding identity

Bindings must use the same branch identity.

Old:

~~~text
binding key = local project root
~~~

New:

~~~text
binding key = local project root + named branch
~~~

Therefore the same checkout path can move between main and foo/bar without
reusing the other branch's remote_dir or launch preference.

Legacy path-only bindings are not silently reused for a named branch. Silent
reuse would recreate the original collision.

## Git transport

A branch-scoped workspace must also be branch-scoped at the Git layer.

New workspaces use:

~~~bash
git clone --branch <branch> --single-branch ...
~~~

When workspace.create has already created the target directory for an early
CloudCLI Session, workspace.prepare initializes Git in place and writes:

~~~text
remote.origin.fetch =
+refs/heads/<branch>:refs/remotes/origin/<branch>
~~~

Subsequent refreshes fetch that explicit refspec only.

The directory and the Git ref namespace therefore describe the same object.

## Shadow implication

Shadow CAS acknowledgement state already includes:

~~~text
remote_host + remote target directory
~~~

Because the target directory is now branch-scoped, the existing Shadow manifest
store automatically isolates:

~~~text
host + AI/main
host + AI/foo/bar
~~~

No second branch key is needed inside ShadowManifestStore.

## CloudCLI implication

CloudCLI receives the exact branch-scoped project_path. Sessions for AI/main and
AI/foo/bar are distinct projects/workspaces instead of two UI sessions pointing
at one mutable checkout.

## Discovery

Remote discovery must search the exact route first:

~~~text
~/wkspace/AI/foo/bar
~/workspace/AI/foo/bar
~/projects/AI/foo/bar
~~~

If none exists, git-shadow chooses the same exact route for creation. The next
run therefore converges on the same path instead of inventing another workspace.

## Invariants

1. One named Git branch maps to one deterministic remote directory.
2. Two different branches of the same repository never share a remote checkout.
3. Branch slash hierarchy is preserved in the filesystem route.
4. A branch-scoped remote checkout fetches only its own branch refspec.
5. Plain folders do not gain a fake branch directory.
6. Binding memory is branch-aware.
7. Shadow state remains isolated by the resulting target path.
8. Git/Shadow/CloudCLI must all receive the same target directory identity.

## Migration

Existing legacy workspaces such as:

~~~text
~/wkspace/AI
~~~

are left untouched. git-shadow does not automatically move them because active
CloudCLI sessions or user files may still reference that path.

The next named-branch run creates or discovers the new deterministic route:

~~~text
~/wkspace/AI/<branch>
~~~

After validation, the legacy workspace can be removed manually.

## Tests

Regression coverage must include:

- foo/bar -> AI/foo/bar routing;
- plain-folder routing without /main;
- path traversal rejection;
- branch-aware RemoteProbe route;
- same local root with two independent branch bindings;
- legacy path-only binding not reused for a named branch;
- branch-scoped edge preparation with a single remote fetch refspec.
