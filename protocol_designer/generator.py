from __future__ import annotations

import json
import pprint
import re
import shutil
import textwrap
from pathlib import Path
from typing import Any

from .core import build_exports, merge_protocol, EMPTY_PROTOCOL


def safe_project_name(value: str | None) -> str:
    text = (value or "generated-agent").strip().lower()
    text = re.sub(r"[^a-z0-9\u4e00-\u9fff_-]+", "-", text)
    text = text.strip("-_")
    return (text or "generated-agent")[:64].rstrip("-_") or "generated-agent"


def safe_py_name(value: str | None, fallback: str = "operation") -> str:
    text = (value or fallback).strip().lower()
    text = re.sub(r"[^a-z0-9_]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    if not text or text[0].isdigit():
        text = f"{fallback}_{text}" if text else fallback
    return text


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.rstrip() + "\n", encoding="utf-8")


def py_string_literal(value: Any) -> str:
    return json.dumps(str(value or ""), ensure_ascii=False)


def py_literal(value: Any) -> str:
    return pprint.pformat(value, width=100, sort_dicts=False)


def indent_literal(value: Any) -> str:
    return py_literal(value).replace("\n", "\n    ")


def render_backend_models(protocol: dict[str, Any]) -> str:
    objects = [item for item in protocol.get("objects") or [] if isinstance(item, dict)]
    object_sections: list[str] = []
    for obj in objects:
        name = safe_py_name(obj.get("name"), "object")
        class_name = "".join(part.capitalize() for part in name.split("_")) or "BusinessObject"
        desc = py_string_literal(obj.get("description") or obj.get("name") or class_name)
        object_sections.append(
            f"class {class_name}(BaseModel):\n"
            f"    __doc__ = {desc}\n"
            f"    id: str = \"\"\n"
            f"    metadata: dict[str, Any] = Field(default_factory=dict)"
        )
    if not object_sections:
        object_sections.append(
            "class BusinessObject(BaseModel):\n"
            "    \"\"\"TODO: 根据 protocol.objects 补充业务对象字段。\"\"\"\n"
            "    id: str = \"\"\n"
            "    metadata: dict[str, Any] = Field(default_factory=dict)"
        )
    header = textwrap.dedent('''
    from __future__ import annotations

    from typing import Any
    from pydantic import BaseModel, Field


    class AgentRequest(BaseModel):
        message: str
        context: dict[str, Any] = Field(default_factory=dict)


    class OperationCall(BaseModel):
        operation: str
        params: dict[str, Any] = Field(default_factory=dict)
        confidence: float = 0.0
        requires_confirmation: bool = False
        missing_info: list[str] = Field(default_factory=list)
        evidence: list[str] = Field(default_factory=list)


    class ValidatorResult(BaseModel):
        name: str
        ok: bool
        reason: str = ""


    class AgentResponse(BaseModel):
        assistant_message: str
        op_call: OperationCall | None = None
        runtime_mode: str = "demo"
        llm_messages: list[dict[str, Any]] = Field(default_factory=list)
        llm_response: dict[str, Any] = Field(default_factory=dict)
        llm_trace: dict[str, Any] = Field(default_factory=dict)
        validator_results: list[ValidatorResult] = Field(default_factory=list)
        next_action: str = ""
        context_pack: dict[str, Any] = Field(default_factory=dict)
        permission_check: dict[str, Any] = Field(default_factory=dict)
        recovery_plan: dict[str, Any] = Field(default_factory=dict)
        executor_preview: dict[str, Any] = Field(default_factory=dict)
        tool_results: list[dict[str, Any]] = Field(default_factory=list)
        state_snapshot: dict[str, Any] = Field(default_factory=dict)
        artifact_versions: list[dict[str, Any]] = Field(default_factory=list)
        eval_case_suggestion: dict[str, Any] = Field(default_factory=dict)
        trace: list[dict[str, Any]] = Field(default_factory=list)
    ''').strip()
    return header + "\n\n\n" + "\n\n\n".join(object_sections) + "\n"


def render_backend_harness(protocol: dict[str, Any]) -> str:
    operations = []
    for op in protocol.get("operations") or []:
        if not isinstance(op, dict) or not op.get("name"):
            continue
        item = dict(op)
        item["name"] = safe_py_name(op.get("name"), "operation")
        item["original_name"] = op.get("name")
        operations.append(item)
    payload = {
        "project_name": protocol.get("project_name") or "Generated Agent",
        "domain_summary": protocol.get("domain_summary") or "",
        "goals": protocol.get("goals") or [],
        "operations": operations,
        "memory_policy": protocol.get("memory_policy") or {},
        "state_model": protocol.get("state_model") or {},
        "artifact_model": protocol.get("artifact_model") or {},
        "tool_registry": protocol.get("tool_registry") or {},
        "permission_policy": protocol.get("permission_policy") or {},
        "error_recovery": protocol.get("error_recovery") or {},
        "eval_policy": protocol.get("eval_policy") or {},
        "workflow": protocol.get("workflow") or {},
    }
    spec_literal = indent_literal(payload)
    return textwrap.dedent(f'''
    from __future__ import annotations

    from typing import Any


    HARNESS_SPEC: dict[str, Any] = {spec_literal}


    def list_operations() -> list[dict[str, Any]]:
        return list(HARNESS_SPEC.get("operations") or [])


    def build_context_pack(message: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        context = context or {{}}
        memory = context.get("memory") or context.get("memories") or []
        if isinstance(memory, dict):
            memory = [memory]
        if not isinstance(memory, list):
            memory = []
        recent_turns = context.get("recent_turns") or context.get("turns") or []
        if isinstance(recent_turns, dict):
            recent_turns = [recent_turns]
        if not isinstance(recent_turns, list):
            recent_turns = []
        conversation_history = context.get("conversation_history") or []
        if isinstance(conversation_history, dict):
            conversation_history = [conversation_history]
        if not isinstance(conversation_history, list):
            conversation_history = []
        memory_context = list(memory)
        for item in recent_turns[-5:]:
            if isinstance(item, dict):
                memory_context.append({{"type": "recent_turn", "role": item.get("role"), "content": item.get("content") or item.get("message") or item.get("assistant_message"), "turn": item.get("turn") or item.get("id")}})
        for item in conversation_history[-6:]:
            if isinstance(item, dict):
                memory_context.append({{"type": "conversation_history", "role": item.get("role"), "content": item.get("content"), "turn": item.get("turn")}})
        state_model = HARNESS_SPEC.get("state_model") or {{}}
        artifact_model = HARNESS_SPEC.get("artifact_model") or {{}}
        tool_registry = HARNESS_SPEC.get("tool_registry") or {{}}
        return {{
            "user_message": message,
            "extra_context": context,
            "project_name": HARNESS_SPEC.get("project_name"),
            "domain_summary": HARNESS_SPEC.get("domain_summary"),
            "goals": HARNESS_SPEC.get("goals") or [],
            "operations": list_operations(),
            "memory_context": memory_context[-12:],
            "conversation_history": conversation_history[-12:],
            "recent_turns": recent_turns[-8:],
            "memory_policy": HARNESS_SPEC.get("memory_policy") or {{}},
            "state_context": {{
                "current_state": context.get("current_state") or context.get("state") or "unknown",
                "state_fields": state_model.get("state_fields") or [],
                "available_transitions": [item for item in state_model.get("transitions") or [] if item.get("from") == context.get("current_state")],
            }},
            "artifact_context": {{
                "artifacts": artifact_model.get("artifacts") or [],
                "versioning": artifact_model.get("versioning") or {{}},
                "review_rules": artifact_model.get("review_rules") or [],
            }},
            "tool_context": {{
                "tools": tool_registry.get("tools") or [],
                "operation_tool_bindings": tool_registry.get("operation_tool_bindings") or [],
            }},
            "permission_policy": HARNESS_SPEC.get("permission_policy") or {{}},
            "error_recovery": HARNESS_SPEC.get("error_recovery") or {{}},
            "eval_policy": HARNESS_SPEC.get("eval_policy") or {{}},
            "workflow": HARNESS_SPEC.get("workflow") or {{}},
        }}


    def evaluate_permission(operation: str, context_pack: dict[str, Any]) -> dict[str, Any]:
        policy = context_pack.get("permission_policy") or {{}}
        forbidden_hits = []
        confirmation_hits = []
        for item in policy.get("forbidden") or []:
            name = str(item.get("name") or item.get("target") or "")
            if name and name == operation:
                forbidden_hits.append(item)
        for item in policy.get("confirmation_required") or []:
            name = str(item.get("name") or item.get("target") or "")
            if name and name == operation:
                confirmation_hits.append(item)
        operation_spec = next((item for item in context_pack.get("operations") or [] if item.get("name") == operation), {{}})
        haystack = f"{{operation}} {{operation_spec.get('description') or ''}}".lower()
        high_risk = operation_spec.get("risk") == "high" or any(word in haystack for word in ["delete", "remove", "overwrite", "export", "submit", "删除", "覆盖", "导出", "提交"])
        status = "forbidden" if forbidden_hits else ("requires_confirmation" if confirmation_hits or high_risk else "allowed")
        return {{
            "status": status,
            "allowed": status == "allowed",
            "requires_confirmation": status == "requires_confirmation",
            "forbidden_hits": forbidden_hits,
            "confirmation_hits": confirmation_hits,
            "reasons": [item.get("reason") or str(item) for item in forbidden_hits + confirmation_hits] or (["高风险操作需要确认。"] if high_risk else ["权限策略允许预览执行。"]),
        }}


    def build_recovery_plan(next_action: str, permission_check: dict[str, Any], validator_results: list[Any]) -> dict[str, Any]:
        if permission_check.get("status") == "forbidden":
            strategy = "block_and_handoff"
            next_step = "阻断执行，整理上下文后转人工或调整权限策略。"
        elif permission_check.get("requires_confirmation"):
            strategy = "await_human_confirmation"
            next_step = "暂停执行，等待用户确认。"
        elif any(not getattr(item, "ok", False) for item in validator_results):
            strategy = "block_and_explain"
            next_step = "解释校验失败原因，让用户补齐信息。"
        elif next_action in {{"ask_clarification", "unsupported"}}:
            strategy = "ask_clarification"
            next_step = "先追问或提示当前协议不支持。"
        else:
            strategy = "continue"
            next_step = "当前只是 Demo 模拟执行，真实系统可继续接入业务执行器。"
        return {{"strategy": strategy, "next_step": next_step}}


    def build_eval_case_suggestion(message: str, op_call: Any, permission_check: dict[str, Any], recovery_plan: dict[str, Any]) -> dict[str, Any]:
        reasons = []
        case_type = "optional_smoke_case"
        if getattr(op_call, "missing_info", []):
            reasons.append("信息不足，需要沉淀为追问/校验评测。")
            case_type = "validator_eval_cases"
        if permission_check.get("status") in {{"forbidden", "requires_confirmation"}}:
            reasons.append("触发权限边界，需要沉淀为权限评测。")
            case_type = "permission_eval_cases"
        if recovery_plan.get("strategy") != "continue":
            reasons.append("触发失败恢复，需要沉淀为恢复评测。")
            case_type = "recovery_eval_cases"
        return {{
            "should_record": bool(reasons),
            "case_type": case_type,
            "reasons": reasons or ["本轮是普通冒烟样本，可选保存。"],
            "candidate_case": {{
                "user_message": message,
                "expected_operation": getattr(op_call, "operation", ""),
                "expected": {{
                    "permission_status": permission_check.get("status"),
                    "recovery_strategy": recovery_plan.get("strategy"),
                }},
            }},
        }}
    ''')


