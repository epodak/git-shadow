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
from typing import Dict, List, Optional, Tuple

from .edge import EdgeClient
from .engine import ShadowEngine
from .git_sync import auto_fast_forward_pull
from .scanner import RepoState
from .shadow_sync import ShadowManifestStore
from .utils import Colors, NO_WINDOW_FLAG, format_size
from .watcher import LocalChangeWatcher


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
        self.stop_event = threading.Event()
        self.is_running = False
        self._keyboard_thread: Optional[threading.Thread] = None

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
                raise RuntimeError("VPS 边缘执行器不可用")
            self.edge_client = client
        return self.edge_client

    def render_dashboard(self, ready_ms: int = 0) -> None:
        """渲染高颜值 Vite / Astro 风格 Dev Server 看板"""
        version = "0.1.0"
        print()
        print(f"  {Colors.BOLD}{Colors.GREEN}GIT-SHADOW{Colors.RESET} {Colors.DIM}v{version}{Colors.RESET}  {Colors.DIM}ready in {ready_ms} ms{Colors.RESET}\n")
        print(f"  {Colors.BOLD}{Colors.GREEN}➜{Colors.RESET}  {Colors.BOLD}Local:{Colors.RESET}     {Colors.CYAN}{self.repo.root_dir}{Colors.RESET}")
        
        target_path = self.engine.remote_dir or f"~/wkspace/{self.repo.repo_name}"
        print(f"  {Colors.BOLD}{Colors.GREEN}➜{Colors.RESET}  {Colors.BOLD}Target:{Colors.RESET}    {Colors.CYAN}{self.remote_host}:{target_path}{Colors.RESET}")
        
        if self.web_url:
            print(f"  {Colors.BOLD}{Colors.GREEN}➜{Colors.RESET}  {Colors.BOLD}Web UI:{Colors.RESET}    {Colors.BOLD}{Colors.CYAN}{self.web_url}{Colors.RESET}")
        
        mode_desc = "⚡ Hot Sync & Edge Broadcast (随行热更新)" if self.launch_mode == "cloudcli" else "💻 Terminal Accompanying"
        print(f"  {Colors.BOLD}{Colors.GREEN}➜{Colors.RESET}  {Colors.BOLD}Mode:{Colors.RESET}      {mode_desc}")
        print()
        print(f"  {Colors.DIM}快捷键: [o] 浏览器打开 Web  [r] 立即同步 (Push)  [p] 远端拉取 (Pull)  [c] 清屏  [q] 退出{Colors.RESET}")
        print(f"  {Colors.DIM}--------------------------------------------------------------------------------{Colors.RESET}\n")

    def _on_edge_event(self, event: dict) -> None:
        """将边缘 RPC 事件映射为热更新时间戳日志"""
        self.get_shadow_store().consume_event(event)

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

    def push_update(self, include_cloudcli: bool = False) -> Tuple[Any, Dict[str, Any]]:
        t_start = time.monotonic()
        edge_client = self.get_executor_client()

        plan = self.engine.build_edge_plan(
            provider=self.provider,
            shadow_files=self.repo.scan_shadow_files(),
            sync_git_pull=False,
            public_url=os.environ.get("GIT_SHADOW_CLOUDCLI_PUBLIC_URL", "https://cli.daduiot.com"),
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

    def pull_remote_shadows(self) -> None:
        edge_client = self.get_executor_client()
        plan = self.engine.build_shadow_pull_plan(
            shadow_files=self.repo.scan_shadow_files(),
            shadow_store=self.get_shadow_store(),
        )
        plan["job_id"] = edge_client.new_job_id()
        edge_client.submit(plan, on_event=self._on_edge_event)

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
        return snapshot

    def open_browser(self) -> None:
        if self.web_url:
            log_hmr("action", f"🌐 正在浏览器中打开: {self.web_url}", Colors.GREEN)
            webbrowser.open(self.web_url)
        else:
            log_hmr("action", "正在重新请求 Web 远程控制台会话...", Colors.CYAN)
            try:
                self.push_update(include_cloudcli=True)
            except Exception as exc:
                log_hmr("error", f"创建会话失败: {exc}", Colors.RED)

    def force_push(self) -> None:
        log_hmr("action", "⚡ 正在执行全量强制同步 (Push)...", Colors.CYAN)
        try:
            _, _, ms = self.push_update(include_cloudcli=False)
            log_hmr("shadow", f"✔ 全量同步完成 (耗时: {ms}ms)", Colors.GREEN)
        except Exception as exc:
            log_hmr("error", f"同步失败: {exc}", Colors.RED)

    def force_pull(self) -> None:
        log_hmr("action", "📥 正在检查并拉取远端变更 (Pull)...", Colors.CYAN)
        try:
            self.pull_remote_shadows()
            if self.repo.is_git:
                res = auto_fast_forward_pull(self.repo.root_dir, branch=self.repo.branch)
                if res["status"] == "updated":
                    log_hmr("remote", "✔ Git 远端提交已自动拉回本地", Colors.GREEN)
                elif res["status"] == "up-to-date":
                    log_hmr("remote", "✔ 远端已是最新，无新增改动", Colors.GREEN)
                elif res["status"] == "blocked":
                    log_hmr("remote", "⚠ 本地有未提交修改，Git 自动拉取跳过", Colors.YELLOW)
        except Exception as exc:
            log_hmr("error", f"拉取失败: {exc}", Colors.RED)

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
        t0 = time.monotonic()

        log_hmr("init", f"正在连接目标主机 {Colors.CYAN}{self.remote_host}{Colors.RESET} 初始化同步与边缘会话...")

        include_cloud = (self.launch_mode == "cloudcli")
        try:
            _, plan, ms = self.push_update(include_cloudcli=include_cloud)
            self.engine.remote_dir = plan.get("project_path", self.engine.remote_dir)
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
        last_git_pull = time.monotonic()
        shadow_pull_interval = 8.0
        git_pull_interval = 10.0

        with LocalChangeWatcher(self.repo.root_dir) as watcher:
            try:
                while not self.stop_event.is_set():
                    notified = watcher.wait_for_quiet(0.4) if watcher.native else watcher.wait(0.4)
                    if self.stop_event.is_set():
                        break

                    now = time.monotonic()
                    current = self.shadow_snapshot() if (not watcher.native or notified) else previous

                    # 1. 本地优先 HMR 推送
                    if current != previous or (watcher.native and notified):
                        changed_files = [p for p in current if current.get(p) != previous.get(p)]
                        count_desc = f"{len(changed_files)} 个影子文件" if changed_files else "本地工作区改动"
                        log_hmr("shadow", f"⚡ 检测到 {count_desc}，正在热同步打入远端...", Colors.CYAN)
                        try:
                            _, _, sync_ms = self.push_update(include_cloudcli=False)
                            previous = current
                            last_pull = now
                            log_hmr("shadow", f"✔ 热更新已打入远端 ({sync_ms}ms)", Colors.GREEN)
                        except Exception as exc:
                            log_hmr("shadow", f"✖ 热同步失败 (稍后重试): {exc}", Colors.RED)
                            time.sleep(1.0)
                        continue

                    # 2. 定周期拉取远端 Shadow
                    if now - last_pull >= shadow_pull_interval:
                        try:
                            pre_pull = self.shadow_snapshot()
                            self.pull_remote_shadows()
                            post_pull = self.shadow_snapshot()
                            for p, h in post_pull.items():
                                if pre_pull.get(p) != h:
                                    previous[p] = h
                            for p in list(previous.keys()):
                                if p in pre_pull and p not in post_pull:
                                    previous.pop(p, None)
                            last_pull = now
                        except Exception as exc:
                            last_pull = now

                    # 3. 定周期拉取远端 Git
                    if self.repo.is_git and now - last_git_pull >= git_pull_interval:
                        result = auto_fast_forward_pull(self.repo.root_dir, branch=self.repo.branch)
                        last_git_pull = now
                        if result["status"] == "updated":
                            self.repo._load_git_info()
                            log_hmr("remote", "📥 远端新提交已自动 fast-forward 合入本地", Colors.GREEN)

            except KeyboardInterrupt:
                pass

        print(f"\n{Colors.GREEN}✔ git-shadow 随行热同步服务已优雅退出。{Colors.RESET}")
        return 0
