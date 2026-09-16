"""
git_shadow.cli
CLI 命令行交互主入口：支持探针诊断、SSH凭证同步、CloudCLI Web弹窗与AI Agent智能选择
"""

import os
import sys
import argparse
import json
import hashlib
import threading
import time
import pathlib
from typing import List, Optional

from . import __version__
from .scanner import RepoState
from .engine import ShadowEngine
from .edge import EdgeClient
from .service import ServiceClient
from .shadow_sync import ShadowManifestStore
from .watcher import LocalChangeWatcher
from .install import install_wrappers
from .probe import RemoteProbe
from .auth import AuthManager
from .tree import DiffTreeRenderer
from .git_sync import auto_fast_forward_pull
from .daemon import LocalDaemonManager
from .utils import (
    log_info,
    log_success,
    log_warn,
    log_error,
    format_size,
    run_cmd,
    Colors
)

BANNER = fr"""{Colors.BOLD}{Colors.CYAN}
   ____ _ _         ____  _               _
  / ___(_) |_      / ___|| |__   __ _  __| | _____      __
 | |  _| | __| ____\___ \| '_ \ / _` |/ _` |/ _ \ \ /\ / /
 | |_| | | |_ |_____|__) | | | | (_| | (_| | (_) \ V  V /
  \____|_|\__|     |____/|_| |_|\__,_|\__,_|\___/ \_/\_/
{Colors.RESET}{Colors.DIM}  Git clones the repo. Shadow overlays the secrets. (v{__version__}){Colors.RESET}
"""

def print_help():
    print(BANNER)
    print(f"""{Colors.BOLD}用法 (Usage):{Colors.RESET}
  git shadow <command> [options] [host] [args...]
  git-shadow <command> [options] [host] [args...]

{Colors.BOLD}核心命令 (Commands):{Colors.RESET}
  {Colors.GREEN}run <host> [agent]{Colors.RESET}  投影并唤起 AI Agent (可指定 cloudcli / opencode / commandcode，不指定则智能挑选)
                    • 指定 cloudcli 时，0秒乐观拉起 Web 浏览器，后台并发流式同步代码与影子
                    • 指定终端 Agent 时，极速秒级直通交互终端
  {Colors.GREEN}probe <host>{Colors.RESET}        诊断探针：感知远端系统、用户主目录、工作区目录树与 AI 工具状态
  {Colors.GREEN}auth sync <host>{Colors.RESET}    一键净化同步本地 SSH/GitHub 鉴权密钥到远端 Linux，打通 Git 权限
  {Colors.GREEN}up <host>{Colors.RESET}           一键投影当前工作区到远端，并进入交互式终端
  {Colors.GREEN}push <host>{Colors.RESET}         通过边缘任务对齐 Git 基线并执行 .gitshadow CAS，不进入终端
  {Colors.GREEN}diff{Colors.RESET}                本地自检：以高保真树状图 (Diff Tree) 预览待投影的影子文件与代码改动
  {Colors.GREEN}pull <host>{Colors.RESET}         从远端对齐拉取最新 Git 代码到本地
  {Colors.GREEN}pull <host> --with-shadows{Colors.RESET} 同时拉取已登记的远端 .gitshadow 变化
  {Colors.GREEN}edge install <host>{Colors.RESET} 安装/更新 VPS 端 JSONL 边缘执行器
  {Colors.GREEN}edge status <host> <job>{Colors.RESET} 查询远端任务状态
  {Colors.GREEN}edge resume <host> <job>{Colors.RESET} 断线后按事件序号恢复任务输出
  {Colors.GREEN}install [dir]{Colors.RESET}     安装本地 git-shadow / git-shadow.cmd wrapper（默认 ~/.local/bin）
  {Colors.GREEN}daemon start <host>{Colors.RESET}   启动当前项目本地后台守护进程 (Session 1 无黑框，脱离终端)
  {Colors.GREEN}daemon stop{Colors.RESET}           停止当前项目本地后台守护进程 (快捷别名: git shadow stop)
  {Colors.GREEN}daemon status{Colors.RESET}         查看本地后台守护进程状态与最新日志 (快捷别名: git shadow status)
  {Colors.GREEN}watch <host>{Colors.RESET}          按需开启本地后台同步 (默认后台运行；加 -f 在前台运行)
  {Colors.GREEN}service <host> load|status|unload{Colors.RESET} 管理项目级 VPS 常驻边缘服务
  {Colors.GREEN}run/push/pull/up ... --service{Colors.RESET} 通过常驻服务提交任务，断开后可恢复
  {Colors.GREEN}run <host> [agent] --watch{Colors.RESET} 持续双向同步 .gitshadow，并对干净 Git 分支自动 ff-only 拉取

{Colors.BOLD}选项 (Options):{Colors.RESET}
  {Colors.YELLOW}-d, --dest <dir>{Colors.RESET}    自定义远端存放目录 (默认自动感知: ~/wkspace/项目名 或 ~/workspace/项目名)
  {Colors.YELLOW}--wip{Colors.RESET}               显式把未提交的 Git 修改作为一次性补丁投影；默认不传输
  {Colors.YELLOW}--watch{Colors.RESET}             保持本地进程运行，静默监听 .gitshadow 变化并提交 CAS 任务
  {Colors.YELLOW}--git-pull-interval <sec>{Colors.RESET}  --watch 时定期对干净分支执行 Git fetch + ff-only pull（默认 10 秒）
  {Colors.YELLOW}--no-git-pull{Colors.RESET}        --watch 时关闭 Git 自动拉取
  {Colors.YELLOW}--service{Colors.RESET}           使用 VPS 项目级常驻服务，而非每次新建边缘进程
  {Colors.YELLOW}--pull{Colors.RESET}              远端分支对齐时，强制拉取远端 origin 最新提交
  {Colors.YELLOW}--with-shadows{Colors.RESET}      pull 时额外执行 Shadow 双向 CAS 检查
  {Colors.YELLOW}-a, --agent <cmd>{Colors.RESET}   指定在远端运行的 AI 命令
  {Colors.YELLOW}--provider <name>{Colors.RESET}   CloudCLI 供应商 (codex/claude/cursor/opencode)
  {Colors.YELLOW}--cloudcli-url <url>{Colors.RESET} VPS 内部 CloudCLI 地址 (默认 http://127.0.0.1:3001)
  {Colors.YELLOW}-v, --version{Colors.RESET}       输出当前版本号
  {Colors.YELLOW}-h, --help{Colors.RESET}          查看帮助信息

{Colors.BOLD}使用示例 (Examples):{Colors.RESET}
  git shadow run aws cloudcli         # 0秒秒开浏览器远程控制台，后台流式对齐代码与影子
  git shadow run aws opencode         # 极速直通并在远端终端拉起 OpenCode
  git shadow run aws commandcode      # 极速直通并在远端终端拉起 CommandCode
  git shadow run aws                  # 自动探测远端已就绪的 AI Agent，列出数字菜单让你挑选
  git shadow auth sync aws            # 一键打通远端 VPS 的 GitHub SSH 权限
  git shadow probe aws                # 诊断远端主机系统与环境
  git shadow diff                     # 查看本地有哪些 .env / 密钥会被影子带走 (树状图呈现)
  git shadow run aws cloudcli --provider codex  # 远端批量执行并打开专属会话
""")