def render_backend_validators(protocol: dict[str, Any]) -> str:
    validators: list[str] = []
    for op in protocol.get("operations") or []:
        if not isinstance(op, dict):
            continue
        for validator in op.get("validators") or []:
            if isinstance(validator, str):
                validators.append(validator)
    for validator in protocol.get("validators") or []:
        if isinstance(validator, str):
            validators.append(validator)
        elif isinstance(validator, dict) and validator.get("name"):
            validators.append(str(validator["name"]))
    seen: set[str] = set()
    funcs: list[str] = []
    for raw in validators:
        name = safe_py_name(raw, "validator")
        if name in seen:
            continue
        seen.add(name)
        raw_literal = py_string_literal(raw)
        funcs.append(
            f"def validate_{name}(state: dict[str, Any], params: dict[str, Any]) -> ValidatorResult:\n"
            f"    rule = {raw_literal}\n"
            f"    if params.get('force_validator_failure') == '{name}':\n"
            f"        return ValidatorResult(name=\"{name}\", ok=False, reason=f\"forced failure for demo: {{rule}}\")\n"
            f"    return ValidatorResult(name=\"{name}\", ok=True, reason=f\"demo validator passed: {{rule}}\")"
        )
    registry_items = ",\n    ".join(f'"{name}": validate_{name}' for name in sorted(seen))
    if not funcs:
        funcs.append(
            "def validate_placeholder(state: dict[str, Any], params: dict[str, Any]) -> ValidatorResult:\n"
            "    \"\"\"TODO: 根据 protocol.validators 补充真实校验器。\"\"\"\n"
            "    return ValidatorResult(name=\"placeholder\", ok=True, reason=\"demo placeholder passed\")"
        )
        registry_items = '"placeholder": validate_placeholder'
    header = textwrap.dedent('''
    from __future__ import annotations

    from typing import Any, Callable
    from .models import ValidatorResult
    ''').strip()
    tail = textwrap.dedent(f'''

    VALIDATOR_REGISTRY: dict[str, Callable[[dict[str, Any], dict[str, Any]], ValidatorResult]] = {{
        {registry_items}
    }}


    def run_validators(names: list[str], state: dict[str, Any], params: dict[str, Any]) -> list[ValidatorResult]:
        results: list[ValidatorResult] = []
        for name in names:
            key = str(name).strip().lower().replace(" ", "_")
            validator = VALIDATOR_REGISTRY.get(key)
            if not validator:
                results.append(ValidatorResult(name=key, ok=False, reason="validator not registered"))
                continue
            results.append(validator(state, params))
        return results
    ''').strip()
    return header + "\n\n\n" + "\n\n\n".join(funcs) + "\n\n\n" + tail + "\n"



def render_backend_store() -> str:
    return textwrap.dedent("""
    from __future__ import annotations

    import copy
    from datetime import datetime, timezone
    from typing import Any


    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()


    class InMemoryStore:
        # Demo 级内存存储。真实项目应替换为数据库、对象存储、文档存储或知识库。

        def __init__(self) -> None:
            self.memory: list[dict[str, Any]] = []
            self.state: dict[str, Any] = {"current_state": "initialized"}
            self.artifacts: list[dict[str, Any]] = []
            self.trace_events: list[dict[str, Any]] = []
            self.jobs: list[dict[str, Any]] = []
            self.turns: list[dict[str, Any]] = []
            self.sessions: dict[str, dict[str, Any]] = {"default": {"id": "default", "turn_count": 0, "created_at": _now(), "updated_at": _now()}}

        def snapshot(self) -> dict[str, Any]:
            return {
                "memory": copy.deepcopy(self.memory[-20:]),
                "state": copy.deepcopy(self.state),
                "artifacts": copy.deepcopy(self.artifacts[-20:]),
                "trace_events": copy.deepcopy(self.trace_events[-80:]),
                "jobs": copy.deepcopy(self.jobs[-20:]),
                "turns": copy.deepcopy(self.turns[-20:]),
                "sessions": copy.deepcopy(list(self.sessions.values())[-20:]),
                "runtime_summary": self.runtime_summary(),
            }

        def read_context(self, incoming: dict[str, Any] | None = None) -> dict[str, Any]:
            incoming = incoming or {}
            merged = copy.deepcopy(incoming)
            merged.setdefault("memory", copy.deepcopy(self.memory[-8:]))
            merged.setdefault("current_state", self.state.get("current_state", "initialized"))
            merged.setdefault("stored_state", copy.deepcopy(self.state))
            merged.setdefault("existing_artifacts", copy.deepcopy(self.artifacts[-8:]))
            merged.setdefault("recent_turns", copy.deepcopy(self.turns[-5:]))
            merged.setdefault("last_trace_events", copy.deepcopy(self.trace_events[-8:]))
            merged.setdefault("session_id", str(incoming.get("session_id") or incoming.get("conversation_id") or "default"))
            return merged

        def update_state(self, patch: dict[str, Any] | None) -> dict[str, Any]:
            if patch:
                self.state.update(copy.deepcopy(patch))
            self.state["updated_at"] = _now()
            return copy.deepcopy(self.state)

        def append_memory(self, content: str, *, memory_type: str = "runtime_observation", source: str = "runtime") -> dict[str, Any]:
            item = {
                "id": f"mem_{len(self.memory) + 1}",
                "type": memory_type,
                "content": content,
                "source": source,
                "created_at": _now(),
            }
            self.memory.append(item)
            return copy.deepcopy(item)

        def create_artifact(self, name: str, payload: dict[str, Any] | None = None, *, source: str = "executor") -> dict[str, Any]:
            version_number = 1 + sum(1 for item in self.artifacts if item.get("name") == name)
            previous = next((item for item in reversed(self.artifacts) if item.get("name") == name), None)
            artifact = {
                "id": f"artifact_{len(self.artifacts) + 1}",
                "name": name,
                "version": f"v{version_number}",
                "version_number": version_number,
                "previous_version_id": previous.get("id") if previous else None,
                "payload": copy.deepcopy(payload or {}),
                "source": source,
                "is_active": True,
                "created_at": _now(),
            }
            for item in self.artifacts:
                if item.get("name") == name:
                    item["is_active"] = False
            self.artifacts.append(artifact)
            return copy.deepcopy(artifact)

        def record_trace(self, event: dict[str, Any]) -> dict[str, Any]:
            payload = copy.deepcopy(event)
            payload.setdefault("phase", payload.get("step") or payload.get("event") or "runtime")
            payload.setdefault("status", "observed")
            item = {"id": f"trace_{len(self.trace_events) + 1}", "at": _now(), **payload}
            self.trace_events.append(item)
            return copy.deepcopy(item)

        def record_turn(self, turn: dict[str, Any]) -> dict[str, Any]:
            payload = copy.deepcopy(turn)
            session_id = str(payload.get("session_id") or "default")
            session = self.sessions.setdefault(session_id, {"id": session_id, "turn_count": 0, "created_at": _now()})
            session["turn_count"] = int(session.get("turn_count") or 0) + 1
            session["updated_at"] = _now()
            item = {"id": f"turn_{len(self.turns) + 1}", "session_id": session_id, "created_at": _now(), **payload}
            self.turns.append(item)
            return copy.deepcopy(item)

        def runtime_summary(self) -> dict[str, Any]:
            return {
                "memory_count": len(self.memory),
                "turn_count": len(self.turns),
                "trace_count": len(self.trace_events),
                "artifact_count": len(self.artifacts),
                "job_count": len(self.jobs),
                "current_state": self.state.get("current_state"),
            }

        def record_job(self, job: dict[str, Any]) -> dict[str, Any]:
            item = {"id": f"job_{len(self.jobs) + 1}", "created_at": _now(), **copy.deepcopy(job)}
            self.jobs.append(item)
            return copy.deepcopy(item)


    STORE = InMemoryStore()
    """)


