import pathlib
import subprocess
import sys
import unittest


class TestCliContract(unittest.TestCase):
    project_root = pathlib.Path(__file__).resolve().parents[1]

    def test_help_lists_supported_edge_recovery_command(self):
        result = subprocess.run(
            [sys.executable, "-m", "git_shadow.cli", "-h"],
            cwd=self.project_root,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("edge resume <host> <job>", result.stdout)
        self.assertIn("run <host> [agent] --watch", result.stdout)
        self.assertIn("轻量能力探针", result.stdout)
        self.assertIn("失败自动降级为同步模式", result.stdout)


if __name__ == "__main__":
    unittest.main()
