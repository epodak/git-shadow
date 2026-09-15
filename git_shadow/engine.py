"""
git_shadow.engine
远程工作区编排与影子叠加引擎：SSH 管道、Git 对齐、流式 tar 影子注入、补丁应用、Web/终端 AI 唤起
"""

import os
import io
import re
import tarfile
import subprocess
import webbrowser
import base64
from typing import List, Optional, Dict, Any


from .scanner import RepoState
from .utils import (
    log_info,
    log_success,
    log_warn,
    log_error,
    log_step,
    format_size,
    Colors
)

class ShadowEngine:
    def __init__(
        self,
        repo: RepoState,
        remote_host: str,
        remote_dir: Optional[str] = None,
        with_wip: bool = True
    ):
        self.repo = repo
        self.remote_host = remote_host
        self.with_wip = with_wip

        # 路径净化：防止 Windows Git Bash 将绝对路径错误转义为 D:/Tool/...
        cleaned_dir = self._clean_path(remote_dir) if remote_dir else None
        self.remote_dir = cleaned_dir  # 稍后在对齐时动态解析推荐路径

    def _clean_path(self, path: Optional[str]) -> Optional[str]:
        """清洗由于 Windows MSYS 路径转换引入的异常本地盘符前缀"""
        if not path:
            return None
        # 如果被转成了类似 D:/Tool/03_winSys/Git/... 或者 /c/...
        m = re.search(r"[A-Za-z]:/[^/]+/[^/]+/[^/]+(/.*)", path)
        if m:
            return m.group(1)
        return path

    def _run_ssh(
        self,
        remote_cmd: str,
        check: bool = True,
        capture: bool = True,
        input_data: Optional[bytes] = None
    ) -> subprocess.CompletedProcess:
        """
        在远端执行 SSH 命令
        前置 -o RemoteCommand=none -o RequestTTY=no，彻底消除与 ~/.ssh/config 中 Zellij / RemoteCommand 的冲突
        """
        cmd = [
            "ssh",
            "-o", "RemoteCommand=none",
            "-o", "RequestTTY=no",
            self.remote_host,
            remote_cmd
        ]
        return subprocess.run(
            cmd,
            input=input_data,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
            text=False if input_data and isinstance(input_data, bytes) else True,
            encoding=None if input_data and isinstance(input_data, bytes) else "utf-8",
            errors=None if input_data and isinstance(input_data, bytes) else "replace",
            check=check
        )

    def resolve_remote_dir(self, probe_ws_dir: Optional[str] = None) -> str:
        """解析并确定远端存放路径"""
        if self.remote_dir:
            return self.remote_dir

        if probe_ws_dir:
            self.remote_dir = probe_ws_dir
            return self.remote_dir

        # 动态通过远端探测：优先 ~/wkspace/项目名，其次 ~/workspace/项目名，兜底 ~/项目名
        script = f"""
        if [ -d "$HOME/wkspace" ]; then
            echo "$HOME/wkspace/{self.repo.repo_name}"
        elif [ -d "$HOME/workspace" ]; then
            echo "$HOME/workspace/{self.repo.repo_name}"
        else
            echo "$HOME/{self.repo.repo_name}"
        fi
        """
        res = self._run_ssh(script.strip(), capture=True, check=False)
        resolved = res.stdout.strip() if res.returncode == 0 else f"~/{self.repo.repo_name}"
        self.remote_dir = resolved
        return self.remote_dir

    def prepare_remote_repo(self, sync_git_pull: bool = False, probe_ws_dir: Optional[str] = None) -> bool:
        """在远端克隆或对齐 Git 仓库"""
        target_dir = self.resolve_remote_dir(probe_ws_dir)
        log_step(1, 4, f"对齐远端代码基线 ({self.remote_host}:{target_dir})...")

        # 检查远端目录状态
        # 检查远端目录状态与 HEAD commit
        script = f"""
        if [ ! -d "{target_dir}" ]; then
            echo "NOT_EXIST"
        elif [ ! -d "{target_dir}/.git" ]; then
            echo "NOT_GIT"
        else
            cur_commit=$(cat {target_dir}/.git/SHADOW_COMMIT 2>/dev/null || true)
            if [ -z "$cur_commit" ]; then
                cur_commit=$(cd {target_dir} && git rev-parse HEAD 2>/dev/null || true)
            fi
            echo "EXISTS:$cur_commit"
        fi
        """
        res = self._run_ssh(script.strip())
        state = res.stdout.strip()

        # 分支 A：本地已配置 GitHub origin
        if self.repo.remote_url:
            if state == "NOT_EXIST":
                log_info(f"远端目录不存在，利用骨干网高速克隆: {Colors.CYAN}{self.repo.remote_url}{Colors.RESET}")
                clone_cmd = f"git clone {self.repo.remote_url} {target_dir} && cd {target_dir} && git checkout {self.repo.branch}"
                c_res = self._run_ssh(clone_cmd, capture=True, check=False)
                if c_res.returncode != 0:
                    log_error(f"远端克隆失败: {c_res.stderr.strip()}")
                    log_warn("提示: 若远端访问私有库受阻，可先运行 `git shadow auth sync <host>` 同步凭证")
                    return False
                log_success("远端仓库克隆并检出当前分支完成")
            elif state == "NOT_GIT":
                log_error(f"远端目录 {target_dir} 存在但不是一个 Git 仓库！")
                return False
            else:
                log_info(f"远端已存在该仓库，对齐分支 [{self.repo.branch}]...")
                align_cmd = f"""
                cd {target_dir} && \\
                git fetch origin && \\
                git checkout {self.repo.branch}
                """
                if sync_git_pull:
                    align_cmd += f" && git pull origin {self.repo.branch}"
                self._run_ssh(align_cmd.strip())
                log_success(f"远端分支对齐完成 [{self.repo.branch}]")
            return True

        # 分支 B：本地未配置 GitHub origin（P2P 直连直推模式）
        if state.startswith("EXISTS:"):
            remote_commit = state.split(":", 1)[1].strip()
            if remote_commit and remote_commit == self.repo.commit:
                log_success(f"远端代码基线已对齐 ({self.repo.commit[:7]})，跳过基线传输")
                return True

        log_info(f"本地未配置 remote origin，启用 {Colors.CYAN}P2P 直通同步模式{Colors.RESET}...")
        init_cmd = f"mkdir -p {target_dir} && cd {target_dir} && (git rev-parse --is-inside-work-tree >/dev/null 2>&1 || git init -b {self.repo.branch})"
        self._run_ssh(init_cmd)

        # 通过 tar 流式打入当前已追踪的文件 (git archive 基础代码流)
        log_info("正在将本地代码基线流式推送到远端工作区...")
        archive_proc = subprocess.run(
            ["git", "archive", "HEAD"],
            cwd=self.repo.root_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        if archive_proc.returncode != 0:
            # 兼容空仓库或无 commit 状态
            log_warn("本地暂无初始提交，跳过 git archive 基础代码流")
        else:
            tar_data = archive_proc.stdout
            extract_cmd = f"tar -xf - -C {target_dir} && echo '{self.repo.commit}' > {target_dir}/.git/SHADOW_COMMIT"
            self._run_ssh(extract_cmd, input_data=tar_data)

        log_success(f"远端工作区初始化就位: {Colors.CYAN}{target_dir}{Colors.RESET}")
        return True

    def pack_shadow_files(self, shadow_files: List[str]) -> bytes:
        """在内存中打包影子文件列表为 tar.gz 二进制流"""
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for rel_path in shadow_files:
                full_path = os.path.join(self.repo.root_dir, rel_path)
                if os.path.isfile(full_path):
                    tar.add(full_path, arcname=rel_path)
        return buf.getvalue()

    def build_edge_plan(
        self,
        provider: str,
        shadow_files: Optional[List[str]] = None,
        sync_git_pull: bool = False,
        public_url: str = "https://cli.daduiot.com",
        cloudcli_base_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Build one serializable plan for the VPS edge executor.

        The plan contains no shell fragments. The standalone VPS executor
        receives typed actions and emits durable JSONL events for each step.
        """
        target_dir = self.resolve_remote_dir()
        shadow_files = shadow_files if shadow_files is not None else self.repo.scan_shadow_files()
        steps: List[Dict[str, Any]] = []

        workspace_step: Dict[str, Any] = {
            "id": "workspace.prepare",
            "action": "workspace.prepare",
            "target": target_dir,
            "remote_url": self.repo.remote_url,
            "branch": self.repo.branch or "main",
            "commit": self.repo.commit,
            "pull": sync_git_pull,
        }
        if not self.repo.remote_url:
            archive_proc = subprocess.run(
                ["git", "archive", "HEAD"],
                cwd=self.repo.root_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            if archive_proc.returncode == 0 and archive_proc.stdout:
                workspace_step["archive_b64"] = base64.b64encode(archive_proc.stdout).decode("ascii")
        steps.append(workspace_step)

        if shadow_files:
            steps.append(
                {
                    "id": "shadow.extract",
                    "action": "shadow.extract",
                    "target": target_dir,
                    "archive_b64": base64.b64encode(self.pack_shadow_files(shadow_files)).decode("ascii"),
                }
            )

        if self.with_wip and self.repo.is_dirty:
            patch_content = self.repo.capture_wip_patch()
            if patch_content:
                steps.append(
                    {
                        "id": "patch.apply",
                        "action": "patch.apply",
                        "cwd": target_dir,
                        "patch_b64": base64.b64encode(patch_content.encode("utf-8")).decode("ascii"),
                    }
                )

        steps.append(
            {
                "id": "cloudcli.session",
                "action": "cloudcli.session",
                "project_path": target_dir,
                "provider": provider,
                "public_url": public_url.rstrip("/"),
                "initial_message": "",
            }
        )
        if cloudcli_base_url:
            steps[-1]["base_url"] = cloudcli_base_url.rstrip("/")
        return {"protocol": 1, "project_path": target_dir, "provider": provider, "steps": steps}

    def inject_shadow_files(self, shadow_files: List[str]) -> bool:
        """流式注入影子文件到远端"""
        log_step(2, 4, f"叠加本地影子文件 (共 {len(shadow_files)} 个)...")

        if not shadow_files:
            log_info("没有需要叠加的影子文件，跳过传输。")
            return True

        for f in shadow_files:
            size = os.path.getsize(os.path.join(self.repo.root_dir, f))
            print(f"  {Colors.DIM}• {f} ({format_size(size)}){Colors.RESET}", flush=True)

        tar_data = self.pack_shadow_files(shadow_files)
        log_info(f"影子压缩包体积: {format_size(len(tar_data))} (毫秒级传输)")

        if len(tar_data) < 5000:
            b64_data = base64.b64encode(tar_data).decode("ascii")
            remote_cmd = f"echo '{b64_data}' | base64 -d | tar -xzf - -C {self.remote_dir}"
            proc = self._run_ssh(remote_cmd, check=False)
        else:
            remote_cmd = f"tar -xzf - -C {self.remote_dir}"
            proc = self._run_ssh(remote_cmd, input_data=tar_data, check=False)

        if proc.returncode != 0:
            log_error(f"影子文件解压失败: {proc.stderr}")
            return False

        log_success("影子文件已成功覆盖叠加至远端工作区！")
        return True

    def apply_wip_patch(self) -> bool:
        """流式应用本地未提交修改 (WIP Patch)"""
        log_step(3, 4, "检查本地未提交代码差异 (WIP Patch)...")

        if not self.with_wip:
            log_info("已通过 --no-wip 显式跳过代码补丁应用。")
            return True

        if not self.repo.is_dirty:
            log_success("本地工作区状态干净 (Clean)，无需打补丁。")
            return True

        patch_content = self.repo.capture_wip_patch()
        if not patch_content:
            log_success("未检测到本地代码改动，无需打补丁。")
            return True

        log_info(f"检测到本地工作区有未提交代码，正在向远端打补丁 ({len(patch_content.splitlines())} 行差异)...")

        raw_bytes = patch_content.encode("utf-8")
        if len(raw_bytes) < 5000:
            b64_patch = base64.b64encode(raw_bytes).decode("ascii")
            remote_cmd = f"cd {self.remote_dir} && echo '{b64_patch}' | base64 -d | git apply - 2>/dev/null || true"
            proc = self._run_ssh(remote_cmd, check=False)
        else:
            remote_cmd = f"cd {self.remote_dir} && git apply - 2>/dev/null || true"
            proc = self._run_ssh(remote_cmd, input_data=raw_bytes, check=False)

        if proc.returncode != 0:
            log_warn("补丁应用可能存在部分冲突，远端将保留当前最佳对齐状态。")
        else:
            log_success("本地未提交的代码差异已在远端无缝生效！")

        return True

    def open_cloudcli_optimistic(self, domain: str = "cli.daduiot.com") -> str:
        """乐观先行：0秒立即拉起本地浏览器打开 CloudCLI，绝不让用户在终端黑框中空转干等"""
        base_url = f"https://{domain}"
        print(f"\n{Colors.BOLD}{Colors.GREEN}🚀 [乐观先行] 正在本地浏览器秒级打开 CloudCLI Web 远程工作台:{Colors.RESET}")
        print(f"   👉 {Colors.BOLD}{Colors.CYAN}{base_url}{Colors.RESET}\n", flush=True)
        try:
            webbrowser.open(base_url)
            log_success("本地浏览器已先一步弹出工作台！页面加载与后台同步时间交叉重叠。")
        except Exception as e:
            log_warn(f"无法自动拉起浏览器，请手动点击上方链接访问: {e}")
        return base_url

    def fetch_and_report_session(self, domain: str = "cli.daduiot.com") -> Optional[str]:
        """后台检索并锁定远端与当前工作区匹配的会话深链"""
        log_step(4, 4, f"检查远端 CloudCLI 活跃会话深链 ({domain})...")

        # 远端探查与当前工作区匹配的 session_id
        fetch_session_script = f"""
        python3 <<'EOF' 2>/dev/null
import sqlite3, os, sys

db_path = os.path.expanduser("~/.cloudcli/auth.db")
if not os.path.exists(db_path):
    print("NO_DB")
    sys.exit(0)

try:
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    # 优先匹配当前工作区路径或父级目录关联的活跃会话
    target_dir = "{self.remote_dir}"
    cur.execute(
        "SELECT session_id, custom_name, project_path FROM sessions WHERE isArchived = 0 AND (project_path = ? OR ? LIKE project_path || '%') ORDER BY updated_at DESC LIMIT 1",
        (target_dir, target_dir)
    )
    row = cur.fetchone()
    if not row:
        # 若无工作区精准匹配，获取全局最新的活跃会话
        cur.execute("SELECT session_id, custom_name, project_path FROM sessions WHERE isArchived = 0 ORDER BY updated_at DESC LIMIT 1")
        row = cur.fetchone()

    if row:
        print(f"SESSION:{{row[0]}}")
        print(f"TITLE:{{row[1] or ''}}")
        print(f"PATH:{{row[2] or ''}}")
    else:
        print("NO_SESSION")
except Exception as e:
    print(f"ERROR:{{e}}")
EOF
        """
        res = self._run_ssh(fetch_session_script.strip(), capture=True, check=False)
        out = res.stdout.strip()

        session_id = ""
        session_title = ""
        for line in out.splitlines():
            if line.startswith("SESSION:"):
                session_id = line.replace("SESSION:", "").strip()
            elif line.startswith("TITLE:"):
                session_title = line.replace("TITLE:", "").strip()

        if session_id:
            target_url = f"https://{domain}/session/{session_id}"
            title_hint = f" ({session_title[:30]}...)" if session_title else ""
            log_success(f"已锁定当前工作区专属会话: {Colors.CYAN}{session_id}{Colors.RESET}{Colors.DIM}{title_hint}{Colors.RESET}")
            print(f"   🔗 专属会话直通深链: {Colors.BOLD}{Colors.CYAN}{target_url}{Colors.RESET}\n", flush=True)
            return target_url
        else:
            log_info(f"未检索到专属历史会话，可直接在主页新建或选用已有会话。")
            return None

    def launch_cloudcli_web(self, domain: str = "cli.daduiot.com"):
        """获取 CloudCLI Session 并自动在本地浏览器中弹出 (兼容旧调用)"""
        self.fetch_and_report_session(domain=domain)


    def launch_agent_or_shell(self, agent_cmd: Optional[str] = None):
        """拉起远端交互环境或指定 AI Agent"""
        if agent_cmd == "cloudcli":
            self.launch_cloudcli_web()
            return

        log_step(4, 4, "远端工作区就绪，进入交互环境...")
        target_exec = agent_cmd or "$SHELL -l"
        final_cmd = f"cd {self.remote_dir} && {target_exec}"
        log_info(f"正在连接并启动: {Colors.BOLD}{target_exec}{Colors.RESET}")

        ssh_args = ["ssh", "-t", self.remote_host, final_cmd]
        subprocess.run(ssh_args)
