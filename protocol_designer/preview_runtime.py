from __future__ import annotations

import json
import re
from typing import Any

from .core import EMPTY_PROTOCOL, ensure_harness_protocol, extract_json, merge_protocol
from .llm import LLMError, chat_json_result, resolve_config

SPECIAL_OPERATIONS = {"ask_clarification", "unsupported", "need_confirmation"}


def _operation_name(op: dict[str, Any]) -> str:
    return str(op.get("name") or "").strip()


def _short(value: Any, limit: int = 500) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[:limit] + "..."


def build_memory_retrieval(protocol: dict[str, Any], user_message: str, extra_context: dict[str, Any] | None = None) -> dict[str, Any]:
    memory = protocol.get("memory_policy") or {}
    extra_context = extra_context or {}
    provided_memory = extra_context.get("memory") or extra_context.get("memories") or []
    if isinstance(provided_memory, dict):
        provided_memory = [provided_memory]
    if not isinstance(provided_memory, list):
        provided_memory = []
    selected = []
    for index, item in enumerate(provided_memory[:8], start=1):
        if isinstance(item, dict):
            selected.append({
                "memory_id": item.get("memory_id") or item.get("id") or f"provided_{index}",
                "type": item.get("type") or "provided_memory",
                "content": _short(item.get("content") or item.get("text") or item, 300),
                "source": item.get("source") or "preview_context",
                "confidence": item.get("confidence", 1.0),
            })
        else:
            selected.append({
                "memory_id": f"provided_{index}",
                "type": "provided_memory",
                "content": _short(item, 300),
                "source": "preview_context",
                "confidence": 1.0,
            })
    return {
        "enabled": bool(memory.get("enabled")),
        "policy_summary": {
            "memory_types": memory.get("memory_types") or [],
            "read_rules": memory.get("read_rules") or [],
            "write_rules": memory.get("write_rules") or [],
            "confirmation_required": memory.get("confirmation_required") or [],
        },
        "retrieved_memories": selected,
        "injection_note": "预览模式不会访问真实记忆库；这里只展示传入记忆和策略如何进入上下文包。",
    }


def build_recovery_context(protocol: dict[str, Any]) -> dict[str, Any]:
    recovery = protocol.get("error_recovery") or {}
    return {
        "strategies": recovery.get("strategies") or [],
        "retry_policy": recovery.get("retry_policy") or {},
        "fallback_policy": recovery.get("fallback_policy") or {},
        "rollback_policy": recovery.get("rollback_policy") or {},
        "human_handoff_policy": recovery.get("human_handoff_policy") or {},
        "tool_failure_policies": recovery.get("tool_failure_policies") or [],
        "record_eval_case_on_failure": bool(recovery.get("record_eval_case_on_failure")),
        "note": "预览模式只推荐失败恢复策略，不会真实重试、回滚或转人工。",
    }


def infer_recovery_plan(result: dict[str, Any]) -> dict[str, Any]:
    context_pack = result.get("context_pack") or {}
    recovery = context_pack.get("recovery_context") or {}
    permission_check = result.get("permission_check") or {}
    op_call = result.get("op_call") or {}
    validators = result.get("validator_results") or []
    executor = result.get("executor_preview") or {}
    artifact_effect = result.get("artifact_effect") or {}
    tool_plan = result.get("tool_plan") or {}
    reasons = []
    strategy = "continue"
    next_step = "当前没有明显失败，继续观察真实执行结果。"
    if permission_check.get("status") == "forbidden":
        strategy = "block_and_handoff"
        reasons.append("权限策略禁止执行。")
        next_step = "阻断执行，整理原因并转人工或让用户调整请求。"
    elif permission_check.get("requires_confirmation"):
        strategy = "await_human_confirmation"
        reasons.append("权限策略要求人工确认。")
        next_step = "暂停执行，生成确认卡片，用户确认后再继续。"
    elif op_call.get("missing_info") or op_call.get("operation") == "ask_clarification":
        strategy = "ask_clarification"
        reasons.append("信息不足或目标不明确。")
        next_step = "追问用户补齐缺失信息，不调用工具。"
    elif any(isinstance(item, dict) and not item.get("ok", False) for item in validators):
        strategy = "block_and_explain"
        reasons.append("校验器存在失败项。")
        next_step = "阻断执行，解释失败原因，并给出可补充的信息。"
    elif artifact_effect.get("requires_snapshot"):
        strategy = "snapshot_then_execute"
        reasons.append("产物可能被修改，需要快照保护。")
        next_step = "创建快照，展示 diff 或预览，再执行修改。"
    elif tool_plan.get("has_high_risk_tool"):
        strategy = "confirm_then_run_tool"
        reasons.append("工具计划包含高风险工具。")
        next_step = "确认权限和风险后再调用工具。"
    elif executor.get("status") in {"blocked", "needs_clarification", "unsupported"}:
        strategy = "fallback_or_ask_user"
        reasons.append("模拟执行器显示当前无法直接执行。")
        next_step = "根据失败类型选择追问、降级或转人工。"
    if not reasons:
        reasons.append("未发现明显失败点。")
    matched_strategies = []
    for item in recovery.get("strategies") or []:
        if isinstance(item, dict) and (strategy in str(item.get("strategy") or "") or item.get("scope") in {"validator", "tool", "artifact", "permission"}):
            matched_strategies.append(item)
    return {
        "strategy": strategy,
        "reasons": reasons,
        "next_step": next_step,
        "matched_strategies": matched_strategies[:5],
        "retry_policy": recovery.get("retry_policy") or {},
        "rollback_policy": recovery.get("rollback_policy") or {},
        "record_eval_case_on_failure": bool(recovery.get("record_eval_case_on_failure")),
    }


def build_permission_context(protocol: dict[str, Any]) -> dict[str, Any]:
    policy = protocol.get("permission_policy") or {}
    return {
        "auto_allowed": policy.get("auto_allowed") or [],
        "confirmation_required": policy.get("confirmation_required") or [],
        "forbidden": policy.get("forbidden") or [],
        "role_required": policy.get("role_required") or [],
        "risk_matrix": policy.get("risk_matrix") or [],
        "note": "预览模式只做权限判断说明，不会真实授权或执行。",
    }


