import base64
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

from git_shadow.edge import EdgeClient, clean_ssh_args


class TestEdgeProtocol(unittest.TestCase):
    def setUp(self):
        self.home = pathlib.Path.home()
        self.state_dir = pathlib.Path(tempfile.mkdtemp(prefix="git_shadow_edge_", dir=str(self.home)))
        self.workspace = self.state_dir / "workspace"
        self.workspace.mkdir()

    def tearDown(self):
        shutil.rmtree(self.state_dir, ignore_errors=True)

    def test_ssh_transport_disables_remote_commands_and_tty(self):
        args = clean_ssh_args("vps", "agent --rpc")
        self.assertEqual(args[:5], ["ssh", "-o", "RemoteCommand=none", "-o", "RequestTTY=no"])
        self.assertEqual(args[-2:], ["vps", "agent --rpc"])

    def test_standalone_agent_executes_plan_and_replays_events(self):
        agent_path = pathlib.Path(__file__).resolve().parents[1] / "git_shadow" / "edge_agent.py"
        job_id = "job-test-protocol"
        request = {
            "type": "submit",
            "job_id": job_id,
            "steps": [
                {
                    "id": "write-file",
                    "action": "exec",
                    "argv": [sys.executable, "-c", "print('edge-ok')"],
                    "cwd": str(self.workspace),
                }
            ],
        }
        process = subprocess.Popen(
            [sys.executable, str(agent_path), "--rpc", "--state-dir", str(self.state_dir / "state")],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        output, error = process.communicate(json.dumps(request) + "\n{" + '"type":"shutdown"' + "}\n", timeout=15)
        self.assertEqual(process.returncode, 0, error)
        events = [json.loads(line) for line in output.splitlines() if line.strip()]
        self.assertTrue(any(item.get("event") == "job.completed" for item in events))
        self.assertTrue(any(item.get("event") == "output" and item.get("message") == "edge-ok" for item in events))

        journal = self.state_dir / "state" / "runs" / job_id / "events.ndjson"
        self.assertTrue(journal.exists())
        persisted = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([item["seq"] for item in persisted], list(range(1, len(persisted) + 1)))

        resume_process = subprocess.Popen(
            [sys.executable, str(agent_path), "--rpc", "--state-dir", str(self.state_dir / "state")],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        replay_output, replay_error = resume_process.communicate(
            json.dumps({"type": "resume", "job_id": job_id, "after_seq": 1})
            + "\n"
            + json.dumps({"type": "shutdown"})
            + "\n",
            timeout=15,
        )
        self.assertEqual(resume_process.returncode, 0, replay_error)
        replay_events = [json.loads(line) for line in replay_output.splitlines() if line.strip()]
        self.assertTrue(any(item.get("replay") and item.get("seq", 0) > 1 for item in replay_events))

    def test_request_metadata_redacts_payloads(self):
        from git_shadow.edge_agent import EventJournal

        journal = EventJournal(self.state_dir / "state", "job-redaction")
        journal.save_request({"token": "secret", "archive_b64": base64.b64encode(b"private").decode(), "safe": "ok"})
        saved = json.loads(journal.request_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["token"], "<redacted>")
        self.assertEqual(saved["archive_b64"], "<redacted>")
        self.assertEqual(saved["safe"], "ok")

    def test_expired_lease_cancels_running_step(self):
        from git_shadow.edge_agent import EdgeExecutor

        executor = EdgeExecutor(str(self.state_dir / "state"))
        executor.submit(
            {
                "type": "submit",
                "job_id": "job-expiry",
                "lease_ttl": 1,
                "steps": [
                    {
                        "id": "slow",
                        "action": "exec",
                        "argv": [sys.executable, "-c", "import time; time.sleep(3)"],
                        "cwd": str(self.workspace),
                    }
                ],
            }
        )
        worker = executor._jobs["job-expiry"]["worker"]
        worker.join(timeout=8)
        self.assertFalse(worker.is_alive())
        state = json.loads((self.state_dir / "state" / "runs" / "job-expiry" / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "failed")
        self.assertIn("lease", state["error"])
        executor._stop.set()
        executor._lease_thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
