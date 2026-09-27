import os
import pathlib
import tempfile
import unittest
from unittest.mock import patch

import git_shadow.paths as paths


class TestGitShadowPaths(unittest.TestCase):
    def test_default_home_is_single_hidden_root(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GIT_SHADOW_HOME", None)
            self.assertEqual(
                paths.git_shadow_home(create=False),
                pathlib.Path.home() / ".git-shadow",
            )

    def test_home_can_be_overridden(self):
        with tempfile.TemporaryDirectory() as tmp:
            expected = pathlib.Path(tmp) / "custom-shadow-home"
            with patch.dict(
                os.environ,
                {"GIT_SHADOW_HOME": str(expected)},
                clear=False,
            ):
                self.assertEqual(paths.git_shadow_home(), expected)
                self.assertTrue(expected.is_dir())

    def test_test_temp_root_stays_inside_git_shadow_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            expected_home = pathlib.Path(tmp) / ".git-shadow-test"
            with patch.dict(
                os.environ,
                {"GIT_SHADOW_HOME": str(expected_home)},
                clear=False,
            ):
                root = paths.test_temp_root()
                self.assertEqual(root, expected_home / "tmp" / "tests")
                self.assertTrue(root.is_dir())

    def test_cleanup_test_tree_retries_until_directory_is_gone(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = pathlib.Path(tmp) / "flaky"
            target.mkdir()
            (target / "x.txt").write_text("x", encoding="utf-8")

            real_rmtree = paths.shutil.rmtree
            calls = {"count": 0}

            def flaky_rmtree(path, onerror=None):
                calls["count"] += 1
                if calls["count"] < 3:
                    return None
                return real_rmtree(path, onerror=onerror)

            with patch.object(paths.shutil, "rmtree", side_effect=flaky_rmtree):
                with patch.object(paths.time, "sleep", return_value=None):
                    self.assertTrue(paths.cleanup_test_tree(target, attempts=4))

            self.assertGreaterEqual(calls["count"], 3)
            self.assertFalse(target.exists())

    def test_legacy_local_state_moves_only_when_destination_is_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp)
            legacy = base / "legacy-state"
            legacy_share = base / "legacy-share"
            target = base / "new-home"
            legacy.mkdir()
            legacy_share.mkdir()
            (legacy / "bindings.json").write_text('{"legacy": true}', encoding="utf-8")
            (legacy / "shadows").mkdir()
            (legacy / "shadows" / "old.json").write_text("legacy", encoding="utf-8")
            (legacy_share / "bin").mkdir()
            (legacy_share / "bin" / "git-shadow-edge-agent.py").write_text(
                "legacy-edge",
                encoding="utf-8",
            )

            target.mkdir()
            (target / "bindings.json").write_text('{"new": true}', encoding="utf-8")

            with patch.object(paths, "LEGACY_LOCAL_STATE", legacy):
                with patch.object(paths, "LEGACY_LOCAL_SHARE", legacy_share):
                    paths.migrate_legacy_local_state(target)

            self.assertEqual(
                (target / "bindings.json").read_text(encoding="utf-8"),
                '{"new": true}',
            )
            self.assertTrue((target / "shadows" / "old.json").is_file())
            self.assertEqual(
                (target / "bin" / "git-shadow-edge-agent.py").read_text(encoding="utf-8"),
                "legacy-edge",
            )
            self.assertTrue((legacy / "bindings.json").is_file())
            self.assertFalse((legacy / "shadows").exists())
            self.assertFalse((legacy_share / "bin").exists())


if __name__ == "__main__":
    unittest.main()
