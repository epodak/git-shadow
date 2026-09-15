import base64
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace

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
        journal.append({"event": "safe"})
        self.assertEqual(journal.request_path.stat().st_mode & 0o077, 0)
        self.assertEqual(journal.events_path.stat().st_mode & 0o077, 0)

    def test_runtime_diagnostics_scrub_cloudcli_credentials(self):
        from git_shadow.edge_agent import EdgeExecutor

        previous = os.environ.get("GIT_SHADOW_CLOUDCLI_TOKEN")
        os.environ["GIT_SHADOW_CLOUDCLI_TOKEN"] = "token-for-test"
        executor = EdgeExecutor(str(self.state_dir / "state-secret-scrub"))
        try:
            wire = executor.emit_event("job-secret-scrub", "output", message="token-for-test leaked")
            self.assertEqual(wire["message"], "<redacted> leaked")
            saved = json.loads((self.state_dir / "state-secret-scrub" / "runs" / "job-secret-scrub" / "events.ndjson").read_text())
            self.assertNotIn("token-for-test", json.dumps(saved))
        finally:
            executor._stop.set()
            executor._lease_thread.join(timeout=2)
            if previous is None:
                os.environ.pop("GIT_SHADOW_CLOUDCLI_TOKEN", None)
            else:
                os.environ["GIT_SHADOW_CLOUDCLI_TOKEN"] = previous

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

    def test_shadow_sync_applies_against_matching_base(self):
        from git_shadow.edge_agent import EdgeExecutor

        target = self.workspace / "project"
        target.mkdir()
        shadow_path = target / ".env"
        shadow_path.write_text("REMOTE_BASE\n", encoding="utf-8")
        payload = b"LOCAL_NEXT\n"
        executor = EdgeExecutor(str(self.state_dir / "state-cas-apply"))
        try:
            executor.submit(
                {
                    "type": "submit",
                    "job_id": "job-shadow-apply",
                    "steps": [
                        {
                            "id": "shadow",
                            "action": "shadow.sync",
                            "target": str(target),
                            "entries": [
                                {
                                    "path": ".env",
                                    "base_hash": hashlib.sha256(b"REMOTE_BASE\n").hexdigest(),
                                    "local_hash": hashlib.sha256(payload).hexdigest(),
                                    "content_b64": base64.b64encode(payload).decode("ascii"),
                                }
                            ],
                        }
                    ],
                }
            )
            worker = executor._jobs["job-shadow-apply"]["worker"]
            worker.join(timeout=8)
            self.assertFalse(worker.is_alive())
            self.assertEqual(shadow_path.read_bytes(), payload)
            state = json.loads((self.state_dir / "state-cas-apply" / "runs" / "job-shadow-apply" / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "completed")
        finally:
            executor._stop.set()
            executor._lease_thread.join(timeout=2)

    def test_shadow_sync_preserves_remote_change_and_reports_conflict(self):
        from git_shadow.edge_agent import EdgeExecutor

        target = self.workspace / "project-conflict"
        target.mkdir()
        shadow_path = target / ".env"
        shadow_path.write_text("REMOTE_CHANGED\n", encoding="utf-8")
        payload = b"LOCAL_NEXT\n"
        executor = EdgeExecutor(str(self.state_dir / "state-cas-conflict"))
        try:
            executor.submit(
                {
                    "type": "submit",
                    "job_id": "job-shadow-conflict",
                    "steps": [
                        {
                            "id": "shadow",
                            "action": "shadow.sync",
                            "target": str(target),
                            "entries": [
                                {
                                    "path": ".env",
                                    "base_hash": hashlib.sha256(b"REMOTE_BASE\n").hexdigest(),
                                    "local_hash": hashlib.sha256(payload).hexdigest(),
                                    "content_b64": base64.b64encode(payload).decode("ascii"),
                                }
                            ],
                        }
                    ],
                }
            )
            worker = executor._jobs["job-shadow-conflict"]["worker"]
            worker.join(timeout=8)
            self.assertFalse(worker.is_alive())
            self.assertEqual(shadow_path.read_text(encoding="utf-8"), "REMOTE_CHANGED\n")
            target_key = hashlib.sha256(str(target).encode("utf-8")).hexdigest()[:32]
            conflict = self.state_dir / "state-cas-conflict" / "conflicts" / target_key / "job-shadow-conflict" / ".env"
            self.assertEqual(conflict.read_bytes(), payload)
            state = json.loads((self.state_dir / "state-cas-conflict" / "runs" / "job-shadow-conflict" / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "failed")
            self.assertIn("shadow CAS conflict", state["error"])
        finally:
            executor._stop.set()
            executor._lease_thread.join(timeout=2)

    def test_local_manifest_records_acknowledged_hash_only(self):
        from git_shadow.shadow_sync import ShadowManifestStore

        project = self.state_dir / "local-project"
        project.mkdir()
        secret = project / ".env"
        secret.write_text("private-value\n", encoding="utf-8")
        store = ShadowManifestStore(str(project), state_root=str(self.state_dir / "local-state"))
        entries = store.build_entries([".env"])
        self.assertEqual(len(entries), 1)
        self.assertIsNone(entries[0]["base_hash"])
        store.consume_event({"event": "shadow.applied", "path": ".env", "local_hash": entries[0]["local_hash"]})
        saved = json.loads(store.path.read_text(encoding="utf-8"))
        self.assertEqual(saved["files"][".env"]["synced_hash"], entries[0]["local_hash"])
        self.assertNotIn("private-value", store.path.read_text(encoding="utf-8"))

    def test_wip_patch_is_not_in_default_edge_plan(self):
        from git_shadow.engine import ShadowEngine

        repo = SimpleNamespace(
            root_dir=str(self.workspace),
            remote_url="https://example.invalid/repo.git",
            branch="main",
            commit="abc123",
            is_dirty=True,
            scan_shadow_files=lambda: [],
            capture_wip_patch=lambda: "diff --git a/file b/file\n",
        )
        engine = ShadowEngine.__new__(ShadowEngine)
        engine.repo = repo
        engine.remote_dir = str(self.workspace / "project-plan")
        default_plan = engine.build_edge_plan(include_cloudcli=False, shadow_files=[])
        wip_plan = engine.build_edge_plan(include_cloudcli=False, shadow_files=[], include_wip=True)
        self.assertNotIn("patch.apply", [step["action"] for step in default_plan["steps"]])
        self.assertIn("patch.apply", [step["action"] for step in wip_plan["steps"]])

    def test_cloudcli_session_is_before_git_prepare(self):
        from git_shadow.engine import ShadowEngine

        repo = SimpleNamespace(
            root_dir=str(self.workspace),
            remote_url="https://example.invalid/repo.git",
            branch="main",
            commit="abc123",
            is_dirty=False,
        )
        engine = ShadowEngine.__new__(ShadowEngine)
        engine.repo = repo
        engine.remote_dir = str(self.workspace / "early-session")
        plan = engine.build_edge_plan(provider="codex", include_cloudcli=True, shadow_files=[])
        self.assertEqual(
            [step["action"] for step in plan["steps"]],
            ["workspace.create", "cloudcli.session", "workspace.prepare"],
        )


if __name__ == "__main__":
    unittest.main()
