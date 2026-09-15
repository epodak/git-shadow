#!/usr/bin/env python3
"""git-shadow VPS edge executor.

This file is deliberately standalone. ``git shadow edge install`` uploads it
to the VPS, where it can run without installing the local package. The wire
protocol is newline-delimited JSON (JSONL) over a clean SSH channel.
"""

from __future__ import annotations

import argparse
import base64
import datetime as _datetime
import errno
import json
import os
import pathlib
import re
import signal
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Iterable, List, Optional, Tuple


JOB_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
DEFAULT_TTL = 600


class EdgeError(RuntimeError):
    """A user-visible, safe-to-serialize executor error."""


def now_iso() -> str:
    return _datetime.datetime.now(_datetime.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def safe_job_id(value: Any) -> str:
    job_id = str(value or "")
    if not JOB_ID_RE.fullmatch(job_id):
        raise EdgeError("invalid job_id")
    return job_id


def redact(value: Any) -> Any:
    """Remove secret-bearing request fields before writing durable state."""
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if lowered.endswith("_b64") or "token" in lowered or "secret" in lowered:
                result[key] = "<redacted>"
            else:
                result[key] = redact(item)
        return result
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def ensure_inside(path_value: str, root: pathlib.Path) -> pathlib.Path:
    """Resolve a path and prevent the executor escaping the user's home."""
    candidate = pathlib.Path(path_value).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise EdgeError("path is outside the remote home directory") from exc
    return resolved


def run_checked(
    argv: List[str],
    cwd: Optional[pathlib.Path] = None,
    input_data: Optional[bytes] = None,
    timeout: int = 900,
) -> Tuple[str, str]:
    try:
        completed = subprocess.run(
            argv,
            cwd=str(cwd) if cwd else None,
            input=input_data,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise EdgeError("executable not found: %s" % argv[0]) from exc
    except subprocess.TimeoutExpired as exc:
        raise EdgeError("command timed out: %s" % argv[0]) from exc

    stdout = completed.stdout.decode("utf-8", "replace") if isinstance(completed.stdout, bytes) else (completed.stdout or "")
    stderr = completed.stderr.decode("utf-8", "replace") if isinstance(completed.stderr, bytes) else (completed.stderr or "")
    if completed.returncode != 0:
        detail = (stderr.strip() or stdout.strip() or "exit code %s" % completed.returncode)[-4000:]
        raise EdgeError("%s failed: %s" % (argv[0], detail))
    return stdout, stderr


class EventJournal:
    def __init__(self, state_root: pathlib.Path, job_id: str):
        self.directory = state_root / "runs" / job_id
        self.events_path = self.directory / "events.ndjson"
        self.state_path = self.directory / "state.json"
        self.request_path = self.directory / "request.json"
        self.directory.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._seq = self._read_last_seq()

    def _read_last_seq(self) -> int:
        if not self.events_path.exists():
            return 0
        last = 0
        try:
            with self.events_path.open("r", encoding="utf-8") as stream:
                for line in stream:
                    try:
                        last = max(last, int(json.loads(line).get("seq", 0)))
                    except (ValueError, TypeError, json.JSONDecodeError):
                        continue
        except OSError:
            return 0
        return last

    def save_request(self, request: Dict[str, Any]) -> None:
        self.request_path.write_text(json_dump(redact(request)) + "\n", encoding="utf-8")

    def save_state(self, state: Dict[str, Any]) -> None:
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json_dump(redact(state)) + "\n", encoding="utf-8")
        os.replace(str(temporary), str(self.state_path))

    def append(self, event: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            self._seq += 1
            payload = {"seq": self._seq, "at": now_iso(), **event}
            with self.events_path.open("a", encoding="utf-8") as stream:
                stream.write(json_dump(payload) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            return payload

    def replay(self, after_seq: int) -> Iterable[Dict[str, Any]]:
        if not self.events_path.exists():
            return []
        output = []
        with self.events_path.open("r", encoding="utf-8") as stream:
            for line in stream:
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if int(payload.get("seq", 0)) > after_seq:
                    payload["replay"] = True
                    output.append(payload)
        return output


class EdgeExecutor:
    def __init__(self, state_root: Optional[str] = None):
        self.home = pathlib.Path.home().resolve()
        self.state_root = ensure_inside(
            state_root or os.environ.get("GIT_SHADOW_STATE_DIR", str(self.home / ".local/share/git-shadow")),
            self.home,
        )
        self.state_root.mkdir(parents=True, exist_ok=True)
        self._write_lock = threading.Lock()
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._stop = threading.Event()
        self._lease_thread = threading.Thread(target=self._lease_loop, daemon=True)
        self._lease_thread.start()

    def _lease_loop(self) -> None:
        while not self._stop.wait(1.0):
            now = time.time()
            for job_id, record in list(self._jobs.items()):
                if record.get("status") not in ("accepted", "running"):
                    continue
                lease_path = record.get("lease_path")
                ttl = int(record.get("lease_ttl", DEFAULT_TTL))
                if not lease_path or not os.path.exists(lease_path):
                    continue
                try:
                    age = now - os.path.getmtime(lease_path)
                except OSError:
                    continue
                if age > ttl and not record["cancel_event"].is_set():
                    record["cancel_event"].set()
                    self.emit_event(job_id, "lease.expired", ttl=ttl, age=int(age))

    def emit_control(self, payload: Dict[str, Any]) -> None:
        with self._write_lock:
            sys.stdout.write(json_dump(payload) + "\n")
            sys.stdout.flush()

    def emit_event(self, job_id: str, event: str, **data: Any) -> Dict[str, Any]:
        record = self._jobs.get(job_id)
        if record is None:
            journal = EventJournal(self.state_root, job_id)
        else:
            journal = record["journal"]
        payload = journal.append({"type": "event", "job_id": job_id, "event": event, **data})
        with self._write_lock:
            sys.stdout.write(json_dump(payload) + "\n")
            sys.stdout.flush()
        return payload

    def _state(self, job_id: str, status: str, **data: Any) -> Dict[str, Any]:
        record = self._jobs.get(job_id)
        state = {"job_id": job_id, "status": status, "updated_at": now_iso(), **data}
        if record:
            record["journal"].save_state(state)
        else:
            EventJournal(self.state_root, job_id).save_state(state)
        return state

    def _validate_cwd(self, value: Optional[str]) -> Optional[pathlib.Path]:
        if not value:
            return None
        return ensure_inside(value, self.home)

    def _run_streaming(self, job_id: str, step_id: str, step: Dict[str, Any]) -> None:
        argv = step.get("argv")
        if not isinstance(argv, list) or not argv or not all(isinstance(item, str) for item in argv):
            raise EdgeError("exec step requires a non-empty argv list")
        cwd = self._validate_cwd(step.get("cwd"))
        if cwd and not cwd.exists():
            raise EdgeError("working directory does not exist: %s" % cwd)
        timeout = int(step.get("timeout", 900))
        environment = os.environ.copy()
        supplied_env = step.get("env", {})
        if supplied_env:
            if not isinstance(supplied_env, dict):
                raise EdgeError("step env must be an object")
            for key, value in supplied_env.items():
                if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(key)):
                    raise EdgeError("invalid environment variable name")
                environment[str(key)] = str(value)

        try:
            process = subprocess.Popen(
                argv,
                cwd=str(cwd) if cwd else None,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                start_new_session=True,
            )
        except FileNotFoundError as exc:
            raise EdgeError("executable not found: %s" % argv[0]) from exc

        def forward(stream: Any, channel: str) -> None:
            for line in iter(stream.readline, ""):
                message = line.rstrip("\n")
                if message:
                    self.emit_event(job_id, "output", step=step_id, channel=channel, message=message)
            stream.close()

        stdout_thread = threading.Thread(target=forward, args=(process.stdout, "stdout"), daemon=True)
        stderr_thread = threading.Thread(target=forward, args=(process.stderr, "stderr"), daemon=True)
        stdout_thread.start()
        stderr_thread.start()

        def terminate_and_reap() -> None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except OSError:
                try:
                    process.kill()
                except OSError:
                    pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    process.kill()
                except OSError:
                    pass
                process.wait(timeout=5)

        try:
            deadline = time.time() + timeout
            while process.poll() is None:
                record = self._jobs.get(job_id)
                if record and record["cancel_event"].is_set():
                    terminate_and_reap()
                    raise EdgeError("step cancelled because the lease expired")
                if time.time() > deadline:
                    raise subprocess.TimeoutExpired(argv, timeout)
                time.sleep(0.1)
            return_code = process.returncode
        except subprocess.TimeoutExpired as exc:
            terminate_and_reap()
            raise EdgeError("step timed out after %ss" % timeout) from exc
        stdout_thread.join(timeout=2)
        stderr_thread.join(timeout=2)
        if return_code != 0:
            raise EdgeError("command exited with code %s" % return_code)

    def _safe_extract(self, payload: bytes, target: pathlib.Path) -> None:
        target.mkdir(parents=True, exist_ok=True)
        with tarfile.open(fileobj=__import__("io").BytesIO(payload), mode="r:gz") as archive:
            for member in archive.getmembers():
                if member.issym() or member.islnk():
                    raise EdgeError("archive links are not allowed")
                member_path = (target / member.name).resolve()
                try:
                    member_path.relative_to(target.resolve())
                except ValueError as exc:
                    raise EdgeError("archive contains an unsafe path") from exc
            archive.extractall(str(target))

    def _workspace_prepare(self, step: Dict[str, Any]) -> None:
        target = ensure_inside(str(step.get("target", "")), self.home)
        remote_url = str(step.get("remote_url") or "").strip()
        branch = str(step.get("branch") or "main")
        pull = bool(step.get("pull"))
        target.parent.mkdir(parents=True, exist_ok=True)

        if remote_url:
            if not target.exists():
                run_checked(["git", "clone", "--", remote_url, str(target)], timeout=1800)
            elif not (target / ".git").exists():
                raise EdgeError("target exists but is not a Git repository: %s" % target)
            run_checked(["git", "fetch", "origin"], cwd=target, timeout=900)
            run_checked(["git", "checkout", branch], cwd=target, timeout=900)
            if pull:
                run_checked(["git", "pull", "origin", branch], cwd=target, timeout=900)
            return

        target.mkdir(parents=True, exist_ok=True)
        if not (target / ".git").exists():
            try:
                run_checked(["git", "init", "-b", branch], cwd=target)
            except EdgeError:
                run_checked(["git", "init"], cwd=target)
        archive_b64 = step.get("archive_b64")
        if archive_b64:
            self._safe_extract(base64.b64decode(str(archive_b64)), target)
            commit = str(step.get("commit") or "")
            if commit:
                (target / ".git/SHADOW_COMMIT").write_text(commit + "\n", encoding="utf-8")

    def _apply_patch(self, step: Dict[str, Any]) -> None:
        cwd = self._validate_cwd(str(step.get("cwd") or ""))
        if not cwd or not cwd.exists():
            raise EdgeError("patch working directory does not exist")
        patch_b64 = step.get("patch_b64")
        if not patch_b64:
            return
        run_checked(["git", "apply", "-"], cwd=cwd, input_data=base64.b64decode(str(patch_b64)), timeout=900)

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        bearer = os.environ.get("GIT_SHADOW_CLOUDCLI_TOKEN", "").strip()
        api_key = os.environ.get("GIT_SHADOW_CLOUDCLI_API_KEY", "").strip()
        if bearer:
            headers["Authorization"] = "Bearer " + bearer
        if api_key:
            headers["X-API-KEY"] = api_key
        return headers

    def _http_json(self, url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                body = response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[-1000:]
            raise EdgeError("CloudCLI API returned HTTP %s: %s" % (exc.code, detail)) from exc
        except urllib.error.URLError as exc:
            raise EdgeError("CloudCLI API unavailable: %s" % exc.reason) from exc
        try:
            parsed = json.loads(body) if body else {}
        except json.JSONDecodeError as exc:
            raise EdgeError("CloudCLI API returned invalid JSON") from exc
        if not isinstance(parsed, dict):
            raise EdgeError("CloudCLI API returned an invalid object")
        return parsed

    def _cloudcli_session(self, job_id: str, step: Dict[str, Any]) -> Dict[str, Any]:
        base_url = str(
            step.get("base_url")
            or os.environ.get("GIT_SHADOW_CLOUDCLI_BASE_URL")
            or "http://127.0.0.1:3001"
        ).rstrip("/")
        public_url = str(step.get("public_url") or base_url).rstrip("/")
        project_path = str(step.get("project_path") or "")
        provider = str(step.get("provider") or "").strip()
        if not project_path or not provider:
            raise EdgeError("CloudCLI session requires project_path and provider")

        self._http_json(base_url + "/api/projects/create-project", {"path": project_path})
        session_response = self._http_json(
            base_url + "/api/providers/sessions",
            {
                "provider": provider,
                "projectPath": project_path,
                "initialMessage": str(step.get("initial_message") or ""),
            },
        )
        session_id = session_response.get("sessionId")
        if not session_id and isinstance(session_response.get("data"), dict):
            session_id = session_response["data"].get("sessionId")
        if not session_id:
            raise EdgeError("CloudCLI API did not return sessionId")
        url = public_url + "/session/" + str(session_id)
        self.emit_event(job_id, "session.ready", session_id=str(session_id), provider=provider, project_path=project_path, url=url)
        return {"session_id": str(session_id), "url": url, "provider": provider, "project_path": project_path}

    def _execute_step(self, job_id: str, step: Dict[str, Any]) -> Dict[str, Any]:
        action = str(step.get("action") or "")
        if action == "exec":
            self._run_streaming(job_id, str(step.get("id") or "step"), step)
            return {}
        if action == "workspace.prepare":
            self._workspace_prepare(step)
            return {}
        if action == "shadow.extract":
            target = ensure_inside(str(step.get("target") or ""), self.home)
            payload = step.get("archive_b64")
            if payload:
                self._safe_extract(base64.b64decode(str(payload)), target)
            return {}
        if action == "patch.apply":
            self._apply_patch(step)
            return {}
        if action == "cloudcli.session":
            return self._cloudcli_session(job_id, step)
        raise EdgeError("unsupported action: %s" % action)

    def _run_job(self, job_id: str, request: Dict[str, Any]) -> None:
        record = self._jobs[job_id]
        journal = record["journal"]
        steps = request.get("steps")
        if not isinstance(steps, list):
            self.emit_event(job_id, "job.failed", error="steps must be an array")
            self._state(job_id, "failed", error="steps must be an array")
            return
        self._state(job_id, "running", step_count=len(steps))
        record["status"] = "running"
        self.emit_event(job_id, "job.started", step_count=len(steps))
        try:
            for index, raw_step in enumerate(steps, start=1):
                if record["cancel_event"].is_set():
                    raise EdgeError("job cancelled because the lease expired")
                if not isinstance(raw_step, dict):
                    raise EdgeError("step %s is not an object" % index)
                step = dict(raw_step)
                step_id = str(step.get("id") or "step-%s" % index)
                self.emit_event(job_id, "step.started", step=step_id, action=step.get("action"), index=index)
                result = self._execute_step(job_id, step)
                self.emit_event(job_id, "step.succeeded", step=step_id, result=result)
            self._state(job_id, "completed")
            record["status"] = "completed"
            try:
                os.unlink(record["lease_path"])
            except OSError:
                pass
            self.emit_event(job_id, "job.completed")
        except Exception as exc:
            message = str(exc)[-4000:]
            self._state(job_id, "failed", error=message)
            record["status"] = "failed"
            try:
                os.unlink(record["lease_path"])
            except OSError:
                pass
            self.emit_event(job_id, "job.failed", error=message)

    def submit(self, request: Dict[str, Any]) -> None:
        job_id = safe_job_id(request.get("job_id"))
        if job_id in self._jobs:
            self.emit_control({"type": "accepted", "job_id": job_id, "reused": True})
            return
        journal = EventJournal(self.state_root, job_id)
        journal.save_request(request)
        lease_ttl = max(1, int(request.get("lease_ttl", DEFAULT_TTL)))
        lease_path = journal.directory / "lease"
        lease_path.touch()
        record = {
            "journal": journal,
            "request": request,
            "started": time.time(),
            "status": "accepted",
            "lease_ttl": lease_ttl,
            "lease_path": str(lease_path),
            "cancel_event": threading.Event(),
        }
        self._jobs[job_id] = record
        journal.save_state({"job_id": job_id, "status": "accepted", "created_at": now_iso()})
        self.emit_control({"type": "accepted", "job_id": job_id})
        worker = threading.Thread(target=self._run_job, args=(job_id, request), daemon=True)
        record["worker"] = worker
        worker.start()

    def resume(self, message: Dict[str, Any]) -> None:
        job_id = safe_job_id(message.get("job_id"))
        after_seq = int(message.get("after_seq", 0))
        journal = EventJournal(self.state_root, job_id)
        self.emit_control({"type": "replay.begin", "job_id": job_id, "after_seq": after_seq})
        for event in journal.replay(after_seq):
            self.emit_control(event)
        self.emit_control({"type": "replay.end", "job_id": job_id, "last_seq": journal._seq})

    def status(self, message: Dict[str, Any]) -> None:
        job_id = safe_job_id(message.get("job_id"))
        state_path = self.state_root / "runs" / job_id / "state.json"
        if not state_path.exists():
            self.emit_control({"type": "status", "job_id": job_id, "found": False})
            return
        self.emit_control({"type": "status", "job_id": job_id, "found": True, "state": json.loads(state_path.read_text(encoding="utf-8"))})

    def serve(self) -> int:
        self.emit_control({"type": "ready", "protocol": 1, "pid": os.getpid()})
        active_threads: List[threading.Thread] = []
        try:
            for line in sys.stdin:
                if not line.strip():
                    continue
                try:
                    message = json.loads(line)
                    if not isinstance(message, dict):
                        raise EdgeError("message must be an object")
                    kind = message.get("type")
                    if kind == "submit":
                        self.submit(message)
                    elif kind == "resume":
                        self.resume(message)
                    elif kind == "status":
                        self.status(message)
                    elif kind == "heartbeat":
                        job_id = message.get("job_id")
                        record = self._jobs.get(str(job_id)) if job_id else None
                        if record and record.get("lease_path"):
                            pathlib.Path(record["lease_path"]).touch()
                        self.emit_control({"type": "heartbeat.ack", "job_id": job_id, "at": now_iso()})
                    elif kind == "shutdown":
                        break
                    else:
                        self.emit_control({"type": "error", "error": "unsupported message type"})
                except Exception as exc:
                    self.emit_control({"type": "error", "error": str(exc)[-4000:]})
        finally:
            active_threads = [record.get("worker") for record in self._jobs.values() if record.get("worker")]
            for worker in active_threads:
                if worker:
                    worker.join()
            self._stop.set()
            self._lease_thread.join(timeout=2)
        return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="git-shadow VPS edge executor")
    parser.add_argument("--rpc", action="store_true", help="serve JSONL RPC on stdin/stdout")
    parser.add_argument("--state-dir", default=None)
    options = parser.parse_args(argv)
    if not options.rpc:
        parser.error("--rpc is required")
    return EdgeExecutor(options.state_dir).serve()


if __name__ == "__main__":
    raise SystemExit(main())