def render_backend_tools(protocol: dict[str, Any]) -> str:
    registry = protocol.get("tool_registry") or {}
    tools = registry.get("tools") or []
    operation_tool_bindings = registry.get("operation_tool_bindings") or []
    tools_literal = indent_literal(tools if isinstance(tools, list) else [])
    bindings_literal = indent_literal(operation_tool_bindings if isinstance(operation_tool_bindings, list) else [])
    return textwrap.dedent(f"""
    from __future__ import annotations

    from typing import Any


    TOOL_REGISTRY: list[dict[str, Any]] = {tools_literal}
    OPERATION_TOOL_BINDINGS: list[dict[str, Any]] = {bindings_literal}
    DEFAULT_TOOL_REGISTRY: list[dict[str, Any]] = [
        {{"name": "demo_context_lookup", "description": "从当前会话记忆和上下文中检索相关信息", "risk": "low", "side_effects": False}},
        {{"name": "demo_artifact_writer", "description": "模拟生成可版本化交付物，用于观察 artifact 流程", "risk": "medium", "side_effects": False}},
    ]


    def list_tools() -> list[dict[str, Any]]:
        return list(TOOL_REGISTRY or DEFAULT_TOOL_REGISTRY)


    def tools_for_operation(operation: str) -> list[dict[str, Any]]:
        names: list[str] = []
        for item in OPERATION_TOOL_BINDINGS:
            if item.get("operation") == operation:
                names.extend(str(name) for name in item.get("tools") or [] if name)
        if not names:
            text = operation.lower()
            for tool in TOOL_REGISTRY:
                haystack = f"{{tool.get('name', '')}} {{tool.get('description', '')}}".lower()
                if any(part and part in haystack for part in text.split("_")):
                    names.append(str(tool.get("name")))
        registry = list_tools()
        by_name = {{str(tool.get("name")): tool for tool in registry if tool.get("name")}}
        matched = [by_name[name] for name in names if name in by_name]
        if matched:
            return matched
        return [registry[0]] if registry else []


    async def dry_run_tool(tool: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
        name = str(tool.get("name") or "unknown_tool")
        risk = str(tool.get("risk") or tool.get("risk_level") or "low")
        side_effects = tool.get("side_effects") or tool.get("has_side_effects") or False
        return {{
            "tool": name,
            "mode": "dry_run",
            "status": "needs_confirmation" if risk == "high" or side_effects else "passed",
            "risk": risk,
            "side_effects": side_effects,
            "input_preview": params,
            "output_preview": {{"message": f"工具 {{name}} 已完成 dry-run；真实调用请在 app/tools.py 接入。"}},
            "trace_hint": {{"layer": "tool_adapter", "file": "backend/app/tools.py", "replace_with": "真实检索、文档、导出或业务 API 调用"}},
        }}
    """)


def render_backend_workflow() -> str:
    return textwrap.dedent("""
    from __future__ import annotations

    from typing import Any
    from .harness import HARNESS_SPEC
    from .runtime import run_agent
    from .models import AgentRequest
    from .store import STORE


    def _node_id(node: dict[str, Any], index: int) -> str:
        return str(node.get("id") or node.get("name") or f"node_{index + 1}")


    async def run_workflow(message: str, context: dict[str, Any] | None = None, max_steps: int = 20) -> dict[str, Any]:
        workflow = HARNESS_SPEC.get("workflow") or {}
        nodes = [item for item in workflow.get("nodes") or [] if isinstance(item, dict)]
        trace: list[dict[str, Any]] = [{"event": "workflow_start", "status": "running", "message": message, "max_steps": max_steps}]
        node_states: dict[str, dict[str, Any]] = {}
        STORE.record_trace({"event": "workflow_start", "status": "running", "message": message})
        if not nodes:
            result = await run_agent(AgentRequest(message=message, context=context or {}))
            data = result.model_dump() if hasattr(result, "model_dump") else result.dict()
            STORE.record_trace({"event": "workflow_single_agent_fallback", "status": data.get("next_action"), "agent_trace_count": len(data.get("trace") or [])})
            job = STORE.record_job({"type": "single_agent", "message": message, "status": data.get("next_action"), "result": data})
            return {
                "mode": "single_agent_fallback",
                "status": data.get("next_action"),
                "summary": {"total_nodes": 1, "completed_nodes": 1, "artifact_count": len(data.get("artifact_versions") or [])},
                "node_states": {"agent_run": {"status": data.get("next_action"), "observation": data.get("assistant_message")}},
                "artifacts": data.get("artifact_versions") or [],
                "trace": data.get("trace") or [],
                "job": job,
            }
        completed = 0
        artifacts: list[dict[str, Any]] = []
        for index, node in enumerate(nodes[:max_steps]):
            node_id = _node_id(node, index)
            node_message = f"{message}\\n当前 workflow 节点：{node.get('name') or node_id}"
            result = await run_agent(AgentRequest(message=node_message, context={**(context or {}), "workflow_node": node}))
            data = result.model_dump() if hasattr(result, "model_dump") else result.dict()
            status = "awaiting_human" if data.get("next_action") == "awaiting_confirmation" else "completed"
            completed += 1 if status == "completed" else 0
            artifacts.extend(data.get("artifact_versions") or [])
            node_states[node_id] = {
                "node_id": node_id,
                "name": node.get("name") or node_id,
                "type": node.get("type") or "agent",
                "status": status,
                "operation": (data.get("op_call") or {}).get("operation"),
                "observation": data.get("assistant_message"),
            }
            node_trace = {"step": index + 1, "event": "workflow_node_run", "node_id": node_id, "status": status, "agent_trace": data.get("trace") or []}
            trace.append(node_trace)
            STORE.record_trace(node_trace)
            if status == "awaiting_human":
                break
        overall_status = "completed" if completed == len(nodes[:max_steps]) else "awaiting_human"
        STORE.record_trace({"event": "workflow_finished", "status": overall_status, "completed_nodes": completed, "total_nodes": len(nodes)})
        job = STORE.record_job({"type": "workflow", "message": message, "status": overall_status, "node_states": node_states})
        return {
            "mode": "workflow_runtime_demo",
            "status": overall_status,
            "summary": {"total_nodes": len(nodes), "completed_nodes": completed, "artifact_count": len(artifacts)},
            "node_states": node_states,
            "artifacts": artifacts,
            "trace": trace,
            "job": job,
        }
    """).strip() + "\n"


