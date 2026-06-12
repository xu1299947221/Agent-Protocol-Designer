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


class DelegatedPlaygroundManager:
    def __init__(self, root_dir: Path) -> None:
        self.root_dir = root_dir
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self._items: dict[str, DelegatedPlaygroundProcess] = {}

    def list_items(self) -> list[dict[str, Any]]:
        self._drop_stopped()
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
        env = dict(os.environ)
        env["OPEN_CLAUDE_FAKE"] = "1" if fake_runner else "0"
        env["DATA_DIR"] = str((project_root / "data").resolve())
        process = subprocess.Popen(
            ["python3", "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
            cwd=str(backend_root),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        item = DelegatedPlaygroundProcess(
            delegated_id=delegated_id,
            session_id=session_id,
            project_name=project_name,
            project_root=project_root,
            backend_root=backend_root,
            port=port,
            process=process,
            last_result={"manifest": manifest},
        )
        self._items[delegated_id] = item
        wait_result = _wait_http(f"{item.base_url}/health", timeout=8)
        item.last_result = {"startup": wait_result, "manifest": manifest}
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

    def create_job(self, delegated_id: str, message: str) -> dict[str, Any]:
        item = self.get(delegated_id)
        result = _post_json(f"{item.base_url}/api/jobs", {"message": message}, timeout=15)
        item.last_result = {"create_job": result}
        return result

    def get_job(self, delegated_id: str, job_id: str) -> dict[str, Any]:
        item = self.get(delegated_id)
        result = _get_json(f"{item.base_url}/api/jobs/{job_id}", timeout=12)
        item.last_result = {"job": result}
        return result

    def list_jobs(self, delegated_id: str) -> dict[str, Any]:
        item = self.get(delegated_id)
        result = _get_json(f"{item.base_url}/api/jobs", timeout=12)
        item.last_result = {"jobs": result}
        return result

    def events(self, delegated_id: str, job_id: str) -> dict[str, Any]:
        item = self.get(delegated_id)
        return _get_json(f"{item.base_url}/api/jobs/{job_id}/events", timeout=12)

    def config(self, delegated_id: str) -> dict[str, Any]:
        item = self.get(delegated_id)
        return _get_json(f"{item.base_url}/api/config", timeout=12)

    def artifacts(self, delegated_id: str, job_id: str) -> dict[str, Any]:
        item = self.get(delegated_id)
        return _get_json(f"{item.base_url}/api/jobs/{job_id}/artifacts", timeout=12)

    def job_logs(self, delegated_id: str, job_id: str) -> dict[str, str]:
        item = self.get(delegated_id)
        stdout = _get_text(f"{item.base_url}/api/jobs/{job_id}/logs/stdout", timeout=12)
        stderr = _get_text(f"{item.base_url}/api/jobs/{job_id}/logs/stderr", timeout=12)
        return {"stdout": stdout, "stderr": stderr}

    def artifact_text(self, delegated_id: str, job_id: str, name: str) -> str:
        item = self.get(delegated_id)
        return _get_text(f"{item.base_url}/api/jobs/{job_id}/artifacts/{name}", timeout=12)

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

    def _drop_stopped(self) -> None:
        for delegated_id, item in list(self._items.items()):
            if item.process.poll() is not None:
                self._items.pop(delegated_id, None)


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


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


def _get_text(url: str, timeout: int = 8) -> str:
    with request.urlopen(url, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def _post_json(url: str, payload: dict[str, Any], timeout: int = 15) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(text or str(exc)) from exc
