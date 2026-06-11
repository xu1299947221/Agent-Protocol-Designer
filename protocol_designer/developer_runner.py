from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

OPEN_CLAUDE_ROOT = Path(os.getenv("APD_OPEN_CLAUDE_ROOT") or "/home/data/rag/open_claude/Openclaude-openclaude")
IGNORE_SCAN_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", "dist"}


RUNNER_LABELS = {
    "open_claude": "open_claude",
    "codex": "Codex CLI",
    "claude_code": "Claude Code CLI",
    "custom_shell": "自定义 Shell",
}


def build_agent_engineering_plan(session: dict[str, Any], workspace: dict[str, Any], user_request: str) -> dict[str, Any]:
    request = str(user_request or "").strip()
    if not request:
        raise ValueError("请先填写 Agent 改造需求")
    protocol = session.get("protocol") or {}
    operations = [op for op in protocol.get("operations") or [] if isinstance(op, dict)]
    workflow_nodes = [node for node in (protocol.get("workflow") or {}).get("nodes") or [] if isinstance(node, dict)]
    text = request.lower()
    cn_text = request
    agent_types = ["Agent Harness 改造"]
    architecture_layers = [
        {"name": "Context Pack（上下文包）", "why": "改造前要明确本轮给 LLM / Planner 看的材料。"},
        {"name": "Intent Planner（意图规划器）", "why": "需要确认用户话术如何绑定到受控 OpCall。"},
        {"name": "OpCall（操作调用）", "why": "工程实现不能让 LLM 随意执行，只能落到协议内操作。"},
        {"name": "Validator（校验器）", "why": "执行前要检查参数、状态、权限和缺失信息。"},
        {"name": "Executor（执行器）", "why": "真实业务逻辑应在执行器或工具适配器中落地。"},
        {"name": "Trace（过程日志）", "why": "改完后要能解释每一步为什么这样做。"},
    ]
    recommended_files = [
        {"path": "backend/app/planner.py", "reason": "调整用户意图到 OpCall 的绑定规则。"},
        {"path": "backend/app/executor.py", "reason": "落地真实业务执行和用户可见回复。"},
        {"path": "backend/app/tools.py", "reason": "接入外部工具、知识库、检索、导出等能力。"},
        {"path": "backend/app/workflow.py", "reason": "复杂任务需要节点化执行和状态记录。"},
        {"path": "backend/app/validators.py", "reason": "补执行前的必要校验。"},
    ]
    if any(word in text or word in cn_text for word in ("rag", "知识库", "检索", "素材", "向量", "graph", "图谱")):
        agent_types.extend(["RAG Tool Use Agent", "Knowledge/RAG Workflow"])
        architecture_layers.extend([
            {"name": "Knowledge Policy（知识检索策略）", "why": "需要定义什么时候检索、查什么、结果如何进入上下文。"},
            {"name": "Tool Registry（工具注册表）", "why": "检索必须通过受控工具调用，而不是写散逻辑。"},
        ])
        _ensure_file(recommended_files, "backend/app/tools.py", "实现 search / retrieve 等知识库工具适配器。")
        _ensure_file(recommended_files, "backend/app/workflow.py", "增加检索节点，并让生成节点依赖检索结果。")
    if any(word in text or word in cn_text for word in ("workflow", "流程", "节点", "并行", "审批", "确认", "招标", "投标", "报告")):
        agent_types.append("Workflow Agent")
        architecture_layers.append({"name": "Workflow Runtime（工作流运行时）", "why": "多步骤任务需要节点、边、暂停点和失败恢复。"})
        _ensure_file(recommended_files, "backend/app/workflow.py", "维护节点级执行、依赖关系和人工确认点。")
    if any(word in text or word in cn_text for word in ("记忆", "memory", "偏好", "长期", "历史")):
        agent_types.append("Memory-aware Agent")
        architecture_layers.append({"name": "Memory Policy（记忆策略）", "why": "需要定义读什么记忆、何时写入、谁确认、如何纠错。"})
        _ensure_file(recommended_files, "backend/app/store.py", "扩展记忆、状态和 Trace 存储。")
    if any(word in text or word in cn_text for word in ("文档", "docx", "导出", "报告", "产物", "编辑")):
        agent_types.append("Artifact Producing Agent")
        architecture_layers.append({"name": "Artifact Model（产物模型）", "why": "文档、报告、导出文件需要版本、来源和回滚。"})
        _ensure_file(recommended_files, "backend/app/executor.py", "生成或更新产物，并返回 artifact_versions。")
    risks = [
        "不要让 Runner 绕过协议直接写死业务流程，应该保留 OpCall / Validator / Executor 边界。",
        "不要把 API Key、.env、token 写入代码、日志或任务包。",
        "不要大范围重构生成工程，优先小步修改并保留可运行接口。",
        "改完必须能通过 /agent/run、/workflow/run、/store/snapshot 查看结果。",
    ]
    acceptance_checks = [
        {"name": "/agent/run", "expect": "能把测试话术绑定到合理 op_call，并返回 assistant_message。"},
        {"name": "/workflow/run", "expect": "如果是多步骤场景，能看到 node_states、artifacts 和 trace。"},
        {"name": "/store/snapshot", "expect": "能看到 state、memory、artifact_versions、trace_events。"},
        {"name": "架构检查", "expect": "改动仍然保留 Context Pack → Planner → OpCall → Validator → Executor → Observation → Trace。"},
    ]
    plan = {
        "request": request,
        "workspace_id": workspace.get("workspace_id"),
        "workspace_name": workspace.get("name") or workspace.get("project_name"),
        "project_root": workspace.get("project_root"),
        "agent_types": _dedupe(agent_types),
        "architecture_layers": _dedupe_dicts(architecture_layers, "name"),
        "recommended_files": _dedupe_dicts(recommended_files, "path"),
        "risks": risks,
        "acceptance_checks": acceptance_checks,
        "current_protocol_summary": {
            "project_name": protocol.get("project_name") or workspace.get("name"),
            "operation_count": len(operations),
            "operations": [op.get("name") for op in operations[:20]],
            "workflow_node_count": len(workflow_nodes),
            "workflow_nodes": [node.get("id") or node.get("name") for node in workflow_nodes[:20]],
        },
    }
    plan["runner_task"] = build_runner_task(plan)
    return plan


