from __future__ import annotations

import json
import os
import shutil
import subprocess
import textwrap
import time
from pathlib import Path
from typing import Any

from protocol_designer.core import build_exports
from protocol_designer.generator import generate_project_scaffold, safe_project_name

DEFAULT_EXCLUDES = (
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "dist",
    "build",
    ".next",
    ".nuxt",
    "target",
    "*.pyc",
    "*.pyo",
    ".DS_Store",
)

DEFAULT_SMOKE_COMMAND = r'''python - <<'PY'
from pathlib import Path
import json
print("APD DockerSandbox smoke test")
print("TASK.md preview:")
print(Path('/workspace/TASK.md').read_text(encoding='utf-8')[:2000])
Path('/workspace/APD_RESULT.json').write_text(json.dumps({
    "status": "ok",
    "message": "sandbox smoke test completed",
}, ensure_ascii=False, indent=2), encoding='utf-8')
PY'''



DEFAULT_CODEX_COMMAND = r'''
MODEL_ARG=""
if [ -n "${OPENAI_MODEL:-}" ]; then MODEL_ARG="-m ${OPENAI_MODEL}"; fi
codex exec \
  --dangerously-bypass-approvals-and-sandbox \
  --skip-git-repo-check \
  --ephemeral \
  -C /workspace/project \
  $MODEL_ARG \
  "$(cat /workspace/TASK.md)"
'''

DEFAULT_CLAUDE_COMMAND = r'''
MODEL_ARG=""
if [ -n "${LLM_MODEL:-}" ]; then MODEL_ARG="--model ${LLM_MODEL}"; fi
claude --print \
  --permission-mode bypassPermissions \
  --no-session-persistence \
  --output-format text \
  $MODEL_ARG \
  "$(cat /workspace/TASK.md)"
'''
def _truncate(value: str, limit: int = 120_000) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + f"\n... truncated {len(value) - limit} chars ..."


def _safe_job_id(value: str) -> str:
    return "".join(ch for ch in value if ch.isalnum() or ch in "_-")[:48] or "job"


def _run_local(args: list[str], cwd: Path | None = None, timeout: int = 10) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=str(cwd) if cwd else None, text=True, capture_output=True, timeout=timeout)


def _copy_project(project_path: str, target: Path) -> dict[str, Any]:
    source_text = str(project_path or "").strip()
    if not source_text:
        target.mkdir(parents=True, exist_ok=True)
        return {"source": "", "copied": False, "reason": "empty project path"}
    source = Path(source_text).expanduser().resolve()
    if not source.exists() or not source.is_dir():
        raise ValueError(f"project_path 不存在或不是目录：{source}")
    ignore = shutil.ignore_patterns(*DEFAULT_EXCLUDES)
    shutil.copytree(source, target, ignore=ignore, symlinks=True)
    return {"source": str(source), "copied": True, "target": str(target), "mode": "copy_existing_project"}


def _generate_scaffold_project(session: dict[str, Any], workspace: Path, target: Path) -> dict[str, Any]:
    protocol = session.get("protocol") or {}
    project_name = safe_project_name(protocol.get("project_name") or session.get("title") or "generated-agent")
    scaffold_root = workspace / "_generated_scaffold"
    written = generate_project_scaffold(session, scaffold_root, project_name=project_name, force=True)
    generated_root = scaffold_root / project_name
    if not generated_root.exists() and written:
        for candidate in written[0].parents:
            if (candidate / "README.md").exists() and (candidate / "backend").exists():
                generated_root = candidate
                break
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(generated_root, target, symlinks=True)
    return {
        "source": str(generated_root),
        "copied": True,
        "target": str(target),
        "mode": "generated_scaffold_from_current_session",
        "project_name": project_name,
        "files": len(written),
    }


