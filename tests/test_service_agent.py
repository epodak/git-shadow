import json
import pathlib
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest


class TestServiceAgent(unittest.TestCase):
    def setUp(self):
        self.root = pathlib.Path(tempfile.mkdtemp(prefix="git_shadow_service_", dir=str(pathlib.Path.home())))
        self.service_root = self.root / "service"
        self.state_root = self.root / "state"
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.service_path = pathlib.Path(__file__).resolve().parents[1] / "git_shadow" / "service_agent.py"

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _start(self, ttl=30):
        process = subprocess.Popen(
            [
                sys.executable,
                str(self.service_path),
                "--serve",
                "--service-root",
                str(self.service_root),
                "--state-dir",
                str(self.state_root),
                "--lease-ttl",
                str(ttl),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        deadline = time.time() + 5
        while time.time() < deadline and not self.service_root.joinpath("service.sock").exists():
            if process.poll() is not None:
                output = process.stderr.read() if process.stderr else ""
                self.fail("service exited before socket appeared: %s" % output)
            time.sleep(0.05)
        self.assertTrue(self.service_root.joinpath("service.sock").exists())
        return process

    def _send(self, request):
        completed = subprocess.run(
            [
                sys.executable,
                str(self.service_path),
                "--send",
                "--service-root",
                str(self.service_root),
            ],
            input=json.dumps(request) + "\n",
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return [json.loads(line) for line in completed.stdout.splitlines() if line.strip()]

    def test_service_accepts_and_streams_a_plan(self):
        process = self._start()
        try:
            events = self._send(
                {
                    "type": "submit",
                    "job_id": "job-service-protocol",
                    "steps": [
                        {
                            "id": "output",
                            "action": "exec",
                            "argv": [sys.executable, "-c", "print('service-ok')"],
                            "cwd": str(self.workspace),
                        }
                    ],
                }
            )
            self.assertTrue(any(item.get("event") == "job.completed" for item in events))
            self.assertTrue(any(item.get("event") == "output" and item.get("message") == "service-ok" for item in events))
            state = json.loads((self.state_root / "runs" / "job-service-protocol" / "state.json").read_text())
            self.assertEqual(state["status"], "completed")
        finally:
            process.terminate()
            process.wait(timeout=5)
            if process.stdout:
                process.stdout.close()
            if process.stderr:
                process.stderr.close()

    def test_service_expires_without_a_lease_heartbeat(self):
        process = self._start(ttl=1)
        try:
            deadline = time.time() + 5
            while time.time() < deadline and process.poll() is None:
                time.sleep(0.1)
            self.assertIsNotNone(process.poll())
            state = json.loads(self.service_root.joinpath("state.json").read_text())
            self.assertEqual(state["status"], "stopped")
            self.assertEqual(state["reason"], "lease_expired")
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
            if process.stdout:
                process.stdout.close()
            if process.stderr:
                process.stderr.close()

    def test_reconnecting_same_job_replays_without_duplicate_execution(self):
        process = self._start()
        request = {
            "type": "submit",
            "job_id": "job-service-reconnect",
            "steps": [
                {
                    "id": "slow-output",
                    "action": "exec",
                    "argv": [sys.executable, "-c", "import time; print('once'); time.sleep(0.4)"],
                    "cwd": str(self.workspace),
                }
            ],
        }
        try:
            connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            connection.connect(str(self.service_root / "service.sock"))
            connection.sendall((json.dumps(request) + "\n").encode())
            connection.shutdown(socket.SHUT_WR)
            accepted = b""
            while b'"type":"accepted"' not in accepted and b'"type": "accepted"' not in accepted:
                chunk = connection.recv(4096)
                if not chunk:
                    break
                accepted += chunk
            connection.close()

            events = self._send(request)
            self.assertTrue(any(item.get("event") == "job.completed" for item in events))
            journal = self.state_root / "runs" / "job-service-reconnect" / "events.ndjson"
            persisted = [json.loads(line) for line in journal.read_text().splitlines()]
            self.assertEqual(sum(item.get("event") == "job.started" for item in persisted), 1)
            self.assertEqual(sum(item.get("event") == "job.completed" for item in persisted), 1)
            self.assertEqual(sum(item.get("event") == "output" and item.get("message") == "once" for item in persisted), 1)
        finally:
            process.terminate()
            process.wait(timeout=5)
            if process.stdout:
                process.stdout.close()
            if process.stderr:
                process.stderr.close()


if __name__ == "__main__":
    unittest.main()
