#!/usr/bin/env python3
"""Standalone VPS service supervisor for git-shadow.

The service keeps the edge executor alive behind a Unix socket. Plans and
Shadow contents travel through the active SSH stream; no request queue file is
written to disk. Durable job state remains owned by the edge executor.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import pathlib
import queue
import signal
import socket
import sys
import threading
import time
from typing import Any, Dict, Optional


def load_edge_module() -> Any:
    directory = pathlib.Path(__file__).resolve().parent
    candidates = [directory / "git-shadow-edge-agent.py", directory / "edge_agent.py"]
    for candidate in candidates:
        if candidate.exists():
            spec = importlib.util.spec_from_file_location("git_shadow_edge_agent", str(candidate))
            if spec is None or spec.loader is None:
                break
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
    raise RuntimeError("git-shadow-edge-agent.py is not installed beside the service agent")


class QuietEdgeExecutor:
    """Load EdgeExecutor without writing protocol records to service stdout."""

    def __init__(self, module: Any, state_root: str, subscribers: Dict[str, list], subscriber_lock: threading.Lock):
        self.module = module
        base = module.EdgeExecutor
        subscribers_ref = subscribers
        lock_ref = subscriber_lock

        class Executor(base):
            def emit_control(self, payload: Dict[str, Any]) -> None:
                return

            def emit_event(self, job_id: str, event: str, **data: Any) -> Dict[str, Any]:
                record = self._jobs.get(job_id)
                journal = record["journal"] if record is not None else module.EventJournal(self.state_root, job_id)
                payload = journal.append({"type": "event", "job_id": job_id, "event": event, **data})
                with lock_ref:
                    for subscriber in list(subscribers_ref.get(job_id, [])):
                        subscriber.put(payload)
                return payload

        self.instance = Executor(state_root)


class ServiceRuntime:
    def __init__(self, service_root: pathlib.Path, state_root: pathlib.Path, lease_ttl: int):
        self.service_root = service_root
        self.state_root = state_root
        self.lease_ttl = max(1, int(lease_ttl))
        self.socket_path = service_root / "service.sock"
        self.pid_path = service_root / "pid"
        self.lease_path = service_root / "lease"
        self.state_path = service_root / "state.json"
        self.stop_event = threading.Event()
        self.subscribers: Dict[str, list] = {}
        self.subscriber_lock = threading.Lock()
        self.module = load_edge_module()
        self.executor = QuietEdgeExecutor(
            self.module,
            str(state_root),
            self.subscribers,
            self.subscriber_lock,
        ).instance
        self._reconcile_interrupted_jobs()

    def _reconcile_interrupted_jobs(self) -> None:
        """Turn in-flight jobs into explicit failures after a service restart.

        Requests are deliberately redacted on disk, so silently replaying a
        Shadow payload after a crash would be unsafe and could duplicate a
        CloudCLI Session.  Preserve the journal and make the interruption
        visible instead; the local controller can submit a deliberate retry.
        """
        runs_root = self.state_root / "runs"
        if not runs_root.exists():
            return
        for run_dir in sorted(runs_root.iterdir()):
            if not run_dir.is_dir():
                continue
            state_path = run_dir / "state.json"
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if state.get("status") not in ("accepted", "running"):
                continue
            job_id = run_dir.name
            journal = self.module.EventJournal(self.state_root, job_id)
            terminal = [event for event in journal.replay(0) if event.get("event") in ("job.completed", "job.failed")]
            if terminal:
                continue
            error = "service restarted before job completed"
            self.executor.emit_event(job_id, "job.failed", error=error, interrupted=True)
            state.update(
                {
                    "status": "interrupted",
                    "error": error,
                    "interrupted": True,
                    "updated_at": self.module.now_iso(),
                }
            )
            journal.save_state(state)

    def _save_state(self, status: str, **data: Any) -> None:
        if status == "running":
            try:
                lease_remaining = max(0, int(self.lease_ttl - (time.time() - self.lease_path.stat().st_mtime)))
            except FileNotFoundError:
                lease_remaining = 0
        else:
            lease_remaining = 0
        payload = {
            "service": "git-shadow",
            "version": 1,
            "status": status,
            "pid": os.getpid() if status == "running" else None,
            "lease_ttl": self.lease_ttl,
            "lease_remaining": lease_remaining,
            "updated_at": int(time.time()),
            **data,
        }
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
        os.replace(str(temporary), str(self.state_path))
        try:
            os.chmod(self.state_path, 0o600)
        except OSError:
            pass

    def _lease_expired(self) -> bool:
        try:
            age = time.time() - self.lease_path.stat().st_mtime
        except FileNotFoundError:
            return True
        return age > self.lease_ttl

    def _handle_connection(self, connection: socket.socket) -> None:
        job_id = ""
        subscriber_queue: Optional[queue.Queue] = None
        last_seq = 0
        try:
            with connection:
                stream = connection.makefile("rwb")
                line = stream.readline()
                if not line:
                    return
                request = json.loads(line.decode("utf-8"))
                if not isinstance(request, dict) or request.get("type") != "submit":
                    raise ValueError("service socket accepts submit requests only")
                job_id = str(request.get("job_id") or "")
                if not job_id:
                    raise ValueError("job_id is required")
                subscriber_queue = queue.Queue()
                with self.subscriber_lock:
                    self.subscribers.setdefault(job_id, []).append(subscriber_queue)
                self.executor.submit(request)
                stream.write(json.dumps({"type": "accepted", "job_id": job_id}).encode("utf-8") + b"\n")
                stream.flush()
                journal = self.module.EventJournal(self.state_root, job_id)
                for event in journal.replay(0):
                    stream.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n")
                    stream.flush()
                    last_seq = max(last_seq, int(event.get("seq", 0)))
                    if event.get("event") in ("job.completed", "job.failed"):
                        return
                while not self.stop_event.is_set():
                    try:
                        event = subscriber_queue.get(timeout=1)
                    except queue.Empty:
                        continue
                    if int(event.get("seq", 0)) <= last_seq:
                        continue
                    last_seq = int(event.get("seq", 0))
                    stream.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n")
                    stream.flush()
                    if event.get("event") in ("job.completed", "job.failed"):
                        return
        except Exception as exc:
            try:
                connection.sendall(json.dumps({"type": "error", "error": str(exc)[-2000:]}).encode("utf-8") + b"\n")
            except OSError:
                pass
        finally:
            if job_id:
                with self.subscriber_lock:
                    subscribers = self.subscribers.get(job_id, [])
                    if subscriber_queue in subscribers:
                        subscribers.remove(subscriber_queue)
                    if not subscribers:
                        self.subscribers.pop(job_id, None)

    def serve(self) -> int:
        self.service_root.mkdir(parents=True, exist_ok=True)
        self.state_root.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.service_root, 0o700)
            os.chmod(self.state_root, 0o700)
        except OSError:
            pass
        try:
            self.socket_path.unlink()
        except FileNotFoundError:
            pass
        self.pid_path.write_text(str(os.getpid()) + "\n", encoding="utf-8")
        self.lease_path.touch()
        self._save_state("running", lease_ttl=self.lease_ttl)

        def stop_handler(signum: int, frame: Any) -> None:
            self.stop_event.set()

        signal.signal(signal.SIGTERM, stop_handler)
        signal.signal(signal.SIGINT, stop_handler)
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(self.socket_path))
        os.chmod(self.socket_path, 0o600)
        server.listen(8)
        server.settimeout(1.0)
        try:
            while not self.stop_event.is_set():
                if self._lease_expired():
                    self._save_state("stopped", reason="lease_expired")
                    break
                self._save_state("running", lease_ttl=self.lease_ttl)
                try:
                    connection, _ = server.accept()
                except socket.timeout:
                    continue
                threading.Thread(target=self._handle_connection, args=(connection,), daemon=True).start()
        finally:
            server.close()
            try:
                self.socket_path.unlink()
            except FileNotFoundError:
                pass
            try:
                self.pid_path.unlink()
            except FileNotFoundError:
                pass
            self._save_state("stopped", reason="requested" if self.stop_event.is_set() else "lease_expired")
            self.executor._stop.set()
            self.executor._lease_thread.join(timeout=2)
        return 0


def send_request(socket_path: str) -> int:
    request = sys.stdin.buffer.readline()
    if not request:
        return 1
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.connect(os.path.expanduser(socket_path))
        connection.sendall(request)
        connection.shutdown(socket.SHUT_WR)
        while True:
            payload = connection.recv(64 * 1024)
            if not payload:
                break
            sys.stdout.buffer.write(payload)
            sys.stdout.buffer.flush()
    return 0


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="git-shadow VPS service agent")
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--send", action="store_true")
    parser.add_argument("--service-root", required=True)
    parser.add_argument("--state-dir", default=None)
    parser.add_argument("--lease-ttl", type=int, default=600)
    options = parser.parse_args(argv)
    service_root = pathlib.Path(options.service_root).expanduser().resolve()
    if options.send:
        return send_request(str(service_root / "service.sock"))
    if not options.serve:
        parser.error("--serve or --send is required")
    home = pathlib.Path.home().resolve()
    state_root = pathlib.Path(options.state_dir or home / ".local/share/git-shadow").expanduser().resolve()
    try:
        service_root.relative_to(home)
        state_root.relative_to(home)
    except ValueError:
        parser.error("service and state paths must remain under the remote home")
    return ServiceRuntime(service_root, state_root, options.lease_ttl).serve()


if __name__ == "__main__":
    raise SystemExit(main())
