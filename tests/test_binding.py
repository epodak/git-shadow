import json
import pathlib
import shutil
import tempfile
import unittest

from git_shadow.binding import (
    get_available_ssh_hosts,
    get_project_binding,
    normalize_project_path,
    set_project_binding,
)


class TestBinding(unittest.TestCase):
    def setUp(self):
        self.temp_dir = pathlib.Path(tempfile.mkdtemp(prefix="git_shadow_binding_test_"))
        self.bindings_file = self.temp_dir / "bindings.json"

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

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
            # 模拟用户在 VPS 和路径提示时均直接按回车
            with patch("builtins.input", side_effect=["", ""]):
                with patch("git_shadow.binding.probe_remote_target", return_value=(False, "~/wkspace/myproj")):
                    host, r_dir = binding_mod.interactive_setup_binding(str(self.temp_dir), default_host="aws")
                    self.assertEqual(host, "aws")
                    self.assertEqual(r_dir, "~/wkspace/myproj")

            # 验证自动持久化
            saved = binding_mod.get_project_binding(str(self.temp_dir))
            self.assertIsNotNone(saved)
            self.assertEqual(saved["host"], "aws")
            self.assertEqual(saved["remote_dir"], "~/wkspace/myproj")
        finally:
            binding_mod.get_bindings_file = orig_func


if __name__ == "__main__":
    unittest.main()
