from __future__ import annotations

import json
import os
import socket
import subprocess
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib import error, request

from .delegated_generator import DEFAULT_OPEN_CLAUDE_SOURCE, generate_delegated_agent_project
from .generator import safe_project_name


@dataclass
class DelegatedPlaygroundProcess:
    delegated_id: str
    session_id: str
    project_name: str
    project_root: Path
    backend_root: Path
    port: int
    process: subprocess.Popen
    fake_runner: bool = True
    created_at: float = field(default_factory=time.time)
    last_result: dict[str, Any] = field(default_factory=dict)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def status(self) -> str:
        return "running" if self.process.poll() is None else "stopped"

    def to_summary(self) -> dict[str, Any]:
        return {
            "delegated_id": self.delegated_id,
            "session_id": self.session_id,
            "project_name": self.project_name,
            "project_root": str(self.project_root),
            "backend_root": str(self.backend_root),
            "port": self.port,
            "base_url": self.base_url,
            "status": self.status(),
            "pid": self.process.pid,
            "created_at": self.created_at,
            "last_result": self.last_result,
            "endpoints": {
                "config": f"{self.base_url}/api/config",
                "jobs": f"{self.base_url}/api/jobs",
                "health": f"{self.base_url}/health",
                "ready": f"{self.base_url}/ready",
            },
        }


class _ExternalProcess:
    def __init__(self, port: int) -> None:
        self.pid = _pid_listening_on_port(port)
        self.stdout = None
        self._port = port

    def poll(self) -> int | None:
        if self.pid:
            try:
                os.kill(self.pid, 0)
                return None
            except OSError:
                return 1
        return None if _is_http_ok(f"http://127.0.0.1:{self._port}/health") else 1

    def terminate(self) -> None:
        if self.pid:
            try:
                os.kill(self.pid, 15)
            except OSError:
                pass
        return None

    def wait(self, timeout: float | None = None) -> int:
        deadline = time.time() + float(timeout or 0)
        while self.poll() is None and time.time() < deadline:
            time.sleep(0.1)
        return 0

    def kill(self) -> None:
        if self.pid:
            try:
                os.kill(self.pid, 9)
            except OSError:
                pass
        return None


