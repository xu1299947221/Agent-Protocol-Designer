from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib import error, request

from .generator import generate_project_scaffold, safe_project_name
from .developer_runner import build_agent_engineering_plan, run_developer_runner


EDITABLE_FILES: dict[str, str] = {
    "backend/app/planner.py": "改 Agent 怎么理解用户话术，以及怎么绑定 OpCall。",
    "backend/app/executor.py": "改 Agent 真正执行后的用户可见回复、状态和产物。",
    "backend/app/tools.py": "改工具适配器，例如知识库、文档、检索、导出。",
    "backend/app/workflow.py": "改复杂任务的节点级运行方式。",
    "backend/app/validators.py": "改参数校验和执行前检查。",
    "backend/protocol.json": "查看或微调 APD 生成的协议。",
    "frontend/AgentPlayground.vue": "改下载后项目里的前端体验页面。",
}

OPEN_CLAUDE_ROOT = Path(os.getenv("APD_OPEN_CLAUDE_ROOT") or "/home/data/rag/open_claude/Openclaude-openclaude")
OPEN_CLAUDE_CLI = OPEN_CLAUDE_ROOT / "dist" / "cli.js"
_IGNORE_SCAN_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", "dist"}



@dataclass
class DevStudioRun:
    workspace_id: str
    project_name: str
    project_root: Path
    backend_root: Path
    port: int
    process: subprocess.Popen[str]
    created_at: float = field(default_factory=time.time)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def stop(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)


