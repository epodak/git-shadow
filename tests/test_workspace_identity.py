import pathlib
import unittest
from types import SimpleNamespace

from git_shadow.engine import ShadowEngine
from git_shadow.probe import RemoteProbe
from git_shadow.workspace_identity import (
    branch_parts,
    branch_route,
    workspace_relative_path,
)


class _Result:
    def __init__(self, stdout, returncode=0):
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


class TestWorkspaceIdentity(unittest.TestCase):
    def test_branch_namespace_becomes_directory_namespace(self):
        self.assertEqual(branch_parts("foo/bar"), ["foo", "bar"])
        self.assertEqual(branch_route("foo/bar"), "foo/bar")
        self.assertEqual(
            workspace_relative_path("AI", "foo/bar", is_git=True),
            "AI/foo/bar",
        )

    def test_plain_folder_does_not_gain_synthetic_main_directory(self):
        self.assertEqual(
            workspace_relative_path("notes", None, is_git=False),
            "notes",
        )

    def test_branch_traversal_is_rejected(self):
        with self.assertRaises(ValueError):
            workspace_relative_path("AI", "../secret", is_git=True)

    def test_engine_resolves_branch_scoped_wkspace_path(self):
        repo = SimpleNamespace(
            repo_name="AI",
            branch="foo/bar",
            is_git=True,
        )
        engine = ShadowEngine.__new__(ShadowEngine)
        engine.repo = repo
        engine.remote_host = "aws"
        engine.remote_dir = None
        engine._run_ssh = lambda *args, **kwargs: _Result(
            "/home/agent/wkspace/AI/foo/bar\n"
        )

        self.assertEqual(
            engine.resolve_remote_dir(),
            "/home/agent/wkspace/AI/foo/bar",
        )

    def test_probe_preferred_path_uses_same_identity(self):
        probe = RemoteProbe("aws")
        probe.data = {
            "home": "/home/agent",
            "workspaces": "wkspace projects",
        }
        self.assertEqual(
            probe.get_preferred_workspace_dir(
                "AI",
                "foo/bar",
                is_git=True,
            ),
            "/home/agent/wkspace/AI/foo/bar",
        )


if __name__ == "__main__":
    unittest.main()
