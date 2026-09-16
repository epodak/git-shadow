"""
git_shadow.daemon
本地用户级后台守护进程治理模块 (Local Edge Daemon Manager)
提供按需启动、状态检测、优雅停止与日志滚动查看能力，脱离当前终端生命周期。
"""

from __future__ import annotations

import ctypes
import datetime
import json
import os
import pathlib
import signal
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple

from .shadow_sync import project_key


def is_process_alive(pid: int) -> bool:
    """跨平台精准探测 PID 进程是否存活并核验真实身份（防 PID 复用假绿自欺欺人）"""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        kernel32 = ctypes.windll.kernel32
        SYNCHRONIZE = 0x00100000
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = kernel32.OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                if exit_code.value != 259:  # STILL_ACTIVE
                    return False
            else:
                return False

            # 防古德哈特伪装：检查进程可执行映像路径，防止 PID 被无关进程复用误判为存活
            buf = ctypes.create_unicode_buffer(1024)
            size = ctypes.c_ulong(1024)
            if hasattr(kernel32, "QueryFullProcessImageNameW") and kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                img = buf.value.lower()
                if not ("python" in img or "git-shadow" in img):
                    return False
            return True
        except (OSError, ValueError):
            return False
        finally:
            kernel32.CloseHandle(handle)
    else:
        try:
            os.kill(pid, 0)
        except OSError:
            return False

        # POSIX 进程身份与防复用校验：核验 /proc/<pid>/cmdline 是否确为 git-shadow 或 python
        cmdline_path = pathlib.Path(f"/proc/{pid}/cmdline")
        try:
            if cmdline_path.exists():
                cmdline = cmdline_path.read_bytes().replace(b"\0", b" ").lower()
                if not (b"git_shadow" in cmdline or b"git-shadow" in cmdline or b"python" in cmdline):
                    return False
        except (OSError, PermissionError):
            pass
        return True