def build_runner_task(plan: dict[str, Any]) -> str:
    layers = "\n".join(f"- {item.get('name')}: {item.get('why')}" for item in plan.get("architecture_layers") or [])
    files = "\n".join(f"- {item.get('path')}: {item.get('reason')}" for item in plan.get("recommended_files") or [])
    risks = "\n".join(f"- {item}" for item in plan.get("risks") or [])
    checks = "\n".join(f"- {item.get('name')}: {item.get('expect')}" for item in plan.get("acceptance_checks") or [])
    protocol_summary = json.dumps(plan.get("current_protocol_summary") or {}, ensure_ascii=False, indent=2)
    return f"""# APD Agent 工程开发任务包

你是 APD 开发台调用的工程开发 Runner。你的用户会编程，但需要你按 Agent 架构边界实现，不要把它当普通 CRUD 改造。

## 目标需求
{plan.get('request')}

## 当前项目
- 工作区：{plan.get('workspace_name')}
- 项目目录：{plan.get('project_root')}

## APD 判断的 Agent 类型
{chr(10).join('- ' + str(item) for item in plan.get('agent_types') or [])}

## 必须关注的 Agent 架构层
{layers}

## 推荐修改文件
{files}

## 当前协议摘要
```json
{protocol_summary}
```

## 约束和风险
{risks}

## 验收标准
{checks}

## 输出要求
完成后请用中文总结：
1. 你如何理解这个 Agent 架构改造需求。
2. 你改了哪些文件，为什么改。
3. 哪些 Agent 架构层被补强了。
4. 如何运行验收。
5. 仍然存在的风险或下一步建议。

禁止输出任何 API Key、token、.env 内容。尽量小步修改，不要无关重构。
""".strip()


def run_developer_runner(
    *,
    runner: str,
    task_text: str,
    project_root: Path,
    work_dir: str = "",
    base_url: str = "",
    api_key: str = "",
    model: str = "",
    command_template: str = "",
    cli_root: str = "",
    timeout_seconds: int = 900,
) -> dict[str, Any]:
    runner = str(runner or "open_claude").strip() or "open_claude"
    run_dir = Path(work_dir).expanduser().resolve() if work_dir else project_root.resolve()
    if not run_dir.exists() or not run_dir.is_dir():
        raise FileNotFoundError(f"工作目录不存在：{run_dir}")
    task_file = project_root / "APD_DEVELOPER_TASK.md"
    task_file.write_text(task_text, encoding="utf-8")
    before = snapshot_files(run_dir)
    env = _build_env(base_url=base_url, api_key=api_key, model=model)
    command, shell = _build_command(runner, task_text, task_file, run_dir, cli_root, command_template)
    timeout_seconds = max(30, min(int(timeout_seconds or 900), 3600))
    started_at = _now()
    try:
        completed = subprocess.run(
            command,
            cwd=str(run_dir),
            env=env,
            text=True,
            input=task_text if runner in {"codex", "claude_code"} and not command_template else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout_seconds,
            shell=shell,
        )
        output = completed.stdout or ""
        return_code = completed.returncode
        status = "completed" if return_code == 0 else "failed"
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or ""
        return_code = 124
        status = "timeout"
    after = snapshot_files(run_dir)
    changed_files = changed_files_between(before, after)
    finished_at = _now()
    return {
        "runner": runner,
        "runner_label": RUNNER_LABELS.get(runner, runner),
        "status": status,
        "return_code": return_code,
        "command_preview": _command_preview(command, shell),
        "work_dir": str(run_dir),
        "task_file": str(task_file),
        "base_url_configured": bool(str(base_url or "").strip()),
        "api_key_configured": bool(str(api_key or "").strip()),
        "model": str(model or "").strip(),
        "changed_files": changed_files,
        "stdout": mask_secret(output, api_key),
        "started_at": started_at,
        "finished_at": finished_at,
        "next_step": "执行完成后请运行当前 Agent 验收；满意后保存新版本。",
    }


