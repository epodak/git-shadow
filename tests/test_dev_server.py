import io
import pathlib
import shutil
import sys
import tempfile
import unittest

from git_shadow.paths import cleanup_test_tree, test_temp_root
from unittest.mock import MagicMock, patch

from git_shadow.dev_server import ShadowDevServer, log_hmr


class TestDevServer(unittest.TestCase):
    def setUp(self):
        self.temp_dir = pathlib.Path(tempfile.mkdtemp(prefix="git_shadow_devserver_", dir=str(test_temp_root())))
        import subprocess
        subprocess.run(["git", "init", str(self.temp_dir)], capture_output=True, check=True)

    def tearDown(self):
        cleanup_test_tree(self.temp_dir)

    def test_init_and_render_dashboard(self):
        server = ShadowDevServer(
            project_root=str(self.temp_dir),
            remote_host="aws",
            remote_dir="~/wkspace/test",
            launch_mode="cloudcli",
            auto_open_browser=False,
        )
        server.web_url = "https://cloudcli.example.invalid/session/test-session-id"

        captured = io.StringIO()
        with patch("sys.stdout", captured):
            server.render_dashboard(ready_ms=150)

        out = captured.getvalue()
        self.assertIn("GIT-SHADOW", out)
        self.assertIn("ready in 150 ms", out)
        self.assertIn(server.repo.root_dir, out)
        self.assertIn("aws:~/wkspace/test", out)
        self.assertIn("https://cloudcli.example.invalid/session/test-session-id", out)
        self.assertIn("[o]", out)
        self.assertIn("[r]", out)

    def test_on_edge_event_captures_session_ready(self):
        server = ShadowDevServer(
            project_root=str(self.temp_dir),
            remote_host="aws",
            auto_open_browser=False,
        )
        # Mock shadow store to avoid filesystem ops
        server.shadow_store = MagicMock()

        test_url = "https://cloudcli.example.invalid/session/abc-123"
        event = {"event": "session.ready", "url": test_url}

        captured = io.StringIO()
        with patch("sys.stdout", captured):
            server._on_edge_event(event)

        self.assertEqual(server.web_url, test_url)
        self.assertIn(test_url, captured.getvalue())

    def test_cloudcli_step_skip_degrades_and_hides_open_hotkey(self):
        server = ShadowDevServer(
            project_root=str(self.temp_dir),
            remote_host="aws",
            launch_mode="cloudcli",
            auto_open_browser=False,
        )
        server.shadow_store = MagicMock()
        server.web_url = "https://cloudcli.example.invalid/session/stale"

        event = {
            "event": "step.skipped",
            "step": "cloudcli.session",
            "action": "cloudcli.session",
            "reason": "CloudCLI API unavailable: Connection refused",
        }
        with patch("sys.stdout", io.StringIO()):
            server._on_edge_event(event)

        self.assertEqual(server.web_capability, "degraded")
        self.assertIsNone(server.web_url)
        self.assertIn("Connection refused", server.web_failure_reason)

        captured = io.StringIO()
        with patch("sys.stdout", captured):
            server.render_dashboard()
        out = captured.getvalue()
        self.assertNotIn("[o]", out)
        self.assertIn("sync-only", out)

    def test_open_browser_requires_fresh_positive_probe_before_retry(self):
        server = ShadowDevServer(
            project_root=str(self.temp_dir),
            remote_host="aws",
            launch_mode="cloudcli",
            auto_open_browser=False,
        )
        server.web_capability = "degraded"
        server.web_failure_reason = "Connection refused"

        with patch.object(server, "_probe_cloudcli", return_value=False):
            with patch.object(server, "push_update") as push_update:
                with patch("sys.stdout", io.StringIO()):
                    server.open_browser()
        push_update.assert_not_called()

    def test_hot_keys_stop(self):
        server = ShadowDevServer(
            project_root=str(self.temp_dir),
            remote_host="aws",
            auto_open_browser=False,
        )
        self.assertFalse(server.stop_event.is_set())
        server.stop()
        self.assertTrue(server.stop_event.is_set())


if __name__ == "__main__":
    unittest.main()
