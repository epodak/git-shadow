import os
import sys
import shutil
import tempfile
import unittest
import subprocess

# 确保能检索到当前工作区的 git_shadow
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from git_shadow.scanner import RepoState

class TestScanner(unittest.TestCase):
    def setUp(self):
        # 创建临时 Git 目录测试
        self.test_dir = tempfile.mkdtemp(prefix="git_shadow_test_")
        subprocess.run(["git", "init"], cwd=self.test_dir, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "TestUser"], cwd=self.test_dir, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.test_dir, check=True)

        # 写入 .gitignore
        gitignore_content = """
node_modules/
.env*
*.secret
dist/
"""
        with open(os.path.join(self.test_dir, ".gitignore"), "w", encoding="utf-8") as f:
            f.write(gitignore_content)

        # 提交 .gitignore
        subprocess.run(["git", "add", ".gitignore"], cwd=self.test_dir, check=True)
        subprocess.run(["git", "commit", "-m", "chore: init .gitignore"], cwd=self.test_dir, check=True)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_shadow_filtering(self):
        """测试真正需要影子的文件与黑名单排除"""
        # 创建一个合法的影子文件 .env.local
        with open(os.path.join(self.test_dir, ".env.local"), "w", encoding="utf-8") as f:
            f.write("API_KEY=123456\n")

        # 创建另一个合法的影子文件 db.secret
        with open(os.path.join(self.test_dir, "db.secret"), "w", encoding="utf-8") as f:
            f.write("SECRET_PASS\n")

        # 创建一个属于黑名单的 node_modules 目录与文件
        nm_dir = os.path.join(self.test_dir, "node_modules", "some-pkg")
        os.makedirs(nm_dir, exist_ok=True)
        with open(os.path.join(nm_dir, "index.js"), "w", encoding="utf-8") as f:
            f.write("console.log('heavy');\n")

        # 运行扫描器
        repo = RepoState(self.test_dir)
        self.assertTrue(repo.is_git)
        shadows = repo.scan_shadow_files()

        # 验证 .env.local 和 db.secret 在列表中
        self.assertIn(".env.local", shadows)
        self.assertIn("db.secret", shadows)

        # 核心断言：node_modules 下的任何文件绝对不得出现在影子列表中
        for s in shadows:
            self.assertFalse(s.startswith("node_modules"), f"错误：node_modules 未被排除 -> {s}")

if __name__ == "__main__":
    unittest.main()
