# ==============================================================================
# ⚡ git-shadow 项目与远端 VPS 绑定管理 (Project Binding & Auto-Discovery)
# ==============================================================================
import json
import os
import pathlib
import re
import subprocess
import sys
from typing import Dict, List, Optional, Tuple

from .scanner import RepoState
from .engine import ShadowEngine
from .probe import RemoteProbe
from .remote_bootstrap import RemoteBootstrapManager, format_cloudcli_plan
from .utils import Colors, NO_WINDOW_FLAG


def get_bindings_file() -> pathlib.Path:
    base = pathlib.Path.home() / ".local/state/git-shadow"
    base.mkdir(parents=True, exist_ok=True)
    return base / "bindings.json"


def load_all_bindings() -> Dict[str, Dict[str, str]]:
    f = get_bindings_file()
    if not f.exists():
        return {}
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def save_all_bindings(bindings: Dict[str, Dict[str, str]]) -> None:
    f = get_bindings_file()
    f.write_text(json.dumps(bindings, ensure_ascii=False, indent=2), encoding="utf-8")


def normalize_project_path(path: str) -> str:
    return str(pathlib.Path(path).resolve()).replace("\\", "/")


def get_project_binding(project_root: str) -> Optional[Dict[str, str]]:
    key = normalize_project_path(project_root)
    bindings = load_all_bindings()
    return bindings.get(key)


def set_project_binding(
    project_root: str,
    host: str,
    remote_dir: Optional[str] = None,
    launch_mode: str = "cloudcli",
) -> Dict[str, str]:
    key = normalize_project_path(project_root)
    bindings = load_all_bindings()
    entry = {
        "host": host,
        "remote_dir": remote_dir or f"~/wkspace/{pathlib.Path(project_root).name}",
        "launch_mode": launch_mode,
        "project_root": key,
    }
    bindings[key] = entry
    save_all_bindings(bindings)
    return entry


def remove_project_binding(project_root: str) -> bool:
    """解除本地对指定项目的 VPS 与路径绑定记录"""
    key = normalize_project_path(project_root)
    bindings = load_all_bindings()
    if key in bindings:
        del bindings[key]
        save_all_bindings(bindings)
        return True
    return False


def get_available_ssh_hosts() -> List[str]:
    """从 ~/.ssh/config 探测用户配置好的全部可用 VPS 主机名"""
    ssh_cfg = pathlib.Path.home() / ".ssh" / "config"
    hosts: List[str] = []
    if not ssh_cfg.exists():
        return hosts

    try:
        text = ssh_cfg.read_text(encoding="utf-8", errors="ignore")
        for line in text.splitlines():
            line = line.strip()
            if line.lower().startswith("host "):
                parts = line[5:].strip().split()
                for p in parts:
                    # 排除通配符与通用托管平台
                    if any(c in p for c in ["*", "?", "!"]):
                        continue
                    p_lower = p.lower()
                    if "github" in p_lower or "gitlab" in p_lower or "gitee" in p_lower:
                        continue
                    if p not in hosts:
                        hosts.append(p)
    except OSError:
        pass
    return hosts


def probe_remote_target(host: str, repo_name: str) -> Tuple[bool, str]:
    """
    通过轻量 SSH 探针快速检查远端是否已有该项目目录。
    返回: (exists, resolved_remote_path)
    """
    probe_script = f"""
    if [ -d "$HOME/wkspace/{repo_name}" ]; then
        echo "EXISTS:$HOME/wkspace/{repo_name}"
    elif [ -d "$HOME/workspace/{repo_name}" ]; then
        echo "EXISTS:$HOME/workspace/{repo_name}"
    elif [ -d "$HOME/{repo_name}" ]; then
        echo "EXISTS:$HOME/{repo_name}"
    elif [ -d "$HOME/wkspace" ]; then
        echo "NEW:$HOME/wkspace/{repo_name}"
    elif [ -d "$HOME/workspace" ]; then
        echo "NEW:$HOME/workspace/{repo_name}"
    else
        echo "NEW:$HOME/wkspace/{repo_name}"
    fi
    """
    cmd = [
        "ssh",
        "-o", "RemoteCommand=none",
        "-o", "RequestTTY=no",
        "-o", "ConnectTimeout=4",
        "-o", "StrictHostKeyChecking=accept-new",
        host,
        probe_script.strip(),
    ]
    try:
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=6,
            creationflags=NO_WINDOW_FLAG,
        )
        out = res.stdout.strip()
        if out.startswith("EXISTS:"):
            return True, out[7:].strip()
        elif out.startswith("NEW:"):
            return False, out[4:].strip()
    except Exception:
        pass
    return False, f"~/wkspace/{repo_name}"