def render_backend_llm_client() -> str:
    return textwrap.dedent('''
    from __future__ import annotations

    import json
    import os
    import time
    from typing import Any

    import httpx


    class LLMRuntimeError(RuntimeError):
        pass


    def resolve_llm_settings(context: dict[str, Any] | None = None) -> dict[str, Any]:
        context = context or {}
        settings = context.get("llm_settings") if isinstance(context.get("llm_settings"), dict) else {}
        runtime_params = context.get("runtime_params") if isinstance(context.get("runtime_params"), dict) else {}
        extra_context = context.get("extra_context") if isinstance(context.get("extra_context"), dict) else {}
        extra_runtime_params = extra_context.get("runtime_params") if isinstance(extra_context.get("runtime_params"), dict) else {}
        for container in (extra_runtime_params, runtime_params, extra_context, context):
            if isinstance(container.get("llm_settings"), dict):
                settings = {**settings, **container.get("llm_settings")}
        api_base = str(settings.get("api_base") or extra_context.get("api_base") or context.get("api_base") or os.getenv("APD_API_BASE") or os.getenv("OPENAI_API_BASE") or "").rstrip("/")
        api_key = str(settings.get("api_key") or extra_context.get("api_key") or context.get("api_key") or os.getenv("APD_API_KEY") or os.getenv("OPENAI_API_KEY") or "")
        model = str(settings.get("model") or extra_context.get("model") or context.get("model") or os.getenv("APD_MODEL") or os.getenv("OPENAI_MODEL") or "")
        timeout = float(settings.get("timeout") or os.getenv("APD_LLM_TIMEOUT") or 120)
        max_tokens = int(settings.get("max_tokens") or os.getenv("APD_MAX_TOKENS") or 4000)
        temperature = float(settings.get("temperature") or os.getenv("APD_TEMPERATURE") or 0.2)
        enabled = str(settings.get("enabled") if "enabled" in settings else context.get("llm_enabled", "1")).lower() not in {"0", "false", "no", "off"}
        return {"api_base": api_base, "api_key": api_key, "model": model, "timeout": timeout, "max_tokens": max_tokens, "temperature": temperature, "enabled": enabled}


    def llm_available(context: dict[str, Any] | None = None) -> bool:
        settings = resolve_llm_settings(context)
        return bool(settings.get("enabled") and settings.get("api_base") and settings.get("api_key") and settings.get("model"))


    async def chat_completion(messages: list[dict[str, str]], context: dict[str, Any] | None = None, *, response_format: str = "text") -> dict[str, Any]:
        settings = resolve_llm_settings(context)
        if not settings.get("enabled"):
            raise LLMRuntimeError("LLM disabled by runtime settings")
        if not settings.get("api_base") or not settings.get("api_key") or not settings.get("model"):
            raise LLMRuntimeError("missing api_base, api_key or model")
        payload: dict[str, Any] = {
            "model": settings["model"],
            "messages": messages,
            "temperature": settings["temperature"],
            "max_tokens": settings["max_tokens"],
        }
        if response_format == "json":
            payload["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {settings['api_key']}", "Content-Type": "application/json"}
        timeout = httpx.Timeout(float(settings["timeout"]), connect=10.0)
        started_at = time.time()
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(f"{settings['api_base']}/chat/completions", headers=headers, json=payload)
        latency_ms = int((time.time() - started_at) * 1000)
        if response.status_code >= 400:
            raise LLMRuntimeError(f"LLM HTTP {response.status_code}: {response.text[:500]}")
        data = response.json()
        try:
            choice = data["choices"][0]
            content = choice["message"].get("content") or ""
            return {"ok": True, "content": content, "model": data.get("model") or settings.get("model"), "usage": data.get("usage"), "finish_reason": choice.get("finish_reason"), "status_code": response.status_code, "latency_ms": latency_ms, "request": {"model": payload.get("model"), "temperature": payload.get("temperature"), "max_tokens": payload.get("max_tokens"), "response_format": response_format}}
        except Exception as exc:
            raise LLMRuntimeError("unexpected LLM response: " + json.dumps(data, ensure_ascii=False)[:1000]) from exc


    def parse_json_object(text: str) -> dict[str, Any]:
        text = (text or "").strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:].strip()
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start:end + 1]
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("LLM JSON response is not an object")
        return data
    ''').strip() + "\n"

def render_backend_executor(protocol: dict[str, Any]) -> str:
    funcs: list[str] = []
    registry: list[str] = []
    for op in protocol.get("operations") or []:
        if not isinstance(op, dict) or not op.get("name"):
            continue
        name = safe_py_name(op.get("name"), "operation")
        desc = py_string_literal(op.get("description") or op.get("name"))
        registry.append(f'"{name}": handle_{name}')
        funcs.append(textwrap.dedent(f'''
        async def handle_{name}(state: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
            description = {desc}
            llm_execution = await _llm_execute_operation("{name}", description, state, params)
            if llm_execution:
                return llm_execution
            return {{
                "status": "simulated",
                "operation": "{name}",
                "params": params,
                "state_patch": {{"last_operation": "{name}", "current_state": "{name}_completed"}},
                "artifact": {{"name": "{name}_result", "payload": {{"operation": "{name}", "description": description}}}},
                "message": f"当前未配置可用 LLM，已进入规则 Demo 降级。Demo 已模拟执行 {name}：{{description}}。",
            }}
        ''').strip())
    if not funcs:
        funcs.append(
            "async def handle_placeholder(state: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:\n"
            "    return {\"status\": \"ask_clarification\", \"message\": \"协议还没有 operation，请先在 APD 中补齐。\"}"
        )
        registry.append('"placeholder": handle_placeholder')
    registry_items = ",\n    ".join(registry)
    header = textwrap.dedent('''
    from __future__ import annotations

    from typing import Any, Awaitable, Callable
    from .llm_client import chat_completion, llm_available, parse_json_object
    ''').strip()
    helper = textwrap.dedent('''
    def _json_dumps(value: Any) -> str:
        import json
        return json.dumps(value, ensure_ascii=False, indent=2, default=str)


    def _build_executor_messages(operation: str, description: str, state: dict[str, Any], params: dict[str, Any]) -> list[dict[str, str]]:
        context = params.get("context") if isinstance(params.get("context"), dict) else {}
        return [
            {
                "role": "system",
                "content": (
                    "你是生产级 Agent 的执行器/回复生成器。你要根据已通过 Planner、Validator 的操作生成用户可见回复。"
                    "不要说自己是 Demo；不要输出固定模板。"
                    "如果需要真实外部系统但当前没有工具结果，要明确说明缺什么，并给出下一步。"
                    "必须输出 JSON 对象，字段：message, status, state_patch, artifact。message 可以是 Markdown。"
                ),
            },
            {
                "role": "user",
                "content": _json_dumps({"operation": operation, "operation_description": description, "params": params, "state": state, "context": context}),
            },
        ]


    async def _llm_execute_operation(operation: str, description: str, state: dict[str, Any], params: dict[str, Any]) -> dict[str, Any] | None:
        context = params.get("context") if isinstance(params.get("context"), dict) else {}
        if not llm_available(context):
            return None
        messages = _build_executor_messages(operation, description, state, params)
        try:
            result = await chat_completion(messages, context, response_format="json")
            data = parse_json_object(result.get("content") or "{}")
            message = str(data.get("message") or "已完成。")
            state_patch = data.get("state_patch") if isinstance(data.get("state_patch"), dict) else {}
            artifact = data.get("artifact") if isinstance(data.get("artifact"), dict) else {}
            artifact.setdefault("name", operation + "_result")
            artifact.setdefault("payload", {"operation": operation, "message": message})
            return {
                "status": str(data.get("status") or "completed"),
                "operation": operation,
                "params": params,
                "state_patch": {"last_operation": operation, "current_state": str(data.get("status") or "completed"), **state_patch},
                "artifact": artifact,
                "message": message,
                "llm_messages": messages,
                "llm_response": result,
            }
        except Exception as exc:
            return {
                "status": "llm_executor_failed",
                "operation": operation,
                "params": params,
                "state_patch": {"last_operation": operation, "current_state": "llm_executor_failed"},
                "artifact": {"name": operation + "_llm_error", "payload": {"error": str(exc)}},
                "message": "LLM 执行器调用失败，已进入可诊断状态：" + str(exc),
                "llm_messages": messages,
                "llm_response": {"ok": False, "error": str(exc)},
            }
    ''').strip()
    tail = textwrap.dedent(f'''

    EXECUTOR_REGISTRY: dict[str, Callable[[dict[str, Any], dict[str, Any]], Awaitable[dict[str, Any]]]] = {{
        {registry_items}
    }}


    async def execute_operation(operation: str, state: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
        handler = EXECUTOR_REGISTRY.get(operation)
        if not handler:
            return {{"status": "error", "message": "unknown operation: " + str(operation)}}
        return await handler(state, params)
    ''').strip()
    return header + "\n\n\n" + "\n\n\n".join(funcs) + "\n\n\n" + helper + "\n\n\n" + tail + "\n"

