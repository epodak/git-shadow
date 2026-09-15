import pathlib
import shutil
import subprocess
import tempfile
import unittest

from git_shadow.edge_agent import EdgeExecutor


class TestWorkspacePrepare(unittest.TestCase):
    def setUp(self):
        self.root = pathlib.Path(tempfile.mkdtemp(prefix="git_shadow_workspace_", dir=str(pathlib.Path.home())))
        self.seed = self.root / "seed"
        self.origin = self.root / "origin.git"
        self.target = self.root / "target"
        self.seed.mkdir()
        subprocess.run(["git", "init", "-b", "main"], cwd=self.seed, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=self.seed, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=self.seed, check=True)
        (self.seed / "README.md").write_text("from-origin\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=self.seed, check=True)
        subprocess.run(["git", "commit", "-m", "initial"], cwd=self.seed, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        subprocess.run(["git", "init", "--bare", str(self.origin)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        subprocess.run(["git", "remote", "add", "origin", str(self.origin)], cwd=self.seed, check=True)
        subprocess.run(["git", "push", "-u", "origin", "main"], cwd=self.seed, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_nonempty_cloudcli_directory_preserves_early_ai_file_after_git_prepare(self):
        self.target.mkdir()
        early_file = self.target / "README.md"
        early_file.write_text("typed-before-clone\n", encoding="utf-8")
        executor = EdgeExecutor(str(self.root / "state"))
        try:
            executor._execute_step(
                "job-workspace-prepare",
                {
                    "action": "workspace.prepare",
                    "target": str(self.target),
                    "remote_url": str(self.origin),
                    "branch": "main",
                },
            )
            self.assertTrue((self.target / ".git" / "config").exists())
            self.assertEqual(early_file.read_text(encoding="utf-8"), "typed-before-clone\n")
            self.assertEqual((self.target / ".git").exists(), True)
            status = subprocess.run(["git", "status", "--porcelain"], cwd=self.target, check=True, text=True, stdout=subprocess.PIPE)
            self.assertIn(" M README.md", status.stdout)
        finally:
            executor._stop.set()
            executor._lease_thread.join(timeout=2)

    def test_existing_dirty_workspace_is_not_reset_by_prepare(self):
        subprocess.run(["git", "clone", "--branch", "main", str(self.origin), str(self.target)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        dirty_file = self.target / "README.md"
        dirty_file.write_text("local-dirty\n", encoding="utf-8")
        executor = EdgeExecutor(str(self.root / "dirty-state"))
        try:
            executor._execute_step(
                "job-dirty-workspace",
                {
                    "action": "workspace.prepare",
                    "target": str(self.target),
                    "remote_url": str(self.origin),
                    "branch": "main",
                },
            )
            self.assertEqual(dirty_file.read_text(encoding="utf-8"), "local-dirty\n")
        finally:
            executor._stop.set()
            executor._lease_thread.join(timeout=2)

    def test_missing_branch_fails_without_resetting_existing_workspace(self):
        subprocess.run(["git", "clone", "--branch", "main", str(self.origin), str(self.target)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        before = (self.target / "README.md").read_text(encoding="utf-8")
        executor = EdgeExecutor(str(self.root / "missing-branch-state"))
        try:
            with self.assertRaises(Exception):
                executor._execute_step(
                    "job-missing-branch",
                    {
                        "action": "workspace.prepare",
                        "target": str(self.target),
                        "remote_url": str(self.origin),
                        "branch": "does-not-exist",
                    },
                )
            self.assertEqual((self.target / "README.md").read_text(encoding="utf-8"), before)
        finally:
            executor._stop.set()
            executor._lease_thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
