"""
git_shadow.dev_server
像 Vite / Astro Dev Server 一样的前台随行热同步与边缘广播交互引擎。
提供高颜值终端看板、即时 HMR 热更新流、边缘 RPC 广播流式回显与单键交互热键。
"""

from __future__ import annotations

import datetime
import hashlib
import os
import pathlib
import sys
import threading
import time
import webbrowser
from typing import Any, Dict, List, Optional, Tuple

from .binding import ensure_gitshadow_file
from .daemon import LocalDaemonManager
from .edge import EdgeClient
from .engine import ShadowEngine
from .git_sync import auto_fast_forward_pull
from .probe import RemoteProbe
from .remote_bootstrap import RemoteBootstrapManager
from .access_path import AccessPathManager
from .scanner import RepoState
from .shadow_sync import ShadowManifestStore
from .utils import Colors, NO_WINDOW_FLAG, format_size
from .watcher import LocalChangeWatcher
from .workspace_identity import workspace_relative_path




def get_current_time_str() -> str:
    return datetime.datetime.now().strftime("%H:%M:%S")


def log_hmr(tag: str, msg: str, color: str = Colors.CYAN) -> None:
    ts = get_current_time_str()
    print(f"{Colors.DIM}{ts}{Colors.RESET} {Colors.BOLD}{color}[{tag}]{Colors.RESET} {msg}", flush=True)


