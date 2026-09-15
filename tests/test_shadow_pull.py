import base64
import hashlib
import json
import pathlib
import shutil
import tempfile
import unittest

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


if __name__ == "__main__":
    unittest.main()
