import io
import pathlib
import shutil
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from git_shadow.dev_server import ShadowDevServer, log_hmr


class TestDevServer(unittest.TestCase):
    def setUp(self):
        self.temp_dir = pathlib.Path(tempfile.mkdtemp(prefix="git_shadow_devserver_"))
        import subprocess
        subprocess.run(["git", "init", str(self.temp_dir)], capture_output=True, check=True)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_init_and_render_dashboard(self):
        server = ShadowDevServer(
            project_root=str(self.temp_dir),
            remote_host="aws",
            remote_dir="~/wkspace/test",
            launch_mode="cloudcli",
            auto_open_browser=False,
        )
        server.web_url = "https://cli.daduiot.com/session/test-session-id"

        captured = io.StringIO()
        with patch("sys.stdout", captured):
            server.render_dashboard(ready_ms=150)

        out = captured.getvalue()
        self.assertIn("GIT-SHADOW", out)
        self.assertIn("ready in 150 ms", out)
        self.assertIn(server.repo.root_dir, out)
        self.assertIn("aws:~/wkspace/test", out)
        self.assertIn("https://cli.daduiot.com/session/test-session-id", out)
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

        test_url = "https://cli.daduiot.com/session/abc-123"
        event = {"event": "session.ready", "url": test_url}

        captured = io.StringIO()
        with patch("sys.stdout", captured):
            server._on_edge_event(event)

        self.assertEqual(server.web_url, test_url)
        self.assertIn(test_url, captured.getvalue())

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
