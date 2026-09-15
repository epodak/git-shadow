import pathlib
import shutil
import subprocess
import tempfile
import unittest

from git_shadow.git_sync import auto_fast_forward_pull


class TestGitSync(unittest.TestCase):
    def setUp(self):
        self.root = pathlib.Path(tempfile.mkdtemp(prefix="git_shadow_git_sync_", dir=str(pathlib.Path.home())))
        self.remote = self.root / "origin.git"
        self.local = self.root / "local"
        self.writer = self.root / "writer"
        subprocess.run(["git", "init", "--bare", str(self.remote)], check=True, capture_output=True)
        subprocess.run(["git", "clone", str(self.remote), str(self.local)], check=True, capture_output=True)
        subprocess.run(["git", "clone", str(self.remote), str(self.writer)], check=True, capture_output=True)
        for checkout in (self.local, self.writer):
            subprocess.run(["git", "checkout", "-b", "main"], cwd=checkout, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test User"], cwd=checkout, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=checkout, check=True)
        (self.writer / "README.md").write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=self.writer, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "base"], cwd=self.writer, check=True, capture_output=True)
        subprocess.run(["git", "push", "-u", "origin", "main"], cwd=self.writer, check=True, capture_output=True)
        subprocess.run(["git", "fetch", "origin", "main"], cwd=self.local, check=True, capture_output=True)
        subprocess.run(["git", "checkout", "-B", "main", "origin/main"], cwd=self.local, check=True, capture_output=True)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_clean_branch_fast_forwards_from_origin(self):
        (self.writer / "README.md").write_text("next\n", encoding="utf-8")
        subprocess.run(["git", "commit", "-am", "next"], cwd=self.writer, check=True, capture_output=True)
        subprocess.run(["git", "push"], cwd=self.writer, check=True, capture_output=True)

        result = auto_fast_forward_pull(str(self.local), branch="main")

        self.assertEqual(result["status"], "updated")
        self.assertEqual((self.local / "README.md").read_text(encoding="utf-8"), "next\n")

    def test_dirty_branch_is_not_touched(self):
        (self.local / "README.md").write_text("local work\n", encoding="utf-8")

        result = auto_fast_forward_pull(str(self.local), branch="main")

        self.assertEqual(result["status"], "blocked")
        self.assertEqual((self.local / "README.md").read_text(encoding="utf-8"), "local work\n")


if __name__ == "__main__":
    unittest.main()
