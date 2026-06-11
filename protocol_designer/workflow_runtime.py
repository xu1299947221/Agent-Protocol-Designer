from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from .core import ensure_harness_protocol
from .preview_runtime import build_context_pack, infer_artifact_effect, infer_tool_plan

TERMINAL_STATUSES = {"completed", "skipped", "failed", "blocked", "awaiting_human"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _node_id(node: dict[str, Any], index: int) -> str:
    return str(node.get("id") or node.get("name") or f"node_{index}").strip()


def _node_name(node: dict[str, Any], node_id: str) -> str:
    return str(node.get("name") or node_id).strip()


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    return [value]


def normalize_workflow_nodes(protocol: dict[str, Any]) -> list[dict[str, Any]]:
    protocol = ensure_harness_protocol(protocol or {})
    workflow = protocol.get("workflow") or {}
    nodes = [item for item in workflow.get("nodes") or [] if isinstance(item, dict)]
    if not nodes:
        operations = [item for item in protocol.get("operations") or [] if isinstance(item, dict) and item.get("name")]
        nodes = [
            {
                "id": str(op.get("name")),
                "type": "agent" if op.get("llm_role") else "tool",
                "name": op.get("description") or op.get("name"),
                "description": op.get("description") or "由 operation 自动生成的运行节点。",
                "operation": op.get("name"),
                "inputs": ["context_pack"],
                "outputs": [f"{op.get('name')}_result"],
                "depends_on": [],
                "requires_confirmation": bool(op.get("requires_confirmation") or str(op.get("risk") or "").lower() == "high"),
            }
            for op in operations[:8]
        ]
    normalized = []
    previous_id = ""
    for index, node in enumerate(nodes, start=1):
        node = deepcopy(node)
        node_id = _node_id(node, index)
        depends_on = _as_list(node.get("depends_on"))
        if not depends_on:
            depends_on = _incoming_dependencies(workflow.get("edges") or [], node_id)
        normalized.append({
            "id": node_id,
            "name": _node_name(node, node_id),
            "type": str(node.get("type") or "agent"),
            "description": str(node.get("description") or ""),
            "operation": str(node.get("operation") or ""),
            "agent": str(node.get("agent") or ""),
            "inputs": _as_list(node.get("inputs")),
            "outputs": _as_list(node.get("outputs")),
            "depends_on": depends_on,
            "requires_confirmation": bool(node.get("requires_confirmation")) or _review_required_before(protocol, node_id),
            "order": index,
            "previous_id": previous_id,
        })
        previous_id = node_id
    return normalized


def _incoming_dependencies(edges: list[Any], node_id: str) -> list[str]:
    deps = []
    for edge in edges:
        if isinstance(edge, dict) and str(edge.get("to") or "") == node_id and edge.get("from"):
            deps.append(str(edge.get("from")))
        elif isinstance(edge, (list, tuple)) and len(edge) >= 2 and str(edge[1]) == node_id:
            deps.append(str(edge[0]))
    return deps


def _review_required_before(protocol: dict[str, Any], node_id: str) -> bool:
    workflow = protocol.get("workflow") or {}
    for point in workflow.get("human_review_points") or []:
        if isinstance(point, dict) and str(point.get("required_before") or "") == node_id:
            return True
    return False


def _operation_requires_confirmation(protocol: dict[str, Any], operation: str) -> bool:
    for op in protocol.get("operations") or []:
        if isinstance(op, dict) and op.get("name") == operation:
            return bool(op.get("requires_confirmation") or str(op.get("risk") or "").lower() == "high")
    return False


def _ready(node: dict[str, Any], node_states: dict[str, dict[str, Any]]) -> bool:
    deps = [dep for dep in node.get("depends_on") or [] if dep]
    return all((node_states.get(dep) or {}).get("status") == "completed" for dep in deps)


def _make_artifact(
    protocol: dict[str, Any],
    node: dict[str, Any],
    index: int,
    existing_artifacts: list[dict[str, Any]] | None = None,
    source_trace_id: str = "",
) -> dict[str, Any]:
    outputs = node.get("outputs") or []
    artifact_name = str(outputs[0] if outputs else f"{node['id']}_artifact")
    existing_artifacts = [item for item in existing_artifacts or [] if isinstance(item, dict)]
    same_name = [item for item in existing_artifacts if item.get("name") == artifact_name]
    version_number = len(same_name) + 1
    previous = same_name[-1] if same_name else None
    ext = "json"
    if any(word in artifact_name.lower() for word in ("doc", "document", "bid", "draft")):
        ext = "md"
    elif any(word in artifact_name.lower() for word in ("report", "matrix", "result")):
        ext = "json"
    artifact_id = f"artifact_{index}_{node['id']}_v{version_number}"
    snapshot_id = f"snapshot_before_{artifact_id}" if previous else ""
    return {
        "id": artifact_id,
        "artifact_key": artifact_name,
        "name": artifact_name,
        "type": ext,
        "version": f"v{version_number}",
        "version_number": version_number,
        "previous_version_id": previous.get("id") if previous else None,
        "snapshot_id": snapshot_id,
        "producer_node": node["id"],
        "status": "mock_created",
        "is_active": True,
        "rollback_available": bool(previous),
        "confirmation_status": _artifact_confirmation_status(protocol, artifact_name, node),
        "source_trace_id": source_trace_id,
        "lineage": {
            "created_from_node": node["id"],
            "previous_version_id": previous.get("id") if previous else None,
            "snapshot_id": snapshot_id,
        },
        "evidence": ["Runtime 雏形只模拟产物创建，不写真实文件。", "产物版本会记录来源节点和 trace，便于后续回滚。"],
        "created_at": _now(),
    }


def _artifact_confirmation_status(protocol: dict[str, Any], artifact_name: str, node: dict[str, Any]) -> str:
    if node.get("requires_confirmation") or node.get("type") == "human_review":
        return "confirmed_by_runtime_gate"
    model = protocol.get("artifact_model") or {}
    for item in model.get("artifacts") or []:
        if not isinstance(item, dict):
            continue
        keys = {str(item.get("id") or ""), str(item.get("name") or ""), str(item.get("type") or "")}
        if artifact_name in keys and item.get("review_required"):
            return "requires_human_review"
    if any(word in artifact_name.lower() for word in ("doc", "document", "bid", "export")) or any(word in artifact_name for word in ("投标", "文档", "导出")):
        return "review_recommended"
    return "auto_created"


def build_artifact_version_index(artifacts: list[dict[str, Any]]) -> dict[str, Any]:
    versions = [item for item in artifacts or [] if isinstance(item, dict)]
    latest_by_name: dict[str, dict[str, Any]] = {}
    snapshots = []
    rollback_points = []
    for artifact in versions:
        name = str(artifact.get("name") or artifact.get("artifact_key") or artifact.get("id") or "")
        if artifact.get("snapshot_id"):
            snapshots.append({
                "snapshot_id": artifact.get("snapshot_id"),
                "before_version_id": artifact.get("previous_version_id"),
                "new_version_id": artifact.get("id"),
                "artifact_name": name,
            })
        if artifact.get("rollback_available"):
            rollback_points.append({
                "artifact_id": artifact.get("id"),
                "artifact_name": name,
                "can_rollback_to": artifact.get("previous_version_id"),
            })
        current = latest_by_name.get(name)
        if not current or int(artifact.get("version_number") or 0) >= int(current.get("version_number") or 0):
            latest_by_name[name] = artifact
    return {
        "versions": versions,
        "latest_by_name": latest_by_name,
        "snapshots": snapshots,
        "rollback_points": rollback_points,
        "policy_note": "真实 Runtime 写入或覆盖产物前应先生成快照；当前雏形记录版本关系和回滚点，不修改真实文件。",
    }


def rollback_artifact_in_job(job: dict[str, Any], artifact_id: str) -> dict[str, Any]:
    result = job.get("result") or {}
    artifacts = [item for item in result.get("artifacts") or [] if isinstance(item, dict)]
    target = next((item for item in artifacts if item.get("id") == artifact_id), None)
    if not target:
        raise KeyError(artifact_id)
    name = target.get("name")
    for artifact in artifacts:
        if artifact.get("name") == name:
            artifact["is_active"] = artifact.get("id") == artifact_id
            if artifact.get("id") == artifact_id:
                artifact["rollback_marked_at"] = _now()
                artifact["status"] = "rollback_target_active"
    trace = result.setdefault("trace", [])
    trace.append({
        "step": "artifact_rollback",
        "artifact_id": artifact_id,
        "artifact_name": name,
        "detail": "已将该产物版本标记为当前活动版本。真实项目应在这里恢复文件或文档内容。",
        "at": _now(),
    })
    result["artifacts"] = artifacts
    result["artifact_versions"] = build_artifact_version_index(artifacts)
    job["result"] = result
    job["status"] = result.get("status") or job.get("status")
    job["updated_at"] = _now()
    return job


def _operation_map(protocol: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(op.get("name")): op for op in protocol.get("operations") or [] if isinstance(op, dict) and op.get("name")}


def _tool_map(protocol: dict[str, Any]) -> dict[str, dict[str, Any]]:
    tools = ((protocol.get("tool_registry") or {}).get("tools") or [])
    return {str(tool.get("name")): tool for tool in tools if isinstance(tool, dict) and tool.get("name")}


def _operation_tools(protocol: dict[str, Any], operation: str) -> list[str]:
    registry = protocol.get("tool_registry") or {}
    for item in registry.get("operation_tool_bindings") or []:
        if isinstance(item, dict) and item.get("operation") == operation:
            return [str(tool) for tool in item.get("tools") or [] if tool]
    return []


def _semantic_tools_for_node(text: str, tool_map: dict[str, dict[str, Any]]) -> list[str]:
    candidates: list[str] = []
    def add(tool: str) -> None:
        if tool in tool_map and tool not in candidates:
            candidates.append(tool)
    if any(word in text for word in ("parse", "解析", "upload", "上传", "file", "文件")):
        add("file_parser")
    if any(word in text for word in ("rag", "search", "retrieve", "检索", "素材", "知识")):
        add("rag_search")
    if any(word in text for word in ("graph", "图谱", "关系")):
        add("graph_query")
    if any(word in text for word in ("export", "导出", "docx", "pdf")):
        add("document_exporter")
    if any(word in text for word in ("edit", "修改", "章节", "文档", "document")):
        add("document_editor")
    if any(word in text for word in ("check", "review", "审校", "检查", "合规")):
        add("quality_checker")
    return candidates


def build_node_implementation_binding(protocol: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
    protocol = ensure_harness_protocol(protocol or {})
    node_type = str(node.get("type") or "agent").lower()
    operation = str(node.get("operation") or "")
    operation_spec = _operation_map(protocol).get(operation, {})
    tool_map = _tool_map(protocol)
    text = " ".join(map(str, [node.get("id"), node.get("name"), node.get("description"), operation])).lower()
    explicit_tools = _as_list(node.get("tool") or node.get("tools") or node.get("tool_name"))
    semantic_tools = _semantic_tools_for_node(text, tool_map)
    operation_tools = explicit_tools or _operation_tools(protocol, operation) or semantic_tools
    validators = _as_list(node.get("validator") or node.get("validators")) or _as_list(operation_spec.get("validators"))
    rag_required_ops = set(map(str, (protocol.get("knowledge_policy") or {}).get("rag_required_operations") or []))
    is_rag = (
        node_type in {"rag", "knowledge", "retrieval", "graph"}
        or operation in rag_required_ops
        or any(word in text for word in ("rag", "search", "retrieve", "retrieval", "knowledge", "graph", "检索", "知识", "素材", "图谱"))
    )
    if node_type in {"human", "human_review", "review"}:
        return {
            "node_id": node["id"],
            "binding_type": "human_review",
            "target": node.get("name") or node["id"],
            "status": "bound",
            "source": "workflow.node.type",
            "runtime_behavior": "pause_until_user_approval",
            "description": "人工确认节点会暂停 Runtime，等待用户确认后继续。",
        }
    if node_type == "validator":
        return {
            "node_id": node["id"],
            "binding_type": "validator",
            "target": validators or ["validator_check"],
            "status": "bound" if validators else "needs_implementation",
            "source": "node.validators / operation.validators",
            "runtime_behavior": "run_validator_rules",
            "description": "Validator 节点应执行确定性校验规则，不应只让 LLM 自己判断。",
        }
    if is_rag:
        kb = (protocol.get("knowledge_policy") or {}).get("knowledge_bases") or []
        targets = operation_tools or ["rag_search"]
        if "graph" in text or "图谱" in text:
            targets = operation_tools or ["graph_query"]
        return {
            "node_id": node["id"],
            "binding_type": "rag",
            "target": targets,
            "status": "bound" if targets or kb else "needs_implementation",
            "source": "knowledge_policy / node semantic",
            "runtime_behavior": "retrieve_with_citations",
            "knowledge_bases": kb,
            "description": "RAG 节点应检索知识库或图谱，并返回证据、引用和置信度。",
        }
    if node_type == "tool" or explicit_tools or (operation_tools and node_type != "agent"):
        tools = operation_tools or [str(node.get("tool") or "").strip()]
        tools = [tool for tool in tools if tool]
        return {
            "node_id": node["id"],
            "binding_type": "tool",
            "target": tools,
            "status": "bound" if tools else "needs_implementation",
            "source": "tool_registry.operation_tool_bindings / node.tool",
            "runtime_behavior": "call_registered_tool",
            "tool_specs": [tool_map.get(tool, {"name": tool}) for tool in tools],
            "description": "Tool 节点应调用工具注册表里的工具，并记录输入、输出、副作用和失败策略。",
        }
    if validators:
        return {
            "node_id": node["id"],
            "binding_type": "validator",
            "target": validators,
            "status": "bound",
            "source": "operation.validators",
            "runtime_behavior": "run_validator_rules",
            "description": "该节点虽不是 validator 类型，但 operation 声明了校验规则，Runtime 会先按校验绑定处理。",
        }
    prompt = node.get("prompt") or node.get("prompt_template") or operation_spec.get("llm_role") or node.get("description") or operation_spec.get("description")
    return {
        "node_id": node["id"],
        "binding_type": "llm_prompt",
        "target": "llm_generate",
        "status": "bound" if prompt else "needs_implementation",
        "source": "node.prompt / operation.llm_role / node.description",
        "runtime_behavior": "call_llm_with_structured_prompt",
        "prompt_preview": str(prompt or "待补充节点 prompt")[:800],
        "description": "Agent 节点应绑定结构化 Prompt，输入 Context Pack，输出可校验 JSON。",
    }


def build_node_implementation_bindings(protocol: dict[str, Any], nodes: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    protocol = ensure_harness_protocol(protocol or {})
    nodes = nodes or normalize_workflow_nodes(protocol)
    return [build_node_implementation_binding(protocol, node) for node in nodes]


def build_runtime_plan(protocol: dict[str, Any], user_message: str = "") -> dict[str, Any]:
    protocol = ensure_harness_protocol(protocol or {})
    workflow = protocol.get("workflow") or {}
    nodes = normalize_workflow_nodes(protocol)
    return {
        "runtime_name": workflow.get("name") or protocol.get("project_name") or "未命名 Runtime",
        "goal": workflow.get("goal") or protocol.get("domain_summary") or "按当前协议模拟执行节点级工作流。",
        "strategy": workflow.get("strategy") or "节点按依赖顺序运行；遇到人工确认点暂停；所有产物只模拟创建并记录 Trace。",
        "user_message": user_message,
        "nodes": nodes,
        "edges": workflow.get("edges") or [],
        "parallel_groups": workflow.get("parallel_groups") or [],
        "human_review_points": workflow.get("human_review_points") or [],
        "artifacts_declared": workflow.get("artifacts") or [],
        "implementation_bindings": build_node_implementation_bindings(protocol, nodes),
        "explanation": [
            "这不是完整业务执行器，而是 APD 内置的 Agent Runtime 雏形。",
            "它把 workflow 节点逐个跑一遍，让你看到哪里会自动执行、哪里会暂停等人工确认、哪里会产生文档或报告产物。",
            "真实项目后续要把每个节点绑定到具体 Tool、Validator、RAG 或编辑器写入逻辑。",
        ],
    }


def run_workflow_once(
    *,
    protocol: dict[str, Any],
    user_message: str = "",
    context: dict[str, Any] | None = None,
    approvals: dict[str, bool] | None = None,
    max_steps: int = 30,
    existing_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    protocol = ensure_harness_protocol(protocol or {})
    context_pack = build_context_pack(protocol, user_message, context or {})
    nodes = normalize_workflow_nodes(protocol)
    approvals = approvals or {}
    previous_states = (existing_result or {}).get("node_states") or {}
    previous_artifacts = (existing_result or {}).get("artifacts") or []
    previous_trace = (existing_result or {}).get("trace") or []
    node_states: dict[str, dict[str, Any]] = {}
    for node in nodes:
        previous = previous_states.get(node["id"]) if isinstance(previous_states, dict) else None
        base = {
            "node_id": node["id"],
            "name": node["name"],
            "type": node["type"],
            "status": "pending",
            "depends_on": node.get("depends_on") or [],
            "requires_confirmation": bool(node.get("requires_confirmation") or _operation_requires_confirmation(protocol, node.get("operation") or "")),
            "implementation_binding": build_node_implementation_binding(protocol, node),
        }
        if isinstance(previous, dict):
            base.update(previous)
            base["depends_on"] = node.get("depends_on") or []
            base["requires_confirmation"] = bool(node.get("requires_confirmation") or previous.get("requires_confirmation") or _operation_requires_confirmation(protocol, node.get("operation") or ""))
            if base.get("status") == "awaiting_human" and approvals.get(node["id"]):
                base["status"] = "pending"
                base["approved_at"] = _now()
                base["observation"] = "用户已确认，节点恢复为待执行。"
        node_states[node["id"]] = base
    trace: list[dict[str, Any]] = list(previous_trace if isinstance(previous_trace, list) else [])
    artifacts: list[dict[str, Any]] = list(previous_artifacts if isinstance(previous_artifacts, list) else [])
    waiting_for: dict[str, Any] | None = None
    completed_count = sum(1 for item in node_states.values() if item.get("status") == "completed")
    step = 0
    progressed = True
    while step < max_steps and progressed:
        progressed = False
        for node in nodes:
            state = node_states[node["id"]]
            if state["status"] != "pending" or not _ready(node, node_states):
                continue
            step += 1
            progressed = True
            state["started_at"] = _now()
            binding = build_node_implementation_binding(protocol, node)
            state["implementation_binding"] = binding
            operation = node.get("operation") or ""
            tool_plan = infer_tool_plan(operation, context_pack) if operation else {"selected_tools": [], "note": "该节点没有绑定 operation。"}
            artifact_effect = infer_artifact_effect(operation, context_pack) if operation else {"operation": "", "effect_type": "workflow_node", "note": "按节点 outputs 模拟产物。"}
            if state["requires_confirmation"] and not approvals.get(node["id"]):
                state.update({
                    "status": "awaiting_human",
                    "finished_at": _now(),
                    "observation": "运行暂停：该节点需要人工确认后才能继续。",
                    "tool_plan": tool_plan,
                    "artifact_effect": artifact_effect,
                })
                waiting_for = {
                    "node_id": node["id"],
                    "name": node["name"],
                    "reason": "节点被标记为人工确认点，或绑定了高风险/需确认 operation。",
                    "how_to_continue": "用户确认后，Runtime 会保存 approval 并从当前节点继续运行，不会重跑已完成节点。",
                }
                trace.append({"step": "await_human", "node_id": node["id"], "detail": waiting_for, "at": _now()})
                return _runtime_result(protocol, context_pack, nodes, node_states, trace, artifacts, waiting_for, status="awaiting_human")
            trace_id = f"trace_{len(trace) + 1}_{node["id"]}"
            produced = _make_artifact(protocol, node, len(artifacts) + 1, artifacts, trace_id)
            for artifact in artifacts:
                if artifact.get("name") == produced.get("name"):
                    artifact["is_active"] = False
            if node.get("outputs") or node.get("type") in {"agent", "tool", "validator", "human_review"}:
                artifacts.append(produced)
            state.update({
                "status": "completed",
                "finished_at": _now(),
                "observation": _node_observation(node, binding),
                "tool_plan": tool_plan,
                "artifact_effect": artifact_effect,
                "produced_artifact_ids": [produced["id"]],
                "execution_preview": _binding_execution_preview(binding),
            })
            completed_count = sum(1 for item in node_states.values() if item.get("status") == "completed")
            trace.append({
                "step": "node_completed",
                "node_id": node["id"],
                "node_name": node["name"],
                "node_type": node["type"],
                "operation": operation,
                "produced_artifact": produced,
                "implementation_binding": binding,
                "execution_preview": _binding_execution_preview(binding),
                "trace_id": produced.get("source_trace_id"),
                "at": _now(),
            })
            if step >= max_steps:
                break
    pending = [node_id for node_id, state in node_states.items() if state["status"] == "pending"]
    awaiting = [node_id for node_id, state in node_states.items() if state["status"] == "awaiting_human"]
    if awaiting:
        status = "awaiting_human"
        node_id = awaiting[0]
        waiting_for = {
            "node_id": node_id,
            "name": node_states[node_id].get("name") or node_id,
            "reason": "该节点仍在等待人工确认。",
            "how_to_continue": "传入该 node_id 的 approval=true 后继续运行。",
        }
    elif pending:
        status = "blocked"
        trace.append({"step": "blocked", "detail": "存在未满足依赖或超过最大步数的节点。", "pending_nodes": pending, "at": _now()})
    else:
        status = "completed"
    return _runtime_result(protocol, context_pack, nodes, node_states, trace, artifacts, waiting_for, status=status, completed_count=completed_count)


def runtime_result_to_job(
    *,
    job_id: str,
    session_id: str,
    result: dict[str, Any],
    user_message: str = "",
    context: dict[str, Any] | None = None,
    approvals: dict[str, bool] | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    now = _now()
    return {
        "job_id": job_id,
        "session_id": session_id,
        "status": result.get("status") or "unknown",
        "title": _job_title(result, user_message),
        "user_message": user_message,
        "context": context or {},
        "approvals": approvals or {},
        "created_at": created_at or now,
        "updated_at": now,
        "result": result,
        "summary": result.get("summary") or {},
        "waiting_for": result.get("waiting_for"),
    }


def _job_title(result: dict[str, Any], user_message: str) -> str:
    plan = result.get("runtime_plan") or {}
    name = plan.get("runtime_name") or "Runtime Job"
    message = " ".join(str(user_message or "").split())
    if message:
        message = message[:30] + ("..." if len(message) > 30 else "")
        return f"{name}：{message}"
    return str(name)


def _node_observation(node: dict[str, Any], binding: dict[str, Any] | None = None) -> str:
    binding = binding or {}
    binding_type = binding.get("binding_type") or node.get("type") or "agent"
    if binding_type == "human_review":
        return "人工确认节点已通过确认，Runtime 继续向下执行。"
    if binding_type == "validator":
        return "已按 Validator 绑定模拟执行校验规则；真实项目应在这里调用确定性 validator。"
    if binding_type == "tool":
        return "已按 Tool Registry 绑定模拟调用工具；真实项目应在这里执行注册工具并记录副作用。"
    if binding_type == "rag":
        return "已按 Knowledge/RAG Policy 绑定模拟检索；真实项目应返回证据、引用和置信度。"
    return "已按 LLM Prompt 绑定模拟 Agent 节点；真实项目应调用 LLM 并输出结构化 JSON。"


def _binding_execution_preview(binding: dict[str, Any]) -> dict[str, Any]:
    binding_type = binding.get("binding_type") or "unknown"
    if binding_type == "tool":
        return {"mode": "tool_call", "targets": binding.get("target") or [], "side_effects_checked": True}
    if binding_type == "validator":
        return {"mode": "validator_call", "rules": binding.get("target") or [], "deterministic_required": True}
    if binding_type == "rag":
        return {"mode": "rag_retrieval", "targets": binding.get("target") or [], "citations_required": True}
    if binding_type == "human_review":
        return {"mode": "human_gate", "approval_required": True}
    if binding_type == "llm_prompt":
        return {"mode": "llm_call", "target": binding.get("target"), "json_output_required": True}
    return {"mode": "needs_implementation", "reason": "节点尚未绑定可执行实现。"}


def _runtime_result(
    protocol: dict[str, Any],
    context_pack: dict[str, Any],
    nodes: list[dict[str, Any]],
    node_states: dict[str, dict[str, Any]],
    trace: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
    waiting_for: dict[str, Any] | None,
    *,
    status: str,
    completed_count: int | None = None,
) -> dict[str, Any]:
    completed_count = completed_count if completed_count is not None else sum(1 for item in node_states.values() if item.get("status") == "completed")
    return {
        "status": status,
        "summary": {
            "total_nodes": len(nodes),
            "completed_nodes": completed_count,
            "pending_nodes": sum(1 for item in node_states.values() if item.get("status") == "pending"),
            "awaiting_human_nodes": sum(1 for item in node_states.values() if item.get("status") == "awaiting_human"),
            "artifact_count": len(artifacts),
            "active_artifact_count": sum(1 for item in artifacts if isinstance(item, dict) and item.get("is_active")),
            "rollback_point_count": len(build_artifact_version_index(artifacts).get("rollback_points") or []),
        },
        "beginner_explanation": {
            "一句话": "APD 现在不只是看一轮 Agent 决策，而是在模拟整个 workflow 怎么一步步跑。",
            "节点是什么": "节点就是流程里的一个小步骤，例如解析文件、抽取要求、人工确认、生成章节、合规检查、导出。",
            "为什么会暂停": "遇到高风险或人工确认节点时，Runtime 必须停下来等人确认，不能让 AI 自己往下改状态或交付文件。",
            "产物是什么": "产物就是每一步产生的东西，例如结构化招标文件、需求矩阵、目录、草稿、合规报告、导出包。",
        },
        "runtime_plan": build_runtime_plan(protocol, context_pack.get("user_message") or ""),
        "context_pack": context_pack,
        "node_states": node_states,
        "artifacts": artifacts,
        "artifact_versions": build_artifact_version_index(artifacts),
        "implementation_bindings": build_node_implementation_bindings(protocol, nodes),
        "waiting_for": waiting_for,
        "trace": trace,
        "next_action": _next_action(status, waiting_for),
    }


def _next_action(status: str, waiting_for: dict[str, Any] | None) -> str:
    if status == "awaiting_human" and waiting_for:
        return f"等待人工确认节点：{waiting_for.get('name') or waiting_for.get('node_id')}"
    if status == "blocked":
        return "检查节点依赖、边或 workflow 设计是否完整。"
    return "工作流模拟运行完成，可以检查产物和 Trace，再决定真实节点怎么实现。"
