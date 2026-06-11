from __future__ import annotations

import json
import socket
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib import error, request

from .generator import generate_project_scaffold, safe_project_name


@dataclass
class DemoProcess:
    demo_id: str
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
            "demo_id": self.demo_id,
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
                "agent_run": f"{self.base_url}/agent/run",
                "workflow_run": f"{self.base_url}/workflow/run",
                "store_snapshot": f"{self.base_url}/store/snapshot",
                "tools": f"{self.base_url}/tools",
            },
        }


class DemoPlaygroundManager:
    def __init__(self, root_dir: Path) -> None:
        self.root_dir = root_dir
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self._demos: dict[str, DemoProcess] = {}

    def list_demos(self) -> list[dict[str, Any]]:
        self._drop_stopped()
        return [demo.to_summary() for demo in sorted(self._demos.values(), key=lambda item: item.created_at, reverse=True)]

    def get(self, demo_id: str) -> DemoProcess:
        demo = self._demos.get(demo_id)
        if not demo:
            raise KeyError(demo_id)
        return demo

    def start_demo(self, session: dict[str, Any], *, project_name: str = "", template: str = "fastapi-vue") -> dict[str, Any]:
        self._drop_stopped()
        session_id = str(session.get("session_id") or "session")
        project_name = safe_project_name(project_name or ((session.get("protocol") or {}).get("project_name")) or session.get("title") or "apd-demo")
        demo_id = f"{session_id[:8]}-{int(time.time() * 1000)}"
        project_root_parent = self.root_dir / demo_id
        written = generate_project_scaffold(session, project_root_parent, template=template, force=True, project_name=project_name)
        project_root = project_root_parent / project_name
        if not project_root.exists() and written:
            for candidate in written[0].parents:
                if (candidate / "README.md").exists() and (candidate / "backend").exists():
                    project_root = candidate
                    break
        backend_root = project_root / "backend"
        port = _find_free_port()
        process = subprocess.Popen(
            ["python3", "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
            cwd=str(backend_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        demo = DemoProcess(
            demo_id=demo_id,
            session_id=session_id,
            project_name=project_name,
            project_root=project_root,
            backend_root=backend_root,
            port=port,
            process=process,
        )
        self._demos[demo_id] = demo
        wait_result = _wait_http(f"{demo.base_url}/health", timeout=8)
        demo.last_result = {"startup": wait_result}
        if not wait_result.get("ok"):
            output = self.read_logs(demo_id, limit=80)
            self.stop_demo(demo_id)
            raise RuntimeError(f"Demo 启动失败：{wait_result.get('error') or 'health check failed'}\n{output}")
        return demo.to_summary()

    def stop_demo(self, demo_id: str) -> dict[str, Any]:
        demo = self.get(demo_id)
        if demo.process.poll() is None:
            demo.process.terminate()
            try:
                demo.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                demo.process.kill()
                demo.process.wait(timeout=3)
        return demo.to_summary()

    def run_agent(self, demo_id: str, message: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        demo = self.get(demo_id)
        result = _post_json(f"{demo.base_url}/agent/run", {"message": message, "context": context or {}})
        demo.last_result = {"agent_run": result}
        return result

    def run_workflow(self, demo_id: str, message: str, context: dict[str, Any] | None = None, max_steps: int = 3) -> dict[str, Any]:
        demo = self.get(demo_id)
        result = _post_json(f"{demo.base_url}/workflow/run", {"message": message, "context": context or {}, "max_steps": max_steps})
        demo.last_result = {"workflow_run": result}
        return result

    def get_store_snapshot(self, demo_id: str) -> dict[str, Any]:
        demo = self.get(demo_id)
        result = _get_json(f"{demo.base_url}/store/snapshot")
        demo.last_result = {"store_snapshot": result}
        return result

    def get_tools(self, demo_id: str) -> dict[str, Any]:
        demo = self.get(demo_id)
        result = _get_json(f"{demo.base_url}/tools")
        demo.last_result = {"tools": result}
        return result

    def read_logs(self, demo_id: str, limit: int = 120) -> str:
        demo = self.get(demo_id)
        lines: list[str] = []
        stream = demo.process.stdout
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
        for demo_id, demo in list(self._demos.items()):
            if demo.process.poll() is not None:
                self._demos.pop(demo_id, None)


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_http(url: str, timeout: float = 8) -> dict[str, Any]:
    deadline = time.time() + timeout
    last_error = ""
    while time.time() < deadline:
        try:
            data = _get_json(url)
            return {"ok": True, "response": data}
        except Exception as exc:
            last_error = str(exc)
            time.sleep(0.25)
    return {"ok": False, "error": last_error}


def _get_json(url: str) -> dict[str, Any]:
    with request.urlopen(url, timeout=8) as response:
        return json.loads(response.read().decode("utf-8"))


def _post_json(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with request.urlopen(req, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(text or str(exc)) from exc
