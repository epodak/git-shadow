import pathlib
import shutil
import tempfile
import time
import unittest

from git_shadow.watcher import LocalChangeWatcher


class TestLocalChangeWatcher(unittest.TestCase):
    def setUp(self):
        self.root = pathlib.Path(tempfile.mkdtemp(prefix="git_shadow_watcher_", dir=str(pathlib.Path.home())))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_watcher_reports_a_new_file_or_falls_back_cleanly(self):
        with LocalChangeWatcher(str(self.root)) as watcher:
            native = watcher.native
            (self.root / ".env").write_text("value\n", encoding="utf-8")
            notified = watcher.wait(1.0)
            if native:
                self.assertTrue(notified)
            else:
                self.assertFalse(watcher.native)

    def test_watcher_adds_new_directories_to_native_tree(self):
        with LocalChangeWatcher(str(self.root)) as watcher:
            if not watcher.native:
                self.skipTest("native inotify is unavailable")
            nested = self.root / "nested"
            nested.mkdir()
            self.assertTrue(watcher.wait(1.0))
            (nested / ".env").write_text("nested\n", encoding="utf-8")
            self.assertTrue(watcher.wait(1.0))


if __name__ == "__main__":
    unittest.main()