class ShadowDevServer:
    """
    像 Vite / Astro 一样的前台随行热同步 Dev Server：
    - 启动时秒开 Web UI 浏览器深链
    - 呈现现代化 Dashboard 交互看板
    - 本地文件修改即时触发 CAS 影子推送与补丁打入 (HMR 风格日志)
    - 远端 Git 与 Shadow 变动自动双向拉回本地
    - 支持交互热键: [o] 浏览器打开, [r] 强制推送, [p] 远端拉取, [c] 清屏, [q] 退出
    """

    def __init__(
        self,
        project_root: str,
        remote_host: str,
        remote_dir: Optional[str] = None,
        launch_mode: str = "cloudcli",
        provider: Optional[str] = None,
        with_wip: bool = True,
        auto_open_browser: bool = True,
    ):
        self.repo = RepoState(project_root)
        self.remote_host = remote_host
        self.remote_dir = remote_dir
        self.launch_mode = launch_mode
        self.provider = (provider or os.environ.get("GIT_SHADOW_PROVIDER", "")).strip().lower() or "claude"
        self.with_wip = with_wip
        self.auto_open_browser = auto_open_browser

        self.engine = ShadowEngine(
            repo=self.repo,
            remote_host=self.remote_host,
            remote_dir=self.remote_dir,
            with_wip=self.with_wip,
        )
        self.shadow_store: Optional[ShadowManifestStore] = None
        self.edge_client: Optional[EdgeClient] = None
        self.web_url: Optional[str] = None
        self.web_base_url: Optional[str] = None
        self.web_access_source: Optional[str] = None
        self.web_capability = "unknown"  # unknown | available | unavailable | degraded
        self.web_failure_reason: Optional[str] = None
        self.stop_event = threading.Event()
        self.is_running = False
        self._keyboard_thread: Optional[threading.Thread] = None
        self._last_warned_remote_commit: Optional[str] = None
        self._last_notified_local_commit: Optional[str] = None

    def get_shadow_store(self) -> ShadowManifestStore:
        if self.shadow_store is None:
            target_dir = self.engine.resolve_remote_dir()
            self.shadow_store = ShadowManifestStore(
                self.repo.root_dir,
                remote_scope=self.remote_host + "\n" + target_dir,
            )
        return self.shadow_store

    def get_executor_client(self) -> EdgeClient:
        if self.edge_client is None:
            client = EdgeClient(self.remote_host)
            if not client.ensure_installed():
                detail = client.last_install_error or "SSH 连接超时或权限不足"
                raise RuntimeError(f"远端边缘执行器不可用 ({detail})")
            self.edge_client = client
        return self.edge_client

    def _mark_web_unavailable(self, reason: str, state: str = "unavailable") -> None:
        self.web_capability = state
        self.web_failure_reason = reason
        self.web_url = None

    def _probe_cloudcli(self) -> bool:
        capability = RemoteProbe(self.remote_host).probe_cloudcli(self.engine)
        if capability.get("available"):
            self.web_capability = "available"
            self.web_failure_reason = None
            return True
        self._mark_web_unavailable(str(capability.get("reason") or "CloudCLI unavailable"))
        return False

    def _resolve_web_access(self) -> bool:
        result = AccessPathManager(self.engine).resolve_cloudcli_access()
        if result.get("available"):
            self.web_base_url = str(result.get("url") or "").rstrip("/") or None
            self.web_access_source = str(result.get("source") or "unknown")
            self.web_failure_reason = None
            return bool(self.web_base_url)
        self.web_base_url = None
        self.web_access_source = None
        self._mark_web_unavailable(
            "no-browser-access-path: %s"
            % str(result.get("reason") or "unavailable"),
            state="degraded",
        )
        return False

    def render_dashboard(self, ready_ms: int = 0) -> None:
        """渲染高颜值 Vite / Astro 风格 Dev Server 看板"""
        version = "0.1.0"
        print()
        print(f"  {Colors.BOLD}{Colors.GREEN}GIT-SHADOW{Colors.RESET} {Colors.DIM}v{version}{Colors.RESET}  {Colors.DIM}ready in {ready_ms} ms{Colors.RESET}\n")
        print(f"  {Colors.BOLD}{Colors.GREEN}➜{Colors.RESET}  {Colors.BOLD}Local:{Colors.RESET}     {Colors.CYAN}{self.repo.root_dir}{Colors.RESET}")
        
        target_path = self.engine.remote_dir or (
            "~/wkspace/"
            + workspace_relative_path(
                self.repo.repo_name,
                self.repo.branch if self.repo.is_git else None,
                is_git=self.repo.is_git,
            )
        )
        print(f"  {Colors.BOLD}{Colors.GREEN}➜{Colors.RESET}  {Colors.BOLD}Target:{Colors.RESET}    {Colors.CYAN}{self.remote_host}:{target_path}{Colors.RESET}")
        
        if self.web_url:
            access_tag = f" [{self.web_access_source}]" if self.web_access_source else ""
            print(f"  {Colors.BOLD}{Colors.GREEN}➜{Colors.RESET}  {Colors.BOLD}Web UI:{Colors.RESET}    {Colors.BOLD}{Colors.CYAN}{self.web_url}{Colors.RESET}{Colors.DIM}{access_tag}{Colors.RESET}")
        elif self.web_base_url and self.launch_mode == "cloudcli":
            access_tag = f" [{self.web_access_source}]" if self.web_access_source else ""
            print(f"  {Colors.BOLD}{Colors.GREEN}➜{Colors.RESET}  {Colors.BOLD}Web UI:{Colors.RESET}    {Colors.CYAN}{self.web_base_url}{Colors.RESET}{Colors.DIM}{access_tag}{Colors.RESET}")
        elif self.launch_mode == "cloudcli" and self.web_capability in ("unavailable", "degraded"):
            print(f"  {Colors.BOLD}{Colors.YELLOW}➜{Colors.RESET}  {Colors.BOLD}Web UI:{Colors.RESET}    {Colors.YELLOW}不可用，已降级{Colors.RESET} {Colors.DIM}({self.web_failure_reason or 'unknown'}){Colors.RESET}")
        
        if self.launch_mode == "cloudcli" and self.web_capability in ("unavailable", "degraded"):
            mode_desc = "⚡ Hot Sync (CloudCLI unavailable → sync-only)"
        elif self.launch_mode == "cloudcli":
            mode_desc = "⚡ Hot Sync & Edge Broadcast (随行热更新)"
        else:
            mode_desc = "💻 Terminal Accompanying"
        print(f"  {Colors.BOLD}{Colors.GREEN}➜{Colors.RESET}  {Colors.BOLD}Mode:{Colors.RESET}      {mode_desc}")
        print()
        web_key_enabled = (
            self.launch_mode == "cloudcli"
            and self.web_capability not in ("unavailable", "degraded")
            and bool(self.web_url or self.web_capability == "available")
        )
        web_key = "[o] 浏览器打开 Web  " if web_key_enabled else ""
        print(f"  {Colors.DIM}快捷键: {web_key}[r] 立即同步 (Push)  [p] 远端拉取 (Pull)  [c] 清屏  [q] 退出{Colors.RESET}")
        print(f"  {Colors.DIM}--------------------------------------------------------------------------------{Colors.RESET}\n")

    def _log_edge_event(self, event: dict) -> None:
        """格式化输出边缘 RPC 步骤流式日志"""
        ev = event.get("event")
        evt_type = event.get("type")

        step_desc_map = {
            "cloudcli.session": "正在创建 CloudCLI 远程会话",
            "workspace.prepare": "正在初始化远端工作区与代码基线",
            "shadow.sync": "正在极速同步私有影子文件",
            "shadow.pull": "正在拉取远端影子文件",
            "patch.apply": "正在应用未提交补丁",
        }

        if ev == "session.ready":
            url = event.get("url")
            if url:
                self.web_capability = "available"
                self.web_failure_reason = None
                self.web_url = url
                log_hmr("edge", f"✔ CloudCLI 远程控制台就绪: {Colors.CYAN}{url}{Colors.RESET}", Colors.GREEN)
                if self.auto_open_browser:
                    try:
                        webbrowser.open(url)
                    except Exception:
                        pass
        elif ev == "step.started":
            step = str(event.get("step") or "")
            action = str(event.get("action") or "")
            desc = step_desc_map.get(action) or step_desc_map.get(step) or f"执行步骤: {step}"
            log_hmr("remote", f"ℹ {desc}...", Colors.BLUE)
        elif ev == "step.succeeded":
            step = str(event.get("step") or "")
            action = str(event.get("action") or "")
            desc = step_desc_map.get(action) or step_desc_map.get(step) or f"步骤完成: {step}"
            log_hmr("remote", f"✔ {desc}", Colors.GREEN)
        elif ev == "step.skipped":
            step = str(event.get("step") or "")
            action = str(event.get("action") or "")
            reason = str(event.get("reason") or "条件不满足")
            if action == "cloudcli.session" or step == "cloudcli.session":
                self._mark_web_unavailable(reason, state="degraded")
                log_hmr("web", f"⚠ CloudCLI 会话不可用，已降级为同步模式；文件同步继续，盲目 [o] 重试已禁用 ({reason[:120]})", Colors.YELLOW)
            else:
                log_hmr("remote", f"ℹ 跳过步骤 {action or step} ({reason[:80]})", Colors.YELLOW)
        elif ev == "step.retry":
            step = str(event.get("step") or "")
            attempt = event.get("attempt", 1)
            err = str(event.get("error") or "")
            log_hmr("retry", f"⚠ 步骤 {step} 重试 (第 {attempt} 次): {err[:120]}", Colors.YELLOW)
        elif ev == "job.completed":
            log_hmr("edge", "✔ 边缘同步任务执行完毕", Colors.GREEN)
        elif ev == "job.failed":
            err = str(event.get("error") or "未知错误")
            log_hmr("edge", f"✖ 边缘任务失败: {err}", Colors.RED)
        elif ev == "shadow.remote":
            p = event.get("path", "")
            log_hmr("remote", f"📥 收到远端 Shadow 同步: {p}", Colors.BLUE)
        elif ev == "shadow.conflict":
            p = event.get("path", "")
            log_hmr("conflict", f"⚠ 影子文件 CAS 冲突: {p}", Colors.YELLOW)
        elif evt_type == "accepted":
            job_id = event.get("job_id", "")
            log_hmr("edge", f"✔ 远端接收任务: {job_id}", Colors.GREEN)

    def _on_edge_event(self, event: dict) -> None:
        """用户显式操作（如本地文件修改触发 HMR 推送）时的事件消费与完整日志打印"""
        self.get_shadow_store().consume_event(event)
        self._log_edge_event(event)

    def push_update(self, include_cloudcli: bool = False) -> Tuple[Any, Dict[str, Any], int]:
        t_start = time.monotonic()
        edge_client = self.get_executor_client()

        plan = self.engine.build_edge_plan(
            provider=self.provider,
            shadow_files=self.repo.scan_shadow_files(),
            sync_git_pull=False,
            public_url=self.web_base_url if include_cloudcli else None,
            cloudcli_base_url=None,
            cloudcli_token=os.environ.get("GIT_SHADOW_CLOUDCLI_TOKEN", "").strip() or None,
            include_wip=self.with_wip,
            include_cloudcli=include_cloudcli,
            shadow_store=self.get_shadow_store(),
        )
        plan["job_id"] = edge_client.new_job_id()

        result = edge_client.submit(plan, on_event=self._on_edge_event)
        elapsed_ms = int((time.monotonic() - t_start) * 1000)
        return result, plan, elapsed_ms

    def pull_remote_shadows(self, silent: bool = True) -> int:
        """拉取远端影子文件；silent=True 时仅在真实有文件同步或冲突时输出日志，日常零刷屏"""
        edge_client = self.get_executor_client()
        plan = self.engine.build_shadow_pull_plan(
            shadow_files=self.repo.scan_shadow_files(),
            shadow_store=self.get_shadow_store(),
        )
        plan["job_id"] = edge_client.new_job_id()

        remote_changes = 0

        def on_pull_event(event: dict) -> None:
            nonlocal remote_changes
            self.get_shadow_store().consume_event(event)
            ev = event.get("event")
            if ev == "shadow.remote":
                remote_changes += 1
                p = event.get("path", "")
                log_hmr("remote", f"📥 收到远端 Shadow 同步: {p}", Colors.BLUE)
            elif ev == "shadow.conflict":
                p = event.get("path", "")
                log_hmr("conflict", f"⚠ 影子文件 CAS 冲突: {p}", Colors.YELLOW)
            elif ev == "job.failed":
                err = str(event.get("error") or "未知错误")
                log_hmr("edge", f"✖ 边缘任务失败: {err}", Colors.RED)
            elif not silent:
                self._log_edge_event(event)

        edge_client.submit(plan, on_event=on_pull_event)
        return remote_changes

    def trigger_remote_git_pull(self, silent: bool = True) -> None:
        """通知远端边缘执行器拉取 GitHub 上的最新提交"""
        edge_client = self.get_executor_client()
        plan = self.engine.build_remote_git_pull_plan()
        plan["job_id"] = edge_client.new_job_id()

        def on_git_pull_event(event: dict) -> None:
            ev = event.get("event")
            if ev == "step.succeeded" and event.get("step_id") == "workspace.prepare":
                log_hmr("git", "✔ 远端已成功拉取 GitHub 最新提交", Colors.GREEN)
            elif ev == "job.failed":
                log_hmr("git", f"✖ 远端 Git 拉取失败: {event.get('error')}", Colors.YELLOW)
            elif not silent:
                self._log_edge_event(event)

        edge_client.submit(plan, on_event=on_git_pull_event)

    def sync_git_bidirectional(self, silent: bool = True) -> Dict[str, Any]:
        """安全双向 Git 智能联动与脏工作区避让 (ADR-2026-09-25)"""
        res = auto_fast_forward_pull(self.repo.root_dir, remote="origin", branch=self.repo.branch or "")
        status = res.get("status")

        if status == "updated":
            # 规则 3(a): 远端有新提交，本地工作区干净，已直接快进拉取
            remote_commit = res.get("remote_commit", "")
            log_hmr("git", f"📥 检测到远端新提交 ({remote_commit})，本地工作区干净，已自动拉取同步 (Fast-forward)", Colors.GREEN)
            self._last_warned_remote_commit = None
        elif status == "blocked" and res.get("has_remote_updates"):
            # 规则 3(b): 远端有新提交，但本地存在未提交/未暂存修改，安全延缓拉取并给出提示
            remote_commit = res.get("remote_commit", "")
            if remote_commit != self._last_warned_remote_commit:
                self._last_warned_remote_commit = remote_commit
                log_hmr("git", f"💡 远端 AI 有新提交 ({remote_commit})，但本地存在未提交/暂存的修改，已安全暂缓自动拉取。请本地提交或暂存后再 pull。", Colors.YELLOW)
        elif status == "local_ahead":
            # 规则 2: 本地有新提交，通知远端自动拉取对齐
            local_commit = res.get("local_commit", "")
            if local_commit != self._last_notified_local_commit:
                self._last_notified_local_commit = local_commit
                log_hmr("git", f"🚀 检测到本地新提交 ({local_commit})，正在通知远端自动拉取对齐...", Colors.CYAN)
                try:
                    self.trigger_remote_git_pull(silent=True)
                except Exception as exc:
                    log_hmr("git", f"⚠ 通知远端自动拉取失败: {exc}", Colors.YELLOW)
        elif status == "diverged":
            log_hmr("git", f"⚠ 本地与远端提交历史分叉 (本地 {res.get('local_commit')}, 远端 {res.get('remote_commit')})，请手动处理合并", Colors.RED)
        elif not silent and status == "up-to-date":
            commit_short = res.get("commit") or (self.repo.commit[:7] if self.repo.commit else "")
            log_hmr("git", f"✔ Git 仓库已是最新状态 ({commit_short})", Colors.GREEN)
        elif not silent and status == "blocked":
            log_hmr("git", "✔ Git 仓库与远端保持一致 (本地有未提交修改，无远端新提交)", Colors.GREEN)
        elif not silent and status == "error":
            log_hmr("git", f"✖ Git 同步检查失败: {res.get('message', res.get('reason'))}", Colors.RED)

        return res

    def shadow_snapshot(self) -> Dict[str, str]:
        snapshot = {}
        for relative_path in self.repo.scan_shadow_files():
            p = os.path.join(self.repo.root_dir, relative_path)
            if os.path.isfile(p):
                digest = hashlib.sha256()
                try:
                    with open(p, "rb") as stream:
                        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                            digest.update(chunk)
                    snapshot[relative_path] = digest.hexdigest()
                except OSError:
                    pass
        # 将 .gitshadow 本身纳入快照，若用户修改白名单规则立即感知
        gitshadow_path = os.path.join(self.repo.root_dir, ".gitshadow")
        if os.path.isfile(gitshadow_path):
            digest = hashlib.sha256()
            try:
                with open(gitshadow_path, "rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
                snapshot[".gitshadow"] = digest.hexdigest()
            except OSError:
                pass
        return snapshot


    def open_browser(self) -> None:
        if self.launch_mode != "cloudcli":
            log_hmr("action", "当前模式未启用 Web 远程控制台。", Colors.YELLOW)
            return

        if self.web_url:
            log_hmr("action", f"🌐 正在浏览器中打开: {self.web_url}", Colors.GREEN)
            webbrowser.open(self.web_url)
            return

        # Never retry a Session blindly. A fresh positive capability probe is
        # required before an explicit [o] action is allowed to submit again.
        if not self._probe_cloudcli():
            log_hmr("web", f"⚠ CloudCLI 仍不可用，保持同步模式，不提交无效 Session 请求 ({self.web_failure_reason})", Colors.YELLOW)
            return

        if not self.web_base_url and not self._resolve_web_access():
            log_hmr(
                "web",
                f"⚠ CloudCLI 已运行，但没有可用浏览器接入路径 ({self.web_failure_reason})",
                Colors.YELLOW,
            )
            return

        log_hmr(
            "action",
            f"CloudCLI 能力探针通过，使用 {self.web_access_source} 接入路径创建会话...",
            Colors.CYAN,
        )
        try:
            self.push_update(include_cloudcli=True)
            if not self.web_url and self.web_capability == "available":
                self._mark_web_unavailable("session-not-created", state="degraded")
        except Exception as exc:
            self._mark_web_unavailable(str(exc), state="degraded")
            log_hmr("error", f"创建会话失败，已降级为同步模式: {exc}", Colors.RED)

    def force_push(self) -> None:
        log_hmr("action", "⚡ 正在执行全量强制同步 (Push)...", Colors.CYAN)
        try:
            _, _, ms = self.push_update(include_cloudcli=False)
            log_hmr("shadow", f"✔ 全量同步完成 (耗时: {ms}ms)", Colors.GREEN)
        except Exception as exc:
            log_hmr("error", f"同步失败: {exc}", Colors.RED)

    def force_pull(self) -> None:
        log_hmr("action", "📥 正在检查并拉取远端影子文件与 Git 提交 (Pull)...", Colors.CYAN)
        try:
            changes = self.pull_remote_shadows(silent=False)
            if changes > 0:
                log_hmr("shadow", f"✔ 远端影子文件拉取完成 (已同步 {changes} 个文件)", Colors.GREEN)
            else:
                log_hmr("shadow", "✔ 远端已是最新，无新增影子文件改动", Colors.GREEN)
        except Exception as exc:
            log_hmr("error", f"影子拉取失败: {exc}", Colors.RED)

        try:
            self.sync_git_bidirectional(silent=False)
        except Exception as exc:
            log_hmr("error", f"Git 拉取检查失败: {exc}", Colors.RED)




    def clear_screen(self) -> None:
        os.system("cls" if os.name == "nt" else "clear")
        self.render_dashboard()

    def stop(self) -> None:
        self.stop_event.set()
        self.is_running = False

    def _start_keyboard_listener(self) -> None:
        def loop():
            if sys.platform == "win32":
                try:
                    import msvcrt
                    while not self.stop_event.is_set():
                        if msvcrt.kbhit():
                            ch = msvcrt.getwch().lower()
                            if ch == "o":
                                self.open_browser()
                            elif ch == "r":
                                self.force_push()
                            elif ch == "p":
                                self.force_pull()
                            elif ch == "c":
                                self.clear_screen()
                            elif ch in ("q", "\x03"):  # q or Ctrl+C
                                self.stop()
                                break
                        time.sleep(0.04)
                    return
                except Exception:
                    pass

            # 非 Windows 或降级
            while not self.stop_event.is_set():
                try:
                    line = sys.stdin.readline()
                    if not line:
                        break
                    cmd = line.strip().lower()
                    if cmd == "o":
                        self.open_browser()
                    elif cmd == "r":
                        self.force_push()
                    elif cmd == "p":
                        self.force_pull()
                    elif cmd == "c":
                        self.clear_screen()
                    elif cmd in ("q", "quit", "exit"):
                        self.stop()
                        break
                except Exception:
                    break

        self._keyboard_thread = threading.Thread(target=loop, daemon=True)
        self._keyboard_thread.start()

    def run(self) -> int:
        """运行 Dev Server 主循环"""
        self.is_running = True
        ensure_gitshadow_file(self.repo.root_dir)
        t0 = time.monotonic()

        log_hmr("init", f"正在连接目标主机 {Colors.CYAN}{self.remote_host}{Colors.RESET} 初始化同步与边缘会话...")

        include_cloud = False
        if self.launch_mode == "cloudcli":
            include_cloud = self._probe_cloudcli()
            if not include_cloud:
                bootstrap = RemoteBootstrapManager(self.engine)
                capability = {
                    "available": False,
                    "reason": self.web_failure_reason or "CloudCLI unavailable",
                }
                if bootstrap.can_repair_cloudcli(capability):
                    log_hmr("web", "ℹ CloudCLI 未就绪，正在通过现有 SSH 自动安装/启动...", Colors.CYAN)
                    bootstrap_result = bootstrap.ensure_cloudcli()
                    if bootstrap_result.get("success"):
                        self.web_capability = "available"
                        self.web_failure_reason = None
                        include_cloud = True
                        metadata = bootstrap_result.get("metadata", {})
                        log_hmr(
                            "web",
                            f"✔ CloudCLI 已就绪 (service={metadata.get('service', 'unknown')}, "
                            f"version={metadata.get('cloudcli_version', '')})",
                            Colors.GREEN,
                        )
                    else:
                        self.web_failure_reason = str(
                            bootstrap_result.get("error") or self.web_failure_reason
                        )
                if not include_cloud:
                    log_hmr(
                        "web",
                        f"⚠ CloudCLI 能力不可用，启动降级为 sync-only；"
                        f"Git/Shadow 同步继续 ({self.web_failure_reason})",
                        Colors.YELLOW,
                    )

            if include_cloud and not self._resolve_web_access():
                include_cloud = False
                log_hmr(
                    "web",
                    f"⚠ CloudCLI 服务已就绪，但无可用浏览器接入路径；"
                    f"降级为 sync-only ({self.web_failure_reason})",
                    Colors.YELLOW,
                )
            elif include_cloud:
                log_hmr(
                    "web",
                    f"✔ Web 接入路径: {self.web_base_url} [{self.web_access_source}]",
                    Colors.GREEN,
                )

        try:
            _, plan, ms = self.push_update(include_cloudcli=include_cloud)
            self.engine.remote_dir = plan.get("project_path", self.engine.remote_dir)
            if include_cloud and not self.web_url and self.web_capability == "available":
                self._mark_web_unavailable("cloudcli.session completed without session.ready", state="degraded")
        except Exception as exc:
            log_hmr("error", f"初始化连接失败: {exc}", Colors.RED)
            return 1

        ready_ms = int((time.monotonic() - t0) * 1000)
        self.render_dashboard(ready_ms=ready_ms)

        # 启动非阻塞按键监听
        self._start_keyboard_listener()

        # 进入热更新监听主循环
        previous = self.shadow_snapshot()
        last_pull = time.monotonic()
        last_heartbeat = time.monotonic()
        shadow_pull_interval = 30.0
        daemon_mgr = LocalDaemonManager(self.repo.root_dir)

        with LocalChangeWatcher(self.repo.root_dir) as watcher:
            try:
                while not self.stop_event.is_set():
                    notified = watcher.wait_for_quiet(0.4) if watcher.native else watcher.wait(0.4)
                    if self.stop_event.is_set():
                        break

                    now = time.monotonic()
                    current = self.shadow_snapshot() if (not watcher.native or notified) else previous

                    # 1. 本地优先 HMR 推送：严格只有 .gitshadow 白名单文件实际发生 SHA-256 变动才触发！
                    if current != previous:
                        changed_files = [p for p in set(current) | set(previous) if current.get(p) != previous.get(p)]
                        desc_items = [p for p in changed_files if p != ".gitshadow"]
                        if not desc_items and ".gitshadow" in changed_files:
                            count_desc = ".gitshadow 契约规则"
                        else:
                            count_desc = f"{len(desc_items)} 个影子文件 ({', '.join(desc_items[:3])}{'...' if len(desc_items) > 3 else ''})"
                        log_hmr("shadow", f"⚡ 检测到 {count_desc} 改动，正在热同步打入远端...", Colors.CYAN)
                        try:
                            _, _, sync_ms = self.push_update(include_cloudcli=False)
                            previous = current
                            last_pull = now
                            log_hmr("shadow", f"✔ 热更新已打入远端 ({sync_ms}ms)", Colors.GREEN)
                        except Exception as exc:
                            log_hmr("shadow", f"✖ 热同步失败 (稍后重试): {exc}", Colors.RED)
                            time.sleep(1.0)
                        continue

                    # 2. 定周期静默探查远端 Shadow 与 Git 双向对齐 (默认 30 秒，完全静默，仅有变动时提示)
                    if now - last_pull >= shadow_pull_interval:
                        try:
                            pre_pull = self.shadow_snapshot()
                            self.pull_remote_shadows(silent=True)
                            post_pull = self.shadow_snapshot()
                            for p, h in post_pull.items():
                                if pre_pull.get(p) != h:
                                    previous[p] = h
                            for p in list(previous.keys()):
                                if p in pre_pull and p not in post_pull:
                                    previous.pop(p, None)
                        except Exception:
                            pass

                        try:
                            self.sync_git_bidirectional(silent=True)
                        except Exception:
                            pass

                        last_pull = now


                    # 3. 本地守护心跳静默保活 (每 60 秒刷新一次，零控制台日志)
                    if now - last_heartbeat >= 60.0:
                        daemon_mgr.touch_heartbeat()
                        last_heartbeat = now


            except KeyboardInterrupt:
                pass


        print(f"\n{Colors.GREEN}✔ git-shadow 随行热同步服务已优雅退出。{Colors.RESET}")
        return 0