def snapshot_files(root: Path) -> dict[str, tuple[int, int]]:
    result: dict[str, tuple[int, int]] = {}
    root = root.resolve()
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel_parts = path.relative_to(root).parts
        if any(part in IGNORE_SCAN_DIRS for part in rel_parts):
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        result[str(path.relative_to(root))] = (int(stat.st_mtime_ns), int(stat.st_size))
    return result


def changed_files_between(before: dict[str, tuple[int, int]], after: dict[str, tuple[int, int]]) -> list[dict[str, str]]:
    changed: list[dict[str, str]] = []
    for rel in sorted(set(before) | set(after)):
        if rel not in before:
            changed.append({"path": rel, "change": "added"})
        elif rel not in after:
            changed.append({"path": rel, "change": "deleted"})
        elif before[rel] != after[rel]:
            changed.append({"path": rel, "change": "modified"})
    return changed[:300]


def mask_secret(text: str, secret: str) -> str:
    if not secret:
        return text
    masked = str(text or "").replace(secret, "***")
    if len(secret) > 12:
        masked = masked.replace(secret[:6], "***").replace(secret[-6:], "***")
    return masked


def _build_env(*, base_url: str, api_key: str, model: str) -> dict[str, str]:
    env = os.environ.copy()
    for key in ("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENAI_BASE_URL"):
        if key in env and key in {"ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN"}:
            env.pop(key, None)
    if base_url.strip():
        env["ANTHROPIC_BASE_URL"] = base_url.strip()
        env["OPENAI_BASE_URL"] = base_url.strip()
    if api_key.strip():
        env["ANTHROPIC_API_KEY"] = api_key.strip()
        env["ANTHROPIC_AUTH_TOKEN"] = api_key.strip()
        env["OPENAI_API_KEY"] = api_key.strip()
    if model.strip():
        env["ANTHROPIC_MODEL"] = model.strip()
        env["OPENAI_MODEL"] = model.strip()
    return env


def _build_command(runner: str, task_text: str, task_file: Path, run_dir: Path, cli_root: str, command_template: str) -> tuple[Any, bool]:
    if command_template.strip():
        command = command_template.format(
            task=shlex.quote(task_text),
            task_file=shlex.quote(str(task_file)),
            project_root=shlex.quote(str(run_dir)),
        )
        return command, True
    if runner == "open_claude":
        effective_cli_root = Path(cli_root).expanduser().resolve() if cli_root else OPEN_CLAUDE_ROOT.resolve()
        cli = effective_cli_root / "dist" / "cli.js"
        if not cli.exists():
            raise FileNotFoundError(f"open_claude CLI 不存在：{cli}")
        return [
            "node",
            "--enable-source-maps",
            str(cli),
            "--dangerously-skip-permissions",
            "--add-dir",
            str(run_dir),
            "-p",
            "--output-format",
            "text",
            task_text,
        ], False
    if runner == "codex":
        return ["codex", "exec", "--skip-git-repo-check", "--dangerously-bypass-approvals-and-sandbox", "-"], False
    if runner == "claude_code":
        return ["claude", "-p", "--dangerously-skip-permissions", task_text], False
    if runner == "custom_shell":
        raise ValueError("custom_shell 需要填写 command_template")
    raise ValueError(f"未知 Runner：{runner}")


def _command_preview(command: Any, shell: bool) -> str:
    if shell:
        return str(command)
    return " ".join(shlex.quote(str(part)) for part in command)


def _ensure_file(files: list[dict[str, str]], path: str, reason: str) -> None:
    if not any(item.get("path") == path for item in files):
        files.append({"path": path, "reason": reason})


def _dedupe(items: list[str]) -> list[str]:
    result: list[str] = []
    for item in items:
        if item and item not in result:
            result.append(item)
    return result


def _dedupe_dicts(items: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        value = str(item.get(key) or "")
        if value and value not in seen:
            seen.add(value)
            result.append(item)
    return result


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
