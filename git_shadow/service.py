"""SSH client for the lease-protected VPS resident service.

The service is a long-lived edge executor behind a remote Unix socket.  SSH
remains the control boundary: plans, Shadow bytes, and live events never use
an on-disk request queue on the VPS.
"""

from __future__ import annotations

import base64
import hashlib
import json
import pathlib
import shlex
import subprocess
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from .edge import EdgeClient, EventHandler, clean_ssh_args, output_text
from .shadow_sync import project_key
from .utils import log_success, log_warn


class ServiceClient(EdgeClient):
    """Manage and submit jobs to the VPS resident service for one project."""

    remote_service_agent_path = "$HOME/.local/share/git-shadow/bin/git-shadow-service-agent.py"
    remote_service_state_root = "$HOME/.local/share/git-shadow/services"
    remote_state_root = "$HOME/.local/share/git-shadow"

    def __init__(self, remote_host: str, project_root: str, python_executable: str = "python3"):
        super().__init__(remote_host, python_executable=python_executable)
        self.project_id = project_key(project_root)
        self.service_root = self.remote_service_state_root + "/" + self.project_id
        self._lease_ttl = 600

    def _service_send_command(self) -> str:
        return (
            "%s %s --send --service-root \"%s\""
            % (shlex.quote(self.python_executable), self.remote_service_agent_path, self.service_root)
        )

    def _service_status_command(self) -> str:
        state_path = self.service_root + "/state.json"
        return (
            "if [ -f \"%s\" ]; then cat \"%s\"; "
            "else printf '{\"status\":\"absent\"}\\n'; fi"
            % (state_path, state_path)
        )

    @staticmethod
    def _json_output(output: Any) -> Dict[str, Any]:
        for line in reversed(output_text(output).splitlines()):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                return value
        return {"status": "unknown"}

    def _upload_file(self, source_path: pathlib.Path, remote_path: str) -> bool:
        local_digest = hashlib.sha256(source_path.read_bytes()).hexdigest()
        check = self._run_ssh(
            "sha256sum \"%s\" 2>/dev/null | awk '{print $1}'" % remote_path
        )
        if output_text(check.stdout).strip() == local_digest:
            return True
        payload = base64.b64encode(source_path.read_bytes())
        command = (
            "mkdir -p ~/.local/share/git-shadow/bin && "
            "base64 -d > \"%s\" && chmod 700 \"%s\""
            % (remote_path, remote_path)
        )
        uploaded = self._run_ssh(command, input_data=payload)
        if uploaded.returncode != 0:
            message = output_text(uploaded.stderr).strip()
            log_warn("VPS 常驻服务安装失败: %s" % (message or "SSH transfer failed"))
            return False
        return True

    def ensure_installed(self) -> bool:
        if not super().ensure_installed():
            return False
        source_path = pathlib.Path(__file__).with_name("service_agent.py")
        if not self._upload_file(source_path, self.remote_service_agent_path):
            return False
        log_success("VPS 常驻服务已就绪")
        return True

    def _touch_lease(self) -> bool:
        result = self._run_ssh("touch \"%s/lease\"" % self.service_root)
        return result.returncode == 0

    def load(self, lease_ttl: int = 600) -> Dict[str, Any]:
        """Start or reuse the project service and return its state."""
        self._lease_ttl = max(1, int(lease_ttl))
        service_log = self.service_root + "/service.log"
        pid_path = self.service_root + "/pid"
        socket_path = self.service_root + "/service.sock"
        lease_path = self.service_root + "/lease"
        command = (
            "mkdir -p \"%s\"; "
            "if [ -f \"%s\" ] && kill -0 \"$(cat \"%s\")\" 2>/dev/null; then "
            "touch \"%s\"; "
            "else rm -f \"%s\" \"%s\" \"%s\"; "
            "nohup %s \"%s\" --serve --service-root \"%s\" --state-dir \"%s\" --lease-ttl %s "
            ">\"%s\" 2>&1 </dev/null & "
            "fi; "
            "for i in $(seq 1 50); do [ -S \"%s\" ] && break; sleep 0.1; done; "
            "if [ -f \"%s\" ]; then cat \"%s\"; else printf '{\"status\":\"starting\"}\\n'; fi"
            % (
                self.service_root,
                pid_path,
                pid_path,
                lease_path,
                socket_path,
                pid_path,
                lease_path,
                shlex.quote(self.python_executable),
                self.remote_service_agent_path,
                self.service_root,
                self.remote_state_root,
                self._lease_ttl,
                service_log,
                socket_path,
                self.service_root + "/state.json",
                self.service_root + "/state.json",
            )
        )
        result = self._run_ssh(command)
        if result.returncode != 0:
            raise RuntimeError(output_text(result.stderr).strip() or "unable to load VPS service")
        return self._json_output(result.stdout)

    def status(self) -> Dict[str, Any]:
        result = self._run_ssh(self._service_status_command())
        if result.returncode != 0:
            return {"status": "unreachable", "error": output_text(result.stderr).strip()}
        return self._json_output(result.stdout)

    def unload(self) -> Dict[str, Any]:
        pid_path = self.service_root + "/pid"
        command = (
            "if [ -f \"%s\" ]; then kill -TERM \"$(cat \"%s\")\" 2>/dev/null || true; "
            "for i in $(seq 1 30); do [ ! -f \"%s\" ] && break; sleep 0.1; done; fi; "
            "if [ -f \"%s\" ]; then cat \"%s\"; else printf '{\"status\":\"stopped\"}\\n'; fi"
            % (pid_path, pid_path, pid_path, self.service_root + "/state.json", self.service_root + "/state.json")
        )
        result = self._run_ssh(command)
        if result.returncode != 0:
            raise RuntimeError(output_text(result.stderr).strip() or "unable to unload VPS service")
        return self._json_output(result.stdout)

    def open(self) -> subprocess.Popen:
        self._touch_lease()
        return subprocess.Popen(
            clean_ssh_args(self.remote_host, self._service_send_command()),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )

    def submit(
        self,
        plan: Dict[str, Any],
        on_event: Optional[EventHandler] = None,
        wait_for_completion: bool = True,
    ) -> Dict[str, Any]:
        """Submit through the resident service and stream its socket events."""
        if not self._touch_lease():
            raise RuntimeError("VPS 常驻服务未加载，请先执行 service load")
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
                self._touch_lease()

        heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
        heartbeat_thread.start()
        try:
            if process.stdin is None:
                raise RuntimeError("VPS service stdin is unavailable")
            process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
            process.stdin.flush()
            process.stdin.close()
            result: Dict[str, Any] = {"job_id": job_id, "status": "accepted"}
            if not wait_for_completion:
                return result
            if process.stdout is None:
                raise RuntimeError("VPS service stdout is unavailable")
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
                    break
            if result["status"] == "failed":
                raise RuntimeError(str(result.get("error")))
            process.wait(timeout=10)
            if process.returncode not in (0, None) and stderr_lines:
                raise RuntimeError("remote service exited: %s" % stderr_lines[-1])
            return result
        finally:
            heartbeat_stop.set()
            if process.poll() is None:
                process.terminate()
