"""
git_shadow.access_path
Resolve how the local controller should reach remote web capabilities.

The remote application stays bound to localhost. Access is a separate layer:

- Tailscale Serve: private tailnet path, preferred when the remote peer is
  reachable directly (or through a peer relay).
- Configured public URL: typically Cloudflare Tunnel, used for public-domain
  access and as an automatic fallback when Tailscale is unavailable or DERP-only.

No personal domain is hard-coded here.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Any, Dict, Iterable, Optional


ACCESS_AUTO = "auto"
ACCESS_TAILSCALE = "tailscale"
ACCESS_PUBLIC = "public"
ACCESS_CHOICES = (ACCESS_AUTO, ACCESS_TAILSCALE, ACCESS_PUBLIC)

PUBLIC_URL_ENVS = (
    "GIT_SHADOW_CLOUDFLARE_URL",
    "GIT_SHADOW_CLOUDCLI_PUBLIC_URL",  # backwards-compatible name
)
TAILSCALE_AUTH_KEY_ENVS = (
    "GIT_SHADOW_TAILSCALE_AUTH_KEY",
    "TS_AUTH_KEY",
)


def _decode(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value or "")


def configured_public_url(explicit: Optional[str] = None) -> Optional[str]:
    value = (explicit or "").strip()
    if value:
        return value.rstrip("/")
    for name in PUBLIC_URL_ENVS:
        value = os.environ.get(name, "").strip()
        if value:
            return value.rstrip("/")
    return None


def configured_tailscale_auth_key(explicit: Optional[str] = None) -> Optional[str]:
    value = (explicit or "").strip()
    if value:
        return value
    for name in TAILSCALE_AUTH_KEY_ENVS:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return None


def normalize_access_preference(value: Optional[str] = None) -> str:
    pref = (value or os.environ.get("GIT_SHADOW_CLOUDCLI_ACCESS", ACCESS_AUTO)).strip().lower()
    if pref not in ACCESS_CHOICES:
        raise ValueError(
            "invalid CloudCLI access preference %r (expected: %s)"
            % (pref, ", ".join(ACCESS_CHOICES))
        )
    return pref


class AccessPathManager:
    """Observe and prepare the browser access path for one remote host."""

    def __init__(self, engine):
        self.engine = engine

    @staticmethod
    def _parse_status(payload: str) -> Dict[str, Any]:
        try:
            value = json.loads(payload)
        except (TypeError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _self_ips(status: Dict[str, Any]) -> list[str]:
        self_info = status.get("Self") or {}
        values = self_info.get("TailscaleIPs") or status.get("TailscaleIPs") or []
        return [str(item) for item in values if str(item)]

    @staticmethod
    def _self_dns(status: Dict[str, Any]) -> str:
        self_info = status.get("Self") or {}
        return str(self_info.get("DNSName") or "").rstrip(".")

    @staticmethod
    def _tailnet_name(status: Dict[str, Any]) -> str:
        current = status.get("CurrentTailnet") or {}
        return str(current.get("Name") or current.get("MagicDNSSuffix") or "").strip()

    @staticmethod
    def _iter_peers(status: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
        peers = status.get("Peer") or {}
        if isinstance(peers, dict):
            for value in peers.values():
                if isinstance(value, dict):
                    yield value
        elif isinstance(peers, list):
            for value in peers:
                if isinstance(value, dict):
                    yield value

    @classmethod
    def _find_peer(
        cls,
        local_status: Dict[str, Any],
        remote_ips: Iterable[str],
        remote_dns: str,
    ) -> Optional[Dict[str, Any]]:
        wanted_ips = set(remote_ips)
        wanted_dns = remote_dns.rstrip(".").lower()
        for peer in cls._iter_peers(local_status):
            peer_ips = {str(item) for item in (peer.get("TailscaleIPs") or [])}
            peer_dns = str(peer.get("DNSName") or "").rstrip(".").lower()
            if wanted_ips.intersection(peer_ips):
                return peer
            if wanted_dns and peer_dns == wanted_dns:
                return peer
        return None

    @staticmethod
    def _connection_type(peer: Optional[Dict[str, Any]]) -> str:
        if not peer:
            return "unknown"
        if str(peer.get("CurAddr") or "").strip():
            return "direct"
        if str(peer.get("PeerRelay") or "").strip():
            return "peer-relay"
        if str(peer.get("Relay") or "").strip():
            return "derp"
        return "unknown"

    def _local_status(self) -> Dict[str, Any]:
        executable = shutil.which("tailscale")
        if not executable:
            return {
                "available": False,
                "installed": False,
                "reason": "local-tailscale-not-installed",
                "status": {},
            }
        try:
            res = subprocess.run(
                [executable, "status", "--json"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {
                "available": False,
                "installed": True,
                "reason": "local-tailscale-status-failed: %s" % exc,
                "status": {},
            }
        status = self._parse_status(res.stdout)
        running = res.returncode == 0 and status.get("BackendState") == "Running"
        return {
            "available": running,
            "installed": True,
            "reason": "ready" if running else "local-tailscale-not-running",
            "status": status,
        }

    def _remote_status(self) -> Dict[str, Any]:
        command = (
            "if ! command -v tailscale >/dev/null 2>&1; then "
            "printf '%s\\n' '__GS_TS_NOT_INSTALLED__'; exit 0; fi; "
            "tailscale status --json"
        )
        try:
            res = self.engine._run_ssh(command, capture=True, check=False)
        except Exception as exc:
            return {
                "available": False,
                "installed": False,
                "reason": "remote-tailscale-probe-failed: %s" % exc,
                "status": {},
            }

        stdout = _decode(getattr(res, "stdout", ""))
        if "__GS_TS_NOT_INSTALLED__" in stdout:
            return {
                "available": False,
                "installed": False,
                "reason": "remote-tailscale-not-installed",
                "status": {},
            }

        status = self._parse_status(stdout)
        running = getattr(res, "returncode", 0) == 0 and status.get("BackendState") == "Running"
        reason = "ready" if running else "remote-tailscale-not-running"
        if getattr(res, "returncode", 0) != 0 and not status:
            stderr = _decode(getattr(res, "stderr", "")).strip()
            reason = "remote-tailscale-status-failed"
            if stderr:
                reason += ": " + stderr[:200]
        return {
            "available": running,
            "installed": bool(status) or getattr(res, "returncode", 0) == 0,
            "reason": reason,
            "status": status,
        }

    def probe_tailscale(self) -> Dict[str, Any]:
        local = self._local_status()
        remote = self._remote_status()
        local_status = local.get("status") or {}
        remote_status = remote.get("status") or {}

        remote_ips = self._self_ips(remote_status)
        remote_dns = self._self_dns(remote_status)
        peer = self._find_peer(local_status, remote_ips, remote_dns)
        peer_visible = peer is not None

        local_tailnet = self._tailnet_name(local_status)
        remote_tailnet = self._tailnet_name(remote_status)
        tailnet_match = bool(
            local_tailnet
            and remote_tailnet
            and local_tailnet == remote_tailnet
        )
        same_tailnet = bool(local.get("available") and remote.get("available") and peer_visible)

        return {
            "available": same_tailnet,
            "same_tailnet": same_tailnet,
            "tailnet_match": tailnet_match,
            "peer_visible": peer_visible,
            "connection": self._connection_type(peer),
            "local_installed": bool(local.get("installed")),
            "local_available": bool(local.get("available")),
            "remote_installed": bool(remote.get("installed")),
            "remote_available": bool(remote.get("available")),
            "local_reason": local.get("reason"),
            "remote_reason": remote.get("reason"),
            "local_tailnet": local_tailnet,
            "remote_tailnet": remote_tailnet,
            "remote_ips": remote_ips,
            "remote_dns": remote_dns,
            "peer_online": bool((peer or {}).get("Online")) if peer else False,
        }

    def ensure_tailscale(
        self,
        auth_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Explicitly enroll a Linux VPS into Tailscale.

        This is never called implicitly by normal run/push/pull operations.
        Enrollment is privileged and requires an auth key supplied through
        stdin, never embedded in the remote command line.
        """
        before = self.probe_tailscale()
        if before.get("same_tailnet"):
            return {
                "success": True,
                "changed": False,
                "before": before,
                "after": before,
            }

        if (
            before.get("remote_available")
            and before.get("remote_tailnet")
            and before.get("local_tailnet")
            and before.get("remote_tailnet") != before.get("local_tailnet")
        ):
            return {
                "success": False,
                "changed": False,
                "before": before,
                "error": "remote-already-on-different-tailnet",
            }

        key = configured_tailscale_auth_key(auth_key)
        if not key:
            return {
                "success": False,
                "changed": False,
                "before": before,
                "error": (
                    "tailscale-auth-key-required: set "
                    "GIT_SHADOW_TAILSCALE_AUTH_KEY (or TS_AUTH_KEY)"
                ),
            }

        script = r"""
set -eu
umask 077

OS_NAME="$(uname -s 2>/dev/null || echo Unknown)"
if [ "$OS_NAME" != "Linux" ]; then
    echo "GS_TS_ERROR=automatic-install-supported-on-linux-only"
    exit 42
fi

if ! command -v sudo >/dev/null 2>&1 || ! sudo -n true >/dev/null 2>&1; then
    echo "GS_TS_ERROR=passwordless-sudo-required"
    exit 43
fi

if ! command -v tailscale >/dev/null 2>&1; then
    if ! command -v curl >/dev/null 2>&1; then
        echo "GS_TS_ERROR=curl-required"
        exit 44
    fi
    curl -fsSL https://tailscale.com/install.sh | sh
fi

KEY_FILE="$(mktemp)"
trap 'rm -f "$KEY_FILE"' EXIT HUP INT TERM
cat > "$KEY_FILE"
chmod 600 "$KEY_FILE"

sudo -n tailscale up --auth-key="file:$KEY_FILE"
echo "GS_TS_READY=1"
"""
        try:
            res = self.engine._run_ssh(
                script.strip(),
                capture=True,
                check=False,
                input_data=key.encode("utf-8"),
            )
        except Exception as exc:
            return {
                "success": False,
                "changed": False,
                "before": before,
                "error": "tailscale-bootstrap-ssh-failed: %s" % exc,
            }

        stdout = _decode(getattr(res, "stdout", ""))
        stderr = _decode(getattr(res, "stderr", ""))
        after = self.probe_tailscale()
        success = bool(after.get("same_tailnet"))
        error = ""
        if not success:
            marker = next(
                (
                    line.split("=", 1)[1].strip()
                    for line in stdout.splitlines()
                    if line.startswith("GS_TS_ERROR=")
                ),
                "",
            )
            error = marker or stderr.strip()[:300] or "tailscale-bootstrap-did-not-join-local-tailnet"

        return {
            "success": success,
            "changed": True,
            "before": before,
            "after": after,
            "error": error,
        }

    def ensure_cloudcli_serve(self, probe: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        ts = probe or self.probe_tailscale()
        if not ts.get("same_tailnet"):
            return {
                "success": False,
                "reason": "remote-is-not-a-visible-tailnet-peer",
                "url": None,
                "tailscale": ts,
            }

        dns_name = str(ts.get("remote_dns") or "").rstrip(".")
        if not dns_name:
            return {
                "success": False,
                "reason": "tailscale-dns-name-unavailable",
                "url": None,
                "tailscale": ts,
            }

        serve_command = (
            "tailscale serve --bg 3001 || "
            "{ rc=$?; "
            "if command -v sudo >/dev/null 2>&1 && sudo -n true >/dev/null 2>&1; then "
            "sudo -n tailscale serve --bg 3001; "
            "else exit $rc; fi; }"
        )
        try:
            res = self.engine._run_ssh(
                serve_command,
                capture=True,
                check=False,
            )
        except Exception as exc:
            return {
                "success": False,
                "reason": "tailscale-serve-failed: %s" % exc,
                "url": None,
                "tailscale": ts,
            }

        if getattr(res, "returncode", 0) != 0:
            detail = _decode(getattr(res, "stderr", "")).strip()
            if not detail:
                detail = _decode(getattr(res, "stdout", "")).strip()
            return {
                "success": False,
                "reason": "tailscale-serve-failed: %s" % (detail[:300] or "unknown"),
                "url": None,
                "tailscale": ts,
            }

        return {
            "success": True,
            "reason": "ready",
            "url": "https://" + dns_name,
            "tailscale": ts,
        }

    def resolve_cloudcli_access(
        self,
        preference: Optional[str] = None,
        public_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        pref = normalize_access_preference(preference)
        configured_public = configured_public_url(public_url)
        ts = self.probe_tailscale()

        serve: Dict[str, Any] = {
            "success": False,
            "reason": "not-attempted",
            "url": None,
            "tailscale": ts,
        }
        connection = str(ts.get("connection") or "unknown")
        should_prepare_serve = (
            pref == ACCESS_TAILSCALE
            or (
                pref == ACCESS_AUTO
                and ts.get("same_tailnet")
                and not (connection == "derp" and configured_public)
            )
        )
        if should_prepare_serve and ts.get("same_tailnet"):
            serve = self.ensure_cloudcli_serve(probe=ts)
        elif (
            pref == ACCESS_AUTO
            and connection == "derp"
            and configured_public
        ):
            serve["reason"] = "skipped-derp-public-preferred"

        ts_url = serve.get("url") if serve.get("success") else None

        if pref == ACCESS_PUBLIC:
            if configured_public:
                return {
                    "available": True,
                    "source": "public",
                    "url": configured_public,
                    "reason": "forced-public",
                    "tailscale": ts,
                    "tailscale_serve": serve,
                }
            return {
                "available": False,
                "source": "none",
                "url": None,
                "reason": "public-url-not-configured",
                "tailscale": ts,
                "tailscale_serve": serve,
            }

        if pref == ACCESS_TAILSCALE:
            return {
                "available": bool(ts_url),
                "source": "tailscale" if ts_url else "none",
                "url": ts_url,
                "reason": "forced-tailscale" if ts_url else serve.get("reason"),
                "tailscale": ts,
                "tailscale_serve": serve,
            }

        # Auto policy:
        # - direct and peer-relay keep browser traffic private on the tailnet;
        # - a DERP-only route prefers an explicitly configured public edge URL;
        # - if no public URL exists, DERP Tailscale still remains a valid
        #   encrypted fallback.
        if ts_url and connection in ("direct", "peer-relay"):
            source = "tailscale"
            reason = "tailnet-%s-preferred" % connection
            url = ts_url
        elif connection == "derp" and configured_public:
            source = "public"
            reason = "tailscale-derp-public-preferred"
            url = configured_public
        elif ts_url:
            source = "tailscale"
            reason = "tailnet-private-path-available"
            url = ts_url
        elif configured_public:
            source = "public"
            reason = "public-fallback"
            url = configured_public
        else:
            source = "none"
            reason = serve.get("reason") or "no-browser-access-path"
            url = None

        return {
            "available": bool(url),
            "source": source,
            "url": url,
            "reason": reason,
            "tailscale": ts,
            "tailscale_serve": serve,
        }
