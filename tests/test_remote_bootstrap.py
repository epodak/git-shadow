import unittest
from unittest.mock import Mock, patch

from git_shadow.remote_bootstrap import (
    CLOUDCLI_PACKAGE,
    CLOUDCLI_VERSION,
    NODE_VERSION,
    NVM_VERSION,
    RemoteBootstrapManager,
    format_cloudcli_plan,
)


class _Result:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _Engine:
    remote_host = "aws"

    def __init__(self):
        self.calls = []

    def _run_ssh(self, command, capture=True, check=False):
        self.calls.append(command)
        return _Result(
            returncode=0,
            stdout=(
                "GS_BOOTSTRAP_OS=Linux\n"
                "GS_BOOTSTRAP_NODE_SOURCE=system\n"
                "GS_BOOTSTRAP_NODE_VERSION=v22.23.3\n"
                "GS_BOOTSTRAP_CLOUDCLI_VERSION=1.37.3\n"
                "GS_BOOTSTRAP_SERVICE=systemd-user\n"
                "GS_BOOTSTRAP_READY=1\n"
            ),
        )


class TestRemoteBootstrapManager(unittest.TestCase):
    def test_repairable_reasons_are_explicit(self):
        self.assertTrue(RemoteBootstrapManager.can_repair_cloudcli(
            {"reason": "cloudcli-not-installed"}
        ))
        self.assertTrue(RemoteBootstrapManager.can_repair_cloudcli(
            {"reason": "cloudcli-service-not-running"}
        ))
        self.assertTrue(RemoteBootstrapManager.can_repair_cloudcli(
            {"reason": "cloudcli-connection-refused"}
        ))
        self.assertFalse(RemoteBootstrapManager.can_repair_cloudcli(
            {"reason": "ssh-probe-failed: timeout"}
        ))

    def test_existing_capability_is_idempotent_and_does_not_install(self):
        engine = _Engine()
        manager = RemoteBootstrapManager(engine)
        ready = {
            "available": True,
            "installed": True,
            "running": True,
            "reachable": True,
            "reason": "ready",
        }
        with patch.object(manager, "probe_cloudcli", return_value=ready):
            result = manager.ensure_cloudcli()

        self.assertTrue(result["success"])
        self.assertFalse(result["changed"])
        self.assertEqual(engine.calls, [])

    def test_missing_capability_runs_one_ssh_bootstrap_then_reprobes(self):
        engine = _Engine()
        manager = RemoteBootstrapManager(engine)
        missing = {
            "available": False,
            "installed": False,
            "running": False,
            "reachable": False,
            "reason": "cloudcli-not-installed",
        }
        ready = {
            "available": True,
            "installed": True,
            "running": True,
            "reachable": True,
            "reason": "ready",
        }

        with patch.object(manager, "probe_cloudcli", side_effect=[missing, ready]):
            result = manager.ensure_cloudcli()

        self.assertTrue(result["success"])
        self.assertTrue(result["changed"])
        self.assertEqual(len(engine.calls), 1)
        self.assertEqual(result["metadata"]["service"], "systemd-user")
        self.assertEqual(result["metadata"]["cloudcli_version"], CLOUDCLI_VERSION)

    def test_ssh_probe_failure_never_attempts_install(self):
        engine = _Engine()
        manager = RemoteBootstrapManager(engine)
        failed = {
            "available": False,
            "reason": "ssh-probe-failed: connection timed out",
        }
        with patch.object(manager, "probe_cloudcli", return_value=failed):
            result = manager.ensure_cloudcli()

        self.assertFalse(result["success"])
        self.assertFalse(result["changed"])
        self.assertEqual(engine.calls, [])

    def test_bootstrap_script_is_user_scoped_cross_platform_and_pinned(self):
        script = RemoteBootstrapManager(_Engine())._cloudcli_bootstrap_script()

        self.assertIn("Linux|Darwin", script)
        self.assertIn("systemctl --user", script)
        self.assertIn("launchctl bootstrap", script)
        self.assertIn("nohup env HOST=127.0.0.1 SERVER_PORT=3001", script)
        self.assertIn(CLOUDCLI_PACKAGE + "@" + CLOUDCLI_VERSION, script)
        self.assertIn(NVM_VERSION, script)
        self.assertIn(NODE_VERSION, script)
        self.assertIn("$HOME/.local/share/git-shadow", script)
        self.assertNotIn("sudo ", script)

    def test_plan_exposes_exact_local_command(self):
        plan = format_cloudcli_plan("mac-studio")
        self.assertIn("git shadow remote ensure mac-studio cloudcli", plan)
        self.assertIn(CLOUDCLI_PACKAGE + "@" + CLOUDCLI_VERSION, plan)


if __name__ == "__main__":
    unittest.main()