def evaluate_permission(operation: str, tool_plan: dict[str, Any], context_pack: dict[str, Any]) -> dict[str, Any]:
    permission = context_pack.get("permission_context") or {}
    op_name = str(operation or "")
    tool_names = set(tool_plan.get("tool_names") or [])
    forbidden_hits = []
    confirmation_hits = []
    role_hits = []
    for item in permission.get("forbidden") or []:
        if not isinstance(item, dict):
            continue
        target = str(item.get("target") or item.get("name") or "")
        if target and (target == op_name or target in tool_names):
            forbidden_hits.append(item)
    for item in permission.get("confirmation_required") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        item_type = str(item.get("type") or "")
        if name == op_name or name in tool_names or (item_type == "operation" and name == op_name) or (item_type == "tool" and name in tool_names):
            confirmation_hits.append(item)
    for item in permission.get("role_required") or []:
        if not isinstance(item, dict):
            continue
        targets = [str(x).lower() for x in item.get("targets") or []]
        haystack = op_name.lower() + " " + " ".join(tool_names).lower()
        if any(target and target in haystack for target in targets):
            role_hits.append(item)
    allowed = not forbidden_hits and not confirmation_hits and not tool_plan.get("has_high_risk_tool")
    requires_confirmation = bool(confirmation_hits or tool_plan.get("has_high_risk_tool") or role_hits)
    status = "forbidden" if forbidden_hits else ("requires_confirmation" if requires_confirmation else "allowed")
    reasons = []
    reasons.extend([item.get("reason") or str(item) for item in forbidden_hits])
    reasons.extend([item.get("reason") or str(item) for item in confirmation_hits])
    if tool_plan.get("has_high_risk_tool"):
        reasons.append("工具计划包含高风险工具。")
    reasons.extend([item.get("reason") or str(item) for item in role_hits])
    if not reasons:
        reasons.append("当前权限策略允许在校验通过后自动执行。")
    return {
        "status": status,
        "allowed": allowed,
        "requires_confirmation": requires_confirmation,
        "forbidden_hits": forbidden_hits,
        "confirmation_hits": confirmation_hits,
        "role_hits": role_hits,
        "reasons": reasons,
        "message": "；".join(reasons[:3]),
    }


def build_tool_context(protocol: dict[str, Any]) -> dict[str, Any]:
    registry = protocol.get("tool_registry") or {}
    return {
        "tools": registry.get("tools") or [],
        "operation_tool_bindings": registry.get("operation_tool_bindings") or [],
        "mcp_servers": registry.get("mcp_servers") or [],
        "failure_policies": registry.get("failure_policies") or [],
        "note": "预览模式只展示工具调用计划，不会真实调用外部工具。",
    }


def infer_tool_plan(operation: str, context_pack: dict[str, Any]) -> dict[str, Any]:
    tool_context = context_pack.get("tool_context") or {}
    bindings = tool_context.get("operation_tool_bindings") or []
    tools = {item.get("name"): item for item in tool_context.get("tools") or [] if isinstance(item, dict) and item.get("name")}
    matched = None
    for item in bindings:
        if isinstance(item, dict) and item.get("operation") == operation:
            matched = item
            break
    tool_names = list((matched or {}).get("tools") or [])
    if not tool_names:
        lower = str(operation or "").lower()
        tool_names = ["validator_check", "trace_writer"]
        if any(key in lower for key in ("generate", "draft", "write", "rewrite", "create")):
            tool_names.insert(0, "llm_generate")
        if any(key in lower for key in ("export", "render")) and "document_exporter" in tools:
            tool_names.append("document_exporter")
    planned_tools = []
    high_risk = []
    side_effects = []
    for name in dict.fromkeys(tool_names):
        spec = tools.get(name) or {"name": name, "risk": "unknown", "side_effects": []}
        planned_tools.append(spec)
        if str(spec.get("risk") or "").lower() == "high":
            high_risk.append(name)
        side_effects.extend(spec.get("side_effects") or [])
    return {
        "operation": operation,
        "tools": planned_tools,
        "tool_names": [item.get("name") for item in planned_tools],
        "execution_order": [item.get("name") for item in planned_tools],
        "has_high_risk_tool": bool(high_risk),
        "high_risk_tools": high_risk,
        "side_effects": list(dict.fromkeys(side_effects)),
        "message": f"本轮预计调用 {len(planned_tools)} 个工具：{', '.join([str(item.get('name')) for item in planned_tools]) or '无'}。",
    }


def build_artifact_context(protocol: dict[str, Any], extra_context: dict[str, Any] | None = None) -> dict[str, Any]:
    artifact_model = protocol.get("artifact_model") or {}
    extra_context = extra_context or {}
    existing = extra_context.get("artifacts") or extra_context.get("existing_artifacts") or []
    if isinstance(existing, dict):
        existing = [existing]
    if not isinstance(existing, list):
        existing = []
    return {
        "artifacts": artifact_model.get("artifacts") or [],
        "operation_bindings": artifact_model.get("operation_bindings") or [],
        "existing_artifacts": existing[:12],
        "versioning": artifact_model.get("versioning") or {},
        "export_formats": artifact_model.get("export_formats") or [],
        "review_rules": artifact_model.get("review_rules") or [],
        "source_trace_required": bool(artifact_model.get("source_trace_required")),
        "note": "预览模式只展示产物读写计划，不会真实创建、覆盖或导出文件。",
    }


