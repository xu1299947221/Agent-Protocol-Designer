from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _items(value: Any) -> list[dict[str, Any]]:
    return [item for item in value or [] if isinstance(item, dict)]


def _safe_id(value: str, fallback: str) -> str:
    safe = "".join(ch.lower() if ch.isalnum() else "_" for ch in str(value or "")).strip("_")
    while "__" in safe:
        safe = safe.replace("__", "_")
    return safe or fallback


def _text_blob(protocol: dict[str, Any], user_message: str = "") -> str:
    parts = [
        str(user_message or ""),
        str(protocol.get("project_name") or ""),
        str(protocol.get("domain_summary") or ""),
    ]
    for op in _items(protocol.get("operations")):
        parts.append(str(op.get("name") or ""))
        parts.append(str(op.get("description") or ""))
    workflow = protocol.get("workflow") or {}
    for node in _items(workflow.get("nodes")):
        parts.append(str(node.get("name") or ""))
        parts.append(str(node.get("type") or ""))
    return "\n".join(parts).lower()


def infer_agent_roles(protocol: dict[str, Any], user_message: str = "") -> list[dict[str, Any]]:
    text = _text_blob(protocol, user_message)
    roles: list[dict[str, Any]] = [
        {
            "id": "coordinator",
            "name": "协调者 Agent",
            "responsibility": "理解总目标、拆分子任务、决定交接顺序，并在冲突时给出仲裁建议。",
            "inputs": ["用户目标", "上下文包", "workflow 计划", "其他 Agent 的观察结果"],
            "outputs": ["协作计划", "任务分配", "冲突处理建议", "最终汇总"],
            "allowed_actions": ["assign_task", "request_handoff", "merge_result", "request_human_review"],
            "risk_level": "medium",
        },
        {
            "id": "executor",
            "name": "执行者 Agent",
            "responsibility": "调用受控工具或执行器完成具体操作，不能绕过 Validator 和权限策略。",
            "inputs": ["OpCall", "工具注册表", "权限策略", "校验结果"],
            "outputs": ["执行结果", "产物草稿", "执行观察"],
            "allowed_actions": ["call_tool", "create_artifact", "update_state", "report_observation"],
            "risk_level": "high",
        },
        {
            "id": "reviewer",
            "name": "审校者 Agent",
            "responsibility": "检查结果是否满足协议、证据、格式、风险和质量要求。",
            "inputs": ["执行结果", "产物版本", "Validator 结果", "评测用例"],
            "outputs": ["质量检查", "修改建议", "是否需要人工确认"],
            "allowed_actions": ["validate_result", "suggest_fix", "request_human_review"],
            "risk_level": "medium",
        },
    ]
    if any(word in text for word in ("rag", "知识", "检索", "素材", "graph", "图谱", "引用", "参考文献")):
        roles.insert(1, {
            "id": "researcher",
            "name": "研究检索 Agent",
            "responsibility": "从知识库、RAG 或图谱中检索证据，输出带来源的候选信息。",
            "inputs": ["检索问题", "知识策略", "证据要求"],
            "outputs": ["证据包", "候选素材", "引用来源", "置信度"],
            "allowed_actions": ["retrieve", "rank_evidence", "bind_source"],
            "risk_level": "medium",
        })
    if any(word in text for word in ("文档", "写作", "报告", "投标", "docx", "outline", "目录", "生成", "编辑")):
        roles.insert(-1, {
            "id": "writer",
            "name": "内容生成 Agent",
            "responsibility": "根据证据、模板、目录或用户要求生成内容草稿，并说明生成依据。",
            "inputs": ["写作目标", "证据包", "模板约束", "当前产物"],
            "outputs": ["内容草稿", "修改说明", "待确认项"],
            "allowed_actions": ["draft_content", "rewrite_section", "summarize", "propose_artifact_update"],
            "risk_level": "medium",
        })
    if any(word in text for word in ("高风险", "审批", "人工", "确认", "删除", "回滚", "发布", "提交")):
        roles.append({
            "id": "human_guard",
            "name": "人工守门人",
            "responsibility": "对高风险操作、不可逆写入、发布和删除类动作做最终确认。",
            "inputs": ["风险说明", "执行预览", "回滚方案"],
            "outputs": ["批准", "拒绝", "补充要求"],
            "allowed_actions": ["approve", "reject", "ask_for_change"],
            "risk_level": "high",
        })
    return roles