SUPPORTED_PROVIDERS = ("codex", "claude", "cursor", "opencode")


def choose_provider(requested: Optional[str]) -> str:
    """Choose the provider before CloudCLI creates its immutable session row."""
    provider = (requested or os.environ.get("GIT_SHADOW_PROVIDER", "")).strip().lower()
    if provider:
        if provider not in SUPPORTED_PROVIDERS:
            raise ValueError("不支持的 AI 供应商: %s (可选: %s)" % (provider, ", ".join(SUPPORTED_PROVIDERS)))
        return provider

    if not sys.stdin.isatty():
        log_warn("非交互终端未指定供应商，默认使用 codex；可用 --provider 或 GIT_SHADOW_PROVIDER 覆盖。")
        return "codex"

    print(f"\n{Colors.BOLD}请选择 CloudCLI AI 供应商:{Colors.RESET}")
    for index, item in enumerate(SUPPORTED_PROVIDERS, start=1):
        print(f"  [{index}] {item}")
    answer = input("请输入序号 [默认 1]: ").strip()
    if not answer:
        return SUPPORTED_PROVIDERS[0]
    try:
        return SUPPORTED_PROVIDERS[int(answer) - 1]
    except (ValueError, IndexError) as exc:
        raise ValueError("无效的供应商选择") from exc


def print_edge_event(event: dict) -> None:
    """Render edge events while retaining the JSONL protocol on the wire."""
    if event.get("type") == "accepted":
        log_success("VPS 已接收任务: %s" % event.get("job_id", ""))
    elif event.get("type") == "ready":
        log_info("VPS 边缘执行器 RPC 通道已建立")
    elif event.get("event") == "step.started":
        log_info("远端开始: %s" % event.get("step", event.get("action", "step")))
    elif event.get("event") == "output":
        channel = event.get("channel", "stdout")
        prefix = "  " if channel == "stdout" else "  [stderr] "
        print(f"{prefix}{event.get('message', '')}", flush=True)
    elif event.get("event") == "step.succeeded":
        log_success("远端完成: %s" % event.get("step", "step"))
    elif event.get("event") == "step.retry":
        log_warn("远端步骤将重试 #%s: %s" % (event.get("attempt", ""), event.get("step", "step")))
    elif event.get("event") == "session.ready":
        url = event.get("url", "")
        log_success("CloudCLI 会话已创建: %s" % url)
        if url:
            try:
                import webbrowser
                webbrowser.open(str(url))
            except Exception as exc:
                log_warn("无法自动打开浏览器，请手动访问 %s (%s)" % (url, exc))
    elif event.get("event") == "job.completed":
        log_success("VPS 任务执行完成")
    elif event.get("event") == "job.failed":
        suffix = "（CloudCLI Session 已创建，任务处于 partial 状态）" if event.get("partial") else ""
        log_error("VPS 任务失败: %s%s" % (event.get("error", "unknown error"), suffix))
    elif event.get("event") == "job.partial":
        log_warn("任务进入 partial 状态：CloudCLI Session 已创建，但后续步骤失败；Session=%s" % event.get("session", {}).get("session_id", ""))
    elif event.get("event") == "shadow.conflict":
        log_error("影子文件 CAS 冲突: %s（远端未覆盖，已保存冲突副本 %s）" % (event.get("path", ""), event.get("conflict_path", "")))
    elif event.get("event") == "shadow.remote":
        if event.get("replay_redacted"):
            log_warn("远端 Shadow 变化来自脱敏重放，将重新发起 pull 获取 live 内容: %s" % event.get("path", ""))
        else:
            log_info("已接收远端 Shadow 变化: %s" % event.get("path", ""))
    elif event.get("type") == "error":
        log_error("VPS 执行器错误: %s" % event.get("error", "unknown error"))