def infer_artifact_effect(operation: str, context_pack: dict[str, Any]) -> dict[str, Any]:
    artifact_context = context_pack.get("artifact_context") or {}
    bindings = artifact_context.get("operation_bindings") or []
    matched = None
    for item in bindings:
        if isinstance(item, dict) and item.get("operation") == operation:
            matched = item
            break
    if not matched:
        lower = str(operation or "").lower()
        writes = []
        reads = []
        if any(key in lower for key in ("draft", "generate", "create", "write", "render")):
            writes = [item.get("id") for item in artifact_context.get("artifacts") or [] if isinstance(item, dict)][:1]
        if any(key in lower for key in ("check", "review", "export")):
            reads = [item.get("id") for item in artifact_context.get("artifacts") or [] if isinstance(item, dict)]
        matched = {"operation": operation, "reads": reads, "writes": writes, "mutation_type": "inferred"}
    writes = matched.get("writes") or []
    reads = matched.get("reads") or []
    return {
        "operation": operation,
        "reads": reads,
        "writes": writes,
        "mutation_type": matched.get("mutation_type") or ("create_or_update" if writes else "read_or_export"),
        "requires_snapshot": bool(writes and (artifact_context.get("versioning") or {}).get("enabled")),
        "requires_review": bool(writes and artifact_context.get("review_rules")),
        "message": f"本轮预计读取 {len(reads)} 个产物，写入/修改 {len(writes)} 个产物。",
    }


def build_state_context(protocol: dict[str, Any], extra_context: dict[str, Any] | None = None) -> dict[str, Any]:
    state_model = protocol.get("state_model") or {}
    extra_context = extra_context or {}
    current_state = extra_context.get("current_state") or extra_context.get("state") or "unknown"
    states = state_model.get("states") or []
    transitions = state_model.get("transitions") or []
    operation_bindings = state_model.get("operation_bindings") or []
    matched_state = None
    for item in states:
        if isinstance(item, dict) and item.get("id") == current_state:
            matched_state = item
            break
    available_transitions = [item for item in transitions if isinstance(item, dict) and item.get("from") == current_state]
    return {
        "current_state": current_state,
        "matched_state": matched_state,
        "available_transitions": available_transitions,
        "state_fields": state_model.get("state_fields") or [],
        "validator_rules": state_model.get("validator_rules") or [],
        "operation_bindings": operation_bindings,
        "note": "预览模式只模拟状态读取和状态校验，不会真实写入状态。",
    }


def build_context_pack(protocol: dict[str, Any], user_message: str, extra_context: dict[str, Any] | None = None) -> dict[str, Any]:
    protocol = ensure_harness_protocol(protocol or {})
    operations = []
    for op in protocol.get("operations") or []:
        if not isinstance(op, dict) or not op.get("name"):
            continue
        operations.append({
            "name": op.get("name"),
            "description": op.get("description") or op.get("purpose") or "",
            "params_schema": op.get("params_schema") or op.get("schema") or op.get("params") or {},
            "validators": op.get("validators") or [],
            "requires_confirmation": bool(op.get("requires_confirmation")),
        })
    memory_retrieval = build_memory_retrieval(protocol, user_message, extra_context)
    memory_context = memory_retrieval.get("retrieved_memories") or []
    state_context = build_state_context(protocol, extra_context)
    artifact_context = build_artifact_context(protocol, extra_context)
    tool_context = build_tool_context(protocol)
    permission_context = build_permission_context(protocol)
    recovery_context = build_recovery_context(protocol)
    context_pack = {
        "user_message": user_message,
        "extra_context": extra_context or {},
        "memory_retrieval": memory_retrieval,
        "memory_context": memory_context,
        "state_context": state_context,
        "artifact_context": artifact_context,
        "tool_context": tool_context,
        "permission_context": permission_context,
        "recovery_context": recovery_context,
        "project_name": protocol.get("project_name") or "",
        "domain_summary": protocol.get("domain_summary") or "",
        "goals": protocol.get("goals") or [],
        "objects": protocol.get("objects") or [],
        "operations": operations,
        "validators": protocol.get("validators") or [],
        "confirmation_rules": protocol.get("confirmation_rules") or [],
        "clarification_rules": protocol.get("clarification_rules") or [],
        "intent_recognition": protocol.get("intent_recognition") or {},
        "routing_policy": protocol.get("routing_policy") or {},
        "intent_binding": protocol.get("intent_binding") or {},
        "workflow": protocol.get("workflow") or {},
        "memory_policy": protocol.get("memory_policy") or {},
        "state_model": protocol.get("state_model") or {},
        "artifact_model": protocol.get("artifact_model") or {},
        "tool_registry": protocol.get("tool_registry") or {},
        "permission_policy": protocol.get("permission_policy") or {},
        "error_recovery": protocol.get("error_recovery") or {},
        "eval_policy": protocol.get("eval_policy") or {},
    }
    return context_pack


def build_preview_prompt(context_pack: dict[str, Any]) -> list[dict[str, str]]:
    operations = context_pack.get("operations") or []
    operation_names = [item.get("name") for item in operations if item.get("name")]
    system = """你是 APD 的 Agent 预览运行器。你不是业务执行器，只能模拟运行当前 Agent 设计。

必须遵守：
- 只输出 JSON，不要输出 Markdown。
- 你要根据上下文包（Context Pack）观察用户输入，结合状态上下文（State Context）、产物上下文（Artifact Context）、工具上下文（Tool Context）、权限上下文（Permission Context）和失败恢复策略（Error Recovery），生成记忆读取（Memory Retrieval）、意图框架（Intent Frame）、操作调用（OpCall）、校验结果（Validator Results）、模拟执行结果（Mock Executor）和记忆写入建议（Memory Write Proposal）。
- 不能声称真实写库、真实导出、真实修改文件；只能说模拟执行或需要接入真实执行器。
- 如果信息不足，operation 使用 ask_clarification，并给出 missing_info 和 clarification_question。
- 如果用户请求不在可用操作范围内，operation 使用 unsupported。
- 如果高风险或需要确认，requires_confirmation 为 true，next_action 使用 awaiting_confirmation。
- 所有面向用户的文字使用中文。
"""
    user = {
        "task": "请预览运行当前 Agent 设计，输出结构化 JSON。",
        "allowed_operations": operation_names + ["ask_clarification", "unsupported", "need_confirmation"],
        "context_pack": context_pack,
        "output_schema": {
            "assistant_message": "中文回复",
            "intent_frame": {
                "intent_type": "create|modify|delete|query|render|confirm|cancel|unknown",
                "action": "动作",
                "target_type": "document|section|selection|object|unknown",
                "target": None,
                "scope": "local|document|workflow|unknown",
                "risk_level": "low|medium|high",
                "confidence": 0.0,
                "evidence": ["证据"]
            },
            "op_call": {
                "operation": "operation name",
                "params": {},
                "confidence": 0.0,
                "requires_confirmation": False,
                "missing_info": [],
                "evidence": []
            },
            "validator_results": [{"name": "validator", "ok": True, "reason": "原因"}],
            "executor_preview": {
                "status": "simulated|blocked|needs_clarification|unsupported",
                "not_executed": True,
                "message": "模拟执行说明",
                "would_do": []
            },
            "memory_write_proposal": {
                "should_write": False,
                "memory_type": "session_memory|task_state_memory|user_preference_memory|project_memory|none",
                "content": "建议写入内容或空",
                "requires_confirmation": True,
                "reason": "为什么建议写入或不写入"
            },
            "trace": [{"step": "memory_retrieval|context_pack|intent_planner|intent_binding|validator|mock_executor|memory_write", "detail": "说明"}],
            "next_action": "done|ask_clarification|awaiting_confirmation|unsupported|validator_failed"
        }
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user, ensure_ascii=False, indent=2)},
    ]