def _write_inputs(session: dict[str, Any], workspace: Path, task: str) -> dict[str, Any]:
    protocol = session.get("protocol") or {}
    exports = build_exports(protocol)
    inputs_dir = workspace / "apd_inputs"
    inputs_dir.mkdir(parents=True, exist_ok=True)
    for name, content in exports.items():
        (inputs_dir / name).write_text(content, encoding="utf-8")
    (inputs_dir / "session.json").write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")
    task_md = textwrap.dedent(f'''
    # APD Engineering Task

    ## 用户任务

    {task.strip() or "请根据 APD 产物完成工程实现或检查。"}

    ## 可用 APD 产物

    - `/workspace/apd_inputs/protocol.json`
    - `/workspace/apd_inputs/workflow.json`
    - `/workspace/apd_inputs/development_plan.md`
    - `/workspace/apd_inputs/workflow_plan.md`
    - `/workspace/apd_inputs/planner_prompt.md`
    - `/workspace/apd_inputs/executor_skeleton.py`
    - `/workspace/apd_inputs/session.json`

    ## 工作目录

    - 项目副本目录：`/workspace/project`
    - 任务文件：`/workspace/TASK.md`

    ## 执行要求

    1. 只能修改 `/workspace/project` 里的项目副本。
    2. 不要假设能访问宿主机真实项目目录。
    3. 如果完成任务，请尽量运行必要测试或检查。
    4. 最后请写入 `/workspace/APD_RESULT.json`，格式建议：

    ```json
    {{
      "status": "done|failed|partial",
      "summary": "做了什么",
      "changed_files": [],
      "tests": [],
      "risks": []
    }}
    ```
    ''').strip() + "\n"
    (workspace / "TASK.md").write_text(task_md, encoding="utf-8")
    return {"exports": sorted(exports), "task_file": str(workspace / "TASK.md")}


def _resolve_image(runner: str, image: str | None) -> str:
    if image and image.strip():
        return image.strip()
    key = f"APD_SANDBOX_{runner.upper()}_IMAGE" if runner else "APD_SANDBOX_IMAGE"
    return os.getenv(key) or os.getenv("APD_SANDBOX_IMAGE") or "python:3.12-slim"


def _resolve_command(runner: str, command: str | None) -> str:
    if command and command.strip():
        return command.strip()
    key = f"APD_SANDBOX_{runner.upper()}_COMMAND" if runner else "APD_SANDBOX_COMMAND"
    configured = os.getenv(key) or os.getenv("APD_SANDBOX_COMMAND")
    if configured:
        return configured
    if runner == "codex":
        return DEFAULT_CODEX_COMMAND
    if runner == "claude":
        return DEFAULT_CLAUDE_COMMAND
    return DEFAULT_SMOKE_COMMAND


def _llm_env(settings: dict[str, Any] | None) -> dict[str, str]:
    settings = settings or {}
    api_base = str(settings.get("api_base") or os.getenv("APD_API_BASE") or os.getenv("OPENAI_BASE_URL") or "").strip()
    api_key = str(settings.get("api_key") or os.getenv("APD_API_KEY") or os.getenv("OPENAI_API_KEY") or "").strip()
    model = str(settings.get("model") or os.getenv("APD_MODEL") or os.getenv("OPENAI_MODEL") or "").strip()
    env: dict[str, str] = {}
    if api_base:
        env.update({"LLM_API_BASE": api_base, "OPENAI_API_BASE": api_base, "OPENAI_BASE_URL": api_base})
    if api_key:
        env.update({"LLM_API_KEY": api_key, "OPENAI_API_KEY": api_key})
    if model:
        env.update({"LLM_MODEL": model, "OPENAI_MODEL": model})
    return env


def _docker_available() -> bool:
    return bool(shutil.which("docker"))


def _collect_git(project_dir: Path) -> dict[str, str]:
    if not (project_dir / ".git").exists():
        return {"status": "", "diff_stat": "", "diff": "", "note": "project copy has no .git directory"}
    result: dict[str, str] = {}
    commands = {
        "status": ["git", "status", "--short"],
        "diff_stat": ["git", "diff", "--stat"],
        "diff": ["git", "diff", "--"],
        "untracked": ["git", "ls-files", "--others", "--exclude-standard"],
    }
    for key, args in commands.items():
        try:
            proc = _run_local(args, cwd=project_dir, timeout=15)
            result[key] = _truncate((proc.stdout or "") + (proc.stderr or ""), 80_000)
        except Exception as exc:
            result[key] = f"collect {key} failed: {exc}"
    return result