def render_backend_planner(protocol: dict[str, Any]) -> str:
    operations = []
    for op in protocol.get("operations") or []:
        if isinstance(op, dict) and op.get("name"):
            operations.append({
                "name": safe_py_name(op.get("name"), "operation"),
                "description": op.get("description") or op.get("name") or "",
                "risk": op.get("risk") or "low",
            })
    operations_literal = indent_literal(operations)
    return textwrap.dedent(f'''
    from __future__ import annotations

    import re
    from typing import Any
    from .llm_client import LLMRuntimeError, chat_completion, llm_available, parse_json_object
    from .models import OperationCall


    KNOWN_OPERATIONS = {operations_literal}
    AMBIGUOUS_WORDS = ["随便", "处理一下", "弄一下", "帮我看看", "搞一下"]


    def build_planner_messages(message: str, context: dict[str, Any] | None = None) -> list[dict[str, str]]:
        context = context or {{}}
        return [
            {{
                "role": "system",
                "content": (
                    "你是生产级 Agent 的意图规划器。你的任务是把用户自然语言理解为一个受控 OpCall。"
                    "只能从给定 operations 中选择 operation；不确定时选择 ask_clarification。"
                    "必须输出 JSON 对象，字段：operation, params, confidence, requires_confirmation, missing_info, evidence。"
                    "params 要保留 user_message，并抽取可执行参数；evidence 用中文解释判断依据。"
                ),
            }},
            {{
                "role": "user",
                "content": json_dumps({{"user_message": message, "context_pack": context, "operations": KNOWN_OPERATIONS}}),
            }},
        ]


    def json_dumps(value: Any) -> str:
        import json
        return json.dumps(value, ensure_ascii=False, indent=2, default=str)


    def _score_operation(message: str, operation: dict[str, Any]) -> tuple[int, list[str]]:
        haystack = (operation.get("name", "") + " " + operation.get("description", "")).lower()
        words = [item for item in re.split(r"[^a-zA-Z0-9\u4e00-\u9fff]+", haystack) if len(item) >= 2]
        evidence = []
        score = 0
        lower_message = message.lower()
        for word in words:
            if word and word in lower_message:
                score += 2 if word == operation.get("name") else 1
                evidence.append(f"命中关键词：{{word}}")
        return score, evidence


    async def plan_operation(message: str, context: dict[str, Any] | None = None) -> OperationCall:
        context = context or {{}}
        if not KNOWN_OPERATIONS:
            return OperationCall(operation="", params={{}}, confidence=0.0, missing_info=["protocol has no operations"], evidence=["协议中没有 operation"])
        if llm_available(context):
            messages = build_planner_messages(message, context)
            try:
                result = await chat_completion(messages, context, response_format="json")
                data = parse_json_object(result.get("content") or "{{}}")
                operation = str(data.get("operation") or "").strip()
                known_names = {{item.get("name") for item in KNOWN_OPERATIONS}}
                if operation not in known_names and operation != "ask_clarification":
                    operation = "ask_clarification"
                    data.setdefault("missing_info", []).append("LLM 返回了不在协议内的 operation")
                params = data.get("params") if isinstance(data.get("params"), dict) else {{}}
                params.setdefault("user_message", message)
                params.setdefault("context", context)
                params["_llm_planner"] = {{"ok": True, "messages": messages, "response": result}}
                return OperationCall(
                    operation=operation,
                    params=params,
                    confidence=float(data.get("confidence") or 0.75),
                    requires_confirmation=bool(data.get("requires_confirmation")),
                    missing_info=[str(x) for x in data.get("missing_info") or []],
                    evidence=[str(x) for x in data.get("evidence") or ["LLM Planner 生成"]],
                )
            except Exception as exc:
                context["_llm_planner_error"] = str(exc)
        if not message.strip() or any(word in message for word in AMBIGUOUS_WORDS):
            return OperationCall(operation="ask_clarification", params={{"user_message": message}}, confidence=0.2, missing_info=["目标或动作不明确"], evidence=["用户话术过于泛化"])
        scored = []
        for operation in KNOWN_OPERATIONS:
            score, evidence = _score_operation(message, operation)
            scored.append((score, operation, evidence))
        scored.sort(key=lambda item: item[0], reverse=True)
        best_score, best_operation, evidence = scored[0]
        if best_score <= 0:
            best_operation = KNOWN_OPERATIONS[0]
            error = context.get("_llm_planner_error")
            evidence = ["未命中明确关键词，规则降级默认选择第一个 operation。" + (f" LLM Planner 失败：{{error}}" if error else " 当前未配置 LLM。")]
            confidence = 0.35
        else:
            confidence = min(0.95, 0.55 + best_score * 0.1)
        return OperationCall(
            operation=best_operation["name"],
            params={{"user_message": message, "context": context}},
            confidence=confidence,
            requires_confirmation=best_operation.get("risk") == "high",
            missing_info=[],
            evidence=evidence,
        )
    ''')


