import base64
import hashlib
import json
import pathlib
import shutil
import tempfile
import unittest
from types import SimpleNamespace

from git_shadow.edge_agent import EdgeError, EdgeExecutor, EventJournal
from git_shadow.shadow_sync import ShadowManifestStore


class TestShadowPull(unittest.TestCase):
    def setUp(self):
        self.root = pathlib.Path(tempfile.mkdtemp(prefix="git_shadow_pull_", dir=str(pathlib.Path.home())))
        self.remote = self.root / "remote"
        self.local = self.root / "local"
        self.remote.mkdir()
        self.local.mkdir()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    @staticmethod
    def digest(payload: bytes) -> str:
        return hashlib.sha256(payload).hexdigest()

    def test_remote_change_is_applied_when_local_is_unchanged(self):
        base = b"BASE\n"
        remote_value = b"REMOTE_NEXT\n"
        (self.remote / ".env").write_bytes(remote_value)
        (self.local / ".env").write_bytes(base)
        store = ShadowManifestStore(str(self.local), state_root=str(self.root / "local-state"))
        store.consume_event({"event": "shadow.applied", "path": ".env", "local_hash": self.digest(base)})

        executor = EdgeExecutor(str(self.root / "remote-state"))
        events = []
        executor.emit_event = lambda job_id, event, **data: events.append({"job_id": job_id, "event": event, **data})
        try:
            result = executor._execute_step(
                "job-pull-apply",
                {
                    "action": "shadow.pull",
                    "target": str(self.remote),
                    "entries": [
                        {
                            "path": ".env",
                            "base_hash": self.digest(base),
                            "local_hash": self.digest(base),
                        }
                    ],
                },
            )
            self.assertEqual(result, {"changed": 1, "conflicts": 0})
            self.assertEqual(events[0]["event"], "shadow.remote")
            store.consume_event(events[0])
            self.assertEqual((self.local / ".env").read_bytes(), remote_value)
            self.assertEqual(store.base_hash(".env"), self.digest(remote_value))
        finally:
            executor._stop.set()
            executor._lease_thread.join(timeout=2)

    def test_remote_change_becomes_local_conflict_when_both_sides_changed(self):
        base = b"BASE\n"
        local_value = b"LOCAL_NEXT\n"
        remote_value = b"REMOTE_NEXT\n"
        (self.remote / ".env").write_bytes(remote_value)
        (self.local / ".env").write_bytes(local_value)
        store = ShadowManifestStore(str(self.local), state_root=str(self.root / "local-state"))
        store.consume_event({"event": "shadow.applied", "path": ".env", "local_hash": self.digest(base)})

        executor = EdgeExecutor(str(self.root / "remote-state"))
        events = []
        executor.emit_event = lambda job_id, event, **data: events.append({"job_id": job_id, "event": event, **data})
        try:
            with self.assertRaises(EdgeError):
                executor._execute_step(
                    "job-pull-conflict",
                    {
                        "action": "shadow.pull",
                        "target": str(self.remote),
                        "entries": [
                            {
                                "path": ".env",
                                "base_hash": self.digest(base),
                                "local_hash": self.digest(local_value),
                            }
                        ],
                    },
                )
            self.assertEqual(events[0]["event"], "shadow.conflict")
            store.consume_event(events[0])
            self.assertEqual((self.local / ".env").read_bytes(), local_value)
            conflict = self.root / "local-state" / "conflicts"
            self.assertEqual(list(conflict.rglob(".env"))[0].read_bytes(), remote_value)
        finally:
            executor._stop.set()
            executor._lease_thread.join(timeout=2)

    def test_event_journal_redacts_remote_shadow_content_but_wire_event_keeps_it(self):
        journal = EventJournal(self.root / "journal-state", "job-redacted-pull")
        content = base64.b64encode(b"private").decode("ascii")
        wire_event = journal.append({"event": "shadow.remote", "content_b64": content})
        saved = json.loads(journal.events_path.read_text(encoding="utf-8").strip())
        self.assertEqual(wire_event["content_b64"], content)
        self.assertEqual(saved["content_b64"], "<redacted>")
        replayed = list(journal.replay(0))[0]
        self.assertTrue(replayed["replay_redacted"])

    def test_push_encodes_delete_empty_file_and_rename_as_cas_entries(self):
        base = b"BASE\n"
        (self.remote / ".env").write_bytes(base)
        (self.local / ".env").write_bytes(base)
        store = ShadowManifestStore(str(self.local), state_root=str(self.root / "local-state"))
        base_hash = self.digest(base)
        store.consume_event({"event": "shadow.applied", "path": ".env", "local_hash": base_hash})
        (self.local / ".env").unlink()
        (self.local / ".env.next").write_bytes(b"")

        entries = store.build_entries([".env.next"])
        by_path = {entry["path"]: entry for entry in entries}
        self.assertTrue(by_path[".env"]["deleted"])
        self.assertEqual(by_path[".env"]["base_hash"], base_hash)
        self.assertFalse(by_path[".env.next"]["deleted"])
        self.assertEqual(by_path[".env.next"]["content_b64"], "")

        executor = EdgeExecutor(str(self.root / "remote-state-rename"))
        events = []
        executor.emit_event = lambda job_id, event, **data: events.append({"job_id": job_id, "event": event, **data})
        try:
            result = executor._execute_step(
                "job-push-rename",
                {"action": "shadow.sync", "target": str(self.remote), "entries": entries},
            )
            self.assertEqual(result, {"applied": 2, "conflicts": 0})
            self.assertFalse((self.remote / ".env").exists())
            self.assertEqual((self.remote / ".env.next").read_bytes(), b"")
            self.assertEqual(len([event for event in events if event["event"] == "shadow.applied"]), 2)
            for event in events:
                store.consume_event(event)
            self.assertIsNone(store.base_hash(".env"))
            self.assertEqual(store.base_hash(".env.next"), self.digest(b""))
        finally:
            executor._stop.set()
            executor._lease_thread.join(timeout=2)

    def test_pull_discovers_new_remote_file_through_shadow_patterns(self):
        remote_value = b"REMOTE_NEW\n"
        (self.remote / ".env.remote").write_bytes(remote_value)
        store = ShadowManifestStore(str(self.local), state_root=str(self.root / "discovery-state"))
        executor = EdgeExecutor(str(self.root / "remote-state-discovery"))
        events = []
        executor.emit_event = lambda job_id, event, **data: events.append({"job_id": job_id, "event": event, **data})
        try:
            result = executor._execute_step(
                "job-pull-discovery",
                {
                    "action": "shadow.pull",
                    "target": str(self.remote),
                    "entries": [],
                    "patterns": [".env*"],
                },
            )
            self.assertEqual(result, {"changed": 1, "conflicts": 0})
            self.assertEqual(events[0]["event"], "shadow.remote")
            store.consume_event(events[0])
            self.assertEqual((self.local / ".env.remote").read_bytes(), remote_value)
        finally:
            executor._stop.set()
            executor._lease_thread.join(timeout=2)

    def test_remote_target_scopes_do_not_share_acknowledgement_baselines(self):
        project = self.root / "scoped-project"
        project.mkdir()
        secret = project / ".env"
        secret.write_text("private-value\n", encoding="utf-8")
        state_root = self.root / "scoped-state"
        first = ShadowManifestStore(
            str(project),
            state_root=str(state_root),
            remote_scope="host-a\n/home/a/project",
        )
        second = ShadowManifestStore(
            str(project),
            state_root=str(state_root),
            remote_scope="host-a\n/home/b/project",
        )

        entries = first.build_entries([".env"])
        first.consume_event({"event": "shadow.applied", "path": ".env", "local_hash": entries[0]["local_hash"]})

        self.assertEqual(first.base_hash(".env"), entries[0]["local_hash"])
        self.assertIsNone(second.base_hash(".env"))
        self.assertNotEqual(first.path, second.path)

    def test_pull_plan_contains_workspace_creation_and_shadow_pull(self):
        from git_shadow.engine import ShadowEngine

        repo = SimpleNamespace(
            root_dir=str(self.local),
            remote_url="https://example.invalid/repo.git",
            branch="main",
            commit="abc123",
            scan_shadow_files=lambda: [".env"],
        )
        engine = ShadowEngine.__new__(ShadowEngine)
        engine.repo = repo
        engine.remote_dir = str(self.remote)
        store = ShadowManifestStore(str(self.local), state_root=str(self.root / "local-state"))
        plan = engine.build_shadow_pull_plan(shadow_files=[".env"], shadow_store=store)
        self.assertEqual([step["action"] for step in plan["steps"]], ["workspace.create", "shadow.pull"])
        self.assertEqual(plan["steps"][1]["entries"][0]["path"], ".env")


if __name__ == "__main__":
    unittest.main()
