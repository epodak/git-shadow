import pathlib
import shutil
import tempfile
import unittest

from git_shadow.install import install_wrappers


class TestInstall(unittest.TestCase):
    def test_install_creates_hot_reload_wrappers(self):
        root = pathlib.Path(tempfile.mkdtemp(prefix="git_shadow_install_", dir=str(pathlib.Path.home())))
        try:
            paths = install_wrappers(str(root))
            self.assertEqual([path.name for path in paths], ["git-shadow", "git-shadow.cmd"])
            unix = (root / "git-shadow").read_text(encoding="utf-8")
            self.assertIn("git_shadow.cli", unix)
            self.assertIn(str(pathlib.Path(__file__).resolve().parents[1]), unix)
            self.assertTrue((root / "git-shadow").stat().st_mode & 0o111)
            self.assertIn("git_shadow.cli", (root / "git-shadow.cmd").read_text(encoding="utf-8"))
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
