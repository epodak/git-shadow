# ADR 2026-09-27 — Unified git-shadow Home Law

## Status

Accepted.

## Problem

git-shadow historically wrote state into multiple locations:

~~~text
~/.local/state/git-shadow
~/.local/share/git-shadow
~~~

Several tests also created temporary Git workspaces directly under the user's
home directory with names such as:

~~~text
~/git_shadow_workspace_xxxxxxxx
~/git_shadow_service_xxxxxxxx
~/git_shadow_cloudcli_xxxxxxxx
~~~

On Windows, a failed assertion or a still-open file/process handle can prevent
tearDown cleanup. That leaves visible test debris in the user's home directory.

This is both a UX bug and an ownership bug: one application should have one
obvious filesystem root.

## Decision

All new git-shadow-owned persistent state uses:

~~~text
~/.git-shadow/
~~~

The root can be overridden with:

~~~text
GIT_SHADOW_HOME
~~~

Local state uses the same root unless the legacy explicit override
GIT_SHADOW_LOCAL_STATE_DIR is supplied.

## Canonical layout

Not every machine uses every directory, but all git-shadow-owned data belongs
under the same root:

~~~text
~/.git-shadow/
├── bindings.json
├── shadows/
├── conflicts/
├── daemon/
├── tmp/
│   └── tests/
├── bin/
├── apps/
│   └── cloudcli/
├── runtime/
├── logs/
├── runs/
└── services/
~~~

Local controller machines mainly use bindings, shadows, conflicts, daemon and
tmp. Remote execution hosts additionally use bin, apps, runtime, logs, runs and
services.

## Test isolation law

Tests must never create git_shadow_* directories directly under Path.home().

All test workspaces are created below:

~~~text
~/.git-shadow/tmp/tests/
~~~

Tests still remove their own directories in tearDown. If cleanup fails because
Windows retains a transient handle, the leak remains inside the application
root rather than polluting the user's home directory.

## Legacy migration

Local state previously stored under:

~~~text
~/.local/state/git-shadow
~~~

is migrated conservatively. Known entries are moved only when the destination
under ~/.git-shadow does not already exist.

Existing destination data always wins. Migration never overwrites a newer
binding, Shadow manifest, conflict tree or daemon state.

Remote runtime previously used:

~~~text
~/.local/share/git-shadow
~/.local/state/git-shadow
~~~

Remote installation/bootstrap recognizes those locations as legacy migration
sources and writes all new runtime state to ~/.git-shadow.

Old locations may remain when both old and new entries exist. They are retained
rather than destructively merged.

## Compatibility

For a transition period, probe PATH may still include the legacy managed-bin
directory after ~/.git-shadow/bin. This is read compatibility only.

New installations, uploads, daemon state, CAS metadata, CloudCLI runtime and
service state must not choose a legacy path as their default destination.

## Invariants

1. One application has one default state root: ~/.git-shadow.
2. Tests never create visible git_shadow_* directories directly under Home.
3. Existing new-path state is never overwritten by automatic legacy migration.
4. Legacy path support is migration/read compatibility, not a write target.
5. Project workspaces remain separate from application state.
6. Secrets stored by Shadow remain in project files; ~/.git-shadow stores only
   acknowledgements, metadata, conflicts and managed runtime state as defined by
   each subsystem.
