from __future__ import annotations

import io
import json
import shutil
import tempfile
import textwrap
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .generator import safe_project_name, write_text


DEFAULT_OPEN_CLAUDE_SOURCE = Path("/home/data/rag/open_claude/Openclaude-openclaude")
EXCLUDED_OPEN_CLAUDE_PARTS = {".git", "node_modules", ".cache", ".tmp", "__pycache__"}


def generate_delegated_agent_project(
    *,
    protocol: dict[str, Any],
    project_name: str,
    agent_name: str,
    agent_goal: str,
    default_task: str = "",
    open_claude_source: Path | None = None,
    bundle_open_claude: bool = True,
) -> tuple[bytes, dict[str, Any]]:
    safe_name = safe_project_name(project_name or agent_name or protocol.get("project_name") or "delegated-agent")
    agent_name = (agent_name or protocol.get("project_name") or safe_name).strip()
    agent_goal = (agent_goal or protocol.get("domain_summary") or "接收用户任务，委托内置 open_claude 执行，并返回过程与产物。").strip()
    default_task = (default_task or "请根据用户输入完成任务，并把最终结果写入 artifacts/report.md 和 artifacts/result.json。").strip()
    source = Path(open_claude_source or DEFAULT_OPEN_CLAUDE_SOURCE)

    with tempfile.TemporaryDirectory(prefix="apd-delegated-agent-") as temp_dir:
        parent = Path(temp_dir)
        project_root = parent / safe_name
        project_root.mkdir(parents=True, exist_ok=True)

        definition = {
            "agent_type": "delegated_agent",
            "agent_name": agent_name,
            "agent_goal": agent_goal,
            "default_task": default_task,
            "protocol": protocol or {},
            "result_contract": {
                "required_files": ["result.json", "report.md"],
                "artifacts_dir": "artifacts",
                "result_json_schema": {
                    "status": "completed|failed|partial",
                    "summary": "给用户看的简短结果",
                    "artifacts": ["report.md"],
                    "next_actions": [],
                },
            },
        }
        _write_project_files(project_root, definition)

        open_claude_manifest: dict[str, Any]
        if bundle_open_claude:
            open_claude_manifest = _copy_open_claude(source, project_root / "runner" / "open_claude" / "Openclaude-openclaude")
        else:
            open_claude_manifest = {
                "source_path": str(source),
                "bundled": False,
                "copied_at": _now(),
                "include": [],
                "exclude": sorted(EXCLUDED_OPEN_CLAUDE_PARTS),
                "file_count": 0,
            }
        write_text(project_root / "runner" / "open_claude_manifest.json", json.dumps(open_claude_manifest, ensure_ascii=False, indent=2))
        write_text(project_root / "data" / ".gitkeep", "")

        manifest = {
            "mode": "delegated_agent",
            "project_name": safe_name,
            "agent_name": agent_name,
            "agent_goal": agent_goal,
            "generated_at": _now(),
            "bundle_open_claude": bundle_open_claude,
            "open_claude": open_claude_manifest,
        }
        write_text(project_root / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for file_path in sorted(project_root.rglob("*")):
                if file_path.is_file():
                    archive.write(file_path, file_path.relative_to(parent))
        buffer.seek(0)
        return buffer.getvalue(), manifest


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_excluded_open_claude_file(relative_path: Path) -> bool:
    if any(part in EXCLUDED_OPEN_CLAUDE_PARTS for part in relative_path.parts):
        return True
    name = relative_path.name
    return name == ".DS_Store" or name.endswith(".log")


def _copy_open_claude(source: Path, target: Path) -> dict[str, Any]:
    if not source.exists() or not source.is_dir():
        raise ValueError(f"open_claude source not found: {source}")
    cli = source / "dist" / "cli.js"
    if not cli.exists():
        raise ValueError(f"open_claude dist/cli.js not found: {cli}")
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    file_count = 0
    for file_path in source.rglob("*"):
        if not file_path.is_file():
            continue
        relative_path = file_path.relative_to(source)
        if _is_excluded_open_claude_file(relative_path):
            continue
        destination = target / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(file_path, destination)
        file_count += 1
    return {
        "source_path": str(source),
        "bundled": True,
        "copied_at": _now(),
        "include": ["dist", "src", "bin", "package.json", "README.md"],
        "exclude": [".git", "node_modules", ".cache", ".tmp", "*.log", ".DS_Store"],
        "file_count": file_count,
    }


def _write_project_files(project_root: Path, definition: dict[str, Any]) -> None:
    definition_json = json.dumps(definition, ensure_ascii=False, indent=2)
    write_text(project_root / "README.md", _readme(definition))
    write_text(project_root / ".env.example", _env_example(definition))
    write_text(project_root / "Dockerfile", _dockerfile())
    write_text(project_root / "docker-compose.yml", _docker_compose())
    write_text(project_root / "backend" / "__init__.py", "")
    write_text(project_root / "backend" / "requirements.txt", _requirements())
    write_text(project_root / "backend" / "app" / "__init__.py", "")
    write_text(project_root / "backend" / "app" / "main.py", _main_py())
    write_text(project_root / "backend" / "app" / "config.py", _config_py())
    write_text(project_root / "backend" / "app" / "schemas.py", _schemas_py())
    write_text(project_root / "backend" / "app" / "agent_definition.py", _agent_definition_py())
    write_text(project_root / "backend" / "app" / "agent_definition.json", definition_json)
    write_text(project_root / "backend" / "app" / "api" / "__init__.py", "")
    write_text(project_root / "backend" / "app" / "api" / "health.py", _health_py())
    write_text(project_root / "backend" / "app" / "api" / "jobs.py", _jobs_py())
    write_text(project_root / "backend" / "app" / "api" / "artifacts.py", _artifacts_py())
    write_text(project_root / "backend" / "app" / "runtime" / "__init__.py", "")
    write_text(project_root / "backend" / "app" / "runtime" / "workspace_manager.py", _workspace_manager_py())
    write_text(project_root / "backend" / "app" / "runtime" / "task_pack_builder.py", _task_pack_builder_py())
    write_text(project_root / "backend" / "app" / "runtime" / "trace_store.py", _trace_store_py())
    write_text(project_root / "backend" / "app" / "runtime" / "artifact_store.py", _artifact_store_py())
    write_text(project_root / "backend" / "app" / "runtime" / "result_parser.py", _result_parser_py())
    write_text(project_root / "backend" / "app" / "runtime" / "openclaude_runner.py", _openclaude_runner_py())
    write_text(project_root / "backend" / "app" / "runtime" / "runner_worker.py", _runner_worker_py())
    write_text(project_root / "backend" / "app" / "templates" / "task_pack_template.md", _task_pack_template())
    write_text(project_root / "frontend" / "index.html", _frontend_html(definition))


def _requirements() -> str:
    return "fastapi>=0.110\nuvicorn[standard]>=0.27\npydantic>=2\n"


def _env_example(definition: dict[str, Any]) -> str:
    agent_name = str(definition.get("agent_name") or "Delegated Agent")
    return f"""
AGENT_NAME={agent_name}
DATA_DIR=
OPEN_CLAUDE_FAKE=1
OPEN_CLAUDE_ROOT=
OPEN_CLAUDE_CLI=
OPENAI_BASE_URL=
OPENAI_API_KEY=
OPENAI_MODEL=
JOB_TIMEOUT_SECONDS=600
MAX_CONCURRENT_JOBS=1
AGENT_API_TOKEN=
"""


def _readme(definition: dict[str, Any]) -> str:
    agent_name = str(definition.get("agent_name") or "Delegated Agent")
    agent_goal = str(definition.get("agent_goal") or "")
    return f"""
# {agent_name}

这是 APD 生成的独立部署型 Delegated Agent。它把用户任务包装成受控 Task Pack，然后委托内置 open_claude 执行，最终保存 `job.json`、`trace/task_pack.md`、日志和 `artifacts/` 产物。

## 重要边界

- V1 是单进程任务执行器，只适合本地或可信内网验证。
- 真实 open_claude 具备文件读写和命令执行能力，不要公网裸露。
- V1 默认 `OPEN_CLAUDE_FAKE=1`，可以不配置模型、不安装 Node 也先跑通链路。
- 如需多 worker、多用户权限、Docker 沙箱和队列，请升级到 V2 架构。

## Agent 目标

{agent_goal}

## 本地启动：fake runner 验收路径

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp ../.env.example ../.env
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

打开：

```text
http://127.0.0.1:8000
```

提交任意任务后应看到：

- Job 状态从 `queued` 到 `completed`
- `data/jobs/<job_id>/trace/task_pack.md`
- `data/jobs/<job_id>/trace/stdout.log`
- `data/jobs/<job_id>/artifacts/report.md`
- `data/jobs/<job_id>/artifacts/result.json`

## 真实 open_claude 验收路径

编辑项目根目录 `.env`：

```env
OPEN_CLAUDE_FAKE=0
OPENAI_BASE_URL=http://your-gateway/v1
OPENAI_API_KEY=sk-...
OPENAI_MODEL=your-model
JOB_TIMEOUT_SECONDS=600
```

建议第一条真实任务使用最小任务：

```text
请在 artifacts/report.md 写一段“hello delegated agent”，并生成 artifacts/result.json。
```

如果失败，查看：

```text
GET /api/jobs/<job_id>/logs/stdout
GET /api/jobs/<job_id>/logs/stderr
```

如果 open_claude 等待首次信任目录确认，请先在服务器上手工运行一次内置 CLI 完成初始化，或继续使用 fake runner 验证服务链路。

## Docker Compose

```bash
cp .env.example .env
docker compose up --build
```

默认仍然走 fake runner。真实 runner 需要镜像内存在 Node.js，并确保 `runner/open_claude/Openclaude-openclaude/dist/cli.js` 可以执行。

## API

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/ready
curl http://127.0.0.1:8000/api/config
curl -X POST http://127.0.0.1:8000/api/jobs -H 'Content-Type: application/json' -d '{{"message":"测试任务"}}'
curl http://127.0.0.1:8000/api/jobs
curl http://127.0.0.1:8000/api/jobs/<job_id>
curl http://127.0.0.1:8000/api/jobs/<job_id>/events
curl http://127.0.0.1:8000/api/jobs/<job_id>/artifacts
```
"""


def _dockerfile() -> str:
    return """
FROM python:3.11-slim

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends nodejs npm && rm -rf /var/lib/apt/lists/*
COPY . /app
RUN pip install --no-cache-dir -r backend/requirements.txt

# V1 默认不复制 node_modules。若 dist/cli.js 不是独立 bundle，可在这里补安装依赖：
# RUN cd runner/open_claude/Openclaude-openclaude && npm install

EXPOSE 8000
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
"""


def _docker_compose() -> str:
    return """
services:
  delegated-agent:
    build: .
    ports:
      - "8000:8000"
    env_file:
      - .env
    volumes:
      - ./data:/app/data
"""


def _main_py() -> str:
    return r'''
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import artifacts, health, jobs
from .config import get_settings


app = FastAPI(title="Delegated Agent Service")
settings = get_settings()


@app.middleware("http")
async def api_token_auth(request: Request, call_next):
    token = settings.agent_api_token
    if token and request.url.path.startswith("/api/"):
        auth = request.headers.get("authorization") or ""
        if auth != f"Bearer {token}":
            return JSONResponse({"error": "unauthorized"}, status_code=401)
    return await call_next(request)


app.include_router(health.router)
app.include_router(jobs.router, prefix="/api")
app.include_router(artifacts.router, prefix="/api")


FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"


@app.get("/")
async def index():
    html = FRONTEND_DIR / "index.html"
    if html.exists():
        return FileResponse(html)
    return HTMLResponse("<h1>Delegated Agent</h1><p>frontend/index.html not found.</p>")


if (FRONTEND_DIR / "static").exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR / "static")), name="static")
'''


def _config_py() -> str:
    return r'''
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _load_env_file() -> None:
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, value = text.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env_file()


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    project_root: Path
    agent_name: str
    data_dir: Path
    open_claude_fake: bool
    open_claude_root: Path
    open_claude_cli: Path
    openai_base_url: str
    openai_api_key: str
    openai_model: str
    job_timeout_seconds: int
    max_concurrent_jobs: int
    agent_api_token: str

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"

    def runtime_check(self) -> dict:
        return {
            "fake_runner": self.open_claude_fake,
            "node_available": shutil.which("node") is not None,
            "open_claude_root": str(self.open_claude_root),
            "open_claude_cli": str(self.open_claude_cli),
            "open_claude_cli_exists": self.open_claude_cli.exists(),
            "model_configured": bool(self.openai_base_url and self.openai_api_key and self.openai_model),
            "max_concurrent_jobs": self.max_concurrent_jobs,
            "job_timeout_seconds": self.job_timeout_seconds,
            "api_token_enabled": bool(self.agent_api_token),
        }


def get_settings() -> Settings:
    data_dir = Path(os.getenv("DATA_DIR") or PROJECT_ROOT / "data").resolve()
    root = Path(os.getenv("OPEN_CLAUDE_ROOT") or PROJECT_ROOT / "runner" / "open_claude" / "Openclaude-openclaude").resolve()
    cli = Path(os.getenv("OPEN_CLAUDE_CLI") or root / "dist" / "cli.js").resolve()
    return Settings(
        project_root=PROJECT_ROOT,
        agent_name=os.getenv("AGENT_NAME") or "Delegated Agent",
        data_dir=data_dir,
        open_claude_fake=_bool_env("OPEN_CLAUDE_FAKE", True),
        open_claude_root=root,
        open_claude_cli=cli,
        openai_base_url=os.getenv("OPENAI_BASE_URL") or "",
        openai_api_key=os.getenv("OPENAI_API_KEY") or "",
        openai_model=os.getenv("OPENAI_MODEL") or "",
        job_timeout_seconds=max(30, int(os.getenv("JOB_TIMEOUT_SECONDS") or "600")),
        max_concurrent_jobs=max(1, int(os.getenv("MAX_CONCURRENT_JOBS") or "1")),
        agent_api_token=os.getenv("AGENT_API_TOKEN") or "",
    )
'''


def _schemas_py() -> str:
    return r'''
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class CreateJobRequest(BaseModel):
    message: str = Field(min_length=1)


class JobSummary(BaseModel):
    job_id: str
    status: str
    summary: str = ""
    created_at: str = ""
    updated_at: str = ""


class JobDetail(JobSummary):
    message: str = ""
    result: dict[str, Any] | None = None
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = None
    task_pack_path: str | None = None


class JobEvent(BaseModel):
    time: str
    type: str
    message: str = ""
    data: dict[str, Any] = Field(default_factory=dict)


class ArtifactInfo(BaseModel):
    name: str
    path: str
    size: int
'''


def _agent_definition_py() -> str:
    return r'''
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DEFINITION_PATH = Path(__file__).with_name("agent_definition.json")


def load_agent_definition() -> dict[str, Any]:
    if not DEFINITION_PATH.exists():
        return {"agent_type": "delegated_agent", "agent_name": "Delegated Agent", "agent_goal": ""}
    return json.loads(DEFINITION_PATH.read_text(encoding="utf-8"))
'''


def _health_py() -> str:
    return r'''
from __future__ import annotations

from fastapi import APIRouter

from ..agent_definition import load_agent_definition
from ..config import get_settings


router = APIRouter()


@router.get("/health")
async def health():
    return {"ok": True}


@router.get("/ready")
async def ready():
    settings = get_settings()
    check = settings.runtime_check()
    ready_ok = check["fake_runner"] or (check["node_available"] and check["open_claude_cli_exists"] and check["model_configured"])
    return {"ok": ready_ok, "check": check}


@router.get("/api/config")
async def config():
    settings = get_settings()
    definition = load_agent_definition()
    return {
        "agent_name": definition.get("agent_name") or settings.agent_name,
        "agent_goal": definition.get("agent_goal") or "",
        "default_task": definition.get("default_task") or "",
        "runtime": settings.runtime_check(),
    }
'''


def _jobs_py() -> str:
    return r'''
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse

from ..schemas import CreateJobRequest
from ..runtime import artifact_store, runner_worker, trace_store, workspace_manager


router = APIRouter()


@router.post("/jobs")
async def create_job(payload: CreateJobRequest):
    job = workspace_manager.create_job(payload.message)
    runner_worker.start_job(job["job_id"])
    return {"job_id": job["job_id"], "status": job["status"]}


@router.get("/jobs")
async def list_jobs():
    return {"jobs": workspace_manager.list_jobs()}


@router.get("/jobs/{job_id}")
async def get_job(job_id: str):
    job = workspace_manager.read_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    job["artifacts"] = artifact_store.list_artifacts(job_id)
    return job


@router.get("/jobs/{job_id}/events")
async def get_events(job_id: str):
    if not workspace_manager.read_job(job_id):
        raise HTTPException(status_code=404, detail="job not found")
    return {"events": trace_store.read_events(job_id)}


@router.get("/jobs/{job_id}/logs/stdout")
async def stdout_log(job_id: str):
    return PlainTextResponse(trace_store.read_log(job_id, "stdout.log"))


@router.get("/jobs/{job_id}/logs/stderr")
async def stderr_log(job_id: str):
    return PlainTextResponse(trace_store.read_log(job_id, "stderr.log"))


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str):
    if not workspace_manager.read_job(job_id):
        raise HTTPException(status_code=404, detail="job not found")
    runner_worker.cancel_job(job_id)
    return {"ok": True, "job_id": job_id}
'''


def _artifacts_py() -> str:
    return r'''
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from ..runtime import artifact_store, workspace_manager


router = APIRouter()


@router.get("/jobs/{job_id}/artifacts")
async def list_artifacts(job_id: str):
    if not workspace_manager.read_job(job_id):
        raise HTTPException(status_code=404, detail="job not found")
    return {"artifacts": artifact_store.list_artifacts(job_id)}


@router.get("/jobs/{job_id}/artifacts/{name:path}")
async def download_artifact(job_id: str, name: str):
    if not workspace_manager.read_job(job_id):
        raise HTTPException(status_code=404, detail="job not found")
    path = artifact_store.resolve_artifact(job_id, name)
    if not path or not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="artifact not found")
    return FileResponse(path, filename=path.name)
'''


def _workspace_manager_py() -> str:
    return r'''
from __future__ import annotations

import json
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import get_settings


LOCK = threading.RLock()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def jobs_dir() -> Path:
    settings = get_settings()
    settings.jobs_dir.mkdir(parents=True, exist_ok=True)
    return settings.jobs_dir


def job_dir(job_id: str) -> Path:
    return jobs_dir() / job_id


def job_json_path(job_id: str) -> Path:
    return job_dir(job_id) / "job.json"


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def create_job(message: str) -> dict[str, Any]:
    job_id = "job_" + datetime.now().strftime("%Y%m%d_%H%M%S_") + secrets.token_hex(4)
    root = job_dir(job_id)
    for name in ["input", "workspace", "artifacts", "trace"]:
        (root / name).mkdir(parents=True, exist_ok=True)
    (root / "input" / "message.txt").write_text(message, encoding="utf-8")
    timestamp = now()
    job = {
        "job_id": job_id,
        "status": "queued",
        "summary": "任务已排队",
        "message": message,
        "created_at": timestamp,
        "updated_at": timestamp,
        "result": None,
        "error": None,
        "cancel_requested": False,
        "task_pack_path": None,
    }
    with LOCK:
        atomic_write_json(job_json_path(job_id), job)
    return job


def read_job(job_id: str) -> dict[str, Any] | None:
    path = job_json_path(job_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def update_job(job_id: str, **changes: Any) -> dict[str, Any]:
    with LOCK:
        job = read_job(job_id) or {"job_id": job_id, "created_at": now()}
        job.update(changes)
        job["updated_at"] = now()
        atomic_write_json(job_json_path(job_id), job)
        return job


def list_jobs() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for path in sorted(jobs_dir().glob("*/job.json"), reverse=True):
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        items.append({
            "job_id": job.get("job_id"),
            "status": job.get("status"),
            "summary": job.get("summary") or "",
            "created_at": job.get("created_at") or "",
            "updated_at": job.get("updated_at") or "",
        })
    return items
'''


def _trace_store_py() -> str:
    return r'''
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .workspace_manager import job_dir


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def trace_dir(job_id: str) -> Path:
    path = job_dir(job_id) / "trace"
    path.mkdir(parents=True, exist_ok=True)
    return path


def append_event(job_id: str, event_type: str, message: str = "", data: dict[str, Any] | None = None) -> None:
    event = {"time": now(), "type": event_type, "message": message, "data": data or {}}
    with (trace_dir(job_id) / "events.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")


def read_events(job_id: str) -> list[dict[str, Any]]:
    path = trace_dir(job_id) / "events.jsonl"
    if not path.exists():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            events.append({"time": now(), "type": "invalid_event", "message": line})
    return events


def append_log(job_id: str, name: str, text: str) -> None:
    with (trace_dir(job_id) / name).open("a", encoding="utf-8", errors="replace") as handle:
        handle.write(text)


def read_log(job_id: str, name: str) -> str:
    path = trace_dir(job_id) / name
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")
'''


def _artifact_store_py() -> str:
    return r'''
from __future__ import annotations

from pathlib import Path

from .workspace_manager import job_dir


def artifacts_dir(job_id: str) -> Path:
    path = job_dir(job_id) / "artifacts"
    path.mkdir(parents=True, exist_ok=True)
    return path


def list_artifacts(job_id: str) -> list[dict]:
    root = artifacts_dir(job_id)
    items = []
    for path in sorted(root.rglob("*")):
        if path.is_file():
            rel = path.relative_to(root).as_posix()
            items.append({"name": rel, "path": rel, "size": path.stat().st_size})
    return items


def resolve_artifact(job_id: str, name: str) -> Path | None:
    root = artifacts_dir(job_id).resolve()
    candidate = (root / name).resolve()
    if root == candidate or root not in candidate.parents:
        return None
    return candidate
'''


def _task_pack_builder_py() -> str:
    return r'''
from __future__ import annotations

from pathlib import Path

from ..agent_definition import load_agent_definition
from .workspace_manager import job_dir, read_job, update_job


TEMPLATE_PATH = Path(__file__).resolve().parents[1] / "templates" / "task_pack_template.md"


def build_task_pack(job_id: str) -> str:
    job = read_job(job_id) or {}
    root = job_dir(job_id).resolve()
    definition = load_agent_definition()
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    task_pack = template.replace("{{agent_name}}", str(definition.get("agent_name") or "Delegated Agent"))
    task_pack = task_pack.replace("{{agent_goal}}", str(definition.get("agent_goal") or ""))
    task_pack = task_pack.replace("{{default_task}}", str(definition.get("default_task") or ""))
    task_pack = task_pack.replace("{{user_request}}", str(job.get("message") or ""))
    task_pack = task_pack.replace("{{job_dir}}", str(root))
    task_pack = task_pack.replace("{{input_dir}}", str(root / "input"))
    task_pack = task_pack.replace("{{workspace_dir}}", str(root / "workspace"))
    task_pack = task_pack.replace("{{artifacts_dir}}", str(root / "artifacts"))
    trace_path = root / "trace" / "task_pack.md"
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    trace_path.write_text(task_pack, encoding="utf-8")
    update_job(job_id, task_pack_path=str(trace_path))
    return task_pack
'''


def _task_pack_template() -> str:
    return """
# Delegated Agent Task Pack

## Agent

- 名称：{{agent_name}}
- 目标：{{agent_goal}}

## 默认任务说明

{{default_task}}

## 用户请求

{{user_request}}

## 可见目录

- Job 根目录：{{job_dir}}
- 输入目录：{{input_dir}}
- 工作区：{{workspace_dir}}
- 产物目录：{{artifacts_dir}}

## 强制执行规则

1. 只能在 Job 根目录内读取和写入。
2. 可以读取 `input/` 和 `workspace/`。
3. 最终交付产物必须写入 `artifacts/`。
4. 不要修改 `trace/` 和 `job.json`。
5. 必须生成 `artifacts/report.md`。
6. 必须生成 `artifacts/result.json`，JSON 至少包含：

```json
{
  "status": "completed",
  "summary": "一句话总结",
  "artifacts": ["report.md"],
  "next_actions": []
}
```
"""


def _result_parser_py() -> str:
    return r'''
from __future__ import annotations

import json
from typing import Any

from .artifact_store import list_artifacts
from .trace_store import read_log
from .workspace_manager import job_dir


def parse_result(job_id: str) -> dict[str, Any]:
    artifacts_root = job_dir(job_id) / "artifacts"
    result_path = artifacts_root / "result.json"
    if result_path.exists():
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
            if not isinstance(result, dict):
                raise ValueError("result.json must be object")
            result.setdefault("status", "completed")
            result.setdefault("summary", "")
            result.setdefault("artifacts", [item["name"] for item in list_artifacts(job_id)])
            result.setdefault("next_actions", [])
            return result
        except Exception as exc:
            return {
                "status": "failed",
                "summary": f"result.json 解析失败：{exc}",
                "artifacts": [item["name"] for item in list_artifacts(job_id)],
                "next_actions": ["检查 artifacts/result.json 是否为合法 JSON。"],
            }
    stdout_tail = read_log(job_id, "stdout.log")[-8000:]
    stderr_tail = read_log(job_id, "stderr.log")[-4000:]
    return {
        "status": "partial",
        "summary": "没有找到 artifacts/result.json，已返回日志摘要。",
        "artifacts": [item["name"] for item in list_artifacts(job_id)],
        "stdout_tail": stdout_tail,
        "stderr_tail": stderr_tail,
        "next_actions": ["让 runner 生成 artifacts/result.json 和 artifacts/report.md。"],
    }
'''


def _openclaude_runner_py() -> str:
    return r'''
from __future__ import annotations

import json
import os
import pty
import re
import select
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from ..config import get_settings
from .trace_store import append_event, append_log
from .workspace_manager import job_dir


RUNNING_PROCESSES: dict[str, subprocess.Popen] = {}
PROCESS_LOCK = threading.RLock()
LAST_OUTPUT_AT: dict[str, float] = {}


def _tail(path: Path, limit: int = 4000) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")[-limit:]


ANSI_RE = re.compile(r"(?:\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07]*(?:\x07|\x1b\\\\)|\x1b[()][A-Za-z0-9]|\x1b[=>]|\x1b[78])")
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _clean_terminal_text(value: str) -> str:
    text = ANSI_RE.sub("", value)
    text = CONTROL_RE.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


def _visible_tail(value: str, limit_lines: int = 18, limit_chars: int = 3000) -> str:
    lines = [line.rstrip() for line in value.splitlines()]
    compact = [line for line in lines if line.strip()]
    return "\n".join(compact[-limit_lines:])[-limit_chars:]


def _fake_run(job_id: str, task_pack: str) -> dict[str, Any]:
    root = job_dir(job_id)
    artifacts = root / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    append_log(job_id, "stdout.log", "[fake runner] started\n")
    append_log(job_id, "stdout.log", task_pack[:1200] + "\n")
    report = (
        "# Fake Runner 报告\n\n"
        "这说明 Delegated Agent 的 Job、Task Pack、Trace、Artifact 链路已经跑通。\n\n"
        "真实 open_claude 可在 `.env` 中把 `OPEN_CLAUDE_FAKE=0` 后启用。\n"
    )
    (artifacts / "report.md").write_text(report, encoding="utf-8")
    result = {
        "status": "completed",
        "summary": "fake runner completed",
        "artifacts": ["report.md", "result.json"],
        "next_actions": ["配置 OPENAI_BASE_URL、OPENAI_API_KEY、OPENAI_MODEL 后测试真实 open_claude。"],
    }
    (artifacts / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    append_event(job_id, "runner_completed", "fake runner completed", {"mode": "fake"})
    return {"exit_code": 0, "mode": "fake"}


def _reader(job_id: str, stream, log_name: str) -> None:
    try:
        for line in iter(stream.readline, ""):
            if not line:
                break
            append_log(job_id, log_name, line)
            LAST_OUTPUT_AT[job_id] = time.time()
            text = line.strip()
            if text:
                append_event(job_id, "runner_output", f"{log_name}: {text[:240]}", {"stream": log_name, "text": text[:1000]})
    finally:
        try:
            stream.close()
        except Exception:
            pass


def _run_with_pty(job_id: str, command: list[str], root: Path, env: dict[str, str], timeout_seconds: int) -> dict[str, Any]:
    master_fd, slave_fd = pty.openpty()
    env = dict(env)
    env.setdefault("TERM", "xterm-256color")
    env.setdefault("FORCE_COLOR", "1")
    process = subprocess.Popen(
        command,
        cwd=str(root),
        env=env,
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        close_fds=True,
    )
    os.close(slave_fd)
    with PROCESS_LOCK:
        RUNNING_PROCESSES[job_id] = process
        LAST_OUTPUT_AT[job_id] = time.time()
    append_event(job_id, "runner_process_started", "open_claude 子进程已启动（PTY 捕获模式）", {"pid": process.pid, "capture": "pty"})

    deadline = time.time() + timeout_seconds
    timed_out = False
    last_heartbeat_at = time.time() - 8
    screen_buffer = ""
    pending = ""
    try:
        while process.poll() is None:
            if time.time() > deadline:
                timed_out = True
                process.kill()
                append_event(job_id, "runner_timeout", "真实 open_claude 超时，已终止进程", {"pid": process.pid, "timeout_seconds": timeout_seconds})
                break
            readable, _, _ = select.select([master_fd], [], [], 0.5)
            if readable:
                try:
                    chunk = os.read(master_fd, 8192)
                except OSError:
                    chunk = b""
                if chunk:
                    raw = chunk.decode("utf-8", errors="replace")
                    cleaned = _clean_terminal_text(raw)
                    if cleaned.strip():
                        append_log(job_id, "stdout.log", cleaned)
                        LAST_OUTPUT_AT[job_id] = time.time()
                        screen_buffer = (screen_buffer + cleaned)[-12000:]
                        pending += cleaned
                        visible = _visible_tail(pending, limit_lines=10, limit_chars=2200)
                        if visible:
                            append_event(job_id, "runner_output", visible[:240], {"stream": "pty", "text": visible})
                            pending = ""
            if time.time() - last_heartbeat_at >= 3:
                last_heartbeat_at = time.time()
                silence_seconds = round(time.time() - LAST_OUTPUT_AT.get(job_id, time.time()), 1)
                screen_text = _visible_tail(screen_buffer, limit_lines=18, limit_chars=3000)
                append_event(
                    job_id,
                    "runner_screen",
                    "open_claude 当前终端屏幕快照",
                    {"pid": process.pid, "silence_seconds": silence_seconds, "capture": "pty", "text": screen_text},
                )
                append_event(job_id, "runner_heartbeat", "open_claude 仍在运行", {"pid": process.pid, "silence_seconds": silence_seconds, "capture": "pty"})
    finally:
        try:
            os.close(master_fd)
        except OSError:
            pass
        with PROCESS_LOCK:
            RUNNING_PROCESSES.pop(job_id, None)
            LAST_OUTPUT_AT.pop(job_id, None)

    return {"process": process, "timed_out": timed_out}


def run_openclaude(job_id: str, task_pack: str) -> dict[str, Any]:
    settings = get_settings()
    root = job_dir(job_id).resolve()
    if settings.open_claude_fake:
        return _fake_run(job_id, task_pack)
    if not settings.open_claude_cli.exists():
        raise RuntimeError(f"open_claude CLI 不存在：{settings.open_claude_cli}")

    env = dict(os.environ)
    for key in [
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_MODEL",
        "OPENAI_BASE_URL",
        "OPENAI_API_KEY",
        "OPENAI_MODEL",
    ]:
        env.pop(key, None)
    if settings.openai_base_url:
        env["OPENAI_BASE_URL"] = settings.openai_base_url
        env["ANTHROPIC_BASE_URL"] = settings.openai_base_url
    if settings.openai_api_key:
        env["OPENAI_API_KEY"] = settings.openai_api_key
        env["ANTHROPIC_API_KEY"] = settings.openai_api_key
        env["ANTHROPIC_AUTH_TOKEN"] = settings.openai_api_key
    if settings.openai_model:
        env["OPENAI_MODEL"] = settings.openai_model
        env["ANTHROPIC_MODEL"] = settings.openai_model

    command = [
        "node",
        "--enable-source-maps",
        str(settings.open_claude_cli),
        "--dangerously-skip-permissions",
        "--add-dir",
        str(root),
        task_pack,
    ]
    command_preview = " ".join(command[:-1]) + " <task_pack>"
    append_event(job_id, "runner_start", "启动真实 open_claude", {"cwd": str(root), "add_dir": str(root), "command": command_preview, "timeout_seconds": settings.job_timeout_seconds})
    append_log(job_id, "stdout.log", "[open_claude command]\n" + command_preview + "\n\n")
    pty_result = _run_with_pty(job_id, command, root, env, settings.job_timeout_seconds)
    process = pty_result["process"]
    timed_out = bool(pty_result.get("timed_out"))
    exit_code = process.returncode
    if timed_out:
        return {"exit_code": exit_code, "mode": "real", "timeout": True}
    append_event(job_id, "runner_exit", "真实 open_claude 已退出", {"exit_code": exit_code, "pid": process.pid})
    if exit_code != 0:
        stderr_tail = _tail(root / "trace" / "stderr.log")
        stdout_tail = _tail(root / "trace" / "stdout.log")
        raise RuntimeError(
            "open_claude 执行失败。可能是模型网关、Key、Node 依赖或首次交互确认导致。"
            f"\nexit_code={exit_code}\nstdout_tail={stdout_tail}\nstderr_tail={stderr_tail}"
        )
    return {"exit_code": exit_code, "mode": "real", "timeout": False}


def cancel_process(job_id: str) -> bool:
    with PROCESS_LOCK:
        process = RUNNING_PROCESSES.get(job_id)
    if process and process.poll() is None:
        process.kill()
        append_event(job_id, "runner_cancelled", "进程已被取消")
        return True
    return False
'''


def _runner_worker_py() -> str:
    return r'''
from __future__ import annotations

import threading
from typing import Any

from ..config import get_settings
from . import openclaude_runner
from .result_parser import parse_result
from .task_pack_builder import build_task_pack
from .trace_store import append_event
from .workspace_manager import read_job, update_job


SEMAPHORE = threading.Semaphore(get_settings().max_concurrent_jobs)


def start_job(job_id: str) -> None:
    thread = threading.Thread(target=run_job, args=(job_id,), daemon=True)
    thread.start()


def run_job(job_id: str) -> None:
    append_event(job_id, "queued", "任务进入队列")
    with SEMAPHORE:
        job = read_job(job_id)
        if not job:
            return
        if job.get("cancel_requested"):
            update_job(job_id, status="failed", summary="任务已取消", error="cancelled before start")
            return
        try:
            update_job(job_id, status="preparing_workspace", summary="准备工作区")
            append_event(job_id, "preparing_workspace", "工作区已创建")
            update_job(job_id, status="building_task_pack", summary="生成 Task Pack")
            task_pack = build_task_pack(job_id)
            append_event(job_id, "task_pack_built", "Task Pack 已生成")
            update_job(job_id, status="running", summary="Runner 执行中")
            runner_result = openclaude_runner.run_openclaude(job_id, task_pack)
            if runner_result.get("timeout"):
                update_job(job_id, status="timeout", summary="Runner 超时", error="job timeout", result=runner_result)
                return
            update_job(job_id, status="parsing_result", summary="解析结果")
            result = parse_result(job_id)
            status = "completed" if result.get("status") in {"completed", "partial"} else "failed"
            update_job(
                job_id,
                status=status,
                summary=result.get("summary") or status,
                result=result,
                error=None if status == "completed" else result.get("summary"),
            )
            append_event(job_id, "completed", "任务完成", {"status": status})
        except Exception as exc:
            update_job(job_id, status="failed", summary="任务失败", error=str(exc))
            append_event(job_id, "failed", str(exc))


def cancel_job(job_id: str) -> None:
    update_job(job_id, cancel_requested=True, summary="已请求取消")
    killed = openclaude_runner.cancel_process(job_id)
    if killed:
        update_job(job_id, status="failed", error="cancelled", summary="任务已取消")
'''


def _frontend_html(definition: dict[str, Any]) -> str:
    title = str(definition.get("agent_name") or "Delegated Agent")
    return f'''
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>{title}</title>
  <style>
    *{{box-sizing:border-box}}body{{margin:0;font-family:Inter,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#0f172a;color:#e5e7eb}}header{{padding:18px 22px;border-bottom:1px solid #334155;background:#020617}}h1{{margin:0;font-size:22px}}.sub{{color:#94a3b8;margin-top:6px}}main{{display:grid;grid-template-columns:360px 1fr;gap:14px;padding:14px;height:calc(100vh - 86px)}}section{{border:1px solid #334155;border-radius:16px;background:#111827;overflow:hidden}}.head{{padding:13px 15px;border-bottom:1px solid #334155;font-weight:800;color:#bae6fd}}.body{{padding:14px;overflow:auto;height:calc(100% - 48px)}}textarea,input{{width:100%;border:1px solid #334155;background:#020617;color:#e5e7eb;border-radius:12px;padding:10px;font:inherit}}textarea{{min-height:160px;resize:vertical}}button{{border:1px solid #38bdf8;background:#0ea5e9;color:#00111f;border-radius:10px;padding:9px 12px;font-weight:800;cursor:pointer}}button.secondary{{background:#172033;color:#e5e7eb;border-color:#475569}}.row{{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:10px}}.card{{border:1px solid #334155;border-radius:14px;background:#020617;padding:12px;margin-bottom:10px}}.muted{{color:#94a3b8;font-size:13px}}.pill{{display:inline-block;border:1px solid #475569;border-radius:999px;padding:3px 8px;font-size:12px;color:#cbd5e1;margin-right:5px}}.ok{{color:#86efac}}.bad{{color:#fecaca}}pre{{white-space:pre-wrap;background:#020617;border:1px solid #334155;border-radius:12px;padding:10px;max-height:280px;overflow:auto}}a{{color:#7dd3fc}}@media(max-width:900px){{main{{grid-template-columns:1fr;height:auto}}}}
  </style>
</head>
<body>
<header>
  <h1 id="agentName">{title}</h1>
  <div class="sub" id="agentGoal">正在读取配置...</div>
</header>
<main>
  <section>
    <div class="head">提交任务</div>
    <div class="body">
      <div class="card">
        <div class="muted">V1 只支持文本任务。fake runner 用于验证链路；真实 runner 会启动内置 open_claude。</div>
      </div>
      <label>API Token（如果服务端启用）</label>
      <input id="token" placeholder="Bearer token，可留空" />
      <div class="row"><button class="secondary" onclick="saveToken()">保存 Token</button><button class="secondary" onclick="loadConfig()">刷新配置</button></div>
      <label style="display:block;margin-top:14px">任务内容</label>
      <textarea id="message" placeholder="例如：请在 artifacts/report.md 写一段 hello delegated agent，并生成 artifacts/result.json。"></textarea>
      <div class="row"><button id="submitBtn" onclick="createJob()">提交任务</button></div>
      <div class="card" style="margin-top:14px">
        <b>运行前检查</b>
        <div id="configBox" class="muted">等待加载...</div>
      </div>
    </div>
  </section>
  <section>
    <div class="head">任务状态 / 日志 / 产物</div>
    <div class="body">
      <div class="row"><button class="secondary" onclick="loadJobs()">刷新任务列表</button><span class="muted" id="statusText"></span></div>
      <div id="jobs"></div>
      <div id="detail"></div>
    </div>
  </section>
</main>
<script>
let currentJobId = null;
let pollTimer = null;

function headers() {{
  const h = {{'Content-Type':'application/json'}};
  const token = localStorage.getItem('delegated_agent_token') || '';
  if (token) h.Authorization = 'Bearer ' + token;
  return h;
}}
function saveToken() {{
  localStorage.setItem('delegated_agent_token', document.getElementById('token').value.trim());
  loadConfig();
}}
async function api(url, options={{}}) {{
  options.headers = Object.assign(headers(), options.headers || {{}});
  const res = await fetch(url, options);
  if (!res.ok) throw new Error(await res.text());
  return res;
}}
async function loadConfig() {{
  document.getElementById('token').value = localStorage.getItem('delegated_agent_token') || '';
  try {{
    const data = await (await api('/api/config')).json();
    agentName.textContent = data.agent_name || 'Delegated Agent';
    agentGoal.textContent = data.agent_goal || '';
    const r = data.runtime || {{}};
    configBox.innerHTML = `
      <div><span class="pill">${{r.fake_runner ? 'fake runner' : '真实 runner'}}</span><span class="pill">Node: ${{r.node_available ? '可用' : '不可用'}}</span><span class="pill">CLI: ${{r.open_claude_cli_exists ? '存在' : '不存在'}}</span><span class="pill">模型: ${{r.model_configured ? '已配置' : '未配置'}}</span></div>
      <pre>${{JSON.stringify(r,null,2)}}</pre>`;
  }} catch (err) {{
    configBox.innerHTML = '<span class="bad">配置读取失败：' + err.message + '</span>';
  }}
}}
async function createJob() {{
  const message = document.getElementById('message').value.trim();
  if (!message) return alert('请输入任务内容');
  submitBtn.disabled = true;
  statusText.textContent = '提交中...';
  try {{
    const data = await (await api('/api/jobs', {{method:'POST', body:JSON.stringify({{message}})}})).json();
    currentJobId = data.job_id;
    statusText.textContent = '已提交：' + currentJobId;
    startPolling();
  }} catch (err) {{
    alert('提交失败：' + err.message);
  }} finally {{
    submitBtn.disabled = false;
  }}
}}
async function loadJobs() {{
  const data = await (await api('/api/jobs')).json();
  jobs.innerHTML = (data.jobs || []).map(j => `<div class="card"><b>${{j.job_id}}</b><div><span class="pill">${{j.status}}</span><span class="muted">${{j.updated_at || ''}}</span></div><div class="muted">${{j.summary || ''}}</div><div class="row"><button class="secondary" onclick="showJob('${{j.job_id}}')">查看</button></div></div>`).join('') || '<div class="muted">暂无任务</div>';
}}
async function showJob(jobId) {{
  currentJobId = jobId;
  const job = await (await api('/api/jobs/' + jobId)).json();
  const events = await (await api('/api/jobs/' + jobId + '/events')).json();
  const artifacts = await (await api('/api/jobs/' + jobId + '/artifacts')).json();
  let stdout = '';
  let stderr = '';
  try {{ stdout = await (await api('/api/jobs/' + jobId + '/logs/stdout')).text(); }} catch (_) {{}}
  try {{ stderr = await (await api('/api/jobs/' + jobId + '/logs/stderr')).text(); }} catch (_) {{}}
  detail.innerHTML = `<div class="card">
    <h3>${{job.job_id}} <span class="pill">${{job.status}}</span></h3>
    <div class="muted">${{job.summary || ''}}</div>
    ${{job.error ? '<pre class="bad">'+escapeHtml(job.error)+'</pre>' : ''}}
    <h4>产物</h4>
    <div>${{(artifacts.artifacts || []).map(a => `<a href="/api/jobs/${{jobId}}/artifacts/${{encodeURIComponent(a.name)}}" target="_blank">${{a.name}}</a> <span class="muted">(${{a.size}} bytes)</span>`).join('<br>') || '<span class="muted">暂无产物</span>'}}</div>
    <h4>Result</h4><pre>${{escapeHtml(JSON.stringify(job.result || {{}}, null, 2))}}</pre>
    <h4>Events</h4><pre>${{escapeHtml(JSON.stringify(events.events || [], null, 2))}}</pre>
    <h4>stdout.log</h4><pre>${{escapeHtml(stdout || '')}}</pre>
    <h4>stderr.log</h4><pre>${{escapeHtml(stderr || '')}}</pre>
  </div>`;
  loadJobs();
}}
function startPolling() {{
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(async () => {{
    if (!currentJobId) return;
    await showJob(currentJobId);
    const job = await (await api('/api/jobs/' + currentJobId)).json();
    if (['completed','failed','timeout'].includes(job.status)) clearInterval(pollTimer);
  }}, 1500);
  showJob(currentJobId);
}}
function escapeHtml(text) {{
  return String(text).replace(/[&<>"']/g, ch => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[ch]));
}}
loadConfig();
loadJobs();
</script>
</body>
</html>
'''
