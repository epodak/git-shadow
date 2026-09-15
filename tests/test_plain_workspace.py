import pathlib
import shutil
import tempfile
import unittest

from git_shadow.edge_agent import EdgeExecutor
from git_shadow.scanner import RepoState


class TestPlainWorkspace(unittest.TestCase):
    def setUp(self):
        self.root = pathlib.Path(tempfile.mkdtemp(prefix="git_shadow_plain_", dir=str(pathlib.Path.home())))
        (self.root / ".env").write_text("plain-secret\n", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_plain_folder_has_project_identity_and_shadow_scan(self):
        repo = RepoState(str(self.root))
        self.assertFalse(repo.is_git)
        self.assertEqual(repo.repo_name, self.root.name)
        self.assertEqual(repo.branch, "main")
        self.assertEqual(repo.scan_shadow_files(), [".env"])

    def test_plain_remote_workspace_does_not_create_git_metadata(self):
        target = self.root / "remote"
        executor = EdgeExecutor(str(self.root / "state"))
        try:
            executor._execute_step(
                "job-plain-workspace",
                {
                    "action": "workspace.prepare",
                    "target": str(target),
                    "git_enabled": False,
                    "branch": "main",
                },
            )
            self.assertTrue(target.is_dir())
            self.assertFalse((target / ".git").exists())
        finally:
            executor._stop.set()
            executor._lease_thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