class DelegatedPlaygroundManager:
    def __init__(self, root_dir: Path) -> None:
        self.root_dir = root_dir
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self._items: dict[str, DelegatedPlaygroundProcess] = {}

    def list_items(self) -> list[dict[str, Any]]:
        self._drop_stopped()
        self._load_disk_summaries()
        return [item.to_summary() for item in sorted(self._items.values(), key=lambda value: value.created_at, reverse=True)]

    def get(self, delegated_id: str) -> DelegatedPlaygroundProcess:
        item = self._items.get(delegated_id)
        if not item:
            raise KeyError(delegated_id)
        return item

    def start(
        self,
        session: dict[str, Any],
        *,
        project_name: str = "",
        agent_name: str = "",
        agent_goal: str = "",
        default_task: str = "",
        open_claude_source: str | Path = DEFAULT_OPEN_CLAUDE_SOURCE,
        fake_runner: bool = True,
    ) -> dict[str, Any]:
        self._drop_stopped()
        session_id = str(session.get("session_id") or "session")
        protocol = session.get("protocol") or {}
        project_name = safe_project_name(project_name or protocol.get("project_name") or session.get("title") or "apd-delegated-agent")
        delegated_id = f"{session_id[:8]}-{int(time.time() * 1000)}"
        root_parent = self.root_dir / delegated_id
        root_parent.mkdir(parents=True, exist_ok=True)

        zip_bytes, manifest = generate_delegated_agent_project(
            protocol=protocol,
            project_name=project_name,
            agent_name=agent_name or protocol.get("project_name") or session.get("title") or project_name,
            agent_goal=agent_goal or protocol.get("domain_summary") or "接收用户任务，委托内置 open_claude 执行，并返回过程与产物。",
            default_task=default_task or "请根据用户输入完成任务，并把最终结果写入 artifacts/report.md 和 artifacts/result.json。",
            open_claude_source=Path(open_claude_source or DEFAULT_OPEN_CLAUDE_SOURCE),
            bundle_open_claude=True,
        )
        zip_path = root_parent / "delegated-agent.zip"
        zip_path.write_bytes(zip_bytes)
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(root_parent)
        project_root = root_parent / project_name
        backend_root = project_root / "backend"
        port = _find_free_port()
        if port > 0 and _is_http_ok(f"http://127.0.0.1:{port}/health"):
            process = _ExternalProcess(port)
        else:
            if port <= 0:
                port = _find_free_port()
            process = self._spawn_process(project_root, backend_root, port, fake_runner=fake_runner)
        item = DelegatedPlaygroundProcess(
            delegated_id=delegated_id,
            session_id=session_id,
            project_name=project_name,
            project_root=project_root,
            backend_root=backend_root,
            port=port,
            process=process,
            fake_runner=fake_runner,
            last_result={"manifest": manifest},
        )
        self._items[delegated_id] = item
        self._save_state(item)
        wait_result = _wait_http(f"{item.base_url}/health", timeout=8)
        item.last_result = {"startup": wait_result, "manifest": manifest}
        self._save_state(item)
        if not wait_result.get("ok"):
            output = self.read_logs(delegated_id, limit=120)
            self.stop(delegated_id)
            raise RuntimeError(f"Delegated Agent 启动失败：{wait_result.get('error') or 'health check failed'}\n{output}")
        return item.to_summary()

    def stop(self, delegated_id: str) -> dict[str, Any]:
        item = self.get(delegated_id)
        if item.process.poll() is None:
            item.process.terminate()
            try:
                item.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                item.process.kill()
                item.process.wait(timeout=3)
        return item.to_summary()

    def restart(self, delegated_id: str) -> dict[str, Any]:
        item = self.get_or_restore(delegated_id)
        if item.process.poll() is None:
            item.process.terminate()
            try:
                item.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                item.process.kill()
                item.process.wait(timeout=3)
        item.process = self._spawn_process(item.project_root, item.backend_root, item.port, fake_runner=item.fake_runner)
        wait_result = _wait_http(f"{item.base_url}/health", timeout=8)
        item.last_result = {"restart": wait_result, "restarted_at": time.time()}
        self._save_state(item)
        if not wait_result.get("ok"):
            output = self.read_logs(delegated_id, limit=120)
            raise RuntimeError(f"Delegated Agent 重启失败：{wait_result.get('error') or 'health check failed'}\n{output}")
        return item.to_summary()

    def get_or_restore(self, delegated_id: str) -> DelegatedPlaygroundProcess:
        item = self._items.get(delegated_id)
        if item:
            return item
        return self._restore_from_disk(delegated_id)

    def create_job(self, delegated_id: str, message: str) -> dict[str, Any]:
        item = self.get_or_restore(delegated_id)
        result = _post_json(f"{item.base_url}/api/jobs", {"message": message}, timeout=15)
        item.last_result = {"create_job": result}
        return result

    def get_job(self, delegated_id: str, job_id: str) -> dict[str, Any]:
        item = self.get_or_restore(delegated_id)
        result = _get_json(f"{item.base_url}/api/jobs/{job_id}", timeout=12)
        item.last_result = {"job": result}
        return result

    def get_job_snapshot(self, delegated_id: str, job_id: str, *, event_limit: int = 160, log_limit: int = 12000) -> dict[str, Any]:
        item = self.get_or_restore(delegated_id)
        job_root = item.project_root / "data" / "jobs" / job_id
        job_path = job_root / "job.json"
        if not job_path.exists():
            raise FileNotFoundError(f"job not found: {job_id}")
        job = json.loads(job_path.read_text(encoding="utf-8"))
        artifacts_root = job_root / "artifacts"
        artifacts = []
        if artifacts_root.exists():
            for path in sorted(artifacts_root.rglob("*")):
                if path.is_file():
                    rel = path.relative_to(artifacts_root).as_posix()
                    artifacts.append({"name": rel, "path": rel, "size": path.stat().st_size})
        report_path = artifacts_root / "report.md"
        report = ""
        if report_path.exists():
            report = _read_text_tail(report_path, limit=log_limit)
        trace_root = job_root / "trace"
        return {
            "job": job,
            "events": _read_jsonl_tail(trace_root / "events.jsonl", limit=event_limit),
            "artifacts": artifacts,
            "report": report,
            "logs": {
                "stdout": _read_text_tail(trace_root / "stdout.log", limit=log_limit),
                "stderr": _read_text_tail(trace_root / "stderr.log", limit=log_limit),
            },
        }

    def list_jobs(self, delegated_id: str) -> dict[str, Any]:
        item = self.get_or_restore(delegated_id)
        result = _get_json(f"{item.base_url}/api/jobs", timeout=12)
        item.last_result = {"jobs": result}
        return result

    def events(self, delegated_id: str, job_id: str) -> dict[str, Any]:
        item = self.get_or_restore(delegated_id)
        return _get_json(f"{item.base_url}/api/jobs/{job_id}/events", timeout=12)

    def config(self, delegated_id: str) -> dict[str, Any]:
        item = self.get_or_restore(delegated_id)
        return _get_json(f"{item.base_url}/api/config", timeout=12)

    def artifacts(self, delegated_id: str, job_id: str) -> dict[str, Any]:
        item = self.get_or_restore(delegated_id)
        return _get_json(f"{item.base_url}/api/jobs/{job_id}/artifacts", timeout=12)

    def job_logs(self, delegated_id: str, job_id: str) -> dict[str, str]:
        item = self.get_or_restore(delegated_id)
        stdout = _get_text(f"{item.base_url}/api/jobs/{job_id}/logs/stdout", timeout=12)
        stderr = _get_text(f"{item.base_url}/api/jobs/{job_id}/logs/stderr", timeout=12)
        return {"stdout": stdout, "stderr": stderr}

    def artifact_text(self, delegated_id: str, job_id: str, name: str) -> str:
        item = self.get_or_restore(delegated_id)
        return _get_text(f"{item.base_url}/api/jobs/{job_id}/artifacts/{name}", timeout=12)

    def _spawn_process(self, project_root: Path, backend_root: Path, port: int, *, fake_runner: bool) -> subprocess.Popen:
        env = dict(os.environ)
        env["OPEN_CLAUDE_FAKE"] = "1" if fake_runner else "0"
        env["DATA_DIR"] = str((project_root / "data").resolve())
        return subprocess.Popen(
            ["python3", "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
            cwd=str(backend_root),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    def read_logs(self, delegated_id: str, limit: int = 120) -> str:
        item = self.get(delegated_id)
        lines: list[str] = []
        stream = item.process.stdout
        if stream:
            while True:
                try:
                    line = stream.readline()
                except Exception:
                    break
                if not line:
                    break
                lines.append(line.rstrip())
                if len(lines) >= limit:
                    break
        return "\n".join(lines[-limit:])

    def _save_state(self, item: DelegatedPlaygroundProcess) -> None:
        state_path = self.root_dir / item.delegated_id / "playground_state.json"
        old_state = _read_json(state_path)
        project_root = str(item.project_root)
        backend_root = str(item.backend_root)
        if project_root in {"", "."}:
            project_root = str(old_state.get("project_root") or "")
        if backend_root in {"", "."}:
            backend_root = str(old_state.get("backend_root") or "")
        payload = {
            "delegated_id": item.delegated_id,
            "session_id": item.session_id,
            "project_name": item.project_name,
            "project_root": project_root,
            "backend_root": backend_root,
            "port": item.port,
            "fake_runner": item.fake_runner,
            "created_at": item.created_at,
            "last_result": item.last_result,
        }
        state_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _restore_from_disk(self, delegated_id: str) -> DelegatedPlaygroundProcess:
        root_parent = self.root_dir / delegated_id
        if not root_parent.exists() or not root_parent.is_dir():
            raise KeyError(delegated_id)
        state = _read_json(root_parent / "playground_state.json")
        project_root_text = str(state.get("project_root") or "").strip()
        project_root = Path(project_root_text) if project_root_text else Path()
        if not project_root_text or project_root == Path(".") or not (project_root / "backend" / "app" / "main.py").exists():
            project_root = self._guess_project_root(root_parent)
        backend_root_text = str(state.get("backend_root") or "").strip()
        backend_root = Path(backend_root_text) if backend_root_text else Path()
        if not backend_root_text or backend_root == Path(".") or not (backend_root / "app" / "main.py").exists():
            backend_root = project_root / "backend"
        if not backend_root.exists():
            raise KeyError(delegated_id)
        port = int(state.get("port") or _find_free_port())
        fake_runner = bool(state.get("fake_runner", True))
        process = self._spawn_process(project_root, backend_root, port, fake_runner=fake_runner)
        item = DelegatedPlaygroundProcess(
            delegated_id=delegated_id,
            session_id=str(state.get("session_id") or delegated_id.split("-", 1)[0]),
            project_name=str(state.get("project_name") or project_root.name),
            project_root=project_root,
            backend_root=backend_root,
            port=port,
            process=process,
            fake_runner=fake_runner,
            created_at=float(state.get("created_at") or time.time()),
            last_result={"restored_from_disk": True},
        )
        self._items[delegated_id] = item
        wait_result = _wait_http(f"{item.base_url}/health", timeout=8)
        item.last_result = {"restore": wait_result, "restored_at": time.time()}
        if not wait_result.get("ok"):
            output = self.read_logs(delegated_id, limit=120)
            self.stop(delegated_id)
            raise RuntimeError(f"Delegated Agent 恢复失败：{wait_result.get('error') or 'health check failed'}\n{output}")
        self._save_state(item)
        return item

    def _guess_project_root(self, root_parent: Path) -> Path:
        candidates = []
        for path in root_parent.iterdir():
            if not path.is_dir():
                continue
            if (path / "backend" / "app" / "main.py").exists():
                candidates.append(path)
        if not candidates:
            raise KeyError(root_parent.name)
        candidates.sort(key=lambda value: value.stat().st_mtime, reverse=True)
        return candidates[0]

    def _drop_stopped(self) -> None:
        for delegated_id, item in list(self._items.items()):
            if item.process.poll() is not None:
                self._items.pop(delegated_id, None)

    def _load_disk_summaries(self) -> None:
        for root_parent in self.root_dir.iterdir():
            if not root_parent.is_dir() or root_parent.name in self._items:
                continue
            state = _read_json(root_parent / "playground_state.json")
            if not state:
                continue
            port = int(state.get("port") or 0)
            if port <= 0 or not _is_http_ok(f"http://127.0.0.1:{port}/health"):
                continue
            project_root = Path(str(state.get("project_root") or ""))
            backend_root = Path(str(state.get("backend_root") or ""))
            if not project_root.exists() or not backend_root.exists():
                continue
            process = _ExternalProcess(port)
            item = DelegatedPlaygroundProcess(
                delegated_id=root_parent.name,
                session_id=str(state.get("session_id") or root_parent.name.split("-", 1)[0]),
                project_name=str(state.get("project_name") or project_root.name),
                project_root=project_root,
                backend_root=backend_root,
                port=port,
                process=process,
                fake_runner=bool(state.get("fake_runner", True)),
                created_at=float(state.get("created_at") or root_parent.stat().st_mtime),
                last_result=dict(state.get("last_result") or {"restored_running_process": True}),
            )
            self._items[root_parent.name] = item


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _pid_listening_on_port(port: int) -> int:
    try:
        result = subprocess.run(
            ["ss", "-ltnp", f"sport = :{int(port)}"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except Exception:
        return 0
    for line in result.stdout.splitlines():
        marker = "pid="
        if marker not in line:
            continue
        tail = line.split(marker, 1)[1]
        number = tail.split(",", 1)[0].strip()
        try:
            return int(number)
        except ValueError:
            continue
    return 0


def _wait_http(url: str, timeout: float = 8) -> dict[str, Any]:
    deadline = time.time() + timeout
    last_error = ""
    while time.time() < deadline:
        try:
            data = _get_json(url, timeout=4)
            return {"ok": True, "response": data}
        except Exception as exc:
            last_error = str(exc)
            time.sleep(0.25)
    return {"ok": False, "error": last_error}


def _get_json(url: str, timeout: int = 8) -> dict[str, Any]:
    with request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _is_http_ok(url: str, timeout: int = 2) -> bool:
    try:
        _get_json(url, timeout=timeout)
        return True
    except Exception:
        return False


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _get_text(url: str, timeout: int = 8) -> str:
    with request.urlopen(url, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def _read_text_tail(path: Path, limit: int = 12000) -> str:
    if not path.exists():
        return ""
    with path.open("rb") as handle:
        try:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - limit))
        except OSError:
            handle.seek(0)
        return handle.read().decode("utf-8", errors="replace")


def _read_jsonl_tail(path: Path, limit: int = 160) -> list[dict[str, Any]]:
    text = _read_text_tail(path, limit=200000)
    events: list[dict[str, Any]] = []
    for line in text.splitlines()[-limit:]:
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            events.append({"type": "invalid_event", "message": line})
    return events


def _post_json(url: str, payload: dict[str, Any], timeout: int = 15) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(text or str(exc)) from exc
