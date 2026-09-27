# Remote Capability Bootstrap

> Status: implemented on feat/remote-capability-bootstrap
>
> Goal: when git-shadow can already reach a remote macOS/Linux host through SSH,
> missing optional capabilities should be prepared from the local controller instead of
> asking the user to SSH into the host and repeat manual installation steps.

## 1. Why this is a separate module

Remote capability handling is split into three responsibilities:

~~~
RemoteProbe
  observes facts
      ↓
RemoteBootstrapManager
  performs explicit side effects
      ↓
binding / cli / dev_server
  decide when to invoke the side effect
~~~

This boundary is intentional.

- probe.py stays observational and safe to call repeatedly.
- remote_bootstrap.py owns installation, service registration and repair.
- UI/CLI layers only orchestrate user intent.

The invariant is:

~~~
Preference != Capability != Bootstrap
~~~

A preference says what the user wants.
A capability probe says what is currently true.
A bootstrap operation changes the remote host so the requested capability can become true.

## 2. Public CLI

Inspect current state:

~~~bash
git shadow remote status aws cloudcli
~~~

This only probes. It does not modify the remote host.

Inspect the installation plan:

~~~bash
git shadow remote plan aws cloudcli
~~~

Ensure CloudCLI exists and is running:

~~~bash
git shadow remote ensure aws cloudcli
~~~

The local controller reuses the already configured SSH connection. No manual remote shell
session is required.

Disable automatic bootstrap for one run:

~~~bash
git shadow run aws cloudcli --no-bootstrap
~~~

Without --no-bootstrap, an explicit CloudCLI run automatically invokes the bootstrap
module when the probe reports a repairable state.

## 3. Repairable states

| Probe reason | Action |
| --- | --- |
| cloudcli-not-installed | install managed CloudCLI |
| cloudcli-service-not-running | install/repair launcher and start service |
| cloudcli-connection-refused | repair/restart managed service |
| ssh-probe-failed: ... | do not attempt installation |

SSH failure is deliberately not interpreted as an application installation problem.

## 4. Managed remote layout

The bootstrapper does not write into the projected project workspace.

~~~
~/.local/share/git-shadow/
├── apps/
│   └── cloudcli/              # managed npm prefix
├── bin/
│   └── cloudcli               # stable git-shadow managed launcher
└── runtime/
    └── nvm/                   # fallback Node runtime only when needed

~/.local/state/git-shadow/
├── cloudcli.pid               # nohup fallback only
└── logs/
    └── cloudcli.log
~~~

This keeps the application/runtime lane separate from Git tracked project files,
Shadow CAS/private files, and project dependencies.

## 5. Node runtime policy

CloudCLI is a Node application.

~~~
existing node >= 22
        │
        ├─ yes → reuse system/user Node
        │
        └─ no  → install private nvm under git-shadow runtime
                    ↓
                 Node 22.23.3
~~~

The bootstrapper does not edit .bashrc, .zshrc, or other shell startup files.

Current pins:

~~~
nvm       v0.40.5
Node      22.23.3
CloudCLI  @cloudcli-ai/cloudcli@1.37.3
~~~

Pins make a remote bootstrap reproducible. Updating them should be an explicit code change
with tests rather than an implicit latest upgrade.

## 6. Service policy

CloudCLI binds locally:

~~~
127.0.0.1:3001
~~~

The bootstrapper chooses the strongest available user-scoped supervisor.

Linux preference:

~~~
systemd --user
  ↓
~/.config/systemd/user/git-shadow-cloudcli.service
~~~

macOS preference:

~~~
launchd
  ↓
~/Library/LaunchAgents/com.git-shadow.cloudcli.plist
~~~

If neither user service manager is usable, the fallback is nohup with state under:

~~~
~/.local/state/git-shadow/cloudcli.pid
~/.local/state/git-shadow/logs/cloudcli.log
~~~

## 7. Run-time flow

For an explicit CloudCLI run:

~~~
git shadow run <host> cloudcli
            ↓
      capability probe
            ↓
   ┌────────┴─────────┐
   │                  │
available         repairable
   │                  │
   │          RemoteBootstrapManager
   │                  ↓
   │             probe again
   │                  │
   └────────┬─────────┘
            ↓
       available?
       │        │
      yes      no
       │        │
 CloudCLI      graceful
 session       sync-only
~~~

This preserves the degradation law:

~~~
CloudCLI unavailable != git-shadow host unavailable
~~~

A failed bootstrap never disables the Git/Shadow synchronization lanes.

## 8. Binding/onboarding behavior

During initial project binding:

1. git-shadow probes the selected host.
2. If CloudCLI is already reachable, Web remains the recommended option.
3. If CloudCLI is absent, stopped, or refusing connections, Web remains selectable and is
   described as automatically repairable.
4. Choosing Web invokes RemoteBootstrapManager over the existing SSH connection.
5. If bootstrap fails, the Web preference may remain saved, but run time still degrades
   safely rather than entering a retry loop.
6. If SSH itself is unavailable, terminal mode remains the safe default.

## 9. Security and mutation boundaries

The remote bootstrap contract intentionally follows these constraints:

- no sudo;
- no root package-manager mutation;
- no project-directory mutation;
- no shell startup file mutation;
- all managed app/runtime files live below git-shadow user state directories;
- CloudCLI listens on loopback by default;
- SSH remains the control plane;
- bootstrap is idempotent when the capability is already healthy.

## 10. Tests

Regression coverage includes:

- repairable vs non-repairable capability states;
- already healthy capability performs zero SSH installation work;
- missing capability performs exactly one bootstrap SSH action then reprobes;
- SSH probe failure does not attempt installation;
- bootstrap script contains no sudo;
- Linux systemd --user, macOS launchd, and nohup fallback contracts;
- pinned Node/NVM/CloudCLI versions;
- onboarding invokes bootstrap for a missing CloudCLI;
- CLI help exposes remote status|plan|ensure and --no-bootstrap.

## 11. Extension point

CloudCLI is the first adapter, not the intended final special case.

The next extraction can evolve toward:

~~~python
class RemoteCapabilityAdapter:
    name: str

    def probe(...)
    def plan(...)
    def ensure(...)
    def start(...)
    def stop(...)
    def uninstall(...)
~~~

Possible future adapters include OpenCode, code-server, Jupyter, Claude Code runtime,
Codex runtime, and project-specific databases or build toolchains.

The reusable fixed point is:

~~~
observe → plan → ensure → verify → degrade safely
~~~