def offline_plan(context_pack: dict[str, Any]) -> dict[str, Any]:
    message = str(context_pack.get("user_message") or "")
    operations = context_pack.get("operations") or []
    selected = "ask_clarification"
    confidence = 0.25
    keywords = [
        ("删除", "delete"),
        ("删掉", "delete"),
        ("导出", "export"),
        ("生成", "generate"),
        ("写", "create"),
        ("大纲", "outline"),
        ("修改", "revise"),
        ("改写", "rewrite"),
        ("润色", "polish"),
        ("检查", "check"),
    ]
    for op in operations:
        name = str(op.get("name") or "")
        desc = str(op.get("description") or "")
        haystack = f"{name} {desc}".lower()
        if any(cn in message and en in haystack for cn, en in keywords):
            selected = name
            confidence = 0.45
            break
    if selected == "ask_clarification" and operations:
        selected = str(operations[0].get("name") or "ask_clarification")
    return {
        "assistant_message": "当前没有成功调用 LLM，我先用离线规则模拟一次预览运行。结果只能用于检查链路，不代表真实智能效果。",
        "intent_frame": {
            "intent_type": "unknown",
            "action": _short(message, 80),
            "target_type": "unknown",
            "target": None,
            "scope": "unknown",
            "risk_level": "medium",
            "confidence": confidence,
            "evidence": ["离线关键词匹配"]
        },
        "op_call": {
            "operation": selected,
            "params": {"user_message": message},
            "confidence": confidence,
            "requires_confirmation": False,
            "missing_info": [],
            "evidence": ["离线规则选择"]
        },
        "validator_results": [],
        "executor_preview": {},
        "memory_write_proposal": {"should_write": False, "memory_type": "none", "content": "", "requires_confirmation": False, "reason": "离线预览不自动建议写入记忆。"},
        "trace": [{"step": "offline_planner", "detail": "LLM 不可用，使用离线规则。"}],
        "next_action": "done",
        "fallback": True,
    }


def build_beginner_explanation(result: dict[str, Any]) -> dict[str, Any]:
    op_call = result.get("op_call") or {}
    operation = op_call.get("operation") or "-"
    next_action = result.get("next_action") or "-"
    confidence = op_call.get("confidence")
    missing_info = op_call.get("missing_info") or []
    validators = result.get("validator_results") or []
    executor = result.get("executor_preview") or {}
    memory_retrieval = result.get("memory_retrieval") or {}
    memory_write = result.get("memory_write_proposal") or {}
    state_context = (result.get("context_pack") or {}).get("state_context") or {}
    artifact_effect = result.get("artifact_effect") or {}
    tool_plan = result.get("tool_plan") or {}
    permission_check = result.get("permission_check") or {}
    recovery_plan = result.get("recovery_plan") or {}
    return {
        "一句话": "这不是在真正执行业务，而是在演示：如果用户这样说，当前 Agent 设计会怎么理解、会选哪个操作、会不会被校验拦住。",
        "它是不是 Agent 的完整结构": "不是完整工程结构，而是一次 Agent 决策链路的剖面图。它展示一轮请求从输入到模拟执行的全过程。",
        "生活类比": [
            "上下文包像把病历、检查单、用户问题一起递给分诊台。",
            "意图框架像分诊台判断：这个人到底要看什么科、问题严不严重。",
            "操作调用像开出一张内部工单：去做哪件事、带哪些参数。",
            "校验结果像护士核对：信息够不够、有没有危险动作、要不要本人确认。",
            "模拟执行器像先演示处理方案，不真的扣费、不真的删文件、不真的发通知。",
            "过程日志像监控录像，出错时能倒回去看是哪一步判断错了。",
        ],
        "这次运行怎么看": {
            "最终动作": next_action,
            "选择的操作": operation,
            "置信度": confidence,
            "缺少的信息": missing_info,
            "校验数量": len(validators),
            "模拟执行状态": executor.get("status"),
            "读取记忆数量": len(memory_retrieval.get("retrieved_memories") or []),
            "是否建议写入记忆": bool(memory_write.get("should_write")),
            "当前状态": state_context.get("current_state"),
            "本轮可能修改产物": artifact_effect.get("writes") or [],
            "预计工具调用": tool_plan.get("tool_names") or [],
            "权限判断": permission_check.get("status"),
            "失败恢复策略": recovery_plan.get("strategy"),
        },
        "如果结果不符合预期该看哪里": [
            "如果 AI 理解错了，先看上下文包是不是没给够信息，或者给了干扰信息。",
            "如果理解对但操作选错了，看操作调用和意图绑定规则，说明 operation 设计或绑定规则可能要改。",
            "如果操作对但被拦住，看校验结果，说明缺参数、需确认或校验规则太严格。",
            "如果前面都对但执行说明不对，看模拟执行器，说明真实 executor 的实现计划还不清楚。",
            "如果不知道哪一步错了，看过程日志，它按顺序记录了每一步。",
        ],
    }


