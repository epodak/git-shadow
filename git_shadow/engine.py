"""
git_shadow.engine
远程工作区编排与分层同步引擎：Git 代码基线、.gitshadow CAS 投影、Web/终端 AI 唤起
"""

import os
import re
import shlex
import subprocess
import webbrowser
import base64
from typing import List, Optional, Dict, Any


from .scanner import RepoState
from .shadow_sync import ShadowManifestStore
from .workspace_identity import branch_route, workspace_relative_path
from .utils import (
    log_info,
    log_success,
    log_warn,
    log_error,
    log_step,
    Colors,
    NO_WINDOW_FLAG,
)

class ShadowEngine:
    def __init__(
        self,
        repo: RepoState,
        remote_host: str,
        remote_dir: Optional[str] = None,
        with_wip: bool = False
    ):
        self.repo = repo
        self.remote_host = remote_host
        self.with_wip = with_wip

        # 路径净化：防止 Windows Git Bash 将绝对路径错误转义为 D:/Tool/...
        cleaned_dir = self._clean_path(remote_dir) if remote_dir else None
        self.remote_dir = cleaned_dir  # 稍后在对齐时动态解析推荐路径
        # Explicit -d/--dest is user-owned routing.  Automatic routing may
        # safely migrate the legacy repo-root layout into branch subfolders.
        self._auto_branch_routing = remote_dir is None

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
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "ClearAllForwardings=yes",
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
            check=check,
            creationflags=NO_WINDOW_FLAG,
        )

    def resolve_remote_dir(self, probe_ws_dir: Optional[str] = None) -> str:
        """解析并确定远端存放路径"""
        if self.remote_dir:
            return self.remote_dir

        if probe_ws_dir:
            self.remote_dir = probe_ws_dir
            return self.remote_dir

        # Git workspace identity is repository + branch.  Branch namespaces
        # such as foo/bar intentionally become nested directories:
        # ~/wkspace/<repo>/foo/bar.
        suffix = workspace_relative_path(
            self.repo.repo_name,
            self.repo.branch if self.repo.is_git else None,
            is_git=self.repo.is_git,
        )
        suffix_q = shlex.quote(suffix)
        script = f"""
        suffix={suffix_q}
        if [ -d "$HOME/wkspace" ]; then
            echo "$HOME/wkspace/$suffix"
        elif [ -d "$HOME/workspace" ]; then
            echo "$HOME/workspace/$suffix"
        elif [ -d "$HOME/projects" ]; then
            echo "$HOME/projects/$suffix"
        else
            echo "$HOME/wkspace/$suffix"
        fi
        """
        res = self._run_ssh(script.strip(), capture=True, check=False)
        resolved = res.stdout.strip() if res.returncode == 0 else f"~/wkspace/{suffix}"
        self.remote_dir = resolved
        return self.remote_dir

    def _workspace_route_step(self, target_dir: str) -> Optional[Dict[str, Any]]:
        """Return a typed migration step for the automatic branch layout.

        Old git-shadow versions used ~/wkspace/<repo> as the Git checkout.
        New routing needs ~/wkspace/<repo>/<branch>.  The edge executor owns
        the safe migration so a new branch is never created *inside* a legacy
        Git worktree.
        """
        if not getattr(self, "_auto_branch_routing", False):
            return None
        if not bool(getattr(self.repo, "is_git", False)):
            return None
        branch = str(getattr(self.repo, "branch", "") or "")
        if not branch:
            return None

        route = branch_route(branch)
        normalized = str(target_dir).replace("\\", "/").rstrip("/")
        suffix = "/" + route
        if not normalized.endswith(suffix):
            return None
        repo_root = normalized[: -len(suffix)]
        if not repo_root:
            return None
        return {
            "id": "workspace.route",
            "action": "workspace.route",
            "repo_root": repo_root,
            "target": normalized,
            "branch": branch,
        }

    def prepare_remote_repo(self, sync_git_pull: bool = False, probe_ws_dir: Optional[str] = None) -> bool:
        """在远端克隆或对齐 Git 仓库"""
        target_dir = self.resolve_remote_dir(probe_ws_dir)
        log_step(1, 4, f"对齐远端代码基线 ({self.remote_host}:{target_dir})...")

        # 检查远端目录状态
        # 检查远端目录状态与 HEAD commit
        target_q = shlex.quote(target_dir)
        script = f"""
        target={target_q}
        if [ ! -d "$target" ]; then
            echo "NOT_EXIST"
        elif [ ! -d "$target/.git" ]; then
            echo "NOT_GIT"
        else
            cur_commit=$(cat "$target/.git/SHADOW_COMMIT" 2>/dev/null || true)
            if [ -z "$cur_commit" ]; then
                cur_commit=$(cd "$target" && git rev-parse HEAD 2>/dev/null || true)
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
                clone_cmd = (
                    "git clone --branch %s --single-branch -- %s %s"
                    % (
                        shlex.quote(self.repo.branch),
                        shlex.quote(self.repo.remote_url),
                        shlex.quote(target_dir),
                    )
                )
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
                branch_q = shlex.quote(self.repo.branch)
                refspec_q = shlex.quote(
                    "+refs/heads/%s:refs/remotes/origin/%s"
                    % (self.repo.branch, self.repo.branch)
                )
                align_cmd = (
                    "cd %s && git fetch origin %s && git checkout %s"
                    % (target_q, refspec_q, branch_q)
                )
                if sync_git_pull:
                    align_cmd += " && git pull origin %s" % branch_q
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
        init_cmd = (
            "mkdir -p %s && cd %s && "
            "(git rev-parse --is-inside-work-tree >/dev/null 2>&1 || git init -b %s)"
            % (target_q, target_q, shlex.quote(self.repo.branch))
        )
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
            extract_cmd = (
                "tar -xf - -C %s && printf '%%s\\n' %s > %s/.git/SHADOW_COMMIT"
                % (target_q, shlex.quote(self.repo.commit), target_q)
            )
            self._run_ssh(extract_cmd, input_data=tar_data)

        log_success(f"远端工作区初始化就位: {Colors.CYAN}{target_dir}{Colors.RESET}")
        return True

    def build_edge_plan(
        self,
        provider: Optional[str] = None,
        shadow_files: Optional[List[str]] = None,
        sync_git_pull: bool = False,
        public_url: str = "https://cli.daduiot.com",
        cloudcli_base_url: Optional[str] = None,
        cloudcli_token: Optional[str] = None,
        include_wip: bool = False,
        include_cloudcli: bool = True,
        shadow_store: Optional[ShadowManifestStore] = None,
    ) -> Dict[str, Any]:
        """Build one serializable plan for the VPS edge executor.

        The plan contains no shell fragments. The standalone VPS executor
        receives typed actions and emits durable JSONL events for each step.
        """
        target_dir = self.resolve_remote_dir()
        shadow_files = shadow_files if shadow_files is not None else self.repo.scan_shadow_files()
        steps: List[Dict[str, Any]] = []

        route_step = self._workspace_route_step(target_dir)
        if route_step:
            steps.append(route_step)

        # Create the project directory first so CloudCLI can create a Session
        # against it before the potentially slow Git clone starts.
        steps.append(
            {
                "id": "workspace.create",
                "action": "workspace.create",
                "target": target_dir,
            }
        )

        workspace_step: Dict[str, Any] = {
            "id": "workspace.prepare",
            "action": "workspace.prepare",
            "target": target_dir,
            "remote_url": self.repo.remote_url,
            "branch": self.repo.branch or "main",
            "commit": self.repo.commit,
            "pull": sync_git_pull,
            "git_enabled": bool(getattr(self.repo, "is_git", True) or self.repo.remote_url),
            "retries": 2,
        }
        if not self.repo.remote_url and getattr(self.repo, "is_git", True):
            archive_proc = subprocess.run(
                ["git", "archive", "HEAD"],
                cwd=self.repo.root_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            if archive_proc.returncode == 0 and archive_proc.stdout:
                workspace_step["archive_b64"] = base64.b64encode(archive_proc.stdout).decode("ascii")
        if include_cloudcli:
            resolved_provider = (provider or os.environ.get("GIT_SHADOW_PROVIDER", "")).strip().lower() or "claude"
            session_step: Dict[str, Any] = {
                "id": "cloudcli.session",
                "action": "cloudcli.session",
                "project_path": target_dir,
                "provider": resolved_provider,
                "public_url": public_url.rstrip("/"),
                "initial_message": "",
                "allow_failure": True,
            }
            if cloudcli_base_url:
                session_step["base_url"] = cloudcli_base_url.rstrip("/")
            if cloudcli_token:
                # Sent only inside the encrypted SSH plan; never emitted as
                # an event or written into the projected workspace.
                session_step["token"] = cloudcli_token
            steps.append(session_step)

        # CloudCLI is now live; clone/init continues as a later edge step.
        steps.append(workspace_step)

        if shadow_files:
            manifest = shadow_store or ShadowManifestStore(self.repo.root_dir)
            steps.append(
                {
                    "id": "shadow.sync",
                    "action": "shadow.sync",
                    "target": target_dir,
                    "entries": manifest.build_entries(shadow_files),
                }
            )

        # A tracked-file patch is deliberately opt-in.  The normal code lane
        # is Git commit/fetch/pull; this step exists only for explicit WIP use.
        if include_wip and self.repo.is_dirty:
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

        return {"protocol": 1, "project_path": target_dir, "provider": provider, "steps": steps}

    def build_shadow_pull_plan(
        self,
        shadow_files: Optional[List[str]] = None,
        shadow_store: Optional[ShadowManifestStore] = None,
    ) -> Dict[str, Any]:
        """Build a remote-to-local Shadow inspection task.

        The VPS never writes local files. It emits remote changes over the
        live SSH stream, and the local manifest store decides whether to apply
        or preserve them as conflicts.
        """
        target_dir = self.resolve_remote_dir()
        files = shadow_files if shadow_files is not None else self.repo.scan_shadow_files()
        manifest = shadow_store or ShadowManifestStore(self.repo.root_dir)
        patterns_getter = getattr(self.repo, "shadow_patterns", None)
        patterns = patterns_getter() if callable(patterns_getter) else []
        steps: List[Dict[str, Any]] = []
        route_step = self._workspace_route_step(target_dir)
        if route_step:
            steps.append(route_step)
        steps.extend(
            [
                {
                    "id": "workspace.create",
                    "action": "workspace.create",
                    "target": target_dir,
                },
                {
                    "id": "shadow.pull",
                    "action": "shadow.pull",
                    "target": target_dir,
                    "entries": manifest.build_pull_entries(files),
                    "patterns": patterns,
                },
            ]
        )
        return {
            "protocol": 1,
            "project_path": target_dir,
            "steps": steps,
        }

    def build_remote_git_pull_plan(self) -> Dict[str, Any]:
        """构建通知远端边缘执行器拉取 GitHub 最新 Git 提交的任务 Plan"""
        target_dir = self.resolve_remote_dir()
        steps: List[Dict[str, Any]] = []
        route_step = self._workspace_route_step(target_dir)
        if route_step:
            steps.append(route_step)
        steps.extend(
            [
                {
                    "id": "workspace.create",
                    "action": "workspace.create",
                    "target": target_dir,
                },
                {
                    "id": "workspace.prepare",
                    "action": "workspace.prepare",
                    "target": target_dir,
                    "remote_url": self.repo.remote_url,
                    "branch": self.repo.branch or "main",
                    "commit": self.repo.commit,
                    "pull": True,
                    "git_enabled": True,
                    "retries": 1,
                },
            ]
        )
        return {
            "protocol": 1,
            "project_path": target_dir,
            "steps": steps,
        }

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
    # Branch-scoped workspace identity requires an exact project path.
    # A parent project (for example ~/wkspace/AI) must never hijack the
    # session for ~/wkspace/AI/foo/bar.
    target_dir = "{self.remote_dir}"
    cur.execute(
        "SELECT session_id, custom_name, project_path FROM sessions WHERE isArchived = 0 AND project_path = ? ORDER BY updated_at DESC LIMIT 1",
        (target_dir,)
    )
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
        final_cmd = "cd %s && %s" % (shlex.quote(str(self.remote_dir)), target_exec)
        log_info(f"正在连接并启动: {Colors.BOLD}{target_exec}{Colors.RESET}")

        ssh_args = ["ssh", "-t", self.remote_host, final_cmd]
        subprocess.run(ssh_args)
