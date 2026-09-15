"""
git_shadow.utils
工具辅助函数：颜色格式化、路径转换、命令执行
"""

import os
import sys
import subprocess
from typing import List, Optional, Tuple

# ANSI Colors
class Colors:
    HEADER = "\033[95m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RESET = "\033[0m"

# Windows VT100 support
if os.name == "nt":
    os.system("")

def log_info(msg: str):
    print(f"{Colors.CYAN}ℹ{Colors.RESET} {msg}", flush=True)

def log_success(msg: str):
    print(f"{Colors.GREEN}✔{Colors.RESET} {msg}", flush=True)

def log_warn(msg: str):
    print(f"{Colors.YELLOW}⚠{Colors.RESET} {msg}", flush=True)

def log_error(msg: str):
    print(f"{Colors.RED}✖{Colors.RESET} {msg}", file=sys.stderr, flush=True)

def log_step(step: int, total: int, msg: str):
    print(f"\n{Colors.BOLD}{Colors.BLUE}[{step}/{total}]{Colors.RESET} {Colors.BOLD}{msg}{Colors.RESET}", flush=True)


def run_cmd(
    cmd: List[str],
    cwd: Optional[str] = None,
    capture: bool = True,
    check: bool = True
) -> Tuple[int, str, str]:
    """运行子进程并安全返回结果"""
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
            text=True,
            encoding="utf-8",
            errors="replace"
        )
        if check and proc.returncode != 0:
            err_msg = proc.stderr if capture else f"Command failed with code {proc.returncode}"
            raise RuntimeError(f"Command {' '.join(cmd)} failed: {err_msg}")
        stdout = proc.stdout if capture else ""
        stderr = proc.stderr if capture else ""
        return proc.returncode, stdout, stderr
    except FileNotFoundError as e:
        raise RuntimeError(f"Executable not found: {cmd[0]}") from e

def format_size(bytes_size: int) -> str:
    """人类可读的文件大小格式化"""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if bytes_size < 1024.0:
            return f"{bytes_size:.1f} {unit}"
        bytes_size /= 1024.0
    return f"{bytes_size:.1f} TB"
