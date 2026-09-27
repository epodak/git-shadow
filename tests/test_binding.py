import json
import pathlib
import shutil
import tempfile
import unittest

from git_shadow.paths import cleanup_test_tree, test_temp_root

from git_shadow.binding import (
    get_available_ssh_hosts,
    get_project_binding,
    normalize_project_path,
    set_project_binding,
)


class TestBinding(unittest.TestCase):
    def setUp(self):
        self.temp_dir = pathlib.Path(tempfile.mkdtemp(prefix="git_shadow_binding_test_", dir=str(test_temp_root())))
        self.bindings_file = self.temp_dir / "bindings.json"

    def tearDown(self):
        cleanup_test_tree(self.temp_dir)

    def test_set_and_get_binding(self):
        project = "D:/test_project/foo"
        key = normalize_project_path(project)
        
        # 覆写存储路径进行测试
        import git_shadow.binding as binding_mod
        orig_func = binding_mod.get_bindings_file
        binding_mod.get_bindings_file = lambda: self.bindings_file
        try:
            res = set_project_binding(project, "aws", "~/wkspace/foo")
            self.assertEqual(res["host"], "aws")
            self.assertEqual(res["remote_dir"], "~/wkspace/foo")

            fetched = get_project_binding(project)
            self.assertIsNotNone(fetched)
            self.assertEqual(fetched["host"], "aws")
            self.assertEqual(fetched["remote_dir"], "~/wkspace/foo")
        finally:
            binding_mod.get_bindings_file = orig_func

    def test_branch_bindings_are_isolated_for_same_local_repo(self):
        import git_shadow.binding as binding_mod

        orig_func = binding_mod.get_bindings_file
        binding_mod.get_bindings_file = lambda: self.bindings_file
        try:
            project = str(self.temp_dir)
            foo = binding_mod.set_project_binding(
                project,
                "aws",
                launch_mode="cloudcli",
                branch="foo/bar",
                repo_name="AI",
                is_git=True,
            )
            main = binding_mod.set_project_binding(
                project,
                "aws",
                launch_mode="terminal",
                branch="main",
                repo_name="AI",
                is_git=True,
            )

            self.assertEqual(foo["remote_dir"], "~/wkspace/AI/foo/bar")
            self.assertEqual(main["remote_dir"], "~/wkspace/AI/main")
            self.assertEqual(
                binding_mod.get_project_binding(project, "foo/bar")["launch_mode"],
                "cloudcli",
            )
            self.assertEqual(
                binding_mod.get_project_binding(project, "main")["launch_mode"],
                "terminal",
            )
        finally:
            binding_mod.get_bindings_file = orig_func

    def test_legacy_path_only_binding_is_not_reused_for_another_branch(self):
        import git_shadow.binding as binding_mod

        orig_func = binding_mod.get_bindings_file
        binding_mod.get_bindings_file = lambda: self.bindings_file
        try:
            project = str(self.temp_dir)
            binding_mod.set_project_binding(
                project,
                "aws",
                "~/wkspace/AI",
                launch_mode="cloudcli",
            )
            self.assertIsNotNone(binding_mod.get_project_binding(project))
            self.assertIsNone(binding_mod.get_project_binding(project, "foo/bar"))
        finally:
            binding_mod.get_bindings_file = orig_func

    def test_get_available_ssh_hosts_filters_properly(self):
        hosts = get_available_ssh_hosts()
        self.assertIsInstance(hosts, list)
        for h in hosts:
            self.assertNotIn("*", h)
            self.assertNotIn("github.com", h)

    def test_interactive_setup_binding_defaults(self):
        from unittest.mock import patch
        import git_shadow.binding as binding_mod

        orig_func = binding_mod.get_bindings_file
        binding_mod.get_bindings_file = lambda: self.bindings_file
        try:
            # 模拟用户在 VPS、路径、打开方式提示时均直接按回车
            with patch("git_shadow.binding.get_available_ssh_hosts", return_value=["aws", "tencent"]):
                with patch("builtins.input", side_effect=["", "", ""]):
                    with patch("git_shadow.binding.probe_remote_target", return_value=(False, "~/wkspace/myproj")):
                        with patch("git_shadow.binding.probe_remote_capabilities", return_value={"cloudcli": {"available": True, "reason": "ready"}}):
                            host, r_dir, mode = binding_mod.interactive_setup_binding(str(self.temp_dir), default_host="aws")
                        self.assertEqual(host, "aws")
                        self.assertEqual(r_dir, "~/wkspace/myproj")
                        self.assertEqual(mode, "cloudcli")

            # 验证自动持久化
            saved = binding_mod.get_project_binding(str(self.temp_dir))
            self.assertIsNotNone(saved)
            self.assertEqual(saved["host"], "aws")
            self.assertEqual(saved["remote_dir"], "~/wkspace/myproj")
            self.assertEqual(saved["launch_mode"], "cloudcli")
        finally:
            binding_mod.get_bindings_file = orig_func

    def test_interactive_setup_binding_cloudcli_unavailable_defaults_terminal(self):
        from unittest.mock import patch
        import git_shadow.binding as binding_mod

        orig_func = binding_mod.get_bindings_file
        binding_mod.get_bindings_file = lambda: self.bindings_file
        try:
            with patch("git_shadow.binding.get_available_ssh_hosts", return_value=["aws"]):
                with patch("builtins.input", side_effect=["", "", ""]):
                    with patch("git_shadow.binding.probe_remote_target", return_value=(False, "~/wkspace/fallback")):
                        with patch("git_shadow.binding.probe_remote_capabilities", return_value={"cloudcli": {"available": False, "reason": "ssh-probe-failed: offline"}}):
                            host, r_dir, mode = binding_mod.interactive_setup_binding(str(self.temp_dir), default_host="aws")
            self.assertEqual(host, "aws")
            self.assertEqual(r_dir, "~/wkspace/fallback")
            self.assertEqual(mode, "terminal")
            saved = binding_mod.get_project_binding(str(self.temp_dir))
            self.assertEqual(saved["launch_mode"], "terminal")
        finally:
            binding_mod.get_bindings_file = orig_func

    def test_interactive_setup_binding_missing_cloudcli_auto_bootstraps(self):
        from unittest.mock import patch
        import git_shadow.binding as binding_mod

        orig_func = binding_mod.get_bindings_file
        binding_mod.get_bindings_file = lambda: self.bindings_file
        try:
            bootstrap_result = {
                "success": True,
                "metadata": {
                    "service": "systemd-user",
                    "cloudcli_version": "1.37.3",
                },
                "after": {"available": True, "reason": "ready"},
            }
            with patch("git_shadow.binding.get_available_ssh_hosts", return_value=["aws"]):
                with patch("builtins.input", side_effect=["", "", ""]):
                    with patch("git_shadow.binding.probe_remote_target", return_value=(False, "~/wkspace/bootstrap")):
                        with patch("git_shadow.binding.probe_remote_capabilities", return_value={"cloudcli": {"available": False, "reason": "cloudcli-not-installed"}}):
                            with patch("git_shadow.binding.RemoteBootstrapManager.ensure_cloudcli", return_value=bootstrap_result) as ensure:
                                host, r_dir, mode = binding_mod.interactive_setup_binding(str(self.temp_dir), default_host="aws")

            self.assertEqual(host, "aws")
            self.assertEqual(r_dir, "~/wkspace/bootstrap")
            self.assertEqual(mode, "cloudcli")
            ensure.assert_called_once()
            saved = binding_mod.get_project_binding(str(self.temp_dir))
            self.assertEqual(saved["launch_mode"], "cloudcli")
        finally:
            binding_mod.get_bindings_file = orig_func

    def test_interactive_setup_binding_custom_mode(self):
        from unittest.mock import patch
        import git_shadow.binding as binding_mod

        orig_func = binding_mod.get_bindings_file
        binding_mod.get_bindings_file = lambda: self.bindings_file
        try:
            # 模拟用户选择主机 1，路径回车，模式选择 3 (silent)
            with patch("git_shadow.binding.get_available_ssh_hosts", return_value=["aws"]):
                with patch("builtins.input", side_effect=["1", "", "3"]):
                    with patch("git_shadow.binding.probe_remote_target", return_value=(False, "~/wkspace/test_silent")):
                        with patch("git_shadow.binding.probe_remote_capabilities", return_value={"cloudcli": {"available": False, "reason": "cloudcli-service-not-running"}}):
                            host, r_dir, mode = binding_mod.interactive_setup_binding(str(self.temp_dir), default_host="aws")
                        self.assertEqual(mode, "silent")
            saved = binding_mod.get_project_binding(str(self.temp_dir))
            self.assertEqual(saved["launch_mode"], "silent")
        finally:
            binding_mod.get_bindings_file = orig_func


if __name__ == "__main__":
    unittest.main()
