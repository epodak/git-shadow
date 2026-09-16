"""Client-side SSH/JSONL transport for the git-shadow VPS edge executor."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import pathlib
import shlex
import subprocess
import sys
import threading
import uuid
from typing import Any, Callable, Dict, List, Optional

from .utils import log_info, log_success, log_warn, NO_WINDOW_FLAG


EventHandler = Callable[[Dict[str, Any]], None]


def output_text(value: Any) -> str:
    """Normalize subprocess output from text and byte upload modes."""
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value or "")


def clean_ssh_args(host: str, remote_command: str) -> List[str]:
    """Build a non-interactive SSH command that ignores hostile SSH config."""
    return [
        "ssh",
        "-o",
        "RemoteCommand=none",
        "-o",
        "RequestTTY=no",
        "-o",
        "StrictHostKeyChecking=accept-new",
        host,
        remote_command,
    ]


class EdgeClient:
    """Submit and stream a job over one SSH JSONL connection."""

    remote_agent_path = "$HOME/.local/share/git-shadow/bin/git-shadow-edge-agent.py"

    def __init__(self, remote_host: str, python_executable: str = "python3"):
        self.remote_host = remote_host
        self.python_executable = python_executable
        self._write_lock = threading.Lock()
        self.last_install_error = ""

    @staticmethod
    def new_job_id() -> str:
        return "job-" + uuid.uuid4().hex[:16]

    def _agent_command(self) -> str:
        return "%s %s --rpc" % (
            shlex.quote(self.python_executable),
            self.remote_agent_path,
        )

    def _run_ssh(self, remote_command: str, input_data: Optional[bytes] = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            clean_ssh_args(self.remote_host, remote_command),
            input=input_data,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            creationflags=NO_WINDOW_FLAG,
        )

    def ensure_installed(self) -> bool:
        """Upload the standalone executor when the VPS copy differs."""
        self.last_install_error = ""
        source_path = pathlib.Path(__file__).with_name("edge_agent.py")
        local_digest = hashlib.sha256(source_path.read_bytes()).hexdigest()
        check = self._run_ssh(
            "sha256sum %s 2>/dev/null | awk '{print $1}'" % self.remote_agent_path
        )
        remote_digest = output_text(check.stdout).strip()
        if remote_digest == local_digest:
            return True
        if check.returncode != 0:
            detail = output_text(check.stderr).strip() or "ssh exit code %s" % check.returncode
            log_warn("VPS 边缘执行器探测失败，将尝试重新安装: %s" % detail[-1000:])

        payload = base64.b64encode(source_path.read_bytes())
        # Tilde expansion must happen in a shell, so keep the destination path
        # literal and quote only the command's fixed pieces.
        command = (
            "mkdir -p ~/.local/share/git-shadow/bin && "
            "base64 -d > ~/.local/share/git-shadow/bin/git-shadow-edge-agent.py && "
            "chmod 700 ~/.local/share/git-shadow/bin/git-shadow-edge-agent.py"
        )
        uploaded = self._run_ssh(command, input_data=payload)
        if uploaded.returncode != 0:
            message = output_text(uploaded.stderr).strip() or "SSH transfer failed"
            self.last_install_error = "upload failed: %s" % message[-1000:]
            log_warn(
                "VPS 边缘执行器安装失败: %s；可重试 `git shadow edge install %s`，"
                "并检查 SSH 写入权限。" % (message[-1000:], self.remote_host)
            )
            return False
        verify = self._run_ssh(
            "sha256sum %s 2>/dev/null | awk '{print $1}'" % self.remote_agent_path
        )
        verified_digest = output_text(verify.stdout).strip()
        if verify.returncode != 0 or verified_digest != local_digest:
            detail = output_text(verify.stderr).strip() or "remote checksum mismatch"
            self.last_install_error = "verification failed: %s" % detail[-1000:]
            log_warn(
                "VPS 边缘执行器安装后校验失败: %s；请重试安装并确认远端磁盘可写。"
                % detail[-1000:]
            )
            return False
        log_success("VPS 边缘执行器已就绪")
        return True

    def open(self) -> subprocess.Popen:
        return subprocess.Popen(
            clean_ssh_args(self.remote_host, self._agent_command()),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=NO_WINDOW_FLAG,
        )

    def _send(self, process: subprocess.Popen, message: Dict[str, Any]) -> None:
        if process.stdin is None:
            raise RuntimeError("edge executor stdin is unavailable")
        with self._write_lock:
            process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
            process.stdin.flush()

    def submit(
        self,
        plan: Dict[str, Any],
        on_event: Optional[EventHandler] = None,
        wait_for_completion: bool = True,
    ) -> Dict[str, Any]:
        """Submit a plan and stream events until it reaches a terminal state."""
        job_id = str(plan.get("job_id") or self.new_job_id())
        request = {**plan, "type": "submit", "job_id": job_id}
        process = self.open()
        stderr_lines: List[str] = []

        def read_stderr() -> None:
            if process.stderr is None:
                return
            for line in process.stderr:
                stderr_lines.append(line.rstrip())

        threading.Thread(target=read_stderr, daemon=True).start()
        heartbeat_stop = threading.Event()

        def heartbeat_loop() -> None:
            while not heartbeat_stop.wait(60):
                if process.poll() is not None:
                    return
                try:
                    self._send(process, {"type": "heartbeat", "job_id": job_id})
                except (BrokenPipeError, OSError):
                    return

        heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
        heartbeat_thread.start()
        try:
            self._send(process, request)
            result: Dict[str, Any] = {"job_id": job_id, "status": "accepted"}
            if not wait_for_completion:
                return result
            if process.stdout is None:
                raise RuntimeError("edge executor stdout is unavailable")
            for line in process.stdout:
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if on_event:
                    on_event(event)
                if event.get("event") == "session.ready":
                    result.update(event)
                if event.get("event") == "job.completed":
                    result["status"] = "completed"
                    break
                if event.get("event") == "job.failed":
                    result["status"] = "failed"
                    result["error"] = event.get("error", "remote job failed")
                    if event.get("partial"):
                        result["partial"] = True
                        result["session"] = event.get("session")
                    break
            if result["status"] == "failed":
                raise RuntimeError(str(result.get("error")))
            if process.poll() is None:
                self._send(process, {"type": "shutdown"})
            process.wait(timeout=10)
            if process.returncode not in (0, None) and stderr_lines:
                raise RuntimeError("remote executor exited: %s" % stderr_lines[-1])
            return result
        finally:
            heartbeat_stop.set()
            if process.poll() is None:
                process.terminate()

    def resume(self, job_id: str, after_seq: int = 0, on_event: Optional[EventHandler] = None) -> List[Dict[str, Any]]:
        process = self.open()
        replay: List[Dict[str, Any]] = []
        try:
            self._send(process, {"type": "resume", "job_id": job_id, "after_seq": after_seq})
            if process.stdout is None:
                return replay
            for line in process.stdout:
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("type") == "event" or event.get("replay"):
                    replay.append(event)
                if on_event:
                    on_event(event)
                if event.get("type") == "replay.end":
                    break
            self._send(process, {"type": "shutdown"})
            process.wait(timeout=10)
            return replay
        finally:
            if process.poll() is None:
                process.terminate()

    def status(self, job_id: str) -> Optional[Dict[str, Any]]:
        process = self.open()
        try:
            self._send(process, {"type": "status", "job_id": job_id})
            if process.stdout is None:
                return None
            for line in process.stdout:
                if not line.strip():
                    continue
                payload = json.loads(line)
                if payload.get("type") == "status":
                    self._send(process, {"type": "shutdown"})
                    process.wait(timeout=10)
                    return payload
            return None
        finally:
            if process.poll() is None:
                process.terminate()
