"""
git_shadow.scanner
本地 Git 仓库与影子文件扫描器：
严格遵循 ADR-2026-09-15 契约：
以 .gitshadow 作为唯一的声明式影子白名单契约，彻底终结黑盒猜谜。
"""

import os
import re
from pathlib import Path
from typing import List, Dict, Any, Optional
from .utils import run_cmd, log_warn, log_info

# 默认内置推荐白名单规则 (仅在项目未显式创建 .gitshadow 时作为优雅兜底)
DEFAULT_GITSHADOW_PATTERNS = [
    ".env*",
    "*.secret",
    "*.key",
    "*.pem",
    "config.local.*",
    "_dev_log/",
    "*.local.md",
]

def glob_to_regex(pattern: str) -> re.Pattern:
    """将通配符规则转换为合规的正向正则"""
    p = pattern.strip().replace("\\", "/")
    # 目录通配 (如 _dev_log/ 或 _dev_log)
    if p.endswith("/"):
        base = p.rstrip("/")
        reg_str = f"^{re.escape(base)}(/.*)?$"
    else:
        # 文件通配 (如 .env* 或 *.secret)
        reg_parts = []
        for segment in p.split("*"):
            reg_parts.append(re.escape(segment))
        reg_str = "^" + ".*".join(reg_parts) + "$"
    return re.compile(reg_str)