def cmd_diff(repo: RepoState):
    """以精美清晰的树状结构 (Diff Tree) 显示本地影子与差异扫描结果"""
    print(f"\n{Colors.BOLD}🔍 正在扫描工作区投影差异...{Colors.RESET}")
    print(f"  • 项目名称: {Colors.CYAN}{repo.repo_name}{Colors.RESET}")
    print(f"  • 当前分支: {Colors.CYAN}{repo.branch}{Colors.RESET}")
    print(f"  • 远程仓库: {Colors.CYAN}{repo.remote_url or '(未设置 origin，支持 P2P 直推)'}{Colors.RESET}")
    print(f"  • 工作区状态: {Colors.YELLOW + '已修改 (Dirty)' if repo.is_dirty else Colors.GREEN + '干净 (Clean)'}{Colors.RESET}")

    tree = DiffTreeRenderer(root_name=repo.repo_name, branch=repo.branch)

    # 1. 添加影子文件
    shadows = repo.scan_shadow_files()
    for f in shadows:
        full_p = os.path.join(repo.root_dir, f)
        sz = os.path.getsize(full_p) if os.path.exists(full_p) else 0
        tree.add_item(f, kind="shadow", size=sz)

    # 2. 添加未提交的 Git 变动文件
    wip_files = repo.get_wip_files()
    for item in wip_files:
        p = item["path"]
        full_p = os.path.join(repo.root_dir, p)
        sz = os.path.getsize(full_p) if os.path.exists(full_p) else 0
        tree.add_item(p, kind=item["kind"], size=sz)

    print(f"\n{Colors.BOLD}🌳 待投影差异树 (Diff Projection Tree):{Colors.RESET}")
    print(tree.render())

    # 3. 如果有未提交的 Git 代码修改，显示紧凑的 git diff --stat 摘要
    if repo.is_dirty:
        code, stat_out, _ = run_cmd(["git", "diff", "--stat", "HEAD"], cwd=repo.root_dir, check=False)
        if stat_out.strip():
            print(f"\n{Colors.BOLD}📝 代码修改明细统计 (Git Diff Stat):{Colors.RESET}")
            for l in stat_out.splitlines():
                print(f"  {Colors.DIM}{l}{Colors.RESET}")


def cmd_daemon_status(project_root: str):
    repo = RepoState(project_root)
    mgr = LocalDaemonManager(repo.root_dir)
    res = mgr.status()
    print(f"\n{Colors.BOLD}======================================================================{Colors.RESET}")
    print(f"{Colors.BOLD} 🚀 git-shadow 本地守护进程状态 (Local Daemon Status){Colors.RESET}")
    print(f"{Colors.BOLD}======================================================================{Colors.RESET}")
    print(f" • 项目路径: {Colors.CYAN}{repo.root_dir}{Colors.RESET}")
    if res["running"] and res.get("info"):
        info = res["info"]
        is_zombie = info.get("is_zombie", False)
        hb_age = info.get("heartbeat_age")
        has_errors = res.get("has_errors", False)

        if is_zombie:
            hb_str = f"（心跳中断 {int(hb_age)}s）" if hb_age is not None else ""
            print(f" • 运行状态: {Colors.RED}🔴 异常僵死 (PID: {info.get('pid')}{hb_str}){Colors.RESET}")
        elif has_errors:
            hb_str = f" (心跳: {int(hb_age)}s 前)" if hb_age is not None else ""
            print(f" • 运行状态: {Colors.YELLOW}🟡 运行中但有告警 (PID: {info.get('pid')}{hb_str}，请检查下方日志){Colors.RESET}")
        else:
            hb_str = f" (心跳正常: {int(hb_age)}s 前)" if hb_age is not None else ""
            print(f" • 运行状态: {Colors.GREEN}🟢 健康运行中 (PID: {info.get('pid')}{hb_str}){Colors.RESET}")
        print(f" • 目标主机: {Colors.CYAN}{info.get('host')}{Colors.RESET}")
        print(f" • 启动时间: {info.get('started_at')}")
        print(f" • 日志文件: {Colors.DIM}{res.get('log_file')}{Colors.RESET}")
        print(f"\n{Colors.BOLD}【最新运行日志 (最近 15 行)】:{Colors.RESET}")
        print("-" * 70)
        logs = res.get("recent_logs", [])
        if logs:
            for line in logs:
                print(f"  {line}")
        else:
            print(f"  {Colors.DIM}(暂无日志内容){Colors.RESET}")
    else:
        print(f" • 运行状态: {Colors.YELLOW}🔴 未运行 (无活跃守护进程){Colors.RESET}")
        print(f" • 提示: 可执行 `git shadow watch <host>` 或 `git shadow daemon start <host>` 开启后台同步。")
    print(f"{Colors.BOLD}======================================================================{Colors.RESET}\n")