def _read_result_file(workspace: Path, project_dir: Path) -> Any:
    for path in (workspace / "APD_RESULT.json", project_dir / "APD_RESULT.json"):
        if not path.exists():
            continue
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            return {"parse_error": str(exc), "raw": _truncate(path.read_text(encoding="utf-8", errors="replace"), 20_000)}
    return None


def run_docker_sandbox(
    *,
    session: dict[str, Any],
    project_path: str = "",
    task: str = "",
    runner: str = "custom",
    image: str | None = None,
    command: str | None = None,
    settings: dict[str, Any] | None = None,
    timeout_seconds: int = 300,
    memory: str = "2g",
    cpus: str = "2",
    network: str = "bridge",
    keep_job: bool = True,
) -> dict[str, Any]:
    if not _docker_available():
        return {"ok": False, "error": "docker command not found", "hint": "请先安装 Docker，或确认 APD 进程能访问 docker。"}

    job_id = _safe_job_id(f"{int(time.time())}-{os.getpid()}")
    jobs_root = Path(os.getenv("APD_SANDBOX_ROOT") or "/tmp/apd-sandbox/jobs").resolve()
    job_dir = jobs_root / job_id
    workspace = job_dir / "workspace"
    project_dir = workspace / "project"
    workspace.mkdir(parents=True, exist_ok=True)

    started = time.time()
    container_name = f"apd-job-{job_id}"
    docker_image = _resolve_image(runner, image)
    docker_command = _resolve_command(runner, command)
    copy_info: dict[str, Any] = {}
    input_info: dict[str, Any] = {}
    stdout = ""
    stderr = ""
    exit_code: int | None = None
    timed_out = False

    try:
        if str(project_path or "").strip():
            copy_info = _copy_project(project_path, project_dir)
        else:
            copy_info = _generate_scaffold_project(session, workspace, project_dir)
        input_info = _write_inputs(session, workspace, task)
        env = _llm_env(settings)
        docker_args = [
            "docker", "run", "--rm",
            "--name", container_name,
            "--memory", str(memory or "2g"),
            "--cpus", str(cpus or "2"),
            "--pids-limit", "512",
            "--security-opt", "no-new-privileges",
            "--cap-drop", "ALL",
            "--user", os.getenv("APD_SANDBOX_USER") or f"{os.getuid()}:{os.getgid()}",
            "-v", f"{workspace}:/workspace",
            "-w", "/workspace/project",
        ]
        if network:
            docker_args.extend(["--network", network])
        for key, value in env.items():
            docker_args.extend(["-e", f"{key}={value}"])
        docker_args.extend([docker_image, "sh", "-lc", docker_command])

        try:
            proc = subprocess.run(docker_args, text=True, capture_output=True, timeout=max(10, int(timeout_seconds or 300)))
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
            exit_code = proc.returncode
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            stdout = exc.stdout or "" if isinstance(exc.stdout, str) else ""
            stderr = exc.stderr or "" if isinstance(exc.stderr, str) else ""
            subprocess.run(["docker", "rm", "-f", container_name], text=True, capture_output=True, timeout=20)
            exit_code = 124

        git_info = _collect_git(project_dir)
        result_file = _read_result_file(workspace, project_dir)
        duration_ms = int((time.time() - started) * 1000)
        return {
            "ok": exit_code == 0 and not timed_out,
            "job_id": job_id,
            "job_dir": str(job_dir) if keep_job else "",
            "runner": runner,
            "image": docker_image,
            "command": docker_command,
            "container": container_name,
            "copy": copy_info,
            "inputs": input_info,
            "exit_code": exit_code,
            "timed_out": timed_out,
            "duration_ms": duration_ms,
            "stdout": _truncate(stdout),
            "stderr": _truncate(stderr),
            "result": result_file,
            "git": git_info,
        }
    except Exception as exc:
        try:
            subprocess.run(["docker", "rm", "-f", container_name], text=True, capture_output=True, timeout=20)
        except Exception:
            pass
        return {
            "ok": False,
            "job_id": job_id,
            "job_dir": str(job_dir),
            "runner": runner,
            "image": docker_image,
            "exit_code": exit_code,
            "error": str(exc),
            "stdout": _truncate(stdout),
            "stderr": _truncate(stderr),
        }
    finally:
        if not keep_job:
            shutil.rmtree(job_dir, ignore_errors=True)