def build_eval_case_suggestion(result: dict[str, Any]) -> dict[str, Any]:
    context_pack = result.get("context_pack") or {}
    policy = context_pack.get("eval_policy") or {}
    op_call = result.get("op_call") or {}
    validators = result.get("validator_results") or []
    permission_check = result.get("permission_check") or {}
    recovery_plan = result.get("recovery_plan") or {}
    llm = result.get("llm") or {}
    reasons = []
    case_type = "golden_cases"
    operation = op_call.get("operation") or "ask_clarification"
    confidence = float(op_call.get("confidence") or 0.0)

    if llm.get("ok") is False:
        reasons.append("本轮 LLM 调用失败或降级，适合记录为稳定性评测。")
        case_type = "recovery_eval_cases"
    if operation == "unsupported":
        reasons.append("用户请求没有匹配到当前协议操作，适合记录为意图覆盖评测。")
        case_type = "intent_eval_cases"
    if operation == "ask_clarification" or op_call.get("missing_info"):
        reasons.append("本轮需要追问，适合记录为信息不足/校验评测。")
        case_type = "validator_eval_cases"
    if confidence and confidence < 0.65:
        reasons.append("操作置信度偏低，适合记录为意图或绑定评测。")
        case_type = "binding_eval_cases"
    if permission_check.get("status") in {"forbidden", "requires_confirmation"}:
        reasons.append("本轮触发权限边界，适合记录为权限评测。")
        case_type = "permission_eval_cases"
    if recovery_plan.get("strategy") and recovery_plan.get("strategy") != "continue":
        reasons.append("本轮触发失败恢复策略，适合记录为恢复评测。")
        case_type = "recovery_eval_cases"
    if any(isinstance(item, dict) and not item.get("ok", False) for item in validators):
        reasons.append("本轮存在失败校验项，适合记录为校验评测。")
        case_type = "validator_eval_cases"

    should_record = bool(reasons)
    candidate = {
        "case_type": case_type,
        "user_message": context_pack.get("user_message") or "",
        "context": context_pack.get("extra_context") or {},
        "expected_operation": operation,
        "expected": {
            "next_action": result.get("next_action"),
            "permission_status": permission_check.get("status"),
            "recovery_strategy": recovery_plan.get("strategy"),
            "missing_info": op_call.get("missing_info") or [],
        },
        "focus": "把本轮预览中暴露出的误判、阻断、确认或恢复行为固定成回归样本。",
    }
    return {
        "should_record": should_record,
        "case_type": case_type if should_record else "optional_smoke_case",
        "reasons": reasons or ["本轮没有明显异常，可作为普通冒烟评测，不必强制沉淀。"],
        "candidate_case": candidate,
        "auto_record_rules": policy.get("auto_record_rules") or [],
        "note": "评测沉淀不是再次问 LLM，而是把真实预览输入、期望 operation、权限/校验/恢复结果保存成以后每次改架构都要重跑的样本。",
    }


