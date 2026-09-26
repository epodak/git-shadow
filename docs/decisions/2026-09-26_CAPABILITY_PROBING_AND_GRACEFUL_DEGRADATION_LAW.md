# ADR — Remote Capability Probing & Graceful Degradation Law

- **Status**: Accepted
- **Date**: 2026-09-26
- **Scope**: onboarding, `git shadow run ... cloudcli`, Dev Server Web hotkey, remote capability discovery

## Context

`git-shadow` historically treated CloudCLI as a static property of a host. Onboarding always recommended Web mode, and the local Dev Server always advertised `[o]`. In reality, Git/Shadow synchronization can remain healthy while the CloudCLI process is missing, stopped, listening on another port, or temporarily refusing connections.

That mismatch created a retry trap:

```text
static "cloudcli" preference
        ↓
submit cloudcli.session
        ↓
Connection refused
        ↓
edge allow_failure skips only the Web step
        ↓
Git / Shadow synchronization succeeds
        ↓
local UI still says [o]
        ↓
blind session retry
```

The edge executor was already partially correct: `cloudcli.session` is allowed to fail without destroying the file synchronization lane. The missing piece was propagating that capability loss back into local runtime state and UI.

## Decision

### 1. Capability is runtime state, not configuration

A saved preference may request CloudCLI, but the actual ability to create a Web Session must be probed at runtime.

The lightweight probe observes:

- whether the `cloudcli` executable exists;
- the PID/port recorded by `~/.cloudcli/local-server.json`;
- whether the recorded process is alive;
- whether the local HTTP/TCP endpoint is actually reachable.

The full environment probe may remain richer, but onboarding and Web launch use this lightweight probe.

### 2. Synchronization and Web are independent capabilities

The invariant is:

```text
CloudCLI unavailable  ≠  git-shadow host unavailable
```

Git/Shadow projection must continue when the Web lane is unavailable. A CloudCLI failure removes only the Web-session morphism.

### 3. Local Web state machine

```text
unknown
  ├── positive probe ─────────────→ available
  └── negative probe ─────────────→ unavailable

available
  ├── session.ready ──────────────→ available
  └── cloudcli.session skipped ───→ degraded

unavailable/degraded
  └── explicit [o]
        ├── positive fresh probe ─→ available → one session request
        └── negative fresh probe ─→ remain degraded, no session request
```

`[o]` is a capability-dependent action. The dashboard must not advertise it while Web is unavailable/degraded.

### 4. Onboarding recommendation follows capability

If CloudCLI is reachable, Web may be the default recommendation.

If it is not reachable, terminal mode becomes the safe default. A user may still explicitly preserve CloudCLI preference, but runtime must probe again and degrade to sync-only when needed.

### 5. No blind optimistic launch

The optimistic browser launch is preserved only after a positive capability probe. If the later Session creation still fails (race, auth, provider error, etc.), the runtime marks Web as degraded and keeps synchronization alive.

## Invariants

1. A failed CloudCLI probe must never block Git/Shadow synchronization.
2. A skipped/failed CloudCLI Session must never leave a stale `web_url`.
3. The UI must not advertise `[o]` while Web capability is unavailable/degraded.
4. Pressing `[o]` after degradation must perform a fresh capability probe before any Session request.
5. A successful fresh probe may restore Web capability without restarting the sync process.

## Tests

Regression tests cover:

- positive and connection-refused capability parsing;
- SSH probe failure → clean unavailable state;
- onboarding defaults to terminal when CloudCLI is unavailable;
- `step.skipped(cloudcli.session)` → degraded state + stale URL removal;
- degraded dashboard hides `[o]`;
- explicit open action cannot blindly submit when the fresh probe is still negative.
