"""
git_shadow.remote_bootstrap
Remote capability bootstrap over the existing SSH connection.

probe.py is observational only. This module owns side effects:
detect missing capability -> install in user space -> register service -> verify.
"""

from __future__ import annotations

from typing import Any, Dict, List

from .probe import RemoteProbe


CLOUDCLI_PACKAGE = "@cloudcli-ai/cloudcli"
CLOUDCLI_VERSION = "1.37.3"
NODE_VERSION = "22.23.3"
NVM_VERSION = "v0.40.5"

_REPAIRABLE_CLOUDCLI_REASONS = {
    "cloudcli-not-installed",
    "cloudcli-service-not-running",
    "cloudcli-connection-refused",
}


class RemoteBootstrapManager:
    """Install and repair optional remote capabilities over the existing SSH lane."""

    def __init__(self, engine):
        self.engine = engine
        self.host = engine.remote_host

    def probe_cloudcli(self) -> Dict[str, Any]:
        return RemoteProbe(self.host).probe_cloudcli(self.engine)

    @staticmethod
    def can_repair_cloudcli(capability: Dict[str, Any]) -> bool:
        reason = str(capability.get("reason") or "")
        return reason in _REPAIRABLE_CLOUDCLI_REASONS

    def cloudcli_plan(self) -> List[str]:
        return [
            "detect remote OS with uname (supported: Linux, macOS)",
            "ensure Node.js >= 22; if absent/old, install user-scoped nvm %s + Node %s"
            % (NVM_VERSION, NODE_VERSION),
            "install %s@%s into ~/.git-shadow/apps/cloudcli"
            % (CLOUDCLI_PACKAGE, CLOUDCLI_VERSION),
            "write managed launcher ~/.git-shadow/bin/cloudcli",
            "bind CloudCLI to 127.0.0.1:3001",
            "register systemd --user on Linux or launchd on macOS; fall back to nohup",
            "probe http://127.0.0.1:3001 and return capability state",
        ]

    def install_command(self) -> str:
        return "git shadow remote ensure %s cloudcli" % self.host

    @staticmethod
    def _parse_markers(stdout: str) -> Dict[str, str]:
        values: Dict[str, str] = {}
        for line in stdout.splitlines():
            if not line.startswith("GS_BOOTSTRAP_") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key[len("GS_BOOTSTRAP_"):].lower()] = value.strip()
        return values

    def ensure_cloudcli(self) -> Dict[str, Any]:
        """Ensure managed CloudCLI is installed, started and reachable."""
        before = self.probe_cloudcli()
        if before.get("available"):
            return {
                "success": True,
                "changed": False,
                "before": before,
                "after": before,
                "metadata": {"service": "existing"},
                "command": self.install_command(),
            }

        reason = str(before.get("reason") or "")
        if reason.startswith("ssh-probe-failed"):
            return {
                "success": False,
                "changed": False,
                "before": before,
                "after": before,
                "metadata": {},
                "command": self.install_command(),
                "error": "SSH capability probe failed; bootstrap was not attempted.",
            }

        result = self.engine._run_ssh(
            self._cloudcli_bootstrap_script(),
            capture=True,
            check=False,
        )
        stdout = getattr(result, "stdout", "") or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", "replace")

        metadata = self._parse_markers(str(stdout))
        after = self.probe_cloudcli()
        success = bool(after.get("available"))
        response: Dict[str, Any] = {
            "success": success,
            "changed": True,
            "before": before,
            "after": after,
            "metadata": metadata,
            "command": self.install_command(),
            "exit_code": int(getattr(result, "returncode", 1)),
        }
        if not success:
            response["error"] = (
                metadata.get("error")
                or str(after.get("reason") or "")
                or ("remote bootstrap exited with code %s" % response["exit_code"])
            )
        return response

    def _cloudcli_bootstrap_script(self) -> str:
        script = r"""set -eu
umask 077

OS=$(uname -s 2>/dev/null || echo Unknown)
ARCH=$(uname -m 2>/dev/null || echo Unknown)
case "$OS" in
  Linux|Darwin) ;;
  *)
    printf 'GS_BOOTSTRAP_ERROR=unsupported-os:%s\n' "$OS"
    exit 20
    ;;
esac

ROOT="$HOME/.git-shadow"
LEGACY_SHARE="$HOME/.local/share/git-shadow"
LEGACY_STATE="$HOME/.local/state/git-shadow"

mkdir -p "$ROOT"
if [ -d "$LEGACY_SHARE" ]; then
  for name in apps bin runtime services runs; do
    if [ ! -e "$ROOT/$name" ] && [ -e "$LEGACY_SHARE/$name" ]; then
      mv "$LEGACY_SHARE/$name" "$ROOT/$name"
    fi
  done
fi
if [ -d "$LEGACY_STATE" ]; then
  for name in cloudcli.pid logs; do
    if [ ! -e "$ROOT/$name" ] && [ -e "$LEGACY_STATE/$name" ]; then
      mv "$LEGACY_STATE/$name" "$ROOT/$name"
    fi
  done
fi

RUNTIME_ROOT="$ROOT/runtime"
NVM_DIR="$RUNTIME_ROOT/nvm"
APP_ROOT="$ROOT/apps/cloudcli"
BIN_DIR="$ROOT/bin"
STATE_ROOT="$ROOT"
LOG_DIR="$ROOT/logs"
mkdir -p "$RUNTIME_ROOT" "$APP_ROOT" "$BIN_DIR" "$LOG_DIR"

export NVM_DIR
export PATH="$BIN_DIR:/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:$HOME/.local/bin:$HOME/bin:$PATH"

node_major() {
  node -p 'Number(process.versions.node.split(".")[0])' 2>/dev/null || echo 0
}

NODE_SOURCE=system
NODE_MAJOR=0
if command -v node >/dev/null 2>&1; then
  NODE_MAJOR=$(node_major)
fi

if [ "$NODE_MAJOR" -lt 22 ]; then
  NODE_SOURCE=nvm
  if ! command -v git >/dev/null 2>&1; then
    printf 'GS_BOOTSTRAP_ERROR=git-required-for-node-bootstrap\n'
    exit 21
  fi

  if [ ! -s "$NVM_DIR/nvm.sh" ]; then
    TMP_NVM="$NVM_DIR.tmp.$$"
    rm -rf "$TMP_NVM"
    git clone --quiet --depth 1 --branch __NVM_VERSION__ https://github.com/nvm-sh/nvm.git "$TMP_NVM"
    rm -rf "$NVM_DIR"
    mv "$TMP_NVM" "$NVM_DIR"
  fi

  . "$NVM_DIR/nvm.sh"
  nvm install __NODE_VERSION__ --no-progress >/dev/null
  nvm alias default __NODE_VERSION__ >/dev/null
  nvm use __NODE_VERSION__ >/dev/null
fi

if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then
  printf 'GS_BOOTSTRAP_ERROR=node-or-npm-unavailable\n'
  exit 22
fi

NODE_BIN_DIR=$(dirname "$(command -v node)")
CURRENT_MANAGED_VERSION=""
if [ -x "$APP_ROOT/bin/cloudcli" ]; then
  CURRENT_MANAGED_VERSION=$(
    PATH="$NODE_BIN_DIR:$PATH" "$APP_ROOT/bin/cloudcli" version 2>/dev/null \
      | grep -o '[0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*' \
      | head -n1 || true
  )
fi

if [ "$CURRENT_MANAGED_VERSION" != "__CLOUDCLI_VERSION__" ]; then
  PATH="$NODE_BIN_DIR:$PATH" npm install --silent --no-audit --no-fund \
    --global --prefix "$APP_ROOT" "__CLOUDCLI_PACKAGE__@__CLOUDCLI_VERSION__"
fi

if [ ! -x "$APP_ROOT/bin/cloudcli" ]; then
  printf 'GS_BOOTSTRAP_ERROR=cloudcli-install-did-not-produce-binary\n'
  exit 23
fi

cat > "$BIN_DIR/cloudcli" <<EOF
#!/bin/sh
export PATH="$NODE_BIN_DIR:$APP_ROOT/bin:\$PATH"
exec "$APP_ROOT/bin/cloudcli" "\$@"
EOF
chmod 700 "$BIN_DIR/cloudcli"

SERVICE_MODE=""
LOG_FILE="$LOG_DIR/cloudcli.log"

if [ "$OS" = "Linux" ] && command -v systemctl >/dev/null 2>&1 \
   && systemctl --user show-environment >/dev/null 2>&1; then
  mkdir -p "$HOME/.config/systemd/user"
  cat > "$HOME/.config/systemd/user/git-shadow-cloudcli.service" <<'EOF'
[Unit]
Description=CloudCLI managed by git-shadow
After=network-online.target

[Service]
Type=simple
Environment=HOST=127.0.0.1
Environment=SERVER_PORT=3001
ExecStart=%h/.git-shadow/bin/cloudcli start
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
EOF
  systemctl --user daemon-reload
  if systemctl --user enable --now git-shadow-cloudcli.service >/dev/null 2>&1; then
    SERVICE_MODE=systemd-user
  fi
fi

if [ -z "$SERVICE_MODE" ] && [ "$OS" = "Darwin" ] && command -v launchctl >/dev/null 2>&1; then
  mkdir -p "$HOME/Library/LaunchAgents"
  PLIST="$HOME/Library/LaunchAgents/com.git-shadow.cloudcli.plist"
  cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.git-shadow.cloudcli</string>
  <key>ProgramArguments</key>
  <array>
    <string>$BIN_DIR/cloudcli</string>
    <string>start</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>HOST</key><string>127.0.0.1</string>
    <key>SERVER_PORT</key><string>3001</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$LOG_FILE</string>
  <key>StandardErrorPath</key><string>$LOG_FILE</string>
</dict>
</plist>
EOF

  UID_NOW=$(id -u)
  launchctl bootout "gui/$UID_NOW/com.git-shadow.cloudcli" >/dev/null 2>&1 || true
  launchctl bootout "user/$UID_NOW/com.git-shadow.cloudcli" >/dev/null 2>&1 || true
  if launchctl bootstrap "gui/$UID_NOW" "$PLIST" >/dev/null 2>&1; then
    launchctl kickstart -k "gui/$UID_NOW/com.git-shadow.cloudcli" >/dev/null 2>&1 || true
    SERVICE_MODE=launchd-gui
  elif launchctl bootstrap "user/$UID_NOW" "$PLIST" >/dev/null 2>&1; then
    launchctl kickstart -k "user/$UID_NOW/com.git-shadow.cloudcli" >/dev/null 2>&1 || true
    SERVICE_MODE=launchd-user
  fi
fi

if [ -z "$SERVICE_MODE" ]; then
  PID_FILE="$STATE_ROOT/cloudcli.pid"
  if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE" 2>/dev/null || true)
    if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" >/dev/null 2>&1; then
      kill "$OLD_PID" >/dev/null 2>&1 || true
      sleep 1
    fi
  fi
  nohup env HOST=127.0.0.1 SERVER_PORT=3001 \
    "$BIN_DIR/cloudcli" start >"$LOG_FILE" 2>&1 </dev/null &
  echo $! > "$PID_FILE"
  SERVICE_MODE=nohup
fi

READY=0
i=0
while [ "$i" -lt 30 ]; do
  if command -v curl >/dev/null 2>&1; then
    HTTP_CODE=$(curl -sS -o /dev/null --connect-timeout 1 --max-time 2 \
      -w '%{http_code}' http://127.0.0.1:3001/ 2>/dev/null || true)
    if [ -n "$HTTP_CODE" ] && [ "$HTTP_CODE" != "000" ]; then
      READY=1
      break
    fi
  elif command -v nc >/dev/null 2>&1; then
    if nc -z -w 2 127.0.0.1 3001 >/dev/null 2>&1; then
      READY=1
      break
    fi
  elif [ -f "$HOME/.cloudcli/local-server.json" ]; then
    READY=1
    break
  fi
  i=$((i + 1))
  sleep 1
done

NODE_VERSION_ACTUAL=$(node -v 2>/dev/null || echo unknown)
CLOUDCLI_VERSION_ACTUAL=$(
  "$BIN_DIR/cloudcli" version 2>/dev/null \
    | grep -o '[0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*' \
    | head -n1 || true
)

printf 'GS_BOOTSTRAP_OS=%s\n' "$OS"
printf 'GS_BOOTSTRAP_ARCH=%s\n' "$ARCH"
printf 'GS_BOOTSTRAP_NODE_SOURCE=%s\n' "$NODE_SOURCE"
printf 'GS_BOOTSTRAP_NODE_VERSION=%s\n' "$NODE_VERSION_ACTUAL"
printf 'GS_BOOTSTRAP_CLOUDCLI_VERSION=%s\n' "$CLOUDCLI_VERSION_ACTUAL"
printf 'GS_BOOTSTRAP_SERVICE=%s\n' "$SERVICE_MODE"
printf 'GS_BOOTSTRAP_LOG=%s\n' "$LOG_FILE"
printf 'GS_BOOTSTRAP_READY=%s\n' "$READY"

if [ "$READY" != "1" ]; then
  printf 'GS_BOOTSTRAP_ERROR=cloudcli-did-not-become-reachable\n'
  exit 24
fi
"""
        return (
            script.replace("__NVM_VERSION__", NVM_VERSION)
            .replace("__NODE_VERSION__", NODE_VERSION)
            .replace("__CLOUDCLI_VERSION__", CLOUDCLI_VERSION)
            .replace("__CLOUDCLI_PACKAGE__", CLOUDCLI_PACKAGE)
        )


def format_cloudcli_plan(host: str) -> str:
    lines = [
        "Remote CloudCLI bootstrap for [%s]:" % host,
        "  command: git shadow remote ensure %s cloudcli" % host,
        "  pinned CloudCLI: %s@%s" % (CLOUDCLI_PACKAGE, CLOUDCLI_VERSION),
        "  Node fallback: nvm %s + Node %s" % (NVM_VERSION, NODE_VERSION),
        "  service: systemd --user (Linux) / launchd (macOS) / nohup fallback",
        "  bind: 127.0.0.1:3001",
    ]
    return "\n".join(lines)