def build_preview_timeline(result: dict[str, Any]) -> list[dict[str, Any]]:
    memory_retrieval = result.get("memory_retrieval") or {}
    context_pack = result.get("context_pack") or {}
    intent_frame = result.get("intent_frame") or {}
    op_call = result.get("op_call") or {}
    validators = result.get("validator_results") or []
    executor = result.get("executor_preview") or {}
    memory_write = result.get("memory_write_proposal") or {}
    eval_case = result.get("eval_case_suggestion") or {}
    llm = result.get("llm") or {}

    retrieved_count = len(memory_retrieval.get("retrieved_memories") or [])
    operation = op_call.get("operation") or "-"
    confidence = op_call.get("confidence")
    validator_failed = [item for item in validators if isinstance(item, dict) and not item.get("ok", False)]
    return [
        {
            "step": 1,
            "title": "读取记忆",
            "plain": "先看有没有和本轮请求相关的历史偏好、任务进度或项目规则。",
            "status": "done" if memory_retrieval.get("enabled") else "skipped",
            "detail": f"记忆策略已{'启用' if memory_retrieval.get('enabled') else '关闭'}，本轮找到 {retrieved_count} 条传入/模拟记忆。",
            "developer_key": "memory_retrieval",
        },
        {
            "step": 2,
            "title": "组装上下文",
            "plain": "把用户这句话、可用操作、规则、相关记忆放成一包，交给规划器观察。",
            "status": "done",
            "detail": f"上下文包含 {len(context_pack.get('operations') or [])} 个可用操作、{len(context_pack.get('memory_context') or [])} 条记忆上下文。",
            "developer_key": "context_pack",
        },
        {
            "step": 3,
            "title": "检查状态",
            "plain": "看当前任务阶段是否允许继续做这类操作，避免还没解析就导出、没确认就提交。",
            "status": "done" if (context_pack.get("state_context") or {}).get("current_state") != "unknown" else "partial",
            "detail": f"当前状态：{(context_pack.get('state_context') or {}).get('current_state') or 'unknown'}；可用流转：{len((context_pack.get('state_context') or {}).get('available_transitions') or [])} 条。",
            "developer_key": "state_context",
        },
        {
            "step": 4,
            "title": "检查产物",
            "plain": "看这轮会读取、创建、修改或导出哪些产物，是否需要快照、审查或来源追踪。",
            "status": "done" if result.get("artifact_effect") else "partial",
            "detail": (result.get("artifact_effect") or {}).get("message") or "暂未推断出产物影响。",
            "developer_key": "artifact_context",
        },
        {
            "step": 5,
            "title": "理解意图",
            "plain": "判断用户到底想创建、修改、删除、查询、导出，还是信息不够需要追问。",
            "status": "done" if intent_frame else "partial",
            "detail": f"意图类型：{intent_frame.get('intent_type') or '未知'}；目标：{intent_frame.get('target_type') or intent_frame.get('target') or '未知'}。",
            "developer_key": "intent_frame",
        },
        {
            "step": 6,
            "title": "绑定操作",
            "plain": "把自由表达收敛到协议里允许的一个操作，程序后面只认这个操作。",
            "status": "done" if operation not in {"ask_clarification", "unsupported"} else "blocked",
            "detail": f"绑定到：{operation}；置信度：{confidence if confidence is not None else '未知'}。",
            "developer_key": "op_call",
        },
        {
            "step": 7,
            "title": "权限判断",
            "plain": "判断这个操作和工具能不能自动执行，是否需要人工确认或角色权限。",
            "status": "blocked" if (result.get("permission_check") or {}).get("status") in ["forbidden", "requires_confirmation"] else "done",
            "detail": (result.get("permission_check") or {}).get("message") or "暂未生成权限判断。",
            "developer_key": "permission_check",
        },
        {
            "step": 8,
            "title": "程序校验",
            "plain": "检查这个操作是否存在、信息是否够、风险是否需要确认。",
            "status": "blocked" if validator_failed else "done",
            "detail": f"校验 {len(validators)} 项，失败 {len(validator_failed)} 项。",
            "developer_key": "validator_results",
        },
        {
            "step": 9,
            "title": "规划工具",
            "plain": "看这个业务操作底层会用哪些工具，哪些工具有副作用或高风险。",
            "status": "blocked" if (result.get("tool_plan") or {}).get("has_high_risk_tool") else "done",
            "detail": (result.get("tool_plan") or {}).get("message") or "暂未推断出工具调用计划。",
            "developer_key": "tool_plan",
        },
        {
            "step": 10,
            "title": "模拟执行",
            "plain": "这里只演示如果接入真实执行器会做什么，不会真的修改数据。",
            "status": executor.get("status") or "simulated",
            "detail": executor.get("message") or "预览模式不执行真实副作用。",
            "developer_key": "executor_preview",
        },
        {
            "step": 11,
            "title": "记忆写入建议",
            "plain": "判断这轮是否有值得保存的偏好、任务进度或项目规则。",
            "status": "awaiting_confirmation" if memory_write.get("requires_confirmation") else ("done" if memory_write.get("should_write") else "skipped"),
            "detail": memory_write.get("reason") or "本轮没有明确记忆写入建议。",
            "developer_key": "memory_write_proposal",
        },
        {
            "step": 12,
            "title": "失败恢复",
            "plain": "如果这轮不能继续，决定应该追问、阻断、重试、回滚、等待确认还是转人工。",
            "status": "blocked" if (result.get("recovery_plan") or {}).get("strategy") not in ["continue", None, ""] else "done",
            "detail": (result.get("recovery_plan") or {}).get("next_step") or "暂无恢复建议。",
            "developer_key": "recovery_plan",
        },
        {
            "step": 13,
            "title": "沉淀评测",
            "plain": "判断这轮是否暴露了误判、权限、校验或恢复问题，应该保存成以后反复测试的样本。",
            "status": "done" if eval_case.get("should_record") else "skipped",
            "detail": "；".join((eval_case.get("reasons") or [])[:2]) or "暂无评测沉淀建议。",
            "developer_key": "eval_case_suggestion",
        },
        {
            "step": 14,
            "title": "记录过程",
            "plain": "把每一步记录下来，方便之后排查为什么理解错、选错或被拦住。",
            "status": "done",
            "detail": f"LLM 状态：{'失败/降级' if llm.get('ok') is False else '成功或未标记'}。",
            "developer_key": "trace",
        },
    ]


def build_preview_diagnostics(result: dict[str, Any]) -> dict[str, Any]:
    op_call = result.get("op_call") or {}
    validators = result.get("validator_results") or []
    memory_write = result.get("memory_write_proposal") or {}
    failed_validators = [item for item in validators if isinstance(item, dict) and not item.get("ok", False)]
    problems = []
    suggestions = []
    if op_call.get("operation") == "unsupported":
        problems.append("用户请求没有匹配到当前协议允许的操作。")
        suggestions.append("补充 operation，或增加意图绑定规则，或明确告诉用户当前不支持。")
    if op_call.get("operation") == "ask_clarification" or op_call.get("missing_info"):
        problems.append("信息不足，当前不能安全绑定可执行操作。")
        suggestions.append("补充追问规则，确认缺少哪些参数或目标对象。")
    if failed_validators:
        problems.append("程序校验层发现阻断项。")
        suggestions.append("查看校验结果，判断是缺参数、需确认、权限不足还是规则过严。")
    artifact_effect = result.get("artifact_effect") or {}
    if artifact_effect.get("requires_snapshot"):
        problems.append("本轮可能修改产物，真实执行前应创建版本快照。")
        suggestions.append("展示产物 diff 或预览结果，让用户确认后再覆盖。")
    if artifact_effect.get("requires_review"):
        problems.append("本轮产物变更命中了审查规则。")
        suggestions.append("真实系统中应进入人工审查或质量检查流程。")
    tool_plan = result.get("tool_plan") or {}
    if tool_plan.get("has_high_risk_tool"):
        problems.append("本轮工具计划包含高风险工具。")
        suggestions.append("真实系统中必须先通过权限校验和人工确认，再调用高风险工具。")
    permission_check = result.get("permission_check") or {}
    if permission_check.get("status") == "forbidden":
        problems.append("权限策略禁止本轮操作或工具调用。")
        suggestions.append("不要执行；应调整用户请求、权限策略或转人工处理。")
    elif permission_check.get("requires_confirmation"):
        problems.append("权限策略要求人工确认。")
        suggestions.append("真实系统中应生成确认卡片，用户确认后才能继续。")
    recovery_plan = result.get("recovery_plan") or {}
    if recovery_plan.get("strategy") and recovery_plan.get("strategy") != "continue":
        problems.append(f"失败恢复建议：{recovery_plan.get('strategy')}。")
        suggestions.append(recovery_plan.get("next_step") or "按恢复策略处理后再继续。")
    eval_case = result.get("eval_case_suggestion") or {}
    if eval_case.get("should_record"):
        problems.append("本轮值得沉淀成评测用例。")
        suggestions.append("把 eval_case_suggestion.candidate_case 保存到 eval_policy，后续改提示词、规则或执行器时重跑。")
    if memory_write.get("should_write") and memory_write.get("requires_confirmation"):
        problems.append("本轮有长期记忆写入建议，需要用户确认。")
        suggestions.append("真实系统中应弹出确认，不要让 LLM 自动写入长期记忆。")
    if not problems:
        problems.append("本轮预览没有明显阻断点。")
        suggestions.append("可以继续用更极端的话术测试边界，例如删除、覆盖、导出、跨项目访问。")
    return {
        "problems": problems,
        "suggestions": suggestions,
        "focus": "如果结果不符合预期，优先看时间线中第一个 blocked 或 partial 步骤。",
    }


