import pathlib
import subprocess
import unittest


class TestAcceptanceScript(unittest.TestCase):
    script = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "acceptance_vps.sh"

    def test_script_has_valid_shell_syntax(self):
        result = subprocess.run(["bash", "-n", str(self.script)], capture_output=True, text=True, errors="replace")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_help_does_not_try_to_use_a_host(self):
        result = subprocess.run(["bash", str(self.script), "--help"], capture_output=True, text=True, errors="replace")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("scripts/acceptance_vps.sh <ssh-host>", result.stdout)

    def test_unknown_option_fails_before_ssh(self):
        result = subprocess.run(["bash", str(self.script), "vps", "--unknown"], capture_output=True, text=True, errors="replace")
        self.assertEqual(result.returncode, 2)
        self.assertIn("未知参数: --unknown", result.stderr)


if __name__ == "__main__":
    unittest.main()
