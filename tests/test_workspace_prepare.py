import pathlib
import shutil
import subprocess
import tempfile
import unittest

from git_shadow.paths import cleanup_test_tree, test_temp_root

from git_shadow.edge_agent import EdgeExecutor


class TestWorkspacePrepare(unittest.TestCase):
    def setUp(self):
        self.root = pathlib.Path(tempfile.mkdtemp(prefix="git_shadow_workspace_", dir=str(test_temp_root())))
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
        cleanup_test_tree(self.root)

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

    def test_legacy_repo_root_migrates_to_its_own_branch_without_losing_dirty_state(self):
        legacy_root = self.root / "wkspace" / "AI"
        legacy_root.parent.mkdir(parents=True)
        subprocess.run(
            ["git", "clone", "--branch", "main", "--single-branch", str(self.origin), str(legacy_root)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        (legacy_root / "README.md").write_text("legacy-dirty\n", encoding="utf-8")
        (legacy_root / "UNTRACKED.local").write_text("keep-me\n", encoding="utf-8")
        requested_target = legacy_root / "foo" / "bar"

        executor = EdgeExecutor(str(self.root / "legacy-route-state"))
        try:
            first = executor._execute_step(
                "job-legacy-route",
                {
                    "action": "workspace.route",
                    "repo_root": str(legacy_root),
                    "target": str(requested_target),
                    "branch": "foo/bar",
                },
            )

            migrated_main = legacy_root / "main"
            self.assertTrue(first["migrated"])
            self.assertTrue((migrated_main / ".git").is_dir())
            self.assertFalse((legacy_root / ".git").exists())
            self.assertEqual(
                (migrated_main / "README.md").read_text(encoding="utf-8"),
                "legacy-dirty\n",
            )
            self.assertEqual(
                (migrated_main / "UNTRACKED.local").read_text(encoding="utf-8"),
                "keep-me\n",
            )
            self.assertFalse(requested_target.exists())

            second = executor._execute_step(
                "job-legacy-route-again",
                {
                    "action": "workspace.route",
                    "repo_root": str(legacy_root),
                    "target": str(requested_target),
                    "branch": "foo/bar",
                },
            )
            self.assertFalse(second["migrated"])
        finally:
            executor._stop.set()
            executor._lease_thread.join(timeout=2)

    def test_branch_scoped_workspace_fetches_only_its_branch(self):
        subprocess.run(
            ["git", "checkout", "-b", "foo/bar"],
            cwd=self.seed,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        (self.seed / "BRANCH.txt").write_text("foo/bar\n", encoding="utf-8")
        subprocess.run(["git", "add", "BRANCH.txt"], cwd=self.seed, check=True)
        subprocess.run(
            ["git", "commit", "-m", "branch"],
            cwd=self.seed,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        subprocess.run(
            ["git", "push", "-u", "origin", "foo/bar"],
            cwd=self.seed,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        target = self.root / "wkspace" / "AI" / "foo" / "bar"
        target.mkdir(parents=True)
        executor = EdgeExecutor(str(self.root / "branch-state"))
        try:
            executor._execute_step(
                "job-branch-workspace",
                {
                    "action": "workspace.prepare",
                    "target": str(target),
                    "remote_url": str(self.origin),
                    "branch": "foo/bar",
                },
            )
            branch = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=target,
                check=True,
                text=True,
                stdout=subprocess.PIPE,
            ).stdout.strip()
            fetch_refspec = subprocess.run(
                ["git", "config", "--get-all", "remote.origin.fetch"],
                cwd=target,
                check=True,
                text=True,
                stdout=subprocess.PIPE,
            ).stdout.splitlines()

            self.assertEqual(branch, "foo/bar")
            self.assertTrue((target / "BRANCH.txt").is_file())
            self.assertIn(
                "+refs/heads/foo/bar:refs/remotes/origin/foo/bar",
                fetch_refspec,
            )
            refs = subprocess.run(
                ["git", "for-each-ref", "--format=%(refname)", "refs/remotes/origin"],
                cwd=target,
                check=True,
                text=True,
                stdout=subprocess.PIPE,
            ).stdout
            self.assertIn("refs/remotes/origin/foo/bar", refs)
            self.assertNotIn("refs/remotes/origin/main", refs)
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
            fetch_refspec = subprocess.run(
                ["git", "config", "--get-all", "remote.origin.fetch"],
                cwd=self.target,
                check=True,
                text=True,
                stdout=subprocess.PIPE,
            ).stdout.splitlines()
            self.assertEqual(
                fetch_refspec,
                ["+refs/heads/main:refs/remotes/origin/main"],
            )
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