class DevStudioManager:
    """Persistent Agent development workspaces built from APD sessions.

    v1 deliberately does not edit code with AI yet. It gives APD a stable base:
    create a generated project, run it, snapshot versions, rollback, and download.
    """

    def __init__(self, root_dir: Path) -> None:
        self.root_dir = root_dir
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.debug_runs: dict[str, DevStudioRun] = {}
        self.debug_histories: dict[str, list[dict[str, Any]]] = {}

    def create_workspace(self, session: dict[str, Any], *, name: str = "", template: str = "fastapi-vue") -> dict[str, Any]:
        session_id = str(session.get("session_id") or "session")
        protocol = session.get("protocol") or {}
        project_name = safe_project_name(name or protocol.get("project_name") or session.get("title") or "agent-workspace")
        workspace_id = f"ws-{uuid.uuid4().hex[:12]}"
        workspace_dir = self.root_dir / workspace_id
        current_parent = workspace_dir / "current"
        current_parent.mkdir(parents=True, exist_ok=True)
        generate_project_scaffold(session, current_parent, template=template, force=True, project_name=project_name)
        current_project_root = current_parent / project_name
        metadata = {
            "workspace_id": workspace_id,
            "session_id": session_id,
            "name": project_name,
            "project_name": project_name,
            "template": template,
            "workspace_dir": str(workspace_dir),
            "project_root": str(current_project_root),
            "current_version": "v1",
            "created_at": _now(),
            "updated_at": _now(),
            "versions": [],
            "last_run": {},
        }
        self._write_metadata(workspace_dir, metadata)
        version = self.save_version(workspace_id, "初始生成：APD 根据当前会话生成第一份可运行 Agent 工程")
        metadata = self._read_metadata(workspace_dir)
        metadata["current_version"] = version["version_id"]
        metadata["updated_at"] = _now()
        self._write_metadata(workspace_dir, metadata)
        return self.get_workspace(workspace_id)

    def list_workspaces(self, session_id: str = "") -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for metadata_path in sorted(self.root_dir.glob("ws-*/metadata.json")):
            try:
                workspace = self._read_metadata(metadata_path.parent)
            except Exception:
                continue
            if session_id and workspace.get("session_id") != session_id:
                continue
            items.append(self._public_workspace(workspace))
        return sorted(items, key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""), reverse=True)

    def get_workspace(self, workspace_id: str) -> dict[str, Any]:
        workspace = self._read_metadata(self._workspace_dir(workspace_id))
        return self._public_workspace(workspace)

    def list_editable_files(self, workspace_id: str) -> list[dict[str, Any]]:
        project_root = self._current_project_root(workspace_id)
        files: list[dict[str, Any]] = []
        for rel_path, purpose in EDITABLE_FILES.items():
            path = project_root / rel_path
            files.append({
                "path": rel_path,
                "purpose": purpose,
                "exists": path.exists(),
                "size": path.stat().st_size if path.exists() else 0,
                "is_code": rel_path.endswith(".py") or rel_path.endswith(".vue"),
            })
        return files

    def read_file(self, workspace_id: str, rel_path: str) -> dict[str, Any]:
        project_root = self._current_project_root(workspace_id)
        safe_rel = self._validate_editable_path(rel_path)
        path = project_root / safe_rel
        if not path.exists():
            raise FileNotFoundError(f"文件不存在：{safe_rel}")
        return {
            "path": safe_rel,
            "purpose": EDITABLE_FILES.get(safe_rel, ""),
            "content": path.read_text(encoding="utf-8"),
            "updated_at": _now(),
        }

    def write_file(self, workspace_id: str, rel_path: str, content: str) -> dict[str, Any]:
        workspace_dir = self._workspace_dir(workspace_id)
        metadata = self._read_metadata(workspace_dir)
        project_root = self._current_project_root(workspace_id)
        safe_rel = self._validate_editable_path(rel_path)
        path = project_root / safe_rel
        if not path.exists():
            raise FileNotFoundError(f"文件不存在：{safe_rel}")
        path.write_text(str(content), encoding="utf-8")
        metadata["updated_at"] = _now()
        metadata["last_edit"] = {"path": safe_rel, "at": _now(), "size": len(str(content).encode("utf-8"))}
        self._write_metadata(workspace_dir, metadata)
        return {"ok": True, "path": safe_rel, "size": path.stat().st_size, "updated_at": metadata["updated_at"]}

    def update_demo_reply(self, workspace_id: str, reply: str) -> dict[str, Any]:
        """No-code quick edit: change executor.py visible assistant reply."""
        reply = str(reply or "").strip()
        if not reply:
            raise ValueError("回复文案不能为空")
        file_data = self.read_file(workspace_id, "backend/app/executor.py")
        content = str(file_data.get("content") or "")
        replacement = "        \"message\": " + json.dumps(reply, ensure_ascii=False) + ","
        new_content, count = re.subn(r'^\s+"message":\s+(?:f)?".*",\s*$', lambda _match: replacement, content, flags=re.MULTILINE)
        if count <= 0:
            raise ValueError("没有找到可替换的 message 行，请改用文件编辑器手动修改 backend/app/executor.py")
        written = self.write_file(workspace_id, "backend/app/executor.py", new_content)
        return {"ok": True, "path": "backend/app/executor.py", "replacement_count": count, "reply": reply, "file": written}

    def build_engineering_plan(self, workspace_id: str, session: dict[str, Any], request: str) -> dict[str, Any]:
        workspace = self.get_workspace(workspace_id)
        return build_agent_engineering_plan(session, workspace, request)

    def run_developer_task(
        self,
        workspace_id: str,
        *,
        session: dict[str, Any],
        request: str,
        runner: str = "open_claude",
        base_url: str = "",
        api_key: str = "",
        work_dir: str = "",
        cli_root: str = "",
        model: str = "",
        command_template: str = "",
        timeout_seconds: int = 900,
    ) -> dict[str, Any]:
        project_root = self._current_project_root(workspace_id)
        plan = self.build_engineering_plan(workspace_id, session, request)
        result = run_developer_runner(
            runner=runner,
            task_text=plan.get("runner_task") or "",
            project_root=project_root,
            work_dir=work_dir,
            base_url=base_url,
            api_key=api_key,
            model=model,
            command_template=command_template,
            cli_root=cli_root,
            timeout_seconds=timeout_seconds,
        )
        workspace_dir = self._workspace_dir(workspace_id)
        metadata = self._read_metadata(workspace_dir)
        metadata["updated_at"] = _now()
        metadata["last_developer_run"] = {
            "runner": runner,
            "status": result.get("status"),
            "changed_files": result.get("changed_files") or [],
            "task_file": result.get("task_file"),
            "finished_at": result.get("finished_at"),
        }
        self._write_metadata(workspace_dir, metadata)
        return {"plan": plan, "runner_result": result}

    def run_open_claude(
        self,
        workspace_id: str,
        *,
        task: str,
        base_url: str = "",
        api_key: str = "",
        work_dir: str = "",
        cli_root: str = "",
        model: str = "",
        timeout_seconds: int = 900,
    ) -> dict[str, Any]:
        project_root = self._current_project_root(workspace_id)
        run_dir = Path(work_dir).expanduser().resolve() if work_dir else project_root.resolve()
        if not run_dir.exists() or not run_dir.is_dir():
            raise FileNotFoundError(f"工作目录不存在：{run_dir}")
        effective_cli_root = Path(cli_root).expanduser().resolve() if cli_root else OPEN_CLAUDE_ROOT.resolve()
        effective_cli = effective_cli_root / "dist" / "cli.js"
        if not effective_cli.exists():
            raise FileNotFoundError(f"open_claude CLI 不存在：{effective_cli}")
        task_text = self._build_open_claude_task(project_root, run_dir, task)
        task_file = project_root / "APD_OPEN_CLAUDE_TASK.md"
        task_file.write_text(task_text, encoding="utf-8")
        before = _snapshot_files(run_dir)
        env = os.environ.copy()
        env.pop("ANTHROPIC_BASE_URL", None)
        env.pop("ANTHROPIC_AUTH_TOKEN", None)
        if base_url.strip():
            env["ANTHROPIC_BASE_URL"] = base_url.strip()
        if api_key.strip():
            env["ANTHROPIC_API_KEY"] = api_key.strip()
            env["ANTHROPIC_AUTH_TOKEN"] = api_key.strip()
        if model.strip():
            env["ANTHROPIC_MODEL"] = model.strip()
        timeout_seconds = max(30, min(int(timeout_seconds or 900), 3600))
        command = [
            "node",
            "--enable-source-maps",
            str(effective_cli),
            "--dangerously-skip-permissions",
            "--add-dir",
            str(run_dir),
            "-p",
            "--output-format",
            "text",
            task_text,
        ]
        started_at = _now()
        try:
            completed = subprocess.run(
                command,
                cwd=str(run_dir),
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=timeout_seconds,
            )
            output = completed.stdout or ""
            return_code = completed.returncode
            status = "completed" if return_code == 0 else "failed"
        except subprocess.TimeoutExpired as exc:
            output = exc.stdout or ""
            return_code = 124
            status = "timeout"
        after = _snapshot_files(run_dir)
        changed_files = _changed_files(before, after)
        metadata = self._read_metadata(self._workspace_dir(workspace_id))
        metadata["updated_at"] = _now()
        metadata["last_open_claude_run"] = {
            "status": status,
            "return_code": return_code,
            "work_dir": str(run_dir),
            "task_file": str(task_file),
            "changed_files": changed_files,
            "started_at": started_at,
            "finished_at": _now(),
        }
        self._write_metadata(self._workspace_dir(workspace_id), metadata)
        return {
            "runner": "open_claude",
            "status": status,
            "return_code": return_code,
            "command_preview": "cd " + str(effective_cli_root) + " && env -u ANTHROPIC_BASE_URL -u ANTHROPIC_AUTH_TOKEN node --enable-source-maps dist/cli.js --dangerously-skip-permissions -p <task>",
            "cli_root": str(effective_cli_root),
            "work_dir": str(run_dir),
            "task_file": str(task_file),
            "base_url_configured": bool(base_url.strip()),
            "api_key_configured": bool(api_key.strip()),
            "model": model.strip(),
            "changed_files": changed_files,
            "stdout": _mask_secret(output, api_key),
            "started_at": started_at,
            "finished_at": _now(),
            "next_step": "如果改动符合预期，点击运行当前版本验收；满意后保存新版本。",
        }

    def _build_open_claude_task(self, project_root: Path, run_dir: Path, task: str) -> str:
        task = str(task or "").strip()
        if not task:
            raise ValueError("请先填写要交给 open_claude 的开发任务")
        return f"""你是 APD Agent 开发台调用的 open_claude 工程开发 Runner。\n\n目标：\n{task}\n\n当前 Agent 工作区：\n{project_root}\n\n本次执行目录：\n{run_dir}\n\n请遵守：\n1. 优先理解这是一个 Agent 架构改造任务，不只是普通代码修改。\n2. 优先关注 Context Pack、Intent Planner、OpCall、Validator、Executor、Tool、Workflow、State、Memory、Artifact、Trace 是否合理。\n3. 尽量小步修改，避免无关重��。\n4. 不要读取或输出密钥、.env、token。\n5. 改完请总结：改了哪些文件、为什么改、如何验证、风险是什么。\n6. 如果无法完成，请说明阻塞原因和下一步建议。\n""".strip()

    def save_version(self, workspace_id: str, message: str = "") -> dict[str, Any]:
        workspace_dir = self._workspace_dir(workspace_id)
        metadata = self._read_metadata(workspace_dir)
        project_name = str(metadata.get("project_name") or metadata.get("name") or "agent-workspace")
        current_project_root = workspace_dir / "current" / project_name
        if not current_project_root.exists():
            raise FileNotFoundError(f"当前项目不存在：{current_project_root}")
        next_number = len(metadata.get("versions") or []) + 1
        version_id = f"v{next_number}"
        snapshot_parent = workspace_dir / "versions" / version_id
        snapshot_project_root = snapshot_parent / project_name
        if snapshot_parent.exists():
            shutil.rmtree(snapshot_parent)
        snapshot_parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(current_project_root, snapshot_project_root)
        summary = self._summarize_project(snapshot_project_root)
        version = {
            "version_id": version_id,
            "version_number": next_number,
            "message": message or f"保存版本 {version_id}",
            "snapshot_dir": str(snapshot_project_root),
            "created_at": _now(),
            "summary": summary,
            "run_result": {},
        }
        metadata.setdefault("versions", []).append(version)
        metadata["current_version"] = version_id
        metadata["updated_at"] = _now()
        self._write_metadata(workspace_dir, metadata)
        return version

    def rollback(self, workspace_id: str, version_id: str) -> dict[str, Any]:
        workspace_dir = self._workspace_dir(workspace_id)
        metadata = self._read_metadata(workspace_dir)
        project_name = str(metadata.get("project_name") or metadata.get("name") or "agent-workspace")
        version = self._find_version(metadata, version_id)
        snapshot_project_root = Path(str(version.get("snapshot_dir") or ""))
        if not snapshot_project_root.exists():
            snapshot_project_root = workspace_dir / "versions" / version_id / project_name
        if not snapshot_project_root.exists():
            raise FileNotFoundError(f"版本快照不存在：{version_id}")
        current_parent = workspace_dir / "current"
        current_project_root = current_parent / project_name
        if current_project_root.exists():
            shutil.rmtree(current_project_root)
        current_parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(snapshot_project_root, current_project_root)
        metadata["current_version"] = version_id
        metadata["updated_at"] = _now()
        metadata["last_rollback"] = {"version_id": version_id, "at": _now()}
        self._write_metadata(workspace_dir, metadata)
        return self.get_workspace(workspace_id)

    def start_debug_session(self, workspace_id: str) -> dict[str, Any]:
        existing = self.debug_runs.get(workspace_id)
        if existing and existing.process.poll() is None:
            return {
                "workspace": self.get_workspace(workspace_id),
                "base_url": existing.base_url,
                "debug_session": {"workspace_id": workspace_id, "status": "running", "port": existing.port, "created_at": existing.created_at},
            }
        workspace_dir = self._workspace_dir(workspace_id)
        metadata = self._read_metadata(workspace_dir)
        project_name = str(metadata.get("project_name") or metadata.get("name") or "agent-workspace")
        project_root = workspace_dir / "current" / project_name
        backend_root = project_root / "backend"
        if not backend_root.exists():
            raise FileNotFoundError(f"后端目录不存在：{backend_root}")
        port = _find_free_port()
        run = DevStudioRun(
            workspace_id=workspace_id,
            project_name=project_name,
            project_root=project_root,
            backend_root=backend_root,
            port=port,
            process=subprocess.Popen(
                ["python3", "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
                cwd=str(backend_root),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            ),
        )
        startup = _wait_http(f"{run.base_url}/health", timeout=8)
        if not startup.get("ok"):
            logs = _read_available_logs(run.process, limit=120)
            run.stop()
            raise RuntimeError(f"调试会话启动失败：{startup.get('error') or 'health check failed'}\n{logs}")
        self.debug_runs[workspace_id] = run
        return {
            "workspace": self.get_workspace(workspace_id),
            "base_url": run.base_url,
            "startup": startup,
            "debug_session": {"workspace_id": workspace_id, "status": "running", "port": run.port, "created_at": run.created_at},
        }

    def debug_turn(self, workspace_id: str, *, message: str, context: dict[str, Any] | None = None, max_steps: int = 6) -> dict[str, Any]:
        session = self.start_debug_session(workspace_id)
        run = self.debug_runs[workspace_id]
        history = self.debug_histories.setdefault(workspace_id, [])
        payload_context = dict(context) if isinstance(context, dict) else {}
        incoming_history = payload_context.get("conversation_history") if isinstance(payload_context.get("conversation_history"), list) else []
        effective_history = (incoming_history or history)[-12:]
        payload_context["conversation_history"] = effective_history
        payload_context.setdefault("recent_turns", history[-8:])
        payload_context.setdefault("memory", [{"type": "debug_turn", "role": item.get("role"), "content": item.get("content"), "turn": item.get("turn")} for item in history[-8:]])
        payload_context["debug_session"] = {"workspace_id": workspace_id, "turn_index": len(history) // 2 + 1, "history_items": len(history)}
        agent = _post_json(f"{run.base_url}/agent/run", {"message": message, "context": payload_context})
        workflow = _post_json(f"{run.base_url}/workflow/run", {"message": message, "context": payload_context, "max_steps": max(1, min(int(max_steps or 6), 30))})
        store = _get_json(f"{run.base_url}/store/snapshot")
        tools = _get_json(f"{run.base_url}/tools")
        assistant_message = _extract_assistant_message(agent)
        history.append({"role": "user", "content": message, "turn": len(history) // 2 + 1, "at": _now()})
        history.append({"role": "assistant", "content": assistant_message, "turn": len(history) // 2, "at": _now()})
        self.debug_histories[workspace_id] = history[-24:]
        result = {
            **session,
            "message": message,
            "agent_run": agent,
            "workflow_run": workflow,
            "store_snapshot": store,
            "tools": tools,
            "logs": _read_available_logs(run.process, limit=80),
            "debug_context": {"history_items": len(self.debug_histories.get(workspace_id) or []), "conversation_history": self.debug_histories.get(workspace_id, [])[-12:]},
            "debug_session": {**(session.get("debug_session") or {}), "status": "running"},
        }
        diagnosis = _diagnose_debug_turn(result, payload_context)
        result["diagnosis"] = diagnosis
        result["repair_task"] = _build_debug_repair_task(message, assistant_message, result, diagnosis)
        workspace_dir = self._workspace_dir(workspace_id)
        metadata = self._read_metadata(workspace_dir)
        metadata["last_debug_turn"] = {"at": _now(), "message": message, "result": _compact_run_result(result)}
        metadata["updated_at"] = _now()
        self._write_metadata(workspace_dir, metadata)
        return result

    def stop_debug_session(self, workspace_id: str) -> dict[str, Any]:
        self.debug_histories.pop(workspace_id, None)
        run = self.debug_runs.pop(workspace_id, None)
        if run:
            run.stop()
            return {"workspace_id": workspace_id, "status": "stopped", "port": run.port}
        return {"workspace_id": workspace_id, "status": "not_running"}

    def restart_debug_session(self, workspace_id: str) -> dict[str, Any]:
        history = list(self.debug_histories.get(workspace_id) or [])
        stopped = self.stop_debug_session(workspace_id)
        if history:
            self.debug_histories[workspace_id] = history[-24:]
        started = self.start_debug_session(workspace_id)
        return {**started, "debug_context": {"history_items": len(self.debug_histories.get(workspace_id) or [])}, "restart": {"previous": stopped, "status": "restarted", "at": _now()}}

    def clear_debug_session(self, workspace_id: str) -> dict[str, Any]:
        self.debug_histories.pop(workspace_id, None)
        stopped = self.stop_debug_session(workspace_id)
        started = self.start_debug_session(workspace_id)
        return {**started, "debug_context": {"history_items": 0, "conversation_history": []}, "clear": {"previous": stopped, "status": "cleared", "at": _now()}}

    def save_debug_testcase(self, workspace_id: str, testcase: dict[str, Any]) -> dict[str, Any]:
        workspace_dir = self._workspace_dir(workspace_id)
        metadata = self._read_metadata(workspace_dir)
        items = metadata.setdefault("debug_testcases", [])
        item = {
            "id": f"case_{uuid.uuid4().hex[:10]}",
            "created_at": _now(),
            "message": str(testcase.get("message") or ""),
            "expected": str(testcase.get("expected") or testcase.get("expectation") or ""),
            "assistant_message": str(testcase.get("assistant_message") or ""),
            "diagnosis": testcase.get("diagnosis") if isinstance(testcase.get("diagnosis"), dict) else {},
            "context": testcase.get("context") if isinstance(testcase.get("context"), dict) else {},
        }
        items.append(item)
        metadata["debug_testcases"] = items[-100:]
        metadata["updated_at"] = _now()
        self._write_metadata(workspace_dir, metadata)
        return {"workspace_id": workspace_id, "testcase": item, "count": len(metadata["debug_testcases"])}

    def list_debug_testcases(self, workspace_id: str) -> dict[str, Any]:
        workspace_dir = self._workspace_dir(workspace_id)
        metadata = self._read_metadata(workspace_dir)
        return {"workspace_id": workspace_id, "testcases": list(metadata.get("debug_testcases") or [])}

    def replay_debug_testcase(self, workspace_id: str, case_id: str) -> dict[str, Any]:
        cases = self.list_debug_testcases(workspace_id).get("testcases") or []
        case = next((item for item in cases if item.get("id") == case_id), None)
        if not case:
            raise KeyError("testcase not found")
        result = self.debug_turn(workspace_id, message=str(case.get("message") or ""), context=case.get("context") if isinstance(case.get("context"), dict) else {}, max_steps=8)
        result["replay"] = {"case_id": case_id, "expected": case.get("expected") or ""}
        return result

    def run_workspace(self, workspace_id: str, *, message: str, context: dict[str, Any] | None = None, max_steps: int = 3) -> dict[str, Any]:
        workspace_dir = self._workspace_dir(workspace_id)
        metadata = self._read_metadata(workspace_dir)
        project_name = str(metadata.get("project_name") or metadata.get("name") or "agent-workspace")
        project_root = workspace_dir / "current" / project_name
        backend_root = project_root / "backend"
        if not backend_root.exists():
            raise FileNotFoundError(f"后端目录不存在：{backend_root}")
        port = _find_free_port()
        run = DevStudioRun(
            workspace_id=workspace_id,
            project_name=project_name,
            project_root=project_root,
            backend_root=backend_root,
            port=port,
            process=subprocess.Popen(
                ["python3", "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
                cwd=str(backend_root),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            ),
        )
        logs = ""
        try:
            startup = _wait_http(f"{run.base_url}/health", timeout=8)
            if not startup.get("ok"):
                logs = _read_available_logs(run.process, limit=120)
                raise RuntimeError(f"工作区启动失败：{startup.get('error') or 'health check failed'}\n{logs}")
            payload_context = context if isinstance(context, dict) else {}
            agent = _post_json(f"{run.base_url}/agent/run", {"message": message, "context": payload_context})
            workflow = _post_json(f"{run.base_url}/workflow/run", {"message": message, "context": payload_context, "max_steps": max(1, min(int(max_steps or 3), 30))})
            store = _get_json(f"{run.base_url}/store/snapshot")
            tools = _get_json(f"{run.base_url}/tools")
            result = {
                "workspace": self.get_workspace(workspace_id),
                "base_url": run.base_url,
                "startup": startup,
                "agent_run": agent,
                "workflow_run": workflow,
                "store_snapshot": store,
                "tools": tools,
                "logs": _read_available_logs(run.process, limit=80),
                "explanation": {
                    "做到哪一步": "APD 已真实启动当前工作区里的 FastAPI Agent 项目，并调用 /agent/run、/workflow/run、/store/snapshot、/tools。",
                    "为什么有业务感受还不强": "v1 运行的是生成骨架和 dry-run 工具；真实业务效果需要继续在工作区里接 LLM Planner、真实工具、真实文档或数据库。",
                    "下一步改哪里": ["backend/app/planner.py", "backend/app/executor.py", "backend/app/tools.py", "backend/app/workflow.py"],
                },
            }
            metadata["last_run"] = {"at": _now(), "message": message, "result": _compact_run_result(result)}
            metadata["updated_at"] = _now()
            self._write_metadata(workspace_dir, metadata)
            return result
        finally:
            run.stop()

    def download_zip(self, workspace_id: str, version_id: str = "current") -> tuple[bytes, str]:
        workspace_dir = self._workspace_dir(workspace_id)
        metadata = self._read_metadata(workspace_dir)
        project_name = str(metadata.get("project_name") or metadata.get("name") or "agent-workspace")
        if version_id and version_id != "current":
            version = self._find_version(metadata, version_id)
            project_root = Path(str(version.get("snapshot_dir") or ""))
            if not project_root.exists():
                project_root = workspace_dir / "versions" / version_id / project_name
            label = version_id
        else:
            project_root = workspace_dir / "current" / project_name
            label = "current"
        if not project_root.exists():
            raise FileNotFoundError(f"项目不存在：{project_root}")
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for file_path in sorted(project_root.rglob("*")):
                if file_path.is_file():
                    archive.write(file_path, file_path.relative_to(project_root.parent))
        filename = f"{project_name}-{label}.zip"
        return buffer.getvalue(), filename

    def _current_project_root(self, workspace_id: str) -> Path:
        workspace_dir = self._workspace_dir(workspace_id)
        metadata = self._read_metadata(workspace_dir)
        project_name = str(metadata.get("project_name") or metadata.get("name") or "agent-workspace")
        project_root = workspace_dir / "current" / project_name
        if not project_root.exists():
            raise FileNotFoundError(f"当前项目不存在：{project_root}")
        return project_root

    def _validate_editable_path(self, rel_path: str) -> str:
        safe_rel = str(rel_path or "").strip().replace("\\", "/")
        if safe_rel not in EDITABLE_FILES:
            raise ValueError(f"不允许编辑此文件：{safe_rel}")
        if safe_rel.startswith("/") or ".." in Path(safe_rel).parts:
            raise ValueError(f"非法文件路径：{safe_rel}")
        return safe_rel

    def _workspace_dir(self, workspace_id: str) -> Path:
        safe = "".join(ch for ch in str(workspace_id) if ch.isalnum() or ch in "_-")
        if not safe.startswith("ws-"):
            raise KeyError(workspace_id)
        path = self.root_dir / safe
        if not (path / "metadata.json").exists():
            raise KeyError(workspace_id)
        return path

    def _read_metadata(self, workspace_dir: Path) -> dict[str, Any]:
        return json.loads((workspace_dir / "metadata.json").read_text(encoding="utf-8"))

    def _write_metadata(self, workspace_dir: Path, metadata: dict[str, Any]) -> None:
        workspace_dir.mkdir(parents=True, exist_ok=True)
        (workspace_dir / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

    def _find_version(self, metadata: dict[str, Any], version_id: str) -> dict[str, Any]:
        for version in metadata.get("versions") or []:
            if version.get("version_id") == version_id:
                return version
        raise KeyError(version_id)

    def _public_workspace(self, metadata: dict[str, Any]) -> dict[str, Any]:
        workspace_dir = self.root_dir / str(metadata.get("workspace_id") or "")
        project_name = str(metadata.get("project_name") or metadata.get("name") or "agent-workspace")
        current_root = workspace_dir / "current" / project_name
        public = dict(metadata)
        public["project_root"] = str(current_root)
        public["summary"] = self._summarize_project(current_root) if current_root.exists() else {}
        public["beginner_guide"] = {
            "工作区": "APD 给你生成的一份可持续修改的 Agent 项目，不是一次性临时 Demo。",
            "版本": "每次觉得效果还行，就拍一张快照，后面不满意可以回滚。",
            "运行": "真实启动当前项目，调用接口看 Agent 链路是否跑通。",
            "下载": "把当前项目打包拿走，可以继续开发或部署。",
        }
        return public

    def _summarize_project(self, project_root: Path) -> dict[str, Any]:
        files = [path for path in project_root.rglob("*") if path.is_file()] if project_root.exists() else []
        key_files = [
            "backend/app/main.py",
            "backend/app/planner.py",
            "backend/app/executor.py",
            "backend/app/tools.py",
            "backend/app/workflow.py",
            "backend/app/store.py",
            "backend/protocol.json",
            "frontend/AgentPlayground.vue",
        ]
        return {
            "file_count": len(files),
            "key_files": [{"path": rel, "exists": (project_root / rel).exists()} for rel in key_files],
            "editable_focus": [
                {"path": "backend/app/planner.py", "purpose": "把用户话术理解成受控操作调用"},
                {"path": "backend/app/executor.py", "purpose": "真正执行业务动作，替换 mock"},
                {"path": "backend/app/tools.py", "purpose": "接知识库、文档、检索、导出等真实工具"},
                {"path": "backend/app/workflow.py", "purpose": "把复杂任务拆成可观察节点"},
            ],
        }


def _snapshot_files(root: Path) -> dict[str, tuple[int, int]]:
    result: dict[str, tuple[int, int]] = {}
    root = root.resolve()
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel_parts = path.relative_to(root).parts
        if any(part in _IGNORE_SCAN_DIRS for part in rel_parts):
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        result[str(path.relative_to(root))] = (int(stat.st_mtime_ns), int(stat.st_size))
    return result


def _changed_files(before: dict[str, tuple[int, int]], after: dict[str, tuple[int, int]]) -> list[dict[str, str]]:
    changed: list[dict[str, str]] = []
    for rel in sorted(set(before) | set(after)):
        if rel not in before:
            changed.append({"path": rel, "change": "added"})
        elif rel not in after:
            changed.append({"path": rel, "change": "deleted"})
        elif before[rel] != after[rel]:
            changed.append({"path": rel, "change": "modified"})
    return changed[:300]


def _mask_secret(text: str, secret: str) -> str:
    if not secret:
        return text
    masked = text.replace(secret, "***")
    if len(secret) > 12:
        masked = masked.replace(secret[:6], "***").replace(secret[-6:], "***")
    return masked


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def _debug_level(label: str, key: str, file: str, reason: str, action: str, confidence: float) -> dict[str, Any]:
    return {"label": label, "key": key, "file": file, "reason": reason, "action": action, "confidence": confidence}


def _diagnose_debug_turn(result: dict[str, Any], context: dict[str, Any] | None = None) -> dict[str, Any]:
    context = context or {}
    agent = result.get("agent_run") or {}
    workflow = result.get("workflow_run") or {}
    store = result.get("store_snapshot") or {}
    op_call = agent.get("op_call") or {}
    operation = str(op_call.get("operation") or "")
    confidence = float(op_call.get("confidence") or 0) if isinstance(op_call, dict) else 0.0
    validator_results = agent.get("validator_results") or []
    failed_validators = [item for item in validator_results if isinstance(item, dict) and not item.get("ok")]
    permission = agent.get("permission_check") or {}
    tool_results = agent.get("tool_results") or []
    node_states = workflow.get("node_states") or {}
    trace_events = store.get("trace_events") or []
    runtime_summary = store.get("runtime_summary") or {}
    history = context.get("conversation_history") if isinstance(context.get("conversation_history"), list) else []
    levels: list[dict[str, Any]] = []

    if not history and int((context.get("debug_session") or {}).get("history_items") or 0) == 0:
        levels.append(_debug_level("上下文/记忆", "context", "backend/app/runtime.py", "本轮是新会话或没有带历史；如果用户依赖上文，回复可能不连贯。", "检查 conversation_history、STORE.read_context 和 memory 是否传入 planner/executor。", 0.74))
    if not operation or operation == "ask_clarification" or confidence < 0.45:
        levels.append(_debug_level("意图识别", "planner", "backend/app/planner.py", "操作未稳定命中或置信度较低。", "优化 planner 的意图识别、参数抽取或接入 LLM Planner。", 0.86))
    if failed_validators or permission.get("status") in {"failed", "denied", "forbidden"}:
        levels.append(_debug_level("校验/权限", "validator", "backend/app/validators.py", "校验失败或权限状态阻止执行。", "检查 validators.py 的规则是否过严、过松或缺少用户提示。", 0.84))
    if not tool_results:
        levels.append(_debug_level("工具调用", "tools", "backend/app/tools.py", "本轮没有工具结果；如果需求依赖检索、文件、模板或导出，这里可能缺能力。", "补齐 tools.py 的真实工具适配，并在 runtime/executor 中调用。", 0.68))
    if not node_states:
        levels.append(_debug_level("流程编排", "workflow", "backend/app/workflow.py", "没有 workflow 节点状态；复杂任务难以定位每一步。", "把复杂任务拆成 workflow 节点，并记录每个节点输入输出。", 0.62))
    assistant = _extract_assistant_message(agent)
    if operation and operation != "ask_clarification" and not failed_validators and assistant:
        levels.append(_debug_level("回复/执行", "executor", "backend/app/executor.py", "操作和校验基本可用，若用户觉得效果不对，通常是执行逻辑或回复组织问题。", "检查 executor.py 是否使用了工具结果、模板、上下文和业务规则。", 0.78))
    if not trace_events:
        levels.append(_debug_level("可观测性", "trace", "backend/app/runtime.py", "store 里没有 trace_events，调试面板无法精确还原过程。", "在 runtime/workflow/tools/executor 中补 trace 记录。", 0.72))

    if not levels:
        levels.append(_debug_level("继续压测", "ok", "tests/test_protocol_shape.py", "当前没有明显结构性问题。", "保存本轮样本，继续用真实业务话术验证。", 0.55))
    primary = max(levels, key=lambda item: item.get("confidence", 0))
    return {
        "summary": f"本轮优先看：{primary['label']}，建议改 {primary['file']}",
        "primary_level": primary,
        "levels": levels,
        "suggested_files": list(dict.fromkeys(item["file"] for item in levels if item.get("file"))),
        "next_test_messages": ["用同一场景再追问一句，验证上下文是否连续", "让 Agent 修改上一轮结果，验证 memory/artifact 是否生效"],
        "runtime_summary": runtime_summary,
    }


def _build_debug_repair_task(message: str, assistant_message: str, result: dict[str, Any], diagnosis: dict[str, Any]) -> str:
    context = result.get("debug_context") or {}
    agent = result.get("agent_run") or {}
    workflow = result.get("workflow_run") or {}
    store = result.get("store_snapshot") or {}
    return (
        "请修复当前 Agent 调试中暴露的问题。\n\n"
        f"【用户本轮话术】\n{message}\n\n"
        f"【Agent 当前回复】\n{assistant_message}\n\n"
        f"【历史上下文】\n{json.dumps(context.get('conversation_history') or [], ensure_ascii=False, indent=2)[:4000]}\n\n"
        f"【APD 诊断结论】\n{diagnosis.get('summary')}\n\n"
        f"【建议修改文件】\n{', '.join(diagnosis.get('suggested_files') or [])}\n\n"
        f"【op_call】\n{json.dumps(agent.get('op_call') or {}, ensure_ascii=False, indent=2)[:2000]}\n\n"
        f"【validator_results】\n{json.dumps(agent.get('validator_results') or [], ensure_ascii=False, indent=2)[:2000]}\n\n"
        f"【tool_results】\n{json.dumps(agent.get('tool_results') or [], ensure_ascii=False, indent=2)[:2000]}\n\n"
        f"【workflow 节点】\n{json.dumps(workflow.get('node_states') or {}, ensure_ascii=False, indent=2)[:3000]}\n\n"
        f"【memory/artifact 摘要】\n{json.dumps(store.get('runtime_summary') or {}, ensure_ascii=False, indent=2)}\n\n"
        "【请你做】\n"
        "1. 先阅读建议修改文件，确认根因。\n"
        "2. 只做与本轮问题相关的最小修改。\n"
        "3. 修改后运行现有测试或最小接口验证。\n"
        "4. 最后告诉我改了哪些文件、如何验证、下一轮应该用什么话术复测。\n"
    )


def _extract_assistant_message(agent: dict[str, Any]) -> str:
    for key in ("assistant_message", "reply", "response", "final_response", "answer", "output", "message"):
        value = agent.get(key) if isinstance(agent, dict) else None
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _compact_run_result(result: dict[str, Any]) -> dict[str, Any]:
    agent = result.get("agent_run") or {}
    workflow = result.get("workflow_run") or {}
    store = result.get("store_snapshot") or {}
    return {
        "op_call": agent.get("op_call"),
        "next_action": agent.get("next_action"),
        "permission": (agent.get("permission_check") or {}).get("status"),
        "workflow_status": workflow.get("status"),
        "artifact_versions": len(agent.get("artifact_versions") or []),
        "trace_events": len(store.get("trace_events") or []),
    }


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
        with request.urlopen(req, timeout=180) as response:
            return json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(text or str(exc)) from exc


def _read_available_logs(process: subprocess.Popen[str], limit: int = 120) -> str:
    return ""