class LocalDaemonManager:
    """管理当前项目的本地后台常驻守护进程"""

    def __init__(self, project_root: str, state_dir: Optional[str] = None):
        self.project_root = pathlib.Path(project_root).resolve()
        default_dir = pathlib.Path.home() / ".local/state/git-shadow/daemon"
        self.state_dir = pathlib.Path(state_dir or default_dir).expanduser()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        
        self.key = project_key(str(self.project_root))
        self.info_file = self.state_dir / f"{self.key}.json"
        self.log_file = self.state_dir / f"{self.key}.log"
        self.heartbeat_file = self.state_dir / f"{self.key}.heartbeat"

    def touch_heartbeat(self) -> None:
        """更新工作进程当前心跳时间戳"""
        try:
            now_str = str(datetime.datetime.now().timestamp())
            self.heartbeat_file.write_text(now_str, encoding="utf-8")
        except OSError:
            pass

    def heartbeat_age(self) -> Optional[float]:
        """获取最近心跳距离当前的秒数，无心跳时返回 None"""
        if not self.heartbeat_file.exists():
            return None
        try:
            ts = float(self.heartbeat_file.read_text(encoding="utf-8").strip())
            return max(0.0, datetime.datetime.now().timestamp() - ts)
        except (ValueError, OSError):
            return None

    def is_running(self) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """检查当前项目的守护进程是否存活，若进程异常退出则清理陈旧 PID"""
        if not self.info_file.exists():
            return False, None
        try:
            info = json.loads(self.info_file.read_text(encoding="utf-8"))
        except Exception:
            self._cleanup()
            return False, None

        pid = info.get("pid")
        if isinstance(pid, int) and is_process_alive(pid):
            hb_age = self.heartbeat_age()
            info["heartbeat_age"] = hb_age
            # 心跳超过 45 秒判定为僵死/假绿
            info["is_zombie"] = bool(hb_age is not None and hb_age > 45.0)
            return True, info
        
        # 进程已死亡，清除孤儿状态文件
        self._cleanup()
        return False, None

    def _spawn_windows(self, cmd_list: List[str], cwd: str, env: Dict[str, str]) -> int:
        """在 Windows 宿主下通过 CIM 创建完全脱离父终端与沙箱 Job Object 的独立顶级后台进程"""
        # 转义单引号以安全内联到 PowerShell
        escaped_cmd = subprocess.list2cmdline(cmd_list).replace("'", "''")
        escaped_cwd = cwd.replace("'", "''")
        
        ps_script = (
            f"$res = Invoke-CimMethod -ClassName Win32_Process -MethodName Create "
            f"-Arguments @{{CommandLine='{escaped_cmd}'; CurrentDirectory='{escaped_cwd}'}}; "
            f"if ($res.ReturnValue -eq 0) {{ $res.ProcessId }} else {{ exit 1 }}"
        )
        try:
            completed = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps_script],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
                check=False,
            )
            output = completed.stdout.strip()
            if completed.returncode == 0 and output.isdigit():
                return int(output)
        except Exception:
            pass

        # 优雅降级到 subprocess.Popen
        flags = 0
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            flags |= subprocess.CREATE_NO_WINDOW
        if hasattr(subprocess, "DETACHED_PROCESS"):
            flags |= subprocess.DETACHED_PROCESS
        proc = subprocess.Popen(
            cmd_list,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=cwd,
            env=env,
            creationflags=flags,
        )
        return proc.pid

    def start(
        self,
        remote_host: str,
        extra_args: Optional[List[str]] = None,
        shadow_pull_interval: float = 10.0,
        git_pull_interval: float = 10.0,
    ) -> Dict[str, Any]:
        """启动后台守护进程（Session 1 用户环境，无窗口脱离终端）"""
        running, info = self.is_running()
        if running and info:
            return {
                "status": "already_running",
                "pid": info.get("pid"),
                "host": info.get("host"),
                "log_file": str(self.log_file),
                "started_at": info.get("started_at"),
            }

        # 选择最佳 Python 解释器：Windows 优先使用 pythonw.exe（无控制台黑框）
        python_bin = sys.executable
        if sys.platform == "win32":
            pythonw = pathlib.Path(sys.executable).with_name("pythonw.exe")
            if pythonw.exists():
                python_bin = str(pythonw)

        source_root = str(pathlib.Path(__file__).resolve().parent.parent)
        env = dict(os.environ)
        env["PYTHONPATH"] = source_root + (os.pathsep + env["PYTHONPATH"] if "PYTHONPATH" in env else "")

        # 组装 worker 启动参数
        worker_cmd = [
            python_bin,
            "-m",
            "git_shadow.cli",
            "_daemon_worker",
            remote_host,
            str(self.project_root),
            "--log-file",
            str(self.log_file),
            "--shadow-pull-interval",
            str(shadow_pull_interval),
            "--git-pull-interval",
            str(git_pull_interval),
        ]
        if extra_args:
            worker_cmd.extend(extra_args)

        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 跨平台完全脱离终端
        if sys.platform == "win32":
            pid = self._spawn_windows(worker_cmd, str(self.project_root), env)
        else:
            proc = subprocess.Popen(
                worker_cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=str(self.project_root),
                env=env,
                start_new_session=True,
            )
            pid = proc.pid

        info = {
            "pid": pid,
            "host": remote_host,
            "project_root": str(self.project_root),
            "log_file": str(self.log_file),
            "started_at": timestamp,
        }
        self.info_file.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")

        return {
            "status": "started",
            "pid": pid,
            "host": remote_host,
            "log_file": str(self.log_file),
            "started_at": timestamp,
        }

    def stop(self) -> Dict[str, Any]:
        """优雅停止后台守护进程"""
        running, info = self.is_running()
        if not running or not info:
            if self.pid_file.exists():
                self._cleanup()
                return {"status": "stale_cleaned"}
            return {"status": "not_running"}

        pid = info["pid"]
        stopped = False

        if sys.platform == "win32":
            # Windows 下通过 taskkill 或 TerminateProcess 杀掉进程树
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
                stopped = True
            except Exception:
                try:
                    os.kill(pid, signal.SIGTERM)
                    stopped = True
                except Exception:
                    pass
        else:
            try:
                os.kill(pid, signal.SIGTERM)
                stopped = True
            except OSError:
                pass

        self._cleanup()
        return {"status": "stopped", "pid": pid, "success": stopped}

    def status(self) -> Dict[str, Any]:
        """获取当前守护进程运行状态与最新日志"""
        running, info = self.is_running()
        recent_logs: List[str] = []
        has_errors = False
        if self.log_file.exists():
            try:
                lines = self.log_file.read_text(encoding="utf-8", errors="replace").splitlines()
                recent_logs = lines[-15:] if len(lines) > 15 else lines
                for l in recent_logs:
                    if "✖" in l or "Traceback" in l or "Exception" in l or "失败" in l:
                        has_errors = True
                        break
            except Exception:
                pass

        return {
            "running": running,
            "info": info,
            "log_file": str(self.log_file),
            "recent_logs": recent_logs,
            "has_errors": has_errors,
        }

    def _cleanup(self) -> None:
        """清理状态元数据与心跳文件"""
        for f in (self.info_file, self.heartbeat_file):
            try:
                if f.exists():
                    f.unlink()
            except OSError:
                pass
