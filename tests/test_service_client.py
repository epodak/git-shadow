import pathlib
import unittest
from types import SimpleNamespace

from git_shadow.service import ServiceClient


class FakeServiceClient(ServiceClient):
    def __init__(self):
        super().__init__("vps-aws", "/home/ubuntu/example-project")
        self.commands = []

    def _run_ssh(self, remote_command, input_data=None):
        self.commands.append(remote_command)
        return SimpleNamespace(returncode=0, stdout='{"status":"running"}\n', stderr="")


class TestServiceClient(unittest.TestCase):
    def test_project_service_paths_are_stable_and_shell_safe(self):
        client = FakeServiceClient()
        loaded = client.load()
        self.assertEqual(loaded["status"], "running")
        self.assertIn("--serve", client.commands[0])
        self.assertIn("--service-root \"$HOME/.local/share/git-shadow/services/", client.commands[0])
        self.assertNotIn(str(pathlib.Path.home()), client.commands[0])

        status = client.status()
        self.assertEqual(status["status"], "running")
        unloaded = client.unload()
        self.assertEqual(unloaded["status"], "running")
        self.assertEqual(len(client.commands), 3)


if __name__ == "__main__":
    unittest.main()