def cmd_daemon_stop(project_root: str):
    repo = RepoState(project_root)
    mgr = LocalDaemonManager(repo.root_dir)
    res = mgr.stop()
    if res["status"] == "stopped":
        log_success(f"已停止本地后台守护进程 (PID: {res.get('pid')})")
    else:
        log_info("当前项目未检测到运行中的本地守护进程。")


def cmd_daemon_start(
    project_root: str,
    host: str,
    shadow_interval: float = 10.0,
    git_interval: float = 10.0,
    no_git_pull: bool = False,
):
    repo = RepoState(project_root)
    mgr = LocalDaemonManager(repo.root_dir)
    extra_args = []
    if no_git_pull:
        extra_args.append("--no-git-pull")
    res = mgr.start(
        remote_host=host,
        extra_args=extra_args,
        shadow_pull_interval=shadow_interval,
        git_pull_interval=git_interval,
    )
    if res["status"] == "already_running":
        log_warn(f"本地守护进程已在运行中 (PID: {res.get('pid')}, 目标: {res.get('host')})")
        log_info("查看状态与日志: git shadow status")
        log_info("停止当前守护:   git shadow stop")
        return
    log_success(f"git-shadow 本地后台守护进程已在当前用户 Session 启动 (PID: {res.get('pid')})")
    print(f"  • 目标主机: {Colors.CYAN}{host}{Colors.RESET}")
    print(f"  • 监控目录: {Colors.CYAN}{repo.root_dir}{Colors.RESET}")
    print(f"  • 运行日志: {Colors.DIM}{res.get('log_file')}{Colors.RESET}")
    print(f"\n{Colors.BOLD}💡 常用管理命令:{Colors.RESET}")
    print(f"  • 查看状态与日志: {Colors.GREEN}git shadow status{Colors.RESET} (或 git shadow daemon status)")
    print(f"  • 停止后台同步:   {Colors.GREEN}git shadow stop{Colors.RESET}   (或 git shadow daemon stop)\n")