def build_message_protocol(roles: list[dict[str, Any]]) -> dict[str, Any]:
    role_ids = [role["id"] for role in roles]
    return {
        "envelope_fields": [
            {"name": "message_id", "meaning": "消息唯一编号，方便 Trace 追踪。"},
            {"name": "from_agent", "meaning": "发送方 Agent。"},
            {"name": "to_agent", "meaning": "接收方 Agent，必须在角色列表内。"},
            {"name": "message_type", "meaning": "task、handoff、observation、conflict、review、final 之一。"},
            {"name": "payload", "meaning": "结构化任务内容或观察结果。"},
            {"name": "evidence", "meaning": "支撑判断的证据、来源、Trace 或产物版本。"},
            {"name": "requires_ack", "meaning": "是否要求接收方确认收到。"},
        ],
        "allowed_message_types": ["task", "handoff", "observation", "conflict", "review", "final"],
        "allowed_agents": role_ids,
        "validation_rules": [
            "from_agent 和 to_agent 必须是已注册角色。",
            "message_type 必须属于允许类型。",
            "涉及状态写入、产物覆盖、删除、发布时必须携带 evidence 和 risk_level。",
            "handoff 消息必须携带 task_id、done、remaining、artifacts、next_owner。",
            "conflict 消息必须携带 conflict_type、options、recommended_resolution。",
        ],
    }


def build_handoff_contract(roles: list[dict[str, Any]], protocol: dict[str, Any]) -> dict[str, Any]:
    workflow = protocol.get("workflow") or {}
    nodes = _items(workflow.get("nodes"))
    handoffs = []
    if nodes:
        for index, node in enumerate(nodes):
            source = "coordinator" if index == 0 else _agent_for_node(nodes[index - 1])
            target = _agent_for_node(node)
            handoffs.append({
                "from_agent": source,
                "to_agent": target,
                "reason": f"进入 workflow 节点：{node.get('name') or node.get('id') or index + 1}",
                "required_payload": ["task_id", "goal", "constraints", "done", "remaining", "artifacts", "trace_refs"],
                "acceptance_check": "接收方必须确认输入足够；不够则返回 observation 请求补充。",
            })
    else:
        for source, target, reason in [
            ("coordinator", "executor", "把用户目标转成可执行 OpCall。"),
            ("executor", "reviewer", "执行后进入质量与风险检查。"),
            ("reviewer", "coordinator", "审校结果回到协调者汇总。"),
        ]:
            if source in {role["id"] for role in roles} and target in {role["id"] for role in roles}:
                handoffs.append({
                    "from_agent": source,
                    "to_agent": target,
                    "reason": reason,
                    "required_payload": ["task_id", "goal", "done", "remaining", "artifacts", "trace_refs"],
                    "acceptance_check": "接收方必须确认输入足够；不够则返回 observation 请求补充。",
                })
    return {
        "handoffs": handoffs,
        "handoff_invariants": [
            "每次交接必须说明已完成什么、还剩什么、产物在哪里、风险是什么。",
            "交接不能只传自然语言，必须传结构化 payload。",
            "接收方不能默认信任上游结果，至少要做输入完整性校验。",
        ],
    }


def _agent_for_node(node: dict[str, Any]) -> str:
    text = " ".join(str(node.get(key) or "") for key in ("id", "type", "name", "operation")).lower()
    if any(word in text for word in ("rag", "retrieve", "search", "检索", "知识", "图谱")):
        return "researcher"
    if any(word in text for word in ("write", "draft", "generate", "outline", "文档", "目录", "生成", "写")):
        return "writer"
    if any(word in text for word in ("review", "validate", "check", "确认", "审", "校验")):
        return "reviewer"
    if any(word in text for word in ("human", "人工", "审批")):
        return "human_guard"
    return "executor"


def build_conflict_policy(roles: list[dict[str, Any]]) -> dict[str, Any]:
    has_human = any(role["id"] == "human_guard" for role in roles)
    return {
        "conflict_types": [
            {"type": "intent_conflict", "meaning": "多个 Agent 对用户意图理解不一致。", "resolution": "回到 coordinator，要求补充 Intent Frame 和证据。"},
            {"type": "evidence_conflict", "meaning": "检索证据互相矛盾或来源不足。", "resolution": "由 researcher 标注来源和置信度，reviewer 决定是否可用。"},
            {"type": "artifact_conflict", "meaning": "多个 Agent 想修改同一个产物版本。", "resolution": "先生成新版本或分支，禁止直接覆盖活动版本。"},
            {"type": "permission_conflict", "meaning": "某个操作风险高或权限不足。", "resolution": "暂停执行，进入人工确认或拒绝执行。"},
        ],
        "arbiter": "human_guard" if has_human else "coordinator",
        "programmatic_guards": [
            "同一产物同一版本不能被两个 Agent 同时写入。",
            "高风险 OpCall 不能由 Agent 自己批准。",
            "缺少证据的结论只能作为草稿，不能进入最终结果。",
            "冲突未解决前，Executor 只能 dry-run。",
        ],
        "fallback": "如果程序规则无法判断，转人工确认；不要让多个 Agent 自行投票后直接执行高风险动作。",
    }