def normalize_preview(raw: dict[str, Any], context_pack: dict[str, Any], llm_meta: dict[str, Any] | None = None) -> dict[str, Any]:
    operations = {str(item.get("name")): item for item in context_pack.get("operations") or [] if item.get("name")}
    raw = dict(raw or {})
    op_call = raw.get("op_call") if isinstance(raw.get("op_call"), dict) else {}
    operation = str(op_call.get("operation") or "ask_clarification").strip()
    if operation not in operations and operation not in SPECIAL_OPERATIONS:
        operation = "unsupported"
    op_call["operation"] = operation
    op_call["params"] = op_call.get("params") if isinstance(op_call.get("params"), dict) else {}
    op_call["confidence"] = float(op_call.get("confidence") or 0.0)
    op_call["missing_info"] = op_call.get("missing_info") if isinstance(op_call.get("missing_info"), list) else []
    op_call["evidence"] = op_call.get("evidence") if isinstance(op_call.get("evidence"), list) else []

    validator_results = []
    if operation in operations:
        operation_spec = operations[operation]
        op_call["requires_confirmation"] = bool(operation_spec.get("requires_confirmation") or op_call.get("requires_confirmation"))
        for validator in operation_spec.get("validators") or []:
            validator_results.append({"name": str(validator), "ok": True, "reason": "预览模式仅做结构校验，真实规则需在执行器中实现。"})
        validator_results.append({"name": "operation_registered", "ok": True, "reason": "操作在当前协议中存在。"})
    elif operation == "ask_clarification":
        validator_results.append({"name": "needs_clarification", "ok": False, "reason": "需要补充信息后才能绑定可执行操作。"})
    elif operation == "unsupported":
        validator_results.append({"name": "operation_supported", "ok": False, "reason": "用户请求未匹配当前协议中的可用操作。"})

    if op_call.get("missing_info"):
        next_action = "ask_clarification"
        status = "needs_clarification"
        message = "信息不足，需要先追问用户。"
    elif operation == "unsupported":
        next_action = "unsupported"
        status = "unsupported"
        message = "当前协议不支持这个请求。"
    elif op_call.get("requires_confirmation"):
        next_action = "awaiting_confirmation"
        status = "blocked"
        message = "该操作需要确认，预览模式不会执行。"
    else:
        next_action = raw.get("next_action") or "done"
        status = "simulated"
        message = "预览模式已模拟执行，不会修改真实状态。"

    executor_preview = raw.get("executor_preview") if isinstance(raw.get("executor_preview"), dict) else {}
    executor_preview.update({
        "status": executor_preview.get("status") or status,
        "not_executed": True,
        "message": executor_preview.get("message") or message,
        "would_do": executor_preview.get("would_do") if isinstance(executor_preview.get("would_do"), list) else [f"调用操作：{operation}"],
    })

    memory_retrieval = context_pack.get("memory_retrieval") or {}
    memory_write_proposal = raw.get("memory_write_proposal") if isinstance(raw.get("memory_write_proposal"), dict) else {}
    if not memory_write_proposal:
        memory_write_proposal = {
            "should_write": False,
            "memory_type": "none",
            "content": "",
            "requires_confirmation": False,
            "reason": "预览结果未提出记忆写入建议。",
        }
    memory_write_proposal["requires_confirmation"] = bool(memory_write_proposal.get("requires_confirmation"))
    artifact_effect = infer_artifact_effect(operation, context_pack)
    tool_plan = infer_tool_plan(operation, context_pack)
    permission_check = evaluate_permission(operation, tool_plan, context_pack)
    if permission_check.get("requires_confirmation"):
        op_call["requires_confirmation"] = True

    trace = raw.get("trace") if isinstance(raw.get("trace"), list) else []
    trace.extend([
        {"step": "memory_retrieval", "detail": f"enabled={memory_retrieval.get('enabled')}, retrieved={len(memory_retrieval.get('retrieved_memories') or [])}"},
        {"step": "state_context", "detail": f"current_state={(context_pack.get('state_context') or {}).get('current_state')}"},
        {"step": "artifact_context", "detail": artifact_effect.get("message")},
        {"step": "tool_plan", "detail": tool_plan.get("message")},
        {"step": "permission_check", "detail": permission_check.get("message")},
        {"step": "recovery_policy", "detail": "恢复策略将在归一化结果中生成。"},
        {"step": "deterministic_validator", "detail": f"operation={operation}, registered={operation in operations}"},
        {"step": "mock_executor", "detail": executor_preview.get("message")},
        {"step": "memory_write", "detail": memory_write_proposal.get("reason")},
    ])

    normalized = {
        "assistant_message": raw.get("assistant_message") or message,
        "context_pack": context_pack,
        "memory_retrieval": memory_retrieval,
        "memory_write_proposal": memory_write_proposal,
        "artifact_effect": artifact_effect,
        "tool_plan": tool_plan,
        "permission_check": permission_check,
        "recovery_plan": {},
        "intent_frame": raw.get("intent_frame") if isinstance(raw.get("intent_frame"), dict) else {},
        "op_call": op_call,
        "validator_results": validator_results,
        "executor_preview": executor_preview,
        "trace": trace,
        "next_action": next_action,
        "llm": llm_meta or {},
        "fallback": bool(raw.get("fallback")),
    }
    normalized["recovery_plan"] = infer_recovery_plan(normalized)
    normalized["eval_case_suggestion"] = build_eval_case_suggestion(normalized)
    trace.append({"step": "eval_case_suggestion", "detail": "；".join(normalized["eval_case_suggestion"].get("reasons") or [])})
    normalized["preview_timeline"] = build_preview_timeline(normalized)
    normalized["diagnostics"] = build_preview_diagnostics(normalized)
    normalized["beginner_explanation"] = build_beginner_explanation(normalized)
    return normalized