def main(args: Optional[List[str]] = None):
    if args is None:
        args = sys.argv[1:]

    if not args or "-h" in args or "--help" in args or "help" in args:
        print_help()
        sys.exit(0)

    if "-v" in args or "--version" in args:
        print(f"git-shadow v{__version__}")
        sys.exit(0)

    subcmd = args[0]
    sub_args = args[1:]

    # 1. 本地自检 diff
    if subcmd == "diff":
        repo = RepoState(".")
        cmd_diff(repo)
        sys.exit(0)

    if subcmd == "install":
        destination = sub_args[0] if sub_args else "~/.local/bin"
        try:
            installed = install_wrappers(destination)
            for path in installed:
                log_success("已安装 CLI wrapper: %s" % path)
            log_info("将 %s 加入 PATH 后即可使用 `git shadow` / `git-shadow`." % pathlib.Path(destination).expanduser())
            sys.exit(0)
        except OSError as exc:
            log_error("本地 wrapper 安装失败: %s" % exc)
            sys.exit(1)

    # 2. auth 子命令族 (例如: git shadow auth sync <host> [--key <key>])
    if subcmd == "auth":
        if not sub_args:
            log_error("用法: git shadow auth sync <host> [--key <key>]")
            sys.exit(1)
        auth_action = sub_args[0]
        if auth_action != "sync" or len(sub_args) < 2:
            log_error("用法: git shadow auth sync <host> [--key <key>]")
            sys.exit(1)
        target_host = sub_args[1]

        spec_key = None
        if "--key" in sub_args:
            k_idx = sub_args.index("--key")
            if k_idx + 1 < len(sub_args):
                spec_key = sub_args[k_idx + 1]

        repo = RepoState(".")
        if not repo.is_git:
            log_error("auth sync 需要在一个有效的 Git 仓库中执行！")
            sys.exit(1)
        engine = ShadowEngine(repo=repo, remote_host=target_host)
        mgr = AuthManager(engine, specified_key=spec_key)
        success = mgr.sync_to_remote()
        sys.exit(0 if success else 1)

    # 3. probe 探针诊断 (例如: git shadow probe <host>)
    if subcmd == "probe":
        if not sub_args:
            log_error("用法: git shadow probe <host>")
            sys.exit(1)
        target_host = sub_args[0]
        repo = RepoState(".")
        engine = ShadowEngine(repo=repo, remote_host=target_host)
        log_info(f"正在全景探测远端主机 [{target_host}] 环境与工具...")
        probe = RemoteProbe(target_host)
        probe.scan(engine)
        probe.display_report()
        sys.exit(0)

    # 3.5 VPS 边缘执行器治理（不要求当前目录必须是 Git 仓库）
    if subcmd == "edge":
        if len(sub_args) < 2:
            log_error("用法: git shadow edge install <host> | edge status <host> <job_id> | edge resume <host> <job_id>")
            sys.exit(1)
        edge_action = sub_args[0]
        edge_host = sub_args[1]
        edge_client = EdgeClient(edge_host)
        if edge_action == "install":
            sys.exit(0 if edge_client.ensure_installed() else 1)
        if len(sub_args) < 3:
            log_error("缺少 job_id")
            sys.exit(1)
        job_id = sub_args[2]
        if edge_action == "status":
            result = edge_client.status(job_id)
            if result:
                print(json.dumps(result, ensure_ascii=False, indent=2))
                sys.exit(0 if result.get("found") else 1)
        elif edge_action == "resume":
            edge_client.resume(job_id, on_event=print_edge_event)
            sys.exit(0)
        log_error("未知 edge 操作: %s" % edge_action)
        sys.exit(1)

    # 3.6 项目级 VPS 常驻服务治理
    if subcmd == "service":
        if len(sub_args) < 2 or sub_args[1] not in ("load", "status", "unload"):
            log_error("用法: git shadow service <host> load|status|unload")
            sys.exit(1)
        service_host, service_action = sub_args[0], sub_args[1]
        repo = RepoState(".")
        service_client = ServiceClient(service_host, repo.root_dir)
        try:
            if service_action == "load":
                if not service_client.ensure_installed():
                    sys.exit(1)
                result = service_client.load()
            elif service_action == "status":
                result = service_client.status()
            else:
                result = service_client.unload()
            print(json.dumps(result, ensure_ascii=False, indent=2))
            sys.exit(0 if result.get("status") not in ("unreachable", "unknown") else 1)
        except Exception as exc:
            log_error("VPS 常驻服务操作失败: %s" % exc)
            sys.exit(1)

    # 3.7 本地用户级守护进程治理 (git shadow daemon start/stop/status)
    if subcmd in ("daemon", "local-daemon"):
        if not sub_args:
            log_error("用法: git shadow daemon start <host> | daemon stop | daemon status")
            sys.exit(1)
        daemon_action = sub_args[0]
        repo = RepoState(".")

        if daemon_action == "start":
            if len(sub_args) < 2:
                log_error("用法: git shadow daemon start <host>")
                sys.exit(1)
            target_host = sub_args[1]
            cmd_daemon_start(repo.root_dir, target_host)
            sys.exit(0)
        elif daemon_action == "stop":
            cmd_daemon_stop(repo.root_dir)
            sys.exit(0)
        elif daemon_action == "status":
            cmd_daemon_status(repo.root_dir)
            sys.exit(0)
        else:
            log_error("未知 daemon 操作: %s (支持: start, stop, status)" % daemon_action)
            sys.exit(1)

    # 快捷别名: git shadow status
    if subcmd == "status":
        repo = RepoState(".")
        cmd_daemon_status(repo.root_dir)
        sys.exit(0)

    # 快捷别名: git shadow stop
    if subcmd == "stop":
        repo = RepoState(".")
        cmd_daemon_stop(repo.root_dir)
        sys.exit(0)

    # 按需 watch 命令 (默认后台，带 -f / --foreground 则前台)
    if subcmd == "watch":
        if not sub_args or sub_args[0] in ("-h", "--help"):
            log_error("用法: git shadow watch <host> [-f|--foreground]")
            sys.exit(1)
        target_host = sub_args[0]
        is_fg = ("-f" in sub_args or "--foreground" in sub_args)
        if not is_fg:
            repo = RepoState(".")
            cmd_daemon_start(repo.root_dir, target_host)
            sys.exit(0)
        sub_args = [a for a in sub_args if a not in ("-f", "--foreground")]

    # 内部后台工作进程入口 (_daemon_worker)
    is_worker = (subcmd == "_daemon_worker")
    if is_worker:
        if len(sub_args) < 2:
            sys.exit(1)
        target_host = sub_args[0]
        worker_root = sub_args[1]

        parser_w = argparse.ArgumentParser(add_help=False)
        parser_w.add_argument("--log-file", default=None)
        parser_w.add_argument("--shadow-pull-interval", type=float, default=10.0)
        parser_w.add_argument("--git-pull-interval", type=float, default=10.0)
        parser_w.add_argument("--no-git-pull", action="store_true")
        w_opts, _ = parser_w.parse_known_args(sub_args[2:])

        if w_opts.log_file:
            log_fh = open(w_opts.log_file, "a", encoding="utf-8", buffering=1, errors="replace")
            sys.stdout = log_fh
            sys.stderr = log_fh
            import datetime
            print(f"\n[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] === git-shadow 本地守护进程启动 (目标主机: {target_host}, PID: {os.getpid()}) ===", flush=True)

        opts = argparse.Namespace(
            host=target_host,
            extra_args=[],
            dest=None,
            wip=False,
            no_wip=True,
            watch=True,
            git_pull_interval=w_opts.git_pull_interval,
            shadow_pull_interval=w_opts.shadow_pull_interval,
            no_git_pull=w_opts.no_git_pull,
            service=False,
            pull=False,
            with_shadows=False,
            agent=None,
            provider=None,
            cloudcli_url=None,
        )
        repo = RepoState(worker_root)
        remote_host = target_host
        remote_dir = None
        with_wip = False
    else:
        # 其余命令需要解析标准选项
        parser = argparse.ArgumentParser(add_help=False)
        parser.add_argument("host", help="目标远程主机 (SSH Host)")
        parser.add_argument("extra_args", nargs="*", help="额外参数或执行命令")
        parser.add_argument("-d", "--dest", default=None, help="远端目标路径")
        parser.add_argument("--wip", action="store_true", help="显式传输未提交修改（一次性 WIP 补丁）")
        parser.add_argument("--no-wip", action="store_true", help=argparse.SUPPRESS)
        parser.add_argument("--watch", action="store_true", help="持续监听 .gitshadow 变化")
        parser.add_argument("--shadow-pull-interval", type=float, default=10.0, help="--watch 时 Shadow 自动拉取间隔（秒）")
        parser.add_argument("--git-pull-interval", type=float, default=10.0, help="--watch 时 Git 自动拉取间隔（秒）")
        parser.add_argument("--no-git-pull", action="store_true", help="--watch 时关闭 Git 自动拉取")
        parser.add_argument("--service", action="store_true", help="使用项目级 VPS 常驻服务")
        parser.add_argument("--pull", action="store_true", help="远端强制拉取")
        parser.add_argument("--with-shadows", action="store_true", help="pull 时同步已登记的远端 Shadow 文件")
        parser.add_argument("-a", "--agent", default=None, help="远端 AI Agent 命令")
        parser.add_argument("--provider", default=None, help="CloudCLI AI 供应商: codex/claude/cursor/opencode")
        parser.add_argument("--cloudcli-url", default=None, help="VPS 内部 CloudCLI 地址，默认读取 GIT_SHADOW_CLOUDCLI_BASE_URL")

        try:
            opts, remaining = parser.parse_known_args(sub_args)
        except Exception as e:
            log_error(f"参数解析错误: {e}")
            print_help()
            sys.exit(1)

        repo = RepoState(".")
        if not repo.is_git:
            if subcmd not in ("run", "push", "up", "pull", "web", "watch"):
                log_error("当前命令需要一个有效的 Git 仓库；run/push/up/pull/watch 可用于普通文件夹。")
                sys.exit(1)

        remote_host = opts.host
        remote_dir = opts.dest
        with_wip = bool(opts.wip and not opts.no_wip)

    engine = ShadowEngine(
        repo=repo,
        remote_host=remote_host,
        remote_dir=remote_dir,
        with_wip=with_wip
    )
    shadow_store: Optional[ShadowManifestStore] = None
    service_client: Optional[ServiceClient] = None
    service_installed = False

    def get_shadow_store() -> ShadowManifestStore:
        """Keep Shadow acknowledgement state independent per remote target."""
        nonlocal shadow_store
        if shadow_store is None:
            target_dir = engine.resolve_remote_dir()
            shadow_store = ShadowManifestStore(
                repo.root_dir,
                remote_scope=remote_host + "\n" + target_dir,
            )
        return shadow_store

    def get_executor_client():
        nonlocal service_client, service_installed
        if not opts.service:
            edge_client = EdgeClient(remote_host)
            if not edge_client.ensure_installed():
                raise RuntimeError("VPS 边缘执行器不可用")
            return edge_client
        if service_client is None:
            service_client = ServiceClient(remote_host, repo.root_dir)
        if not service_installed:
            if not service_client.ensure_installed():
                raise RuntimeError("VPS 常驻服务不可用")
            service_installed = True
        # Recreates a lease-expired service, and is a no-op for a live one.
        service_client.load()
        return service_client

    def submit_projection(include_cloudcli: bool = False, provider: Optional[str] = None):
        edge_client = get_executor_client()
        plan = engine.build_edge_plan(
            provider=provider,
            shadow_files=repo.scan_shadow_files(),
            sync_git_pull=opts.pull,
            public_url=os.environ.get("GIT_SHADOW_CLOUDCLI_PUBLIC_URL", "https://cli.daduiot.com"),
            cloudcli_base_url=opts.cloudcli_url,
            cloudcli_token=os.environ.get("GIT_SHADOW_CLOUDCLI_TOKEN", "").strip() or None,
            include_wip=with_wip,
            include_cloudcli=include_cloudcli,
            shadow_store=get_shadow_store(),
        )
        plan["job_id"] = edge_client.new_job_id()

        def on_event(event: dict) -> None:
            print_edge_event(event)
            get_shadow_store().consume_event(event)

        return edge_client.submit(plan, on_event=on_event), plan

    def submit_shadow_pull(refresh_attempt: bool = False):
        edge_client = get_executor_client()
        plan = engine.build_shadow_pull_plan(
            shadow_files=repo.scan_shadow_files(),
            shadow_store=get_shadow_store(),
        )
        plan["job_id"] = edge_client.new_job_id()

        replay_redacted = {"value": False}

        def on_event(event: dict) -> None:
            print_edge_event(event)
            if event.get("event") == "shadow.remote" and event.get("replay_redacted"):
                replay_redacted["value"] = True
            get_shadow_store().consume_event(event)

        result = edge_client.submit(plan, on_event=on_event)
        if replay_redacted["value"] and not refresh_attempt:
            log_info("正在通过新的 live Shadow pull 补齐脱敏重放内容...")
            return submit_shadow_pull(refresh_attempt=True)
        return result, plan

    def shadow_snapshot():
        snapshot = {}
        for relative_path in repo.scan_shadow_files():
            path = os.path.join(repo.root_dir, relative_path)
            if os.path.isfile(path):
                digest = hashlib.sha256()
                with open(path, "rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
                snapshot[relative_path] = digest.hexdigest()
        return snapshot

    def watch_shadow(stop_event: Optional[threading.Event] = None) -> None:
        """Watch Shadow and safely fast-forward the Git lane from its remote."""
        stop_event = stop_event or threading.Event()
        previous = shadow_snapshot()
        now = time.monotonic()
        # 初始 last_pull 设为当前时间，给本地优先 Push 留出窗口，绝不让启动瞬间的 Pull 冲掉待同步文件
        last_pull = now
        last_git_pull = now
        git_pull_blocked = False
        git_pull_interval = max(2.0, float(opts.git_pull_interval))
        shadow_pull_interval = max(3.0, float(getattr(opts, "shadow_pull_interval", 10.0)))
        log_info("已进入个人持续同步：.gitshadow 自动双向 CAS，Git 仅对干净分支执行 ff-only 自动拉取。")
        daemon_mgr = LocalDaemonManager(repo.root_dir)
        daemon_mgr.touch_heartbeat()
        with LocalChangeWatcher(repo.root_dir) as local_watcher:
            if local_watcher.native:
                log_info("本地使用原生文件系统事件监听 (Linux inotify / Windows ChangeNotification)。")
            try:
                while not stop_event.is_set():
                    daemon_mgr.touch_heartbeat()
                    notified = local_watcher.wait_for_quiet(0.5) if local_watcher.native else local_watcher.wait(0.5)
                    if stop_event.is_set():
                        break
                    now = time.monotonic()
                    current = shadow_snapshot() if (not local_watcher.native or notified) else previous
                    
                    # 1. 本地优先：检测到本地影子文件改动，立即推送到远端 (Local -> Remote Push)
                    if current != previous:
                        try:
                            submit_projection(include_cloudcli=False)
                            previous = current
                            last_pull = now
                        except Exception as exc:
                            log_error("Shadow 自动同步失败（保留当前基线，稍后重试）: %s" % exc)
                            time.sleep(1.0)
                        continue

                    # 2. 定周期远端探查：拉取远端变更 (Remote -> Local Pull)
                    if now - last_pull >= shadow_pull_interval:
                        try:
                            pre_pull_snap = shadow_snapshot()
                            submit_shadow_pull()
                            post_pull_snap = shadow_snapshot()
                            # 仅将远端真正写入本地成功的文件合入 previous；
                            # 如果本地在此期间有修改，保留差异以在下轮循环立即 push
                            for p, h in post_pull_snap.items():
                                if pre_pull_snap.get(p) != h:
                                    previous[p] = h
                            for p in list(previous.keys()):
                                if p in pre_pull_snap and p not in post_pull_snap:
                                    previous.pop(p, None)
                            last_pull = now
                        except Exception as exc:
                            log_error("远端 Shadow 自动拉取失败（稍后重试）: %s" % exc)
                            last_pull = now
                    if repo.is_git and not opts.no_git_pull and now - last_git_pull >= git_pull_interval:
                        result = auto_fast_forward_pull(repo.root_dir, branch=repo.branch)
                        last_git_pull = now
                        if result["status"] == "updated":
                            repo._load_git_info()
                            git_pull_blocked = False
                            log_success("Git 远端提交已自动 fast-forward 拉回本地。")
                        elif result["status"] == "blocked":
                            if not git_pull_blocked:
                                log_warn("Git 自动拉取暂停：本地工作区有未提交修改；不会覆盖本地文件。")
                                git_pull_blocked = True
                        elif result["status"] == "error":
                            log_error("Git 自动拉取失败（稍后重试）: %s" % result.get("message", result.get("reason", "unknown error")))
                        elif result["status"] == "up-to-date":
                            git_pull_blocked = False
            except KeyboardInterrupt:
                stop_event.set()

    # 4. web 命令兼容重定向 (暂缓/废除独立 web 命令，统一收敛至 run)
    if subcmd == "web":
        log_warn("提示: 独立 'web' 命令因与具体工具强耦合已暂缓并废除，统一收敛至 'run' 命令。")
        log_info(f"正在为您自动切换执行: git shadow run {remote_host} cloudcli")
        subcmd = "run"
        opts.extra_args = ["cloudcli"]

    # 5. push 命令：仅静默推送
    if subcmd == "push":
        log_info(f"正在连接目标主机 [{remote_host}]，提交分层同步任务...")
        try:
            _, plan = submit_projection(include_cloudcli=False)
        except Exception as exc:
            log_error("VPS 分层同步任务未完成: %s" % exc)
            sys.exit(1)
        log_success(f"已成功同步 Git 基线与 .gitshadow 到 {remote_host}:{plan['project_path']}")

    # 6. run 命令：统一工作负载运行入口 (支持 Web Remote 与终端 AI Agent)
    elif subcmd == "run":
        # 确定要运行的目标 agent / 命令
        run_target = None
        if opts.extra_args:
            run_target = " ".join(opts.extra_args)
        elif opts.agent:
            run_target = opts.agent

        preferred_ws = None
        if not run_target:
            # 仅在未指定目标时，才调用全景探针扫描以呈现交互式选择菜单
            probe = RemoteProbe(remote_host)
            probe.scan(engine)
            preferred_ws = probe.get_preferred_workspace_dir(repo.repo_name) if not remote_dir else None

            available_agents = probe.get_available_agents()
            print(f"\n{Colors.BOLD}🤖 远端主机 [{remote_host}] 就绪的环境与 AI 智能体:{Colors.RESET}")
            for idx, ag in enumerate(available_agents, start=1):
                icon = "🌐" if ag.get("type") == "web" else "💻"
                print(f"  [{idx}] {icon} {ag['name']}")

            print(f"\n请输入序号以启动对应环境 [默认 1]: ", end="", flush=True)
            try:
                user_choice = sys.stdin.readline().strip()
                choice_idx = int(user_choice) if user_choice else 1
                if 1 <= choice_idx <= len(available_agents):
                    selected = available_agents[choice_idx - 1]
                    if selected.get("type") == "web":
                        run_target = "cloudcli"
                    else:
                        run_target = selected.get("cmd")
                else:
                    run_target = "$SHELL -l"
            except Exception:
                run_target = "$SHELL -l"

        # 分流 A：Web Remote 远程控制台 (如 CloudCLI)
        if run_target == "cloudcli":
            try:
                provider = choose_provider(opts.provider)
            except ValueError as exc:
                log_error(str(exc))
                sys.exit(1)

            # 【乐观先行】：先打开工作台；任务完成后再自动跳到 session 深链。
            engine.open_cloudcli_optimistic()
            if preferred_ws:
                engine.remote_dir = preferred_ws
            try:
                submit_projection(include_cloudcli=True, provider=provider)
            except Exception as exc:
                log_error("VPS 边缘任务未完成: %s" % exc)
                sys.exit(1)
            if opts.watch:
                watch_shadow()
            sys.exit(0)

        # 分流 B：终端交互型 Agent (如 opencode / commandcode / $SHELL)
        else:
            if preferred_ws:
                engine.remote_dir = preferred_ws
            try:
                _, plan = submit_projection(include_cloudcli=False)
            except Exception as exc:
                log_error("VPS 分层同步任务未完成: %s" % exc)
                sys.exit(1)
            engine.remote_dir = plan["project_path"]
            if opts.watch:
                stop_watch = threading.Event()
                watcher = threading.Thread(target=watch_shadow, args=(stop_watch,), daemon=True)
                watcher.start()
                try:
                    engine.launch_agent_or_shell(agent_cmd=run_target)
                finally:
                    stop_watch.set()
                    watcher.join(timeout=2)
            else:
                engine.launch_agent_or_shell(agent_cmd=run_target)

    # 7. up 命令：投影并进入
    elif subcmd == "up":
        probe = RemoteProbe(remote_host)
        probe.scan(engine)
        preferred_ws = probe.get_preferred_workspace_dir(repo.repo_name) if not remote_dir else None

        if preferred_ws:
            engine.remote_dir = preferred_ws
        try:
            _, plan = submit_projection(include_cloudcli=False)
        except Exception as exc:
            log_error("VPS 分层同步任务未完成: %s" % exc)
            sys.exit(1)
        engine.remote_dir = plan["project_path"]
        if opts.watch:
            stop_watch = threading.Event()
            watcher = threading.Thread(target=watch_shadow, args=(stop_watch,), daemon=True)
            watcher.start()
            try:
                engine.launch_agent_or_shell(agent_cmd=opts.agent)
            finally:
                stop_watch.set()
                watcher.join(timeout=2)
        else:
            engine.launch_agent_or_shell(agent_cmd=opts.agent)

    # 7.5 watch 命令前台执行 / _daemon_worker
    elif subcmd in ("watch", "_daemon_worker"):
        watch_shadow()
        sys.exit(0)

    # 8. pull 命令：本地拉取
    elif subcmd == "pull":
        git_pull_returncode = 0
        if repo.is_git:
            log_info("正在从远端 Git 仓库拉取最新提交到本地...")
            import subprocess
            result = subprocess.run(["git", "pull"])
            if result.returncode != 0:
                git_pull_returncode = result.returncode
                if opts.with_shadows:
                    log_warn("Git 拉取未完成，继续执行独立的 Shadow 拉取；本地文件不会被覆盖。")
                else:
                    sys.exit(result.returncode)
        elif not opts.with_shadows:
            log_error("普通文件夹没有 Git lane；如需拉取 Shadow，请追加 --with-shadows。")
            sys.exit(1)
        if opts.with_shadows:
            try:
                submit_shadow_pull()
            except Exception as exc:
                log_error("远端 Shadow 拉取失败: %s" % exc)
                sys.exit(1)
        if git_pull_returncode:
            sys.exit(git_pull_returncode)

    else:
        log_error(f"未知子命令: {subcmd}")
        print_help()
        sys.exit(1)

if __name__ == "__main__":
    main()