def render_backend_runtime(protocol: dict[str, Any]) -> str:
    op_validators: dict[str, list[str]] = {}
    confirms: dict[str, bool] = {}
    for op in protocol.get("operations") or []:
        if not isinstance(op, dict) or not op.get("name"):
            continue
        name = safe_py_name(op.get("name"), "operation")
        op_validators[name] = [safe_py_name(v, "validator") for v in op.get("validators") or [] if isinstance(v, str)]
        confirms[name] = bool(op.get("requires_confirmation") or str(op.get("risk") or "").lower() == "high")
    op_validators_literal = indent_literal(op_validators)
    confirms_literal = indent_literal(confirms)
    return textwrap.dedent(f'''
    from __future__ import annotations

    from typing import Any
    from .executor import execute_operation
    from .harness import build_context_pack, build_eval_case_suggestion, build_recovery_plan, evaluate_permission
    from .llm_client import llm_available
    from .models import AgentResponse, AgentRequest
    from .planner import plan_operation
    from .store import STORE
    from .tools import dry_run_tool, tools_for_operation
    from .validators import run_validators


    OP_VALIDATORS = {op_validators_literal}
    OP_REQUIRES_CONFIRMATION = {confirms_literal}


    def _dump_model(value):
        return value.model_dump() if hasattr(value, "model_dump") else value.dict()


    def _build_llm_trace(op_call, execution: dict[str, Any] | None = None) -> dict[str, Any]:
        execution = execution or {{}}
        planner = op_call.params.get("_llm_planner") if getattr(op_call, "params", None) else None
        planner = planner if isinstance(planner, dict) else {{}}
        executor_messages = execution.get("llm_messages") if isinstance(execution.get("llm_messages"), list) else []
        executor_response = execution.get("llm_response") if isinstance(execution.get("llm_response"), dict) else None
        calls = {{
            "planner": {{
                "name": "Intent Planner / 意图规划",
                "ok": bool(planner.get("ok")),
                "messages": planner.get("messages") or [],
                "response": planner.get("response"),
                "summary": "LLM 把用户话术绑定到受控 OpCall。",
            }},
            "executor": {{
                "name": "Executor Writer / 执行回复",
                "ok": bool(executor_response and executor_response.get("ok") is not False),
                "messages": executor_messages,
                "response": executor_response,
                "summary": "LLM 根据 OpCall、上下文和工具结果生成用户可见回复。",
            }},
        }}
        return calls


    def _persist_runtime_turn(message: str, context_pack: dict[str, Any], next_action: str, assistant_message: str, trace: list[dict[str, Any]]) -> None:
        session_id = str((context_pack.get("extra_context") or {{}}).get("session_id") or (context_pack.get("extra_context") or {{}}).get("conversation_id") or "default")
        for item in trace:
            STORE.record_trace(item)
        STORE.record_turn({{
            "session_id": session_id,
            "message": message,
            "assistant_message": assistant_message,
            "next_action": next_action,
            "trace_count": len(trace),
        }})


    async def run_agent(request: AgentRequest, state: dict[str, Any] | None = None) -> AgentResponse:
        state = state or {{}}
        trace: list[dict[str, Any]] = []
        runtime_context = STORE.read_context(request.context)
        context_pack = build_context_pack(request.message, runtime_context)
        trace.append({{"step": "context_pack", "phase": "observe", "status": "ok", "detail": {{"operations": len(context_pack.get('operations') or []), "memory_items": len(context_pack.get('memory_context') or []), "recent_turns": len(context_pack.get('recent_turns') or []), "conversation_turns": len(context_pack.get('conversation_history') or [])}}}})
        runtime_mode = "llm_production" if llm_available(context_pack) else "demo_fallback"
        trace.append({{"step": "runtime_mode", "phase": "observe", "status": runtime_mode, "detail": {{"llm_available": runtime_mode == "llm_production"}}}})
        op_call = await plan_operation(request.message, context_pack)
        trace.append({{"step": "intent_planner", "phase": "reason", "status": "ok" if op_call.operation else "needs_clarification", "detail": _dump_model(op_call)}})
        if not op_call.operation or op_call.operation == "ask_clarification" or op_call.missing_info:
            permission_check = {{"status": "skipped", "allowed": False, "reasons": ["需要先追问，未进入权限判断。"]}}
            recovery_plan = build_recovery_plan("ask_clarification", permission_check, [])
            eval_case = build_eval_case_suggestion(request.message, op_call, permission_check, recovery_plan)
            assistant_message = "信息不足，需要先追问用户再执行。"
            _persist_runtime_turn(request.message, context_pack, "ask_clarification", assistant_message, trace)
            return AgentResponse(
                assistant_message=assistant_message,
                op_call=op_call,
                runtime_mode=runtime_mode,
                llm_messages=(op_call.params.get("_llm_planner") or {{}}).get("messages") or [],
                llm_response={{"planner": (op_call.params.get("_llm_planner") or {{}}).get("response")}},
                llm_trace=_build_llm_trace(op_call),
                next_action="ask_clarification",
                context_pack=context_pack,
                permission_check=permission_check,
                recovery_plan=recovery_plan,
                eval_case_suggestion=eval_case,
                trace=trace,
            )

        op_call.requires_confirmation = bool(OP_REQUIRES_CONFIRMATION.get(op_call.operation, op_call.requires_confirmation))
        permission_check = evaluate_permission(op_call.operation, context_pack)
        trace.append({{"step": "permission_check", "phase": "guard", "status": permission_check.get("status"), "detail": permission_check}})
        validator_results = run_validators(OP_VALIDATORS.get(op_call.operation, []), state, op_call.params)
        trace.append({{"step": "validator", "phase": "validate", "status": "failed" if any(not item.ok for item in validator_results) else "passed", "detail": [_dump_model(item) for item in validator_results]}})
        if any(not item.ok for item in validator_results):
            recovery_plan = build_recovery_plan("validator_failed", permission_check, validator_results)
            eval_case = build_eval_case_suggestion(request.message, op_call, permission_check, recovery_plan)
            assistant_message = "操作未通过校验，请根据失败原因补充信息或调整请求。"
            _persist_runtime_turn(request.message, context_pack, "validator_failed", assistant_message, trace)
            return AgentResponse(
                assistant_message=assistant_message,
                op_call=op_call,
                runtime_mode=runtime_mode,
                llm_messages=(op_call.params.get("_llm_planner") or {{}}).get("messages") or [],
                llm_response={{"planner": (op_call.params.get("_llm_planner") or {{}}).get("response")}},
                llm_trace=_build_llm_trace(op_call),
                validator_results=validator_results,
                next_action="validator_failed",
                context_pack=context_pack,
                permission_check=permission_check,
                recovery_plan=recovery_plan,
                eval_case_suggestion=eval_case,
                trace=trace,
            )
        tool_results = []
        for tool in tools_for_operation(op_call.operation):
            tool_results.append(await dry_run_tool(tool, op_call.params))
        if tool_results:
            trace.append({{"step": "tool_dry_run", "phase": "act", "status": "dry_run", "detail": tool_results}})
        if permission_check.get("status") == "forbidden" or permission_check.get("requires_confirmation") or op_call.requires_confirmation:
            recovery_plan = build_recovery_plan("awaiting_confirmation", permission_check, validator_results)
            eval_case = build_eval_case_suggestion(request.message, op_call, permission_check, recovery_plan)
            assistant_message = "该操作需要用户确认或权限处理后才能执行。"
            _persist_runtime_turn(request.message, context_pack, "awaiting_confirmation" if permission_check.get("status") != "forbidden" else "forbidden", assistant_message, trace)
            return AgentResponse(
                assistant_message=assistant_message,
                op_call=op_call,
                runtime_mode=runtime_mode,
                llm_messages=(op_call.params.get("_llm_planner") or {{}}).get("messages") or [],
                llm_response={{"planner": (op_call.params.get("_llm_planner") or {{}}).get("response")}},
                llm_trace=_build_llm_trace(op_call),
                validator_results=validator_results,
                next_action="awaiting_confirmation" if permission_check.get("status") != "forbidden" else "forbidden",
                context_pack=context_pack,
                permission_check=permission_check,
                recovery_plan=recovery_plan,
                tool_results=tool_results,
                state_snapshot=STORE.snapshot().get("state") or {{}},
                artifact_versions=STORE.snapshot().get("artifacts") or [],
                eval_case_suggestion=eval_case,
                trace=trace,
            )

        execution = await execute_operation(op_call.operation, state, op_call.params)
        trace.append({{"step": "executor_preview", "phase": "act", "status": str(execution.get("status") or "done"), "detail": execution}})
        state_snapshot = STORE.update_state(execution.get("state_patch") or {{}})
        created_artifact = None
        if isinstance(execution.get("artifact"), dict):
            artifact_payload = execution.get("artifact") or {{}}
            created_artifact = STORE.create_artifact(str(artifact_payload.get("name") or op_call.operation + "_artifact"), artifact_payload.get("payload") or {{}}, source=op_call.operation)
            trace.append({{"step": "artifact_version", "phase": "artifact", "status": "created", "detail": created_artifact}})
        STORE.append_memory(str(execution.get("message") or ""), source=op_call.operation)
        _persist_runtime_turn(request.message, context_pack, str(execution.get("status") or "done"), str(execution.get("message") or "Demo 已模拟执行。"), trace)
        recovery_plan = build_recovery_plan(str(execution.get("status") or "done"), permission_check, validator_results)
        eval_case = build_eval_case_suggestion(request.message, op_call, permission_check, recovery_plan)
        artifact_versions = STORE.snapshot().get("artifacts") or []
        return AgentResponse(
            assistant_message=str(execution.get("message") or "Demo 已模拟执行。"),
            op_call=op_call,
            runtime_mode=runtime_mode,
            llm_messages=(op_call.params.get("_llm_planner") or {{}}).get("messages") or execution.get("llm_messages") or [],
            llm_response={{"planner": (op_call.params.get("_llm_planner") or {{}}).get("response"), "executor": execution.get("llm_response")}},
            llm_trace=_build_llm_trace(op_call, execution),
            validator_results=validator_results,
            next_action=str(execution.get("status") or "done"),
            context_pack=context_pack,
            permission_check=permission_check,
            recovery_plan=recovery_plan,
            executor_preview=execution,
            tool_results=tool_results,
            state_snapshot=state_snapshot,
            artifact_versions=artifact_versions,
            eval_case_suggestion=eval_case,
            trace=trace,
        )
    ''')


