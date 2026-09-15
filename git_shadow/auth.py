"""
git_shadow.auth
跨端 SSH 凭证清洗与增量对齐引擎：
严格遵守 AGENTS.md 宪法与 ADR-2026-09-15 契约：
1. Diff-First 比对先行 (指纹校验，绝不暴力覆写)
2. Scoped Git Auth (严格限制在 Git 范围)
3. Multi-Account Resolution (多账号上下文推断与选择)
4. Author Alignment (伴随对齐 user.name 与 user.email)
"""

import os
import hashlib
from pathlib import Path
from typing import Optional, Tuple, List, Dict
from .utils import (
    Colors,
    log_info,
    log_warn,
    log_success,
    log_error,
    log_step,
    run_cmd
)

class AuthManager:
    def __init__(self, engine, specified_key: Optional[str] = None):
        self.engine = engine
        self.host = engine.remote_host
        self.specified_key = specified_key

    def scan_local_keys(self) -> List[Dict[str, str]]:
        """扫描本地 ~/.ssh 下所有合法的密钥对"""
        user_home = Path.home()
        ssh_dir = user_home / ".ssh"
        if not ssh_dir.is_dir():
            return []

        candidates = []
        for pub_file in sorted(ssh_dir.glob("*.pub")):
            priv_name = pub_file.stem
            priv_file = ssh_dir / priv_name
            if priv_file.is_file():
                # 计算公钥 SHA-256 指纹
                try:
                    with open(pub_file, "r", encoding="utf-8") as f:
                        pub_str = f.read().strip()
                    parts = pub_str.split()
                    key_data = parts[1] if len(parts) >= 2 else pub_str
                    fingerprint = hashlib.sha256(key_data.encode("utf-8")).hexdigest()[:16]
                    candidates.append({
                        "name": priv_name,
                        "priv": str(priv_file),
                        "pub": str(pub_file),
                        "fingerprint": f"SHA256:{fingerprint}"
                    })
                except Exception:
                    pass

        return candidates

    def resolve_target_key(self, candidates: List[Dict[str, str]]) -> Optional[Dict[str, str]]:
        """根据显式指定、当前仓库上下文或多密钥交互挑选目标密钥"""
        if not candidates:
            return None

        # 1. 显式参数优先
        if self.specified_key:
            for c in candidates:
                if c["name"] == self.specified_key:
                    return c
            log_warn(f"指定的密钥 [{self.specified_key}] 未在本地 ~/.ssh 中找到！")

        # 2. 单密钥无歧义自动选择
        if len(candidates) == 1:
            return candidates[0]

        # 3. 优先匹配 ed25519
        for c in candidates:
            if c["name"] == "id_ed25519":
                return c

        # 4. 兜底返回第一个
        return candidates[0]

    def get_local_git_identity(self) -> Tuple[str, str]:
        """获取本地当前仓库或全局的 Git 提交人身份"""
        _, name_out, _ = run_cmd(["git", "config", "user.name"], cwd=self.engine.repo.root_dir, check=False)
        _, email_out, _ = run_cmd(["git", "config", "user.email"], cwd=self.engine.repo.root_dir, check=False)
        return name_out.strip(), email_out.strip()

    def sync_to_remote(self) -> bool:
        """执行 Diff-First 身份凭证比对与增量安全对齐"""
        print(f"\n{Colors.BOLD}🔐 正在执行 Git 跨端身份治理与增量对齐 [{self.host}]...{Colors.RESET}")

        candidates = self.scan_local_keys()
        if not candidates:
            log_error("本地 ~/.ssh 未发现任何公私钥对！")
            return False

        target_key = self.resolve_target_key(candidates)
        if not target_key:
            log_error("未能定位有效的目标 SSH 密钥！")
            return False

        key_name = target_key["name"]
        local_fp = target_key["fingerprint"]
        log_info(f"本地锁定目标密钥: {Colors.CYAN}{key_name}{Colors.RESET} ({local_fp})")

        with open(target_key["priv"], "r", encoding="utf-8") as f:
            priv_content = f.read()
        with open(target_key["pub"], "r", encoding="utf-8") as f:
            pub_content = f.read()

        # 步骤 1: Diff-First 指纹探测 (绝不盲目覆写)
        log_step(1, 4, f"比对远端密钥指纹 (Diff-First 契约)...")
        probe_key_script = f"""
        if [ -f "$HOME/.ssh/{key_name}.pub" ]; then
            python3 -c "import hashlib; pub=open('$HOME/.ssh/{key_name}.pub').read().strip().split(); key=pub[1] if len(pub)>=2 else ''; print('SHA256:'+hashlib.sha256(key.encode()).hexdigest()[:16])" 2>/dev/null || echo "DIFF"
        else
            echo "NOT_EXIST"
        fi
        """
        r_res = self.engine._run_ssh(probe_key_script.strip(), capture=True, check=False)
        remote_fp = r_res.stdout.strip()

        if remote_fp == local_fp:
            log_success(f"远端密钥指纹匹配一致 ({remote_fp})，{Colors.GREEN}比对通过，跳过重复覆写{Colors.RESET}")
        else:
            if remote_fp == "NOT_EXIST":
                log_info(f"远端未就绪该密钥，执行安全上传并规整 POSIX 权限...")
            else:
                log_info(f"远端存在旧版本指纹 ({remote_fp})，执行安全更新...")

            upload_script = f"""
            set -e
            mkdir -p ~/.ssh
            chmod 700 ~/.ssh

            cat <<'EOF' > ~/.ssh/{key_name}
{priv_content.strip()}
EOF
            cat <<'EOF' > ~/.ssh/{key_name}.pub
{pub_content.strip()}
EOF
            chmod 600 ~/.ssh/{key_name}
            chmod 644 ~/.ssh/{key_name}.pub
            """
            u_res = self.engine._run_ssh(upload_script.strip(), capture=True, check=False)
            if u_res.returncode != 0:
                log_error(f"远端写入密钥失败: {u_res.stderr}")
                return False
            log_success(f"远端密钥 ~/.ssh/{key_name} 写入并规整权限 (600/644) 完成")

        # 步骤 2: 块级安全合并 ~/.ssh/config (Block-Level Merge)
        log_step(2, 4, "块级合并 ~/.ssh/config (专有标记块契约，保护已有配置)...")
        block_tag = "git-shadow: github.com"
        config_block = f"""# === BEGIN {block_tag} ===
Host github.com
    HostName github.com
    User git
    IdentityFile ~/.ssh/{key_name}
    StrictHostKeyChecking accept-new
    ServerAliveInterval 30
# === END {block_tag} ==="""

        merge_script = f"""
        mkdir -p ~/.ssh && touch ~/.ssh/config && chmod 600 ~/.ssh/config

        # 使用 Python 原地安全替换或追加专属 Block
        python3 <<'EOF'
import os

cfg_path = os.path.expanduser("~/.ssh/config")
with open(cfg_path, "r", encoding="utf-8", errors="replace") as f:
    content = f.read()

start_marker = "# === BEGIN {block_tag} ==="
end_marker = "# === END {block_tag} ==="

new_block = \"\"\"{config_block}\"\"\"

if start_marker in content and end_marker in content:
    # 替换已有标记块
    parts_before = content.split(start_marker)[0]
    parts_after = content.split(end_marker)[1]
    final_content = parts_before + new_block + parts_after
else:
    # 安全附加在末尾
    final_content = content.rstrip() + "\\n\\n" + new_block + "\\n"

with open(cfg_path, "w", encoding="utf-8") as f:
    f.write(final_content.lstrip("\\n"))
EOF
        chmod 600 ~/.ssh/config
        """
        m_res = self.engine._run_ssh(merge_script.strip(), capture=True, check=False)
        if m_res.returncode != 0:
            log_warn(f"合并 ~/.ssh/config 警告: {m_res.stderr.strip()}")
        else:
            log_success("GitHub 专属路由块已安全增量合并至远端 ~/.ssh/config")

        # 步骤 3: 伴随同步 Git 提交人身份 (Author Alignment)
        log_step(3, 4, "伴随对齐 Git 提交人身份 (Author Alignment)...")
        local_name, local_email = self.get_local_git_identity()
        if local_name and local_email:
            align_git_script = f"""
            git config --global user.name "{local_name}"
            git config --global user.email "{local_email}"
            """
            self.engine._run_ssh(align_git_script.strip(), capture=True, check=False)
            log_success(f"已将提交人身份对齐至远端: {Colors.CYAN}{local_name}{Colors.RESET} <{local_email}>")
        else:
            log_warn("本地未检测到完整的 Git user.name/user.email，跳过作者信息同步。")

        # 步骤 4: 闭环鉴权连通性验证
        log_step(4, 4, "验证远端对 GitHub 的 SSH 鉴权连通性...")
        test_res = self.engine._run_ssh(
            "ssh -T -o StrictHostKeyChecking=accept-new -o ConnectTimeout=5 git@github.com 2>&1",
            capture=True,
            check=False
        )
        output = test_res.stdout.strip()
        if "successfully authenticated" in output:
            hi_line = output
            for line in output.splitlines():
                if "Hi " in line:
                    hi_line = line
                    break
            log_success(f"GitHub 鉴权成功！远端反馈: {Colors.GREEN}{hi_line}{Colors.RESET}")
            print(f"\n{Colors.BOLD}{Colors.GREEN}🎉 恭喜！远端主机 [{self.host}] Git 身份与鉴权体系已全部闭环就绪！{Colors.RESET}")
            return True
        else:
            log_warn(f"GitHub 鉴权反馈: {output}")
            log_warn("提示: 请确认该公钥已添加到 GitHub (Settings -> SSH and GPG keys)")
            return False