def build_collaboration_trace(roles: list[dict[str, Any]], handoff_contract: dict[str, Any], user_message: str = "") -> list[dict[str, Any]]:
    trace: list[dict[str, Any]] = []
    trace.append({
        "step": 1,
        "event": "collaboration_started",
        "agent": "coordinator",
        "detail": "协调者读取用户目标和上下文包，判断是否需要多 Agent 协作。",
        "input": user_message,
        "at": _now(),
    })
    for index, handoff in enumerate(handoff_contract.get("handoffs") or [], start=2):
        trace.append({
            "step": index,
            "event": "handoff",
            "from_agent": handoff.get("from_agent"),
            "to_agent": handoff.get("to_agent"),
            "detail": handoff.get("reason"),
            "required_payload": handoff.get("required_payload") or [],
            "status": "simulated",
            "at": _now(),
        })
    trace.append({
        "step": len(trace) + 1,
        "event": "collaboration_review",
        "agent": "reviewer" if any(role["id"] == "reviewer" for role in roles) else "coordinator",
        "detail": "审校协作结果、冲突、证据和产物版本，再决定是否输出最终结果或请求人工确认。",
        "status": "simulated",
        "at": _now(),
    })
    return trace


def analyze_multi_agent_collaboration(protocol: dict[str, Any], user_message: str = "") -> dict[str, Any]:
    protocol = protocol or {}
    roles = infer_agent_roles(protocol, user_message)
    message_protocol = build_message_protocol(roles)
    handoff_contract = build_handoff_contract(roles, protocol)
    conflict_policy = build_conflict_policy(roles)
    trace = build_collaboration_trace(roles, handoff_contract, user_message)
    role_count = len(roles)
    handoff_count = len(handoff_contract.get("handoffs") or [])
    readiness_score = min(100, 35 + role_count * 8 + min(handoff_count, 8) * 5)
    gaps = []
    if not ((protocol.get("workflow") or {}).get("nodes") or []):
        gaps.append("当前协议还没有 workflow 节点，多 Agent 只能给出通用协作草案。")
    if not (protocol.get("tool_registry") or {}).get("tools"):
        gaps.append("缺少工具注册表，执行者 Agent 还不能绑定真实工具。")
    if not protocol.get("permission_policy"):
        gaps.append("缺少权限策略，高风险交接只能建议人工确认。")
    if not protocol.get("artifact_model"):
        gaps.append("缺少产物模型，多 Agent 修改同一文档时难以做版本隔离。")
    if not gaps:
        gaps.append("基础协作结构完整，可以继续把角色绑定到真实 Runtime 节点。")
    return {
        "summary": {
            "role_count": role_count,
            "handoff_count": handoff_count,
            "readiness_score": readiness_score,
            "recommended_mode": "multi_agent_coordination" if role_count >= 4 or handoff_count >= 3 else "single_agent_with_review",
        },
        "beginner_explanation": {
            "一句话": "Multi-Agent 协作就是把一个复杂 Agent 拆成多个有职责边界的小 Agent，再用消息、交接和冲突规则把它们管起来。",
            "为什么需要角色": "角色不是为了热闹，而是防止一个 LLM 同时负责规划、执行、审校和批准，导致责任混乱。",
            "为什么需要交接": "交接让上一个 Agent 必须说清楚完成了什么、证据是什么、下一个 Agent 该接着做什么。",
            "为什么需要冲突策略": "多个 Agent 判断不一致时，不能谁声音大听谁，必须有程序规则、审校或人工确认。",
        },
        "roles": roles,
        "message_protocol": message_protocol,
        "handoff_contract": handoff_contract,
        "conflict_policy": conflict_policy,
        "collaboration_trace": trace,
        "gaps": gaps,
        "next_action": "先把这些角色映射到 workflow 节点；真实开发时再把每个 Agent 的输入输出、工具权限和评测用例固定下来。",
    }
