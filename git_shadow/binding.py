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

    # 3. 询问打开与交互方式
    print(f"\n{Colors.YELLOW}❓ 请选择该工作区的默认打开与交互方式:{Colors.RESET}")
    print(f"   [1] 🌐 CloudCLI Web 远程工作台 {Colors.GREEN}(推荐: 秒开浏览器，接收远端边缘广播并跳转深链){Colors.RESET}")
    print(f"   [2] 💻 远端终端 AI Agent (进入交互式终端 Shell / OpenCode / CommandCode)")
    print(f"   [3] ⚡ 仅后台静默同步 (纯后台无头同步，不弹出任何界面)")
    try:
        mode_input = input("\n请输入编号 [默认: 1]: ").strip()
    except (EOFError, KeyboardInterrupt):
        print(f"\n{Colors.RED}已取消配置。{Colors.RESET}")
        sys.exit(1)

    if mode_input == "2":
        launch_mode = "terminal"
    elif mode_input == "3":
        launch_mode = "silent"
    else:
        launch_mode = "cloudcli"

    # 4. 持久化保存记忆
    set_project_binding(str(project_path), selected_host, remote_dir, launch_mode=launch_mode)
    print(f"\n{Colors.GREEN}✔ 已成功绑定项目至 [{selected_host}:{remote_dir}] (打开方式: {launch_mode})，后续运行无需重复配置！{Colors.RESET}")
    return selected_host, remote_dir, launch_mode