def render_backend_debug() -> str:
    return textwrap.dedent('''
    from __future__ import annotations

    from typing import Any


    LAYER_GUIDE: list[dict[str, Any]] = [
        {"layer": "Context Pack / 上下文包", "file": "backend/app/harness.py", "when": "Agent 没拿到历史、状态、素材或协议上下文", "fix": "补 build_context_pack 或接入真实记忆/知识库"},
        {"layer": "Intent Planner / 意图规划", "file": "backend/app/planner.py", "when": "用户意图理解错、OpCall 绑定错、缺少追问", "fix": "改 plan_operation，必要时接入 LLM Planner"},
        {"layer": "Validator / 校验", "file": "backend/app/validators.py", "when": "参数缺失没拦住、错误地拦截、风险判断不清", "fix": "把 Demo 校验器替换为确定性业务校验"},
        {"layer": "Tools / 工具", "file": "backend/app/tools.py", "when": "需要检索、读文件、导出、查库但没有真实结果", "fix": "把 dry-run 工具替换成真实工具适配器"},
        {"layer": "Executor / 执行", "file": "backend/app/executor.py", "when": "回复内容不对、没有生成产物、状态更新不对", "fix": "改 execute_operation 和 operation handler"},
        {"layer": "Workflow / 多步流程", "file": "backend/app/workflow.py", "when": "需要分节点、暂停确认、并行或循环执行", "fix": "升级 run_workflow 的节点状态机"},
        {"layer": "Memory & Artifact / 记忆和产物", "file": "backend/app/store.py", "when": "刷新后状态丢失、缺版本、缺 Trace", "fix": "替换为数据库/对象存储，并保留 record_trace"},
    ]


    def runtime_debug_guide() -> dict[str, Any]:
        return {
            "goal": "帮助你把 Runtime Inspector 里看到的问题定位到具体架构层和文件。",
            "loop": ["发送测试话术", "查看 Agent 回复", "看 trace/节点状态", "定位层级", "修改对应文件", "重启当前 Agent", "再次验证"],
            "layers": LAYER_GUIDE,
        }


    def recommendations_from_snapshot(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
        summary = snapshot.get("runtime_summary") or {}
        trace_events = snapshot.get("trace_events") or []
        recommendations: list[dict[str, Any]] = []
        if int(summary.get("trace_count") or 0) == 0:
            recommendations.append({"level": "Trace", "file": "backend/app/runtime.py", "reason": "还没有过程日志，Runtime Inspector 会像黑盒。", "action": "先跑一次 /agent/run；如果仍为空，检查 STORE.record_trace。"})
        if not snapshot.get("memory"):
            recommendations.append({"level": "Memory", "file": "backend/app/store.py", "reason": "没有记忆记录，连续对话难以观察上下文变化。", "action": "在执行成功、追问和失败分支写入 append_memory。"})
        if not snapshot.get("artifacts"):
            recommendations.append({"level": "Artifact", "file": "backend/app/executor.py", "reason": "没有产物版本，生成/修改类 Agent 难以验收。", "action": "让 executor 返回 artifact 字段，由 runtime 创建版本。"})
        if trace_events and not any(item.get("step") == "intent_planner" for item in trace_events):
            recommendations.append({"level": "Planner", "file": "backend/app/planner.py", "reason": "Trace 中缺少意图规划事件。", "action": "保留 intent_planner trace，方便判断是否理解错。"})
        if not recommendations:
            recommendations.append({"level": "OK", "file": "docs/runtime_debug_guide.md", "reason": "当前快照已经具备基础可观察性。", "action": "继续用真实业务话术压测，并把失败样本写入测试。"})
        return recommendations
    ''')


def render_backend_main() -> str:
    return textwrap.dedent('''
    from __future__ import annotations

    from typing import Any
    from fastapi import FastAPI
    from pydantic import BaseModel, Field
    from .debug import recommendations_from_snapshot, runtime_debug_guide
    from .harness import HARNESS_SPEC
    from .models import AgentRequest, AgentResponse
    from .runtime import run_agent
    from .store import STORE
    from .tools import list_tools
    from .workflow import run_workflow


    class WorkflowRunRequest(BaseModel):
        message: str
        context: dict[str, Any] = Field(default_factory=dict)
        max_steps: int = 20


    app = FastAPI(title="Generated Agent Harness Demo")


    @app.get("/")
    async def root() -> dict:
        return {
            "message": "Agent Harness Demo is running",
            "try": ["POST /agent/run", "POST /workflow/run"],
            "spec": "GET /harness/spec",
            "store": "GET /store/snapshot",
        }


    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}


    @app.get("/harness/spec")
    async def harness_spec() -> dict:
        return HARNESS_SPEC


    @app.get("/tools")
    async def tools() -> dict:
        return {"tools": list_tools()}


    @app.get("/store/snapshot")
    async def store_snapshot() -> dict:
        return STORE.snapshot()


    @app.get("/debug/guide")
    async def debug_guide() -> dict:
        return runtime_debug_guide()


    @app.get("/debug/recommendations")
    async def debug_recommendations() -> dict:
        snapshot = STORE.snapshot()
        return {"recommendations": recommendations_from_snapshot(snapshot), "snapshot_summary": snapshot.get("runtime_summary") or {}}


    @app.post("/agent/run", response_model=AgentResponse)
    async def agent_run(request: AgentRequest) -> AgentResponse:
        return await run_agent(request)


    @app.post("/workflow/run")
    async def workflow_run(request: WorkflowRunRequest) -> dict:
        return await run_workflow(request.message, request.context, request.max_steps)
    ''')

def render_frontend_vue(project_name: str) -> str:
    return textwrap.dedent(f'''
    <template>
      <main class="agent-page">
        <section class="panel">
          <h1>{project_name}</h1>
          <p>这是 APD 生成的 Agent Playground 骨架。真实业务逻辑需要继续实现 Planner、Validator 和 Executor。</p>
          <textarea v-model="message" placeholder="输入一句用户话术，体验 Planner → OpCall → Validator → Executor 流程" />
          <button @click="run" :disabled="loading">{{{{ loading ? '运行中...' : '运行 Agent' }}}}</button>
        </section>

        <section class="panel">
          <h2>运行结果</h2>
          <pre>{{{{ resultText }}}}</pre>
        </section>
      </main>
    </template>

    <script setup>
    import {{ computed, ref }} from 'vue'
    import {{ runAgent }} from './api'

    const message = ref('')
    const loading = ref(false)
    const result = ref(null)
    const resultText = computed(() => result.value ? JSON.stringify(result.value, null, 2) : '等待运行...')

    async function run() {{
      loading.value = true
      try {{
        result.value = await runAgent({{ message: message.value, context: {{}} }})
      }} catch (error) {{
        result.value = {{ error: String(error) }}
      }} finally {{
        loading.value = false
      }}
    }}
    </script>

    <style scoped>
    .agent-page {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; padding: 24px; }}
    .panel {{ border: 1px solid #d9d9d9; border-radius: 12px; padding: 16px; background: #fff; }}
    textarea {{ width: 100%; min-height: 160px; margin: 12px 0; padding: 10px; }}
    button {{ padding: 8px 14px; border: 0; border-radius: 8px; background: #1677ff; color: white; cursor: pointer; }}
    pre {{ white-space: pre-wrap; background: #111827; color: #e5e7eb; padding: 12px; border-radius: 8px; min-height: 240px; }}
    </style>
    ''')


def render_frontend_api() -> str:
    return textwrap.dedent('''
    async function postJson(path, payload) {
      const response = await fetch(path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`)
      }
      return response.json()
    }

    export async function runAgent(payload) {
      return postJson('/agent/run', payload)
    }

    export async function runWorkflow(payload) {
      return postJson('/workflow/run', payload)
    }
    ''')

def render_readme(protocol: dict[str, Any], template: str) -> str:
    project_name = protocol.get("project_name") or "Generated Agent"
    operations = [op.get("name") for op in protocol.get("operations") or [] if isinstance(op, dict)]
    return textwrap.dedent(f'''
    # {project_name}

    这是 APD 生成的可运行 Agent Harness Demo。它不是完整业务系统，但已经能跑通：

    ```text
    Context Pack / 上下文包
    → Intent Planner / 意图规划器
    → OpCall / 操作调用
    → Validator / 校验器
    → Permission / 权限判断
    → Executor Preview / 模拟执行器
    → Recovery / 失败恢复
    → Eval Case Suggestion / 评测沉淀建议
    → Trace / 过程日志
    ```

    ## 生成说明

    - 模板：`{template}`
    - 生成目标：提供可启动、可体验、可继续开发的 Agent Harness 工程骨架
    - 注意：真实业务逻辑仍需在 TODO 处补齐，当前执行器只做安全模拟执行

    ## 已包含

    ```text
    backend/
      app/main.py              # FastAPI 入口，含 /agent/run 和 /harness/spec
      app/harness.py           # APD 协议转成运行时上下文、权限、恢复、评测沉淀
      app/models.py            # 请求/响应/业务对象骨架
      app/planner.py           # 规则版 Demo Planner，真实项目替换为 LLM Planner
      app/validators.py        # Validator 注册表和可强制失败的 Demo 校验器
      app/executor.py          # 安全模拟执行器
      app/debug.py             # Runtime Inspector 调试建议和层级定位
      app/store.py             # Demo 内存存储，保存状态、记忆、产物版本和 Trace
      app/tools.py             # Tool Adapter dry-run 模板，真实工具从这里接入
      app/workflow.py          # Workflow Runtime Demo，支持 /workflow/run
      app/runtime.py           # Harness 主链路
      protocol.json
      workflow.json
      requirements.txt
      tests/test_protocol_shape.py

    frontend/
      AgentPlayground.vue
      api.js

    docs/
      workflow_plan.md
      development_plan.md
      planner_prompt.md
      executor_skeleton.py
      architecture_check.md
      eval_policy.md
      development_advice.md
    ```

    ## 当前 operations

    {chr(10).join(f'- `{name}`' for name in operations) if operations else '- 暂无 operation，请先完善 protocol。'}

    ## 后端启动

    ```bash
    cd backend
    python -m venv .venv
    source .venv/bin/activate
    pip install -r requirements.txt
    uvicorn app.main:app --reload --port 8000
    ```

    ## 快速测试

    ```bash
    curl -X POST http://127.0.0.1:8000/agent/run \\
      -H 'Content-Type: application/json' \\
      -d '{{"message":"测试一句用户话术","context":{{"current_state":"drafting"}}}}'
    ```

    你会看到 `context_pack`、`permission_check`、`tool_results`、`state_snapshot`、`artifact_versions`、`recovery_plan`、`eval_case_suggestion` 和 `trace`。这些就是 APD 设计出的 Agent Harness 运行结构。

    ## Workflow 运行

    ```bash
    curl -X POST http://127.0.0.1:8000/workflow/run \
      -H 'Content-Type: application/json' \
      -d '{{"message":"按当前 workflow 跑一次","context":{{"current_state":"uploaded"}},"max_steps":5}}'
    ```

    ## 查看 Demo 内存状态

    ```bash
    curl http://127.0.0.1:8000/store/snapshot
    curl http://127.0.0.1:8000/debug/guide
    curl http://127.0.0.1:8000/debug/recommendations
    curl http://127.0.0.1:8000/tools
    ```

    ## 本地测试

    ```bash
    cd backend
    pytest -q
    ```

    ## 下一步

    1. 在 `backend/app/planner.py` 中把规则版 Demo Planner 替换为真实 LLM Planner。
    2. 在 `backend/app/validators.py` 中把 Demo 校验器替换成确定性业务校验。
    3. 在 `backend/app/executor.py` 中接入真实业务执行器，但保留权限、确认、回滚边界。
    4. 在 `backend/app/tools.py` 中把 dry-run Tool Adapter 替换成真实工具调用。
    5. 在 `backend/app/store.py` 中把 Demo 内存存储替换成数据库、对象存储或文档存储。
    6. 在 `backend/app/workflow.py` 中把节点级模拟运行升级成真实 Workflow Runtime。
    7. 先阅读 `docs/development_advice.md`，再根据 `docs/eval_policy.md` 把真实失败样本沉淀成回归测试。
    ''')


