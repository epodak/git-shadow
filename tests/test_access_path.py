import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from git_shadow.access_path import (
    AccessPathManager,
    configured_public_url,
)


class FakeEngine:
    def __init__(self):
        self.calls = []
        self.remote_status = {
            "BackendState": "Running",
            "CurrentTailnet": {
                "Name": "tailnet.example",
                "MagicDNSSuffix": "tailnet.example.ts.net",
            },
            "Self": {
                "DNSName": "vps.tailnet.example.ts.net.",
                "TailscaleIPs": ["100.64.0.20"],
            },
        }

    def _run_ssh(self, command, capture=True, check=False, input_data=None):
        self.calls.append((command, input_data))
        if "tailscale status --json" in command:
            import json
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(self.remote_status),
                stderr="",
            )
        if "tailscale serve --bg 3001" in command:
            return SimpleNamespace(returncode=0, stdout="Available within your tailnet", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")


def local_status(connection="direct"):
    peer = {
        "DNSName": "vps.tailnet.example.ts.net.",
        "TailscaleIPs": ["100.64.0.20"],
        "Online": True,
        "CurAddr": "",
        "PeerRelay": "",
        "Relay": "",
    }
    if connection == "direct":
        peer["CurAddr"] = "203.0.113.2:41641"
    elif connection == "peer-relay":
        peer["PeerRelay"] = "100.64.0.30:41641:vni:1"
    elif connection == "derp":
        peer["Relay"] = "sea"

    return {
        "BackendState": "Running",
        "CurrentTailnet": {
            "Name": "tailnet.example",
            "MagicDNSSuffix": "tailnet.example.ts.net",
        },
        "Self": {
            "DNSName": "laptop.tailnet.example.ts.net.",
            "TailscaleIPs": ["100.64.0.10"],
        },
        "Peer": {"nodekey:test": peer},
    }


class TestAccessPath(unittest.TestCase):
    def test_same_tailnet_direct_peer_is_detected(self):
        manager = AccessPathManager(FakeEngine())
        with patch.object(
            manager,
            "_local_status",
            return_value={
                "available": True,
                "installed": True,
                "reason": "ready",
                "status": local_status("direct"),
            },
        ):
            result = manager.probe_tailscale()

        self.assertTrue(result["same_tailnet"])
        self.assertEqual(result["connection"], "direct")
        self.assertEqual(result["remote_dns"], "vps.tailnet.example.ts.net")

    def test_auto_prefers_private_tailscale_when_direct(self):
        manager = AccessPathManager(FakeEngine())
        probe = {
            "same_tailnet": True,
            "connection": "direct",
            "remote_dns": "vps.tailnet.example.ts.net",
        }
        with patch.object(manager, "probe_tailscale", return_value=probe):
            with patch.object(
                manager,
                "ensure_cloudcli_serve",
                return_value={
                    "success": True,
                    "url": "https://vps.tailnet.example.ts.net",
                    "reason": "ready",
                    "tailscale": probe,
                },
            ):
                result = manager.resolve_cloudcli_access(
                    preference="auto",
                    public_url="https://cloud.example.com",
                )

        self.assertEqual(result["source"], "tailscale")
        self.assertEqual(result["url"], "https://vps.tailnet.example.ts.net")

    def test_auto_prefers_configured_public_url_when_tailscale_is_derp_only(self):
        manager = AccessPathManager(FakeEngine())
        probe = {
            "same_tailnet": True,
            "connection": "derp",
            "remote_dns": "vps.tailnet.example.ts.net",
        }
        with patch.object(manager, "probe_tailscale", return_value=probe):
            with patch.object(
                manager,
                "ensure_cloudcli_serve",
                return_value={
                    "success": True,
                    "url": "https://vps.tailnet.example.ts.net",
                    "reason": "ready",
                    "tailscale": probe,
                },
            ):
                result = manager.resolve_cloudcli_access(
                    preference="auto",
                    public_url="https://cloud.example.com",
                )

        self.assertEqual(result["source"], "public")
        self.assertEqual(result["reason"], "tailscale-derp-public-preferred")
        self.assertEqual(result["url"], "https://cloud.example.com")

    def test_tailscale_serve_keeps_cloudcli_bound_to_localhost(self):
        engine = FakeEngine()
        manager = AccessPathManager(engine)
        probe = {
            "same_tailnet": True,
            "connection": "direct",
            "remote_dns": "vps.tailnet.example.ts.net",
        }
        result = manager.ensure_cloudcli_serve(probe=probe)

        self.assertTrue(result["success"])
        self.assertEqual(result["url"], "https://vps.tailnet.example.ts.net")
        self.assertTrue(
            any("tailscale serve --bg 3001" in call[0] for call in engine.calls)
        )

    def test_public_url_has_no_personal_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(configured_public_url())
        with patch.dict(
            os.environ,
            {"GIT_SHADOW_CLOUDFLARE_URL": "https://cloud.example.com/"},
            clear=True,
        ):
            self.assertEqual(
                configured_public_url(),
                "https://cloud.example.com",
            )

    def test_enrollment_refuses_to_switch_an_existing_different_tailnet(self):
        manager = AccessPathManager(FakeEngine())
        before = {
            "same_tailnet": False,
            "remote_available": True,
            "local_tailnet": "my-tailnet.example",
            "remote_tailnet": "other-tailnet.example",
        }
        with patch.object(manager, "probe_tailscale", return_value=before):
            with patch.dict(
                os.environ,
                {"GIT_SHADOW_TAILSCALE_AUTH_KEY": "tskey-auth-test"},
                clear=True,
            ):
                result = manager.ensure_tailscale()

        self.assertFalse(result["success"])
        self.assertFalse(result["changed"])
        self.assertEqual(result["error"], "remote-already-on-different-tailnet")

    def test_explicit_tailscale_enrollment_requires_auth_key(self):
        manager = AccessPathManager(FakeEngine())
        with patch.object(
            manager,
            "probe_tailscale",
            return_value={
                "same_tailnet": False,
                "remote_installed": False,
            },
        ):
            with patch.dict(os.environ, {}, clear=True):
                result = manager.ensure_tailscale()

        self.assertFalse(result["success"])
        self.assertEqual(result["changed"], False)
        self.assertIn("auth-key-required", result["error"])


if __name__ == "__main__":
    unittest.main()