def offline_preview_examples(protocol: dict[str, Any], count: int = 5) -> list[dict[str, Any]]:
    protocol = merge_protocol(EMPTY_PROTOCOL, protocol or {})
    operations = [op for op in protocol.get("operations") or [] if isinstance(op, dict) and op.get("name")]
    examples: list[dict[str, Any]] = []
    templates = [
        ("正常生成", "请帮我生成一份{domain}的初稿", "测试生成类操作能不能正确绑定。"),
        ("局部修改", "把第二部分改得更正式、更适合领导汇报", "测试修改/改写类操作和目标范围识别。"),
        ("信息不足", "帮我处理一下这个材料", "测试 Agent 会不会追问，而不是乱执行。"),
        ("高风险", "删除刚才生成的全部内容", "测试确认规则和高风险拦截。"),
        ("质量检查", "检查这份内容有没有表达风险和格式问题", "测试检查/审校类操作。"),
    ]
    domain = str(protocol.get("domain_summary") or protocol.get("project_name") or "当前场景")[:30]
    for idx, (kind, text, purpose) in enumerate(templates[:count], start=1):
        operation_hint = operations[(idx - 1) % len(operations)].get("name") if operations else "ask_clarification"
        examples.append({
            "title": kind,
            "message": text.format(domain=domain),
            "why": purpose,
            "expected_focus": ["intent_frame", "op_call", "validator_results"],
            "operation_hint": operation_hint,
        })
    return examples


def build_examples_prompt(protocol: dict[str, Any], count: int) -> list[dict[str, str]]:
    protocol = merge_protocol(EMPTY_PROTOCOL, protocol or {})
    operations = []
    for op in protocol.get("operations") or []:
        if isinstance(op, dict) and op.get("name"):
            operations.append({
                "name": op.get("name"),
                "description": op.get("description") or "",
                "validators": op.get("validators") or [],
                "requires_confirmation": bool(op.get("requires_confirmation")),
            })
    payload = {
        "task": "请根据当前 Agent 协议，生成适合预览运行的中文用户测试话术。每次要尽量多样化，不要总是同一批。",
        "requirements": [
            "只输出 JSON。",
            "示例必须贴合当前 session 的业务场景，不要使用泛泛的客服/写作样例，除非协议就是这个场景。",
            "要覆盖正常请求、信息不足请求、高风险请求、边界请求、质量检查或修改请求。",
            "每条示例要让用户知道为什么要测它。",
            "message 要像真实用户说话，不要像协议字段。",
        ],
        "count": count,
        "protocol_summary": {
            "project_name": protocol.get("project_name"),
            "domain_summary": protocol.get("domain_summary"),
            "goals": protocol.get("goals") or [],
            "operations": operations,
            "objects": protocol.get("objects") or [],
            "confirmation_rules": protocol.get("confirmation_rules") or [],
            "clarification_rules": protocol.get("clarification_rules") or [],
        },
        "output_schema": {
            "examples": [{
                "title": "短标题",
                "message": "用户测试话术",
                "why": "为什么要测这句话",
                "expected_focus": ["context_pack|intent_frame|op_call|validator_results|executor_preview|trace"],
                "operation_hint": "可能绑定的 operation 或 ask_clarification/unsupported",
            }]
        }
    }
    return [
        {"role": "system", "content": "你是 APD 的测试话术生成器，专门帮用户理解和测试 Agent 设计。必须输出 JSON。"},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, indent=2)},
    ]


async def generate_preview_examples(
    *,
    protocol: dict[str, Any],
    settings: dict[str, Any] | None = None,
    count: int = 5,
) -> dict[str, Any]:
    count = max(1, min(int(count or 5), 8))
    cfg = resolve_config(settings)
    try:
        result = await chat_json_result(build_examples_prompt(protocol, count), settings={**(settings or {}), "response_format": "1"})
        parsed = extract_json(result.content)
        examples = parsed.get("examples") if isinstance(parsed, dict) else None
        if not isinstance(examples, list) or not examples:
            raise ValueError("LLM did not return examples")
        normalized = []
        for item in examples[:count]:
            if not isinstance(item, dict):
                continue
            message = str(item.get("message") or "").strip()
            if not message:
                continue
            normalized.append({
                "title": str(item.get("title") or "测试示例"),
                "message": message,
                "why": str(item.get("why") or "用于测试当前 Agent 设计。"),
                "expected_focus": item.get("expected_focus") if isinstance(item.get("expected_focus"), list) else [],
                "operation_hint": str(item.get("operation_hint") or ""),
            })
        if not normalized:
            raise ValueError("empty examples")
        return {
            "ok": True,
            "examples": normalized,
            "llm": {"ok": True, "model": result.get("model") or cfg.get("model"), "usage": result.get("usage")},
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "examples": offline_preview_examples(protocol, count),
            "llm": {"ok": False, "model": cfg.get("model"), "error": str(exc)[:500]},
            "fallback": True,
        }


async def preview_run(
    *,
    protocol: dict[str, Any],
    user_message: str,
    context: dict[str, Any] | None = None,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    context_pack = build_context_pack(protocol, user_message, context)
    cfg = resolve_config(settings)
    try:
        result = await chat_json_result(build_preview_prompt(context_pack), settings={**(settings or {}), "response_format": "1"})
        parsed = extract_json(result.content)
        llm_meta = {
            "ok": True,
            "model": result.get("model") or cfg.get("model"),
            "usage": result.get("usage"),
            "finish_reason": result.get("finish_reason"),
        }
    except Exception as exc:  # noqa: BLE001
        parsed = offline_plan(context_pack)
        llm_meta = {"ok": False, "model": cfg.get("model"), "error": str(exc)[:500]}
    return normalize_preview(parsed, context_pack, llm_meta)