def probe_remote_capabilities(project_root: str, host: str) -> Dict[str, object]:
    """Probe host capabilities that affect onboarding choices.

    A capability is runtime state, not a static host property. The returned
    structure is intentionally small so setup can make a fast recommendation
    without running the full environment/version scan.
    """
    try:
        repo = RepoState(project_root)
        engine = ShadowEngine(repo=repo, remote_host=host)
        cloudcli = RemoteProbe(host).probe_cloudcli(engine)
    except Exception as exc:
        cloudcli = {
            "available": False,
            "reason": "capability-probe-failed: %s" % exc,
        }
    return {"cloudcli": cloudcli}


def interactive_setup_binding(project_root: str, default_host: Optional[str] = None) -> Tuple[str, str, str]:
    """
    交互式智能引导用户挑选目标 VPS、远端承载路径及默认打开交互方式。
    返回: (selected_host, remote_dir, launch_mode)
    """
    project_path = pathlib.Path(project_root).resolve()
    repo_name = project_path.name

    hosts = get_available_ssh_hosts()
    # 优先使用传入的 default_host、或历史绑定、或 hosts 中的 aws / 第一个主机
    if default_host:
        preferred_host = default_host
    elif "aws" in hosts:
        preferred_host = "aws"
    elif hosts:
        preferred_host = hosts[0]
    else:
        preferred_host = "aws"

    print()
    print(f"{Colors.BOLD}{Colors.CYAN}======================================================================{Colors.RESET}")
    print(f"{Colors.BOLD} 🚀 git-shadow 目标主机与路径配置引导{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.CYAN}======================================================================{Colors.RESET}")
    print(f" • 当前项目: {Colors.GREEN}{project_path}{Colors.RESET}")
    print()

    # 1. 选择 VPS 主机
    print(f"{Colors.YELLOW}❓ 请选择想要同步的目标 VPS 主机:{Colors.RESET}")
    menu_hosts: List[str] = []
    
    # 保证推荐的主机排在第一位
    if preferred_host in hosts:
        menu_hosts.append(preferred_host)
    for h in hosts:
        if h not in menu_hosts:
            menu_hosts.append(h)

    for i, h in enumerate(menu_hosts, 1):
        suffix = f" {Colors.GREEN}(推荐){Colors.RESET}" if h == preferred_host else ""
        print(f"   [{i}] {h}{suffix}")
    print(f"   [{len(menu_hosts) + 1}] 手动输入其他 SSH Host")

    prompt_str = f"\n请输入编号或主机名 [默认: 1 ({preferred_host})]: "
    try:
        user_choice = input(prompt_str).strip()
    except (EOFError, KeyboardInterrupt):
        print(f"\n{Colors.RED}已取消配置。{Colors.RESET}")
        sys.exit(1)

    selected_host = preferred_host
    if user_choice:
        if user_choice.isdigit():
            idx = int(user_choice) - 1
            if 0 <= idx < len(menu_hosts):
                selected_host = menu_hosts[idx]
            elif idx == len(menu_hosts):
                custom = input("请输入自定义 SSH Host: ").strip()
                if custom:
                    selected_host = custom
        else:
            selected_host = user_choice

    # 2. 远端路径推导与确认
    print(f"\n正在快速探测远端主机 [{selected_host}] 的工作区环境...")
    is_existing, suggested_dir = probe_remote_target(selected_host, repo_name)

    if is_existing:
        print(f"✔ 远端已检测到现有目录: {Colors.GREEN}{suggested_dir}{Colors.RESET}")
        remote_dir = suggested_dir
    else:
        print(f"{Colors.YELLOW}❓ 请确认远端存放目录路径:{Colors.RESET}")
        print(f"   默认推荐: {Colors.CYAN}{suggested_dir}{Colors.RESET}")
        try:
            custom_dir = input(f"直接按回车确认，或输入新路径 [默认: {suggested_dir}]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print(f"\n{Colors.RED}已取消配置。{Colors.RESET}")
            sys.exit(1)
        remote_dir = custom_dir if custom_dir else suggested_dir

    # 3. 先探测主机能力，再询问打开与交互方式。能力是运行时事实，不能硬编码推荐。
    print(f"\n正在探测远端主机 [{selected_host}] 的可用交互能力...")
    capabilities = probe_remote_capabilities(str(project_path), selected_host)
    cloudcli = capabilities.get("cloudcli", {})
    cloudcli_available = bool(cloudcli.get("available"))
    cloudcli_repairable = RemoteBootstrapManager.can_repair_cloudcli(cloudcli)
    default_mode = "1" if (cloudcli_available or cloudcli_repairable) else "2"

    print(f"\n{Colors.YELLOW}❓ 请选择该工作区的默认打开与交互方式:{Colors.RESET}")
    if cloudcli_available:
        print(f"   [1] 🌐 CloudCLI Web 远程工作台 {Colors.GREEN}(推荐: 能力探针已通过){Colors.RESET}")
        print(f"   [2] 💻 远端终端 AI Agent (进入交互式终端 Shell / OpenCode / CommandCode)")
    elif cloudcli_repairable:
        reason = str(cloudcli.get("reason") or "unavailable")
        print(f"   [1] 🌐 CloudCLI Web 远程工作台 {Colors.GREEN}(推荐: 当前 {reason}，选择后通过 SSH 自动安装/启动){Colors.RESET}")
        print(f"       {Colors.DIM}{format_cloudcli_plan(selected_host).splitlines()[1].strip()}{Colors.RESET}")
        print(f"   [2] 💻 远端终端 AI Agent (不安装 CloudCLI)")
    else:
        reason = str(cloudcli.get("reason") or "unavailable")
        print(f"   [1] 🌐 CloudCLI Web 远程工作台 {Colors.YELLOW}(当前不可用且无法自动修复: {reason}){Colors.RESET}")
        print(f"   [2] 💻 远端终端 AI Agent {Colors.GREEN}(推荐){Colors.RESET}")
    print(f"   [3] ⚡ 仅后台静默同步 (纯后台无头同步，不弹出任何界面)")
    try:
        mode_input = input(f"\n请输入编号 [默认: {default_mode}]: ").strip()
    except (EOFError, KeyboardInterrupt):
        print(f"\n{Colors.RED}已取消配置。{Colors.RESET}")
        sys.exit(1)

    resolved_mode = mode_input or default_mode
    if resolved_mode == "2":
        launch_mode = "terminal"
    elif resolved_mode == "3":
        launch_mode = "silent"
    else:
        launch_mode = "cloudcli"
        if not cloudcli_available and cloudcli_repairable:
            print(f"\n{Colors.CYAN}ℹ 正在通过现有 SSH 连接自动准备 CloudCLI，无需手工登录远端...{Colors.RESET}")
            bootstrap_engine = ShadowEngine(repo=RepoState(str(project_path)), remote_host=selected_host)
            bootstrap = RemoteBootstrapManager(bootstrap_engine)
            bootstrap_result = bootstrap.ensure_cloudcli()
            if bootstrap_result.get("success"):
                metadata = bootstrap_result.get("metadata", {})
                print(
                    f"{Colors.GREEN}✔ CloudCLI 已就绪 "
                    f"(service={metadata.get('service', 'unknown')}, "
                    f"version={metadata.get('cloudcli_version', '')}){Colors.RESET}"
                )
                cloudcli_available = True
            else:
                print(
                    f"{Colors.YELLOW}⚠ CloudCLI 自动准备未完成: "
                    f"{bootstrap_result.get('error', 'unknown error')}。"
                    f" 已保留 Web 偏好，运行时仍会优雅降级。{Colors.RESET}"
                )
        elif not cloudcli_available:
            print(f"{Colors.YELLOW}⚠ 已保留 CloudCLI 偏好；运行时会再次探测，仍不可用时自动降级为同步模式，不会盲目重试。{Colors.RESET}")

    # 4. 检查并引导创建 .gitshadow 契约白名单文件
    ensure_gitshadow_file(str(project_path))

    # 5. 持久化保存记忆
    set_project_binding(str(project_path), selected_host, remote_dir, launch_mode=launch_mode)
    print(f"\n{Colors.GREEN}✔ 已成功绑定项目至 [{selected_host}:{remote_dir}] (打开方式: {launch_mode})，后续运行无需重复配置！{Colors.RESET}")
    return selected_host, remote_dir, launch_mode


GITSHADOW_TEMPLATE = """# ==============================================================================
# ⚡ .gitshadow — 私有影子资产跨端同步白名单契约 (ADR-2026-09-15)
# ==============================================================================
# 【第一性原理与所有权隔离】：
# 1. GitTrackedLane：代码基线由 Git 管理，远端 VPS 自动通过 GitHub 拉取对齐；
# 2. ShadowLane：只有在此白名单中列出的私有文件，才会通过 CAS 原子同步到 VPS；
# 3. 严禁混入 .git、node_modules、venv 等重型环境或版本库元数据。
# ==============================================================================

# 环境变量与私有配置
.env*
*.local
config.local.*

# 私钥与证书凭据
*.key
*.pem
*.secret

# 本地工程调试轨迹与认知文档
_dev_log/
*.local.md
"""


def ensure_gitshadow_file(project_root: str, interactive: bool = True) -> bool:
    """检查项目根目录是否存在 .gitshadow 契约文件，若无则主动引导用户确认创建"""
    root = pathlib.Path(project_root).resolve()
    gitshadow_path = root / ".gitshadow"
    if gitshadow_path.is_file():
        return True

    print()
    print(f"{Colors.BOLD}{Colors.YELLOW}======================================================================{Colors.RESET}")
    print(f"{Colors.BOLD} 📄 未检测到 .gitshadow 私有影子文件契约{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.YELLOW}======================================================================{Colors.RESET}")
    print(f" • 当前工作区: {Colors.CYAN}{root}{Colors.RESET}")
    print(f" • 依据《分层同步所有权律》：")
    print(f"   - Git 代码由远端直接从 GitHub 自动对齐拉取；")
    print(f"   - 只有在 {Colors.CYAN}.gitshadow{Colors.RESET} 中声明的私有敏感文件，才会通过 CAS 原子同步到 VPS；")
    print(f"   - 严禁盲目全量同步，严禁把 .git、node_modules 等非影子文件卷入同步。")
    print()

    if interactive and sys.stdin.isatty():
        try:
            choice = input(f"{Colors.YELLOW}❓ 是否立即为您生成推荐的 .gitshadow 白名单契约模板？ [Y/n]: {Colors.RESET}").strip().lower()
        except (EOFError, KeyboardInterrupt):
            choice = "y"
        if choice in ("", "y", "yes"):
            gitshadow_path.write_text(GITSHADOW_TEMPLATE, encoding="utf-8")
            print(f"{Colors.GREEN}✔ 已成功创建 .gitshadow 契约文件！{Colors.RESET}\n")
            return True
        else:
            print(f"{Colors.YELLOW}⚠ 您跳过了 .gitshadow 创建，系统将临时使用默认内存白名单。{Colors.RESET}\n")
            return False
    else:
        # 非交互环境自动创建
        gitshadow_path.write_text(GITSHADOW_TEMPLATE, encoding="utf-8")
        print(f"{Colors.GREEN}✔ 已自动为工作区创建推荐的 .gitshadow 契约文件。{Colors.RESET}\n")
        return True