class RepoState:
    def __init__(self, root_dir: str):
        self.root_dir = os.path.abspath(root_dir)
        self.is_git = False
        self.remote_url = ""
        self.repo_name = os.path.basename(self.root_dir.rstrip(os.sep)) or "project"
        self.branch = "main"
        self.commit = ""
        self.is_dirty = False
        self.ignored_files: List[str] = []
        self.untracked_files: List[str] = []
        self.shadow_files: List[str] = []
        self.wip_patch: str = ""
        self.has_custom_gitshadow = False
        self._load_git_info()

    def _load_git_info(self):
        # 1. 检查是否为 git 仓库
        code, stdout, _ = run_cmd(["git", "rev-parse", "--is-inside-work-tree"], cwd=self.root_dir, check=False)
        if code != 0 or stdout.strip() != "true":
            self.is_git = False
            return
        self.is_git = True

        # 2. 根目录绝对路径校准
        _, toplevel, _ = run_cmd(["git", "rev-parse", "--show-toplevel"], cwd=self.root_dir)
        self.root_dir = toplevel.strip()

        # 3. 获取 remote origin url
        code, remote, _ = run_cmd(["git", "config", "--get", "remote.origin.url"], cwd=self.root_dir, check=False)
        self.remote_url = remote.strip()

        if self.remote_url:
            base = os.path.basename(self.remote_url.rstrip("/"))
            if base.endswith(".git"):
                base = base[:-4]
            self.repo_name = base
        else:
            self.repo_name = os.path.basename(self.root_dir)

        # 4. 获取当前分支与 commit
        _, branch, _ = run_cmd(["git", "branch", "--show-current"], cwd=self.root_dir, check=False)
        self.branch = branch.strip() or "main"

        _, commit, _ = run_cmd(["git", "rev-parse", "HEAD"], cwd=self.root_dir, check=False)
        self.commit = commit.strip()

        # 5. 检查工作区是否有未提交修改
        code, status_out, _ = run_cmd(["git", "status", "--porcelain"], cwd=self.root_dir, check=False)
        self.is_dirty = bool(status_out.strip())

    def scan_shadow_files(self, include_untracked: bool = True) -> List[str]:
        """依据 .gitshadow 契约扫描所有符合条件的影子文件"""
        # Git projects use Git's ignored/untracked inventory. A plain folder
        # has no Git index, so walk only the explicit Shadow patterns instead.
        if self.is_git:
            code, ignored_out, _ = run_cmd(
                ["git", "ls-files", "-o", "-i", "--exclude-standard"],
                cwd=self.root_dir,
                check=False
            )
            raw_ignored = [f.strip() for f in ignored_out.splitlines() if f.strip()]
            raw_untracked = []
            if include_untracked:
                code, untracked_out, _ = run_cmd(
                    ["git", "ls-files", "-o", "--exclude-standard"],
                    cwd=self.root_dir,
                    check=False
                )
                raw_untracked = [f.strip() for f in untracked_out.splitlines() if f.strip()]
        else:
            raw_ignored = []
            raw_untracked = []
            skip_dirs = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".next"}
            for current_root, directories, filenames in os.walk(self.root_dir):
                directories[:] = [name for name in directories if name not in skip_dirs]
                for filename in filenames:
                    full_path = os.path.join(current_root, filename)
                    relative = os.path.relpath(full_path, self.root_dir)
                    raw_untracked.append(relative.replace(os.sep, "/"))

        all_candidates = list(dict.fromkeys(raw_ignored + raw_untracked))

        # 2. 读取项目根目录的 .gitshadow 契约规则
        shadow_regexes = [glob_to_regex(pattern) for pattern in self.shadow_patterns()]

        # 3. 读取可选的 .shadowignore 规则
        ignore_regexes = []
        shadowignore_path = os.path.join(self.root_dir, ".shadowignore")
        if os.path.isfile(shadowignore_path):
            try:
                with open(shadowignore_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#"):
                            ignore_regexes.append(glob_to_regex(line))
            except Exception:
                pass

        # 4. 白名单收敛筛选有效影子文件
        valid_shadows = []
        for file_rel in all_candidates:
            norm_path = file_rel.replace("\\", "/")
            full_path = os.path.join(self.root_dir, file_rel)
            if not os.path.isfile(full_path):
                continue

            # 必须匹配 .gitshadow 白名单
            matched = False
            for s_reg in shadow_regexes:
                if s_reg.search(norm_path):
                    matched = True
                    break

            if not matched:
                continue

            # 且不得命中 .shadowignore 排除项
            excluded = False
            for i_reg in ignore_regexes:
                if i_reg.search(norm_path):
                    excluded = True
                    break

            if not excluded:
                valid_shadows.append(norm_path)

        self.shadow_files = sorted(valid_shadows)
        return self.shadow_files

    def shadow_patterns(self) -> List[str]:
        """Return the same explicit Shadow glob rules used by the scanner."""
        gitshadow_path = os.path.join(self.root_dir, ".gitshadow")
        if os.path.isfile(gitshadow_path):
            self.has_custom_gitshadow = True
            try:
                with open(gitshadow_path, "r", encoding="utf-8") as stream:
                    return [
                        line.strip()
                        for line in stream
                        if line.strip() and not line.strip().startswith("#")
                    ]
            except (OSError, UnicodeDecodeError) as exc:
                log_warn(f"无法读取 .gitshadow 契约文件 ({exc})，回退至安全默认白名单")
                return list(DEFAULT_GITSHADOW_PATTERNS)
        self.has_custom_gitshadow = False
        return list(DEFAULT_GITSHADOW_PATTERNS)

    def capture_wip_patch(self) -> str:
        """捕获未提交的暂存与未暂存代码修改"""
        if not self.is_git or not self.is_dirty:
            self.wip_patch = ""
            return ""

        code, patch_out, _ = run_cmd(["git", "diff", "HEAD"], cwd=self.root_dir, check=False)
        if code == 0 and patch_out.strip():
            self.wip_patch = patch_out
        else:
            self.wip_patch = ""
        return self.wip_patch

    def get_wip_files(self) -> List[Dict[str, str]]:
        """获取工作区中有变动的代码文件列表及状态"""
        if not self.is_git or not self.is_dirty:
            return []
        code, out, _ = run_cmd(["git", "status", "--porcelain"], cwd=self.root_dir, check=False)
        if code != 0 or not out.strip():
            return []

        results = []
        for line in out.splitlines():
            line = line.rstrip()
            if len(line) < 4:
                continue
            status_code = line[:2]
            filepath = line[3:].strip()
            if filepath.startswith('"') and filepath.endswith('"'):
                filepath = filepath[1:-1]
            if " -> " in filepath:
                filepath = filepath.split(" -> ")[1]

            kind = "modified"
            if "??" in status_code:
                kind = "untracked"
            elif "D" in status_code:
                kind = "deleted"
            elif "A" in status_code:
                kind = "added"

            results.append({"path": filepath.replace("\\", "/"), "kind": kind, "code": status_code})
        return results