def render_runtime_debug_guide() -> str:
    return textwrap.dedent('''
    # Runtime Inspector 调试指南

    这份工程不是只返回一个结果，而是故意把 Agent 拆成可观察层级，方便你边开发边调试。

    ## 调试闭环

    ```text
    发送测试话术
    → 看 Agent 回复
    → 看 trace / workflow node_states / store snapshot
    → 判断问题在哪层
    → 修改对应文件
    → 重启当前 Agent
    → 再次发送同一条测试话术验证
    ```

    ## 看到问题时先改哪里

    | 现象 | 优先文件 | 原因 |
    |---|---|---|
    | 意图理解错、选错操作 | `backend/app/planner.py` | Planner 负责自然语言到 OpCall |
    | 参数缺失没拦住、风险没确认 | `backend/app/validators.py` | Validator 是确定性校验层 |
    | 回复内容不符合业务 | `backend/app/executor.py` | Executor 决定用户可见回复和产物 |
    | 需要检索/读文件/导出但没真实结果 | `backend/app/tools.py` | Tools 负责外部能力适配 |
    | 多步骤流程不可见或顺序不对 | `backend/app/workflow.py` | Workflow 负责节点状态和暂停继续 |
    | 连续对话、状态、产物版本缺失 | `backend/app/store.py` | Store 负责记忆、状态、产物和 Trace |
    | Runtime Inspector 看起来黑盒 | `backend/app/runtime.py` | Runtime 需要持续记录 trace |

    ## 内置调试接口

    ```bash
    curl http://127.0.0.1:8000/debug/guide
    curl http://127.0.0.1:8000/debug/recommendations
    curl http://127.0.0.1:8000/store/snapshot
    ```

    `debug/recommendations` 会根据当前 store 快照给出缺少 trace、memory、artifact 等建议。
    ''')


def render_backend_requirements() -> str:
    return "fastapi\nuvicorn\npydantic\npytest\npytest-asyncio\nhttpx\n"


def render_test() -> str:
    return textwrap.dedent('''
    import json
    from pathlib import Path

    import pytest

    from app.debug import recommendations_from_snapshot, runtime_debug_guide
    from app.harness import HARNESS_SPEC
    from app.models import AgentRequest
    from app.runtime import run_agent
    from app.store import STORE
    from app.tools import list_tools
    from app.workflow import run_workflow


    def test_protocol_and_workflow_exist():
        root = Path(__file__).resolve().parents[1]
        protocol = json.loads((root / 'protocol.json').read_text(encoding='utf-8'))
        workflow = json.loads((root / 'workflow.json').read_text(encoding='utf-8'))
        assert isinstance(protocol, dict)
        assert isinstance(workflow, dict)
        assert 'operations' in protocol
        assert 'nodes' in workflow
        assert isinstance(HARNESS_SPEC, dict)


    @pytest.mark.asyncio
    async def test_agent_harness_demo_runs():
        result = await run_agent(AgentRequest(message='测试一句用户话术', context={'current_state': 'drafting'}))
        assert result.context_pack
        assert result.permission_check
        assert result.recovery_plan
        assert result.eval_case_suggestion
        assert result.trace
        assert result.state_snapshot
        assert result.artifact_versions
        snapshot = STORE.snapshot()
        assert snapshot['trace_events']
        assert snapshot['memory']
        assert snapshot['turns']
        assert snapshot['runtime_summary']['turn_count'] >= 1
        assert runtime_debug_guide()['layers']
        assert recommendations_from_snapshot(snapshot)


    @pytest.mark.asyncio
    async def test_workflow_runtime_demo_runs():
        result = await run_workflow('按当前 workflow 跑一次', {'current_state': 'drafting'}, max_steps=3)
        assert result['summary']['total_nodes'] >= 1
        assert result['node_states']
        assert 'job' in result


    def test_tool_registry_adapter_is_available():
        assert isinstance(list_tools(), list)
    ''')

def generate_project_scaffold(
    session: dict[str, Any],
    out_dir: Path,
    *,
    template: str = "fastapi-vue",
    force: bool = False,
    project_name: str | None = None,
) -> list[Path]:
    if template != "fastapi-vue":
        raise ValueError(f"unsupported template: {template}")
    protocol = merge_protocol(EMPTY_PROTOCOL, session.get("protocol") or {})
    exports = build_exports(protocol)
    project_name = safe_project_name(project_name or protocol.get("project_name") or session.get("title") or "generated-agent")
    target = out_dir / project_name if out_dir.name != project_name else out_dir
    if target.exists() and any(target.iterdir()):
        if not force:
            raise FileExistsError(f"output directory is not empty: {target}. use --force to overwrite")
        shutil.rmtree(target)
    written: list[Path] = []

    files = {
        "README.md": render_readme(protocol, template),
        "backend/protocol.json": exports["protocol.json"],
        "backend/workflow.json": exports["workflow.json"],
        "backend/app/__init__.py": "",
        "backend/app/main.py": render_backend_main(),
        "backend/app/harness.py": render_backend_harness(protocol),
        "backend/app/models.py": render_backend_models(protocol),
        "backend/app/llm_client.py": render_backend_llm_client(),
        "backend/app/planner.py": render_backend_planner(protocol),
        "backend/app/validators.py": render_backend_validators(protocol),
        "backend/app/executor.py": render_backend_executor(protocol),
        "backend/app/debug.py": render_backend_debug(),
        "backend/app/store.py": render_backend_store(),
        "backend/app/tools.py": render_backend_tools(protocol),
        "backend/app/workflow.py": render_backend_workflow(),
        "backend/app/runtime.py": render_backend_runtime(protocol),
        "backend/requirements.txt": render_backend_requirements(),
        "backend/tests/test_protocol_shape.py": render_test(),
        "frontend/AgentPlayground.vue": render_frontend_vue(protocol.get("project_name") or project_name),
        "frontend/api.js": render_frontend_api(),
        "docs/workflow_plan.md": exports["workflow_plan.md"],
        "docs/development_plan.md": exports["development_plan.md"],
        "docs/planner_prompt.md": exports["planner_prompt.md"],
        "docs/executor_skeleton.py": exports["executor_skeleton.py"],
        "docs/architecture_check.md": exports["architecture_check.md"],
        "docs/eval_policy.md": exports["eval_policy.md"],
        "docs/development_advice.md": exports["development_advice.md"],
        "docs/runtime_debug_guide.md": render_runtime_debug_guide(),
        "session.json": json.dumps(session, ensure_ascii=False, indent=2),
    }
    for rel, content in files.items():
        path = target / rel
        write_text(path, content)
        written.append(path)
    return written
