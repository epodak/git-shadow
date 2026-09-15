import json
import pathlib
import shutil
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from git_shadow.edge_agent import EdgeExecutor


class TestCloudCLIProtocol(unittest.TestCase):
    def setUp(self):
        self.state_dir = pathlib.Path(tempfile.mkdtemp(prefix="git_shadow_cloudcli_", dir=str(pathlib.Path.home())))
        self.records = []

        records = self.records

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length) or b"{}")
                records.append((self.path, payload))
                response = {"sessionId": "session-test"} if self.path.endswith("/providers/sessions") else {}
                encoded = json.dumps(response).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def log_message(self, format, *args):
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(timeout=2)
        shutil.rmtree(self.state_dir, ignore_errors=True)

    def test_session_is_created_after_directory_and_before_git_prepare(self):
        target = self.state_dir / "project"
        executor = EdgeExecutor(str(self.state_dir / "state"))
        try:
            executor.submit(
                {
                    "type": "submit",
                    "job_id": "job-cloudcli-order",
                    "steps": [
                        {"id": "workspace-create", "action": "workspace.create", "target": str(target)},
                        {
                            "id": "cloudcli-session",
                            "action": "cloudcli.session",
                            "project_path": str(target),
                            "provider": "codex",
                            "base_url": "http://127.0.0.1:%s" % self.server.server_port,
                            "public_url": "https://cli.daduiot.com",
                        },
                        {
                            "id": "workspace-prepare",
                            "action": "workspace.prepare",
                            "target": str(target),
                            "branch": "main",
                        },
                    ],
                }
            )
            worker = executor._jobs["job-cloudcli-order"]["worker"]
            worker.join(timeout=10)
            self.assertFalse(worker.is_alive())

            events_path = self.state_dir / "state" / "runs" / "job-cloudcli-order" / "events.ndjson"
            events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(events[-1]["event"], "job.completed")
            session_ready = next(index for index, event in enumerate(events) if event.get("event") == "session.ready")
            prepare_started = next(
                index
                for index, event in enumerate(events)
                if event.get("event") == "step.started" and event.get("step") == "workspace-prepare"
            )
            self.assertLess(session_ready, prepare_started)
            self.assertTrue(target.is_dir())
            self.assertTrue((target / ".git").is_dir())
            self.assertEqual(
                [path for path, _ in self.records],
                ["/api/projects/create-project", "/api/providers/sessions"],
            )
            self.assertEqual(self.records[0][1]["path"], str(target))
            self.assertEqual(self.records[1][1]["projectPath"], str(target))
        finally:
            executor._stop.set()
            executor._lease_thread.join(timeout=2)

    def test_session_then_later_failure_is_marked_partial(self):
        target = self.state_dir / "partial-project"
        executor = EdgeExecutor(str(self.state_dir / "partial-state"))
        try:
            executor.submit(
                {
                    "type": "submit",
                    "job_id": "job-cloudcli-partial",
                    "steps": [
                        {"id": "workspace-create", "action": "workspace.create", "target": str(target)},
                        {
                            "id": "cloudcli-session",
                            "action": "cloudcli.session",
                            "project_path": str(target),
                            "provider": "codex",
                            "base_url": "http://127.0.0.1:%s" % self.server.server_port,
                            "public_url": "https://cli.daduiot.com",
                        },
                        {
                            "id": "later-failure",
                            "action": "exec",
                            "argv": ["/bin/sh", "-c", "exit 17"],
                            "cwd": str(target),
                        },
                    ],
                }
            )
            worker = executor._jobs["job-cloudcli-partial"]["worker"]
            worker.join(timeout=10)
            self.assertFalse(worker.is_alive())
            events_path = self.state_dir / "partial-state" / "runs" / "job-cloudcli-partial" / "events.ndjson"
            events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
            partial = next(event for event in events if event.get("event") == "job.partial")
            failed = next(event for event in events if event.get("event") == "job.failed")
            self.assertEqual(partial["session"]["session_id"], "session-test")
            self.assertTrue(failed["partial"])
            self.assertEqual(failed["session"]["url"], "https://cli.daduiot.com/session/session-test")
            state = json.loads((self.state_dir / "partial-state" / "runs" / "job-cloudcli-partial" / "state.json").read_text())
            self.assertTrue(state["partial"])
            self.assertEqual(state["session"]["session_id"], "session-test")
        finally:
            executor._stop.set()
            executor._lease_thread.join(timeout=2)

    def test_cloudcli_rejects_unsafe_workspace_and_invalid_url(self):
        executor = EdgeExecutor(str(self.state_dir / "validation-state"))
        try:
            with self.assertRaisesRegex(Exception, "outside the remote home"):
                executor._execute_step(
                    "job-cloudcli-validation",
                    {
                        "action": "cloudcli.session",
                        "project_path": "/tmp/not-inside-home",
                        "provider": "codex",
                        "base_url": "http://127.0.0.1:1",
                    },
                )
            with self.assertRaisesRegex(Exception, r"absolute HTTP\(S\) URL"):
                executor._execute_step(
                    "job-cloudcli-validation",
                    {
                        "action": "cloudcli.session",
                        "project_path": str(self.state_dir),
                        "provider": "codex",
                        "base_url": "localhost:3001",
                    },
                )
        finally:
            executor._stop.set()
            executor._lease_thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
