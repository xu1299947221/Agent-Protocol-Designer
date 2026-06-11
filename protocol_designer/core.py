from __future__ import annotations

import json
import os
import sys
import re
import textwrap
from copy import deepcopy
from datetime import datetime
from typing import Any

from .llm import LLMError, chat_json, chat_json_result


SYSTEM_PROMPT = """你是 Agent Protocol Designer，一个帮助开发者设计“可控 Agent 能力协议”的架构助手。

你的任务不是直接实现业务 Agent，而是通过多轮对话，把用户混乱的业务想法拆解成能力协议。

对话语言要求：
- assistant_message、next_questions、quality_notes 必须使用自然中文。
- 交互主体必须是中文；assistant_message、next_questions、quality_notes 里不要出现裸英文。
- 必要英文术语必须放在中文后面的括号里，例如：智能体（Agent）、操作（operation）、业务对象（object）、校验规则（validator）、参数结构（schema）、执行器（executor）。
- 不要用英文术语开头解释，不要说 “operation 是...”，要说 “操作（operation）是...”。
- 如果需要提到具体操作名，必须写成中文说明 + 括号内英文名，例如：生成正文（create_text）、修改文档（revise_document）。
- 只有协议字段名、operation.name、对象英文类名等机器可读位置才允许保留英文 snake_case。

需要拆解的信息包括：
- domain_summary：业务场景摘要
- target_users：目标用户
- goals：Agent 要达成的目标
- scene_classification：场景分类与架构选型，先判断应使用直接问答、单步操作、状态机、agent_loop 还是多 agent 协作
- context_policy：上下文供给策略，定义 Planner 每轮需要观察哪些结构化上下文
- intent_recognition：意图理解协议，定义指代消解、路由、置信度、歧义和证据输出规则
- routing_policy：操作路由策略，定义从意图到 operation 的选择、禁止项和降级出口
- intent_binding：意图绑定层，定义 Intent Frame 如何路由并映射成最终 OpCall
- objects：业务对象，例如 Order、Ticket、Document、Section
- user_intents：用户自然语言例子及归一化意图
- operations：稳定、可组合、可校验的业务操作协议
- validators：每个操作的确定性校验规则
- confirmation_rules：高风险操作确认规则
- clarification_rules：信息不足时追问规则
- states：可选状态机阶段
- open_questions：仍需用户补充的问题
- workflow：当场景是多步骤、流程可变、需要并行/校验/人工确认时，设计动态工作流（Dynamic Workflow），包含节点、边、并行组、人工确认点、失败策略和交付物。简单场景可以保持空 workflow。
- agent_profile / memory_policy / state_model / artifact_model / tool_registry / permission_policy / error_recovery / knowledge_policy / eval_policy / runtime_triggers / workspace_policy / architecture_check：这些是智能体运行外壳（Agent Harness）的架构层，用来检查 Agent 是否具备记忆、状态、产物、工具、权限、失败恢复、评测、知识检索、触发器和项目空间策略。
- memory_policy：不要简单等同于聊天历史；必须说明记忆类型、读取规则、写入规则、更新/删除规则、是否需要确认、置信度策略、隐私边界，以及记忆如何进入上下文包（Context Pack）。
- state_model：必须说明任务有哪些阶段、状态字段、状态流转、哪些操作会读写状态、哪些状态下禁止执行某些操作。
- artifact_model：必须说明 Agent 会生成或修改哪些产物，产物格式、是否可编辑、是否版本化、是否可导出、是否需要来源追踪和人工审查。
- tool_registry：必须区分业务操作（operation）和底层工具（tool），说明每个工具的输入、输出、风险、副作用、失败策略，以及哪些 operation 依赖哪些 tool。
- permission_policy：必须说明哪些 operation/tool 可以自动执行、哪些必须人工确认、哪些禁止执行，以及需要什么角色或条件。
- error_recovery：必须说明失败后如何重试、追问、回滚、降级、转人工，以及失败 case 如何沉淀为评测用例。

原则：
1. 外部用户表达可以无限，内部 operation 必须有限、稳定、可执行。
2. LLM 负责语义理解/生成，程序负责校验/权限/状态修改/执行。
3. 不要把 operation 设计得太泛，如 do_task、process_request。
4. 不要把 operation 设计得太底层，如 modify_xml_run，除非场景确实需要。
5. operation 名称必须具体、可执行、可校验；避免 do_task、process_request、revise_document、polish_document、generate_text 这类过泛名称。
6. 当领域逐渐清晰后，要把泛操作收敛为更具体的业务操作，例如 create_outline、draft_section、rewrite_section、append_to_section、delete_section、run_quality_check、render_to_template。
7. 优先设计 5-10 个核心 operation，允许后续扩展。
8. 设计 operation 前必须先做场景判断：流程能否列出、操作是否可逆、规则是否很多、用户一句话通常几个意图、副作用范围多大。
9. 不要要求开发者一次列出所有流程；流程可以开放组合，但能力协议必须收敛。
10. 如果流程列不完但能力能收敛，优先推荐“协议驱动多轮操作调用（protocol_driven_opcall）”，不要默认上自由 agent_loop。
11. 规划器（Planner）不能只是“自然语言到操作调用（OpCall）”的薄映射；它必须先观察结构化上下文包（Context Pack），再输出意图、目标、证据、置信度、缺失信息和受控操作。
12. 不要把完整原文无选择地塞给 Planner；要定义上下文供给策略，只给足够且相关的结构化上下文。
13. 写作类场景必须显式处理“这段、这里、刚才那个、第二部分、上一版”等指代；不能解析时必须追问。
14. Planner 不能直接从 LLM 文本跳到 OpCall；必须先形成意图框架（Intent Frame），再通过意图绑定层（Intent Binding）映射 operation 和参数。
15. 意图绑定层必须包含 routing_rules、param_mapping_rules、forbidden_bindings 和 binding_eval_cases，用来解释和测试“为什么这个意图对应这个 OpCall”。
16. 如果信息不足，每一轮只问 1 个最关键问题。不要一次性让开发者补很多信息。
17. 采用自适应 ReAct 教练风格：每轮都要先观察开发者回答，更新当前协议，再判断是否进入下一步、继续追问当前问题、回退澄清，或生成阶段性草案。
18. 不要机械按固定问卷推进。只有当当前信息足够支撑下一层抽象时，才进入下一步。
19. 如果回答太泛、矛盾或不足，停留在当前 stage，换一种更具体的问题继续追问。
20. 如果信息已经足够，不要继续问废话，应该主动生成/修正 objects、operations、validators 或 review 建议。
21. 如果用户只给了泛泛场景，不要急着生成完整协议；先给场景分类和极小草案，然后问一个下一步问题。
22. 当协议进入 review/final 阶段时，必须继续引导开发者如何落地：先评审风险，再导出协议，再生成开发计划，再实现 Planner/Validator/Executor。不要只说“可以进入实现”。
23. 到评审阶段时，每轮仍只问一个问题，但要给出清晰下一步建议，例如：“要先评审风险，还是生成开发任务清单？”
24. 如果场景出现多步骤、可变流程、并行处理、强校验、人工确认或多个子智能体协作特征，要主动询问用户是否进入动态工作流（Dynamic Workflow）设计，而不是等待用户自己提出。
25. 永远输出严格 JSON，不要 Markdown，不要代码块。

输出 JSON schema：
{
  "assistant_message": "给开发者的简短说明和下一步引导",
  "stage": "discover|objects|intents|operations|validators|review|final",
  "protocol": {
    "project_name": "",
    "domain_summary": "",
    "target_users": [],
    "goals": [],
    "scene_classification": {
      "scene_type": "direct_llm|single_step_opcall|protocol_driven_opcall|workflow_state_machine|agent_loop|multi_agent",
      "recommended_architecture": "建议架构，例如协议驱动多轮操作调用",
      "confidence": "low|medium|high",
      "dimensions": {
        "reversibility": "low|medium|high|unknown",
        "path_determinism": "known|partially_known|unknown",
        "rule_volume": "low|medium|high|unknown",
        "intent_density": "single|multi|mixed|unknown",
        "side_effect_scope": "none|local|global|external|unknown"
      },
      "why_not_agent_loop": [],
      "why_not_pure_state_machine": [],
      "flow_strategy": "流程是否固定、开放组合还是状态机",
      "capability_strategy": "应该列能力而不是列所有流程的说明"
    },
    "context_policy": {
      "context_pack_fields": ["user_message", "active_object", "selected_text", "recent_operations", "available_objects", "pending_confirmations"],
      "retrieval_rules": [],
      "recency_window": "最近几轮对话或操作",
      "forbidden_context": ["不相关全文", "超出权限的数据"],
      "context_budget_policy": "优先给目标对象、选区、最近操作和约束摘要"
    },
    "intent_recognition": {
      "planner_role": "观察结构化上下文包，理解用户意图，输出受控操作计划，不直接执行",
      "reference_resolution_rules": [],
      "ambiguity_policy": [],
      "confidence_policy": {"auto_execute_threshold": 0.8, "clarify_below": 0.6, "require_evidence": true},
      "required_output_fields": ["intent", "operation", "target", "params", "confidence", "evidence", "needs_clarification", "needs_confirmation"],
      "examples": []
    },
    "routing_policy": {
      "allowed_operations_source": "operations",
      "multi_intent_policy": "拆成有限操作序列；超过安全上限时追问",
      "forbidden_routes": [],
      "fallback_operations": ["ask_clarification", "unsupported", "need_confirmation"]
    },
    "intent_binding": {
      "intent_frame_schema": {
        "intent_type": "create|modify|delete|query|render|confirm|cancel|unknown",
        "action": "shorten|expand|rewrite|polish|move|delete|fill|export|unknown",
        "target_type": "document|section|paragraph|template_field|selection|unknown",
        "target_ref": "用户原始指代表达",
        "target_resolved_id": "解析后的对象 ID 或 null",
        "scope": "local|document|template|global|unknown",
        "constraints": {},
        "risk": "low|medium|high",
        "confidence": 0.0,
        "evidence": [],
        "missing_info": []
      },
      "routing_rules": [],
      "param_mapping_rules": [],
      "forbidden_bindings": [],
      "binding_eval_cases": []
    },
    "objects": [{"name": "", "description": "", "key_fields": [], "examples": []}],
    "user_intents": [{"example": "", "normalized_intent": "", "operation_hint": ""}],
    "operations": [{
      "name": "snake_case",
      "description": "",
      "risk": "low|medium|high",
      "input_schema": {"type": "object", "properties": {}, "required": []},
      "llm_role": "LLM 在此操作中负责什么",
      "executor_role": "程序在此操作中负责什么",
      "validators": ["确定性校验规则名"],
      "requires_confirmation": false,
      "failure_policy": "失败后如何处理"
    }],
    "states": [],
    "clarification_rules": [],
    "confirmation_rules": [],
    "open_questions": [],
    "workflow": {
      "name": "",
      "goal": "",
      "strategy": "",
      "nodes": [{"id": "", "type": "agent|tool|validator|human_review", "name": "", "description": "", "operation": "", "agent": "", "inputs": [], "outputs": [], "depends_on": []}],
      "edges": [{"from": "", "to": "", "condition": ""}],
      "parallel_groups": [{"name": "", "node_ids": []}],
      "human_review_points": [{"id": "", "description": "", "required_before": ""}],
      "failure_strategy": [{"scope": "", "strategy": "retry|fallback|ask_human|rollback", "description": ""}],
      "artifacts": [{"name": "", "type": "json|md|docx|code", "description": ""}]
    }
  },
  "next_questions": ["每轮最多一个问题"],
  "quality_notes": [],
  "trace": [{"step": "", "detail": ""}]
}
"""


EMPTY_WORKFLOW: dict[str, Any] = {
    "name": "",
    "goal": "",
    "strategy": "",
    "nodes": [],
    "edges": [],
    "parallel_groups": [],
    "human_review_points": [],
    "failure_strategy": [],
    "artifacts": [],
}

EMPTY_AGENT_PROFILE: dict[str, Any] = {
    "agent_name": "",
    "role": "",
    "target_users": [],
    "interaction_style": "",
    "model_policy": {},
    "skills_summary": [],
    "knowledge_scope": "",
    "memory_scope": "",
}

EMPTY_MEMORY_POLICY: dict[str, Any] = {
    "enabled": False,
    "memory_types": [],
    "read_rules": [],
    "write_rules": [],
    "update_rules": [],
    "delete_rules": [],
    "confirmation_required": [],
    "confidence_policy": {},
    "context_injection_rules": [],
    "privacy_rules": [],
}

EMPTY_STATE_MODEL: dict[str, Any] = {
    "states": [],
    "transitions": [],
    "state_fields": [],
    "state_store": "",
    "validator_rules": [],
}

EMPTY_ARTIFACT_MODEL: dict[str, Any] = {
    "artifacts": [],
    "versioning": {"enabled": False, "snapshot_rules": [], "rollback_rules": []},
    "export_formats": [],
    "review_rules": [],
    "source_trace_required": False,
}

EMPTY_TOOL_REGISTRY: dict[str, Any] = {
    "tools": [],
    "operation_tool_bindings": [],
    "mcp_servers": [],
    "failure_policies": [],
}

EMPTY_PERMISSION_POLICY: dict[str, Any] = {
    "auto_allowed": [],
    "confirmation_required": [],
    "forbidden": [],
    "role_required": [],
    "risk_matrix": [],
}

EMPTY_ERROR_RECOVERY: dict[str, Any] = {
    "strategies": [],
    "retry_policy": {},
    "fallback_policy": {},
    "rollback_policy": {},
    "human_handoff_policy": {},
    "record_eval_case_on_failure": True,
}

EMPTY_KNOWLEDGE_POLICY: dict[str, Any] = {
    "knowledge_bases": [],
    "retrieval_rules": [],
    "citation_policy": "",
    "conflict_resolution": "",
    "rag_required_operations": [],
}

EMPTY_EVAL_POLICY: dict[str, Any] = {
    "case_types": [],
    "success_metrics": [],
    "regression_rules": [],
    "coverage_targets": [],
    "auto_record_rules": [],
    "case_groups": {},
    "golden_cases": [],
}

EMPTY_RUNTIME_TRIGGERS: dict[str, Any] = {
    "manual": True,
    "scheduled": [],
    "event_driven": [],
    "max_runtime_seconds": None,
}

EMPTY_WORKSPACE_POLICY: dict[str, Any] = {
    "project_scope": "",
    "data_boundaries": [],
    "collaboration_rules": [],
    "session_retention": "",
}

EMPTY_ARCHITECTURE_CHECK: dict[str, Any] = {
    "completeness_percent": 0,
    "covered_layers": [],
    "partial_layers": [],
    "missing_layers": [],
    "top_recommendations": [],
    "layers": [],
}

EMPTY_PROTOCOL: dict[str, Any] = {
    "project_name": "",
    "domain_summary": "",
    "target_users": [],
    "goals": [],
    "scene_classification": {},
    "context_policy": {},
    "intent_recognition": {},
    "routing_policy": {},
    "intent_binding": {},
    "agent_profile": deepcopy(EMPTY_AGENT_PROFILE),
    "memory_policy": deepcopy(EMPTY_MEMORY_POLICY),
    "state_model": deepcopy(EMPTY_STATE_MODEL),
    "artifact_model": deepcopy(EMPTY_ARTIFACT_MODEL),
    "tool_registry": deepcopy(EMPTY_TOOL_REGISTRY),
    "permission_policy": deepcopy(EMPTY_PERMISSION_POLICY),
    "error_recovery": deepcopy(EMPTY_ERROR_RECOVERY),
    "knowledge_policy": deepcopy(EMPTY_KNOWLEDGE_POLICY),
    "eval_policy": deepcopy(EMPTY_EVAL_POLICY),
    "runtime_triggers": deepcopy(EMPTY_RUNTIME_TRIGGERS),
    "workspace_policy": deepcopy(EMPTY_WORKSPACE_POLICY),
    "architecture_check": deepcopy(EMPTY_ARCHITECTURE_CHECK),
    "objects": [],
    "user_intents": [],
    "operations": [],
    "states": [],
    "clarification_rules": [],
    "confirmation_rules": [],
    "open_questions": [],
    "workflow": deepcopy(EMPTY_WORKFLOW),
}



def localize_user_text(text: str) -> str:
    replacements = {
        "Agent": "智能体（Agent）",
        "agent": "智能体（agent）",
        "operation": "操作（operation）",
        "operations": "操作（operations）",
        "object": "业务对象（object）",
        "objects": "业务对象（objects）",
        "validator": "校验规则（validator）",
        "validators": "校验规则（validators）",
        "schema": "参数结构（schema）",
        "executor": "执行器（executor）",
        "Planner": "规划器（Planner）",
        "Executor": "执行器（Executor）",
        "create_text": "生成文本（create_text）",
        "revise_document": "修改文档（revise_document）",
        "generate_text": "生成文本（generate_text）",
        "polish_document": "润色文档（polish_document）",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    text = text.replace("智能体（智能体（Agent））", "智能体（Agent）")
    text = text.replace("操作（操作（operation））", "操作（operation）")
    text = text.replace("业务对象（业务对象（object））", "业务对象（object）")
    text = text.replace("校验规则（校验规则（validator））", "校验规则（validator）")
    return text


META_PREFIXES = (
    "差异：", "差异:", "无变化：", "无变化:", "新增：", "新增:",
    "调整：", "调整:", "补充：", "补充:", "原值：", "原值:",
    "修改：", "修改:", "更新：", "更新:",
)
STRING_PROTOCOL_FIELDS = {"description", "llm_role", "executor_role", "failure_policy"}
STRUCTURAL_FIELDS = {"input_schema", "output_schema", "properties", "required", "items"}
RULE_LIST_FIELDS = {"confirmation_rules", "clarification_rules"}


def strip_meta_prefix(value: str) -> str:
    text = str(value).strip()
    changed = True
    while changed:
        changed = False
        for prefix in META_PREFIXES:
            if text.startswith(prefix):
                text = text[len(prefix):].strip()
                changed = True
    return text


def normalize_rule_text(value: str) -> str:
    text = strip_meta_prefix(str(value))
    replacements = {
        "，": ",", "。": ".", "；": ";", "：": ":", "（": "(", "）": ")",
        "“": '"', "”": '"', "‘": "'", "’": "'", " ": "", "\t": "", "\n": "",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text.lower().strip(".,;:。；， ")


def better_rule(existing: str, candidate: str) -> str:
    existing_clean = strip_meta_prefix(existing)
    candidate_clean = strip_meta_prefix(candidate)
    existing_had_prefix = existing_clean != str(existing).strip()
    candidate_had_prefix = candidate_clean != str(candidate).strip()
    if existing_had_prefix and not candidate_had_prefix:
        return candidate_clean
    if candidate_had_prefix and not existing_had_prefix:
        return existing_clean
    return candidate_clean if len(candidate_clean) >= len(existing_clean) else existing_clean


def dedupe_rules(values: list[Any]) -> list[str]:
    result: list[str] = []
    normalized: list[str] = []
    for raw in values:
        if raw in (None, ""):
            continue
        text = strip_meta_prefix(str(raw))
        if not text:
            continue
        norm = normalize_rule_text(text)
        if not norm:
            continue
        matched = None
        for idx, old_norm in enumerate(normalized):
            if norm == old_norm or norm in old_norm or old_norm in norm:
                matched = idx
                break
        if matched is None:
            normalized.append(norm)
            result.append(text)
        else:
            chosen = better_rule(result[matched], text)
            result[matched] = chosen
            normalized[matched] = normalize_rule_text(chosen)
    return result


def sanitize_protocol_value(value: Any, *, field_name: str | None = None) -> Any:
    if isinstance(value, str):
        return strip_meta_prefix(value) if field_name in STRING_PROTOCOL_FIELDS or field_name is None else value
    if isinstance(value, list):
        if field_name in RULE_LIST_FIELDS:
            return dedupe_rules(value)
        return [sanitize_protocol_value(item) for item in value]
    if isinstance(value, dict):
        return {k: sanitize_protocol_value(v, field_name=k) for k, v in value.items()}
    return value


def is_empty_structural_value(value: Any) -> bool:
    return value in ({}, [])


def merge_dict_preserve(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(old or {})
    for key, value in (new or {}).items():
        value = sanitize_protocol_value(value, field_name=key)
        old_value = merged.get(key)
        if value in (None, ""):
            continue
        if key in STRUCTURAL_FIELDS and is_empty_structural_value(value) and old_value not in (None, {}, []):
            continue
        if isinstance(value, dict) and isinstance(old_value, dict):
            merged[key] = merge_dict_preserve(old_value, value)
        elif isinstance(value, list) and isinstance(old_value, list):
            if key in RULE_LIST_FIELDS:
                merged[key] = dedupe_rules(list(old_value) + list(value))
            elif key == "validators":
                merged[key] = dedupe_rules(list(old_value) + list(value))
            elif key in STRUCTURAL_FIELDS and not value and old_value:
                merged[key] = old_value
            else:
                merged[key] = _merge_list(old_value, value, key)
        else:
            merged[key] = value
    return merged


def merge_protocol(old: dict[str, Any] | None, new: dict[str, Any] | None) -> dict[str, Any]:
    result = deepcopy(old or EMPTY_PROTOCOL)
    new = sanitize_protocol_value(new or {})
    for key, value in new.items():
        if value in (None, ""):
            continue
        if key in STRUCTURAL_FIELDS and is_empty_structural_value(value) and result.get(key) not in (None, {}, []):
            continue
        if isinstance(value, list):
            if key in RULE_LIST_FIELDS:
                result[key] = dedupe_rules(list(result.get(key, []) or []) + list(value))
            else:
                result[key] = _merge_list(result.get(key, []) or [], value, key)
        elif isinstance(value, dict):
            result[key] = merge_dict_preserve(result.get(key) or {}, value)
        else:
            result[key] = value
    for rule_key in RULE_LIST_FIELDS:
        if isinstance(result.get(rule_key), list):
            result[rule_key] = dedupe_rules(result[rule_key])
    return result


def _merge_list(old: list[Any], new: list[Any], key: str) -> list[Any]:
    if key in RULE_LIST_FIELDS or key == "validators":
        return dedupe_rules(list(old or []) + list(new or []))
    id_key = {
        "objects": "name",
        "operations": "name",
        "user_intents": "example",
        "nodes": "id",
        "parallel_groups": "name",
        "human_review_points": "id",
        "failure_strategy": "scope",
        "artifacts": "name",
        "tools": "name",
        "operation_tool_bindings": "operation",
        "mcp_servers": "name",
        "states": "id",
        "transitions": "from",
        "state_fields": "name",
        "knowledge_bases": "name",
        "golden_cases": "id",
    }.get(key)
    if not id_key:
        merged = list(old or [])
        for item in new or []:
            cleaned = sanitize_protocol_value(item)
            if cleaned not in merged:
                merged.append(cleaned)
        return merged
    merged_map: dict[str, Any] = {}
    order: list[str] = []
    for item in list(old or []) + list(new or []):
        item = sanitize_protocol_value(item)
        if not isinstance(item, dict):
            token = normalize_rule_text(str(item))
            if token not in merged_map:
                order.append(token)
            merged_map[token] = item
            continue
        token = str(item.get(id_key) or item).strip().lower()
        if not token:
            continue
        if token not in merged_map:
            order.append(token)
            merged_map[token] = item
        else:
            existing = dict(merged_map[token])
            merged_map[token] = merge_dict_preserve(existing, item)
    return [merged_map[token] for token in order]

def extract_json(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if not text:
        raise ValueError("empty response")
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start:end + 1])
        raise


async def design_step(
    user_message: str,
    *,
    protocol: dict[str, Any] | None = None,
    history: list[dict[str, str]] | None = None,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    protocol = protocol or deepcopy(EMPTY_PROTOCOL)
    history = history or []
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": json.dumps(
                {
                    "current_protocol": protocol,
                    "conversation_history": history[-12:],
                    "developer_message": user_message,
                    "instruction": "像自适应架构教练一样工作：观察开发者回答，更新协议状态，判断下一步是继续追问、生成草案、补校验、评审，还是收束。不要机械进入下一步。对话内容用中文表达，少用英文术语。",
                },
                ensure_ascii=False,
            ),
        },
    ]
    try:
        base_settings = dict(settings or {})
        trace_items: list[dict[str, Any]] = []
        max_tokens = int(base_settings.get("max_tokens") or os.getenv("APD_MAX_TOKENS", "8000"))
        data = None
        last_error: Exception | None = None
        for attempt in range(2):
            attempt_settings = {**base_settings, "max_tokens": max_tokens}
            result = await chat_json_result(messages, settings=attempt_settings)
            raw = result.content
            trace_items.append({
                "step": "llm_call",
                "attempt": attempt + 1,
                "finish_reason": result.get("finish_reason"),
                "max_tokens": result.get("max_tokens"),
                "usage": result.get("usage"),
            })
            try:
                data = extract_json(raw)
                break
            except Exception as parse_exc:
                last_error = parse_exc
                failure_detail = str(parse_exc)[:500]
                trace_items.append({"step": "json_parse_error", "attempt": attempt + 1, "detail": failure_detail})
                if result.get("finish_reason") == "length" or attempt == 0:
                    max_tokens = min(max_tokens * 2, 32000)
                    messages.append({
                        "role": "user",
                        "content": json.dumps(
                            {
                                "retry_feedback": "上一次输出无法解析为严格 JSON，请只返回符合 schema 的完整 JSON，不要 Markdown，不要代码块。",
                                "parse_error": failure_detail,
                                "finish_reason": result.get("finish_reason"),
                                "next_max_tokens": max_tokens,
                            },
                            ensure_ascii=False,
                        ),
                    })
                    trace_items.append({"step": "retry_feedback", "attempt": attempt + 1, "next_max_tokens": max_tokens})
                    continue
                raise
        if data is None:
            raise last_error or ValueError("no json data")
        data["protocol"] = ensure_harness_protocol(ensure_scene_classification(merge_protocol(protocol, data.get("protocol") or {}), user_message))
        data.setdefault("trace", [])
        data["trace"] = list(data.get("trace") or []) + trace_items
        data["fallback"] = False
        return normalize_response(data)
    except Exception as exc:  # noqa: BLE001
        print(f"[apd] llm error: {exc!r}", file=sys.stderr, flush=True)
        result = fallback_step(user_message, protocol, str(exc))
        result.setdefault("trace", [])
        result["trace"].append({"step": "error", "detail": repr(exc)[:500]})
        if "trace_items" in locals() and trace_items:
            result["trace"].append({"step": "attempts", "items": trace_items})
        return result


def workflow_has_design(protocol: dict[str, Any]) -> bool:
    workflow = protocol.get("workflow") or {}
    if not isinstance(workflow, dict):
        return False
    return bool(
        workflow.get("nodes")
        or workflow.get("edges")
        or workflow.get("parallel_groups")
        or workflow.get("human_review_points")
        or workflow.get("failure_strategy")
    )


def should_recommend_workflow(protocol: dict[str, Any]) -> tuple[bool, str]:
    protocol = ensure_workflow(ensure_scene_classification(protocol))
    if workflow_has_design(protocol):
        return False, ""
    scene = protocol.get("scene_classification") or {}
    scene_type = str(scene.get("scene_type") or "").lower()
    summary_text = " ".join([
        str(protocol.get("project_name") or ""),
        str(protocol.get("domain_summary") or ""),
        " ".join(map(str, protocol.get("goals") or [])),
        str(scene.get("recommended_architecture") or ""),
    ]).lower()
    operations = protocol.get("operations") or []
    objects = protocol.get("objects") or []
    complex_scene_types = {"workflow_state_machine", "multi_agent", "agent_loop"}
    complex_keywords = [
        "多步骤", "流程", "并行", "分支", "回退", "人工确认", "校验", "验证", "审校",
        "招标", "投标", "标书", "响应矩阵", "知识库", "检索", "图谱", "解析", "导出",
        "长文", "章节", "目录", "报告", "文档生成", "多 agent", "multi_agent",
    ]
    has_complex_keyword = any(keyword.lower() in summary_text for keyword in complex_keywords)
    has_protocol_shape = len(operations) >= 3 or len(objects) >= 3

    if scene_type in complex_scene_types:
        return True, "当前场景已被识别为多步骤或多智能体协作类型。"
    if scene_type == "protocol_driven_opcall" and has_complex_keyword and has_protocol_shape:
        return True, "当前场景虽然可以先做能力协议，但已经出现多步骤、校验、文档/知识库/导出等工作流特征。"
    if has_complex_keyword and len(operations) >= 5:
        return True, "当前协议已经包含较多操作，并且出现流程编排或校验特征。"
    return False, ""


def apply_workflow_guidance(response: dict[str, Any]) -> dict[str, Any]:
    protocol = ensure_workflow(response.get("protocol") or {})
    response["protocol"] = protocol
    recommend, reason = should_recommend_workflow(protocol)
    if not recommend:
        return response
    question = f"{reason} 要不要我下一步进入动态工作流（Dynamic Workflow）设计，把当前能力协议继续拆成节点、边、并行组、人工确认点、失败策略和导出产物？"
    existing_questions = response.get("next_questions") or []
    if existing_questions and "workflow" in str(existing_questions[0]).lower():
        return response
    response["next_questions"] = [question]
    notes = list(response.get("quality_notes") or [])
    notes.append("系统已自动识别到该场景可能需要动态工作流（Dynamic Workflow）；这是可选增强，不会影响原能力协议。")
    response["quality_notes"] = notes
    return response


ARCHITECTURE_LAYER_QUESTIONS: dict[str, str] = {
    "Agent Profile / 智能体画像": "为了先确定智能体画像，请用一句话说明：这个 Agent 的角色是什么，它主要服务哪类用户？",
    "Memory Policy / 记忆策略": "这个 Agent 是否需要跨轮记住信息？如果需要，优先记任务进度、用户偏好、项目规则，还是历史失败案例？",
    "State Model / 状态模型": "这个 Agent 的任务有没有明显阶段？例如待上传、已解析、待审核、已导出这类状态。",
    "Artifact Model / 产物模型": "这个 Agent 最终会生成或修改什么产物？例如文档、报告、表格、代码、图谱或可导出的文件。",
    "Tool Registry / 工具注册": "这个 Agent 需要依赖哪些底层工具或外部系统？例如知识库检索、文档解析、图谱查询、数据库、导出工具。",
    "Permission Policy / 权限策略": "哪些操作可以自动执行，哪些必须先让用户确认？请先说最危险的 1-2 类操作。",
    "Error Recovery / 失败恢复": "如果这个 Agent 执行失败，应该优先重试、追问用户、回滚、换工具，还是转人工处理？",
    "Eval Cases / 评测用例": "你最担心 Agent 理解错哪类用户话术？给我一个容易误判的例子即可。",
    "Knowledge/RAG Policy / 知识检索策略": "这个 Agent 是否需要查知识库、资料库或图谱？如果需要，哪些结论必须带来源证据？",
    "Versioning/Rollback / 版本回滚": "这个 Agent 修改内容或状态后，是否需要支持撤回、版本快照或恢复上一版？",
    "Cost/Latency Budget / 成本耗时预算": "这个 Agent 对响应速度或运行成本有没有限制？例如必须秒级回复，还是可以长时间生成。",
    "Workspace Policy / 项目空间策略": "这个 Agent 的数据是否只属于某个项目或工作区？是否需要限制跨项目读取？",
}


def architecture_question_for(protocol: dict[str, Any]) -> tuple[str, str] | None:
    check = (protocol.get("architecture_check") or build_architecture_check(protocol))
    covered = set(check.get("covered_layers") or [])
    # 先补智能体画像，再补场景增强层。跳过已经由默认协议覆盖的核心执行链路。
    priority = [
        "Agent Profile / 智能体画像",
        "Memory Policy / 记忆策略",
        "State Model / 状态模型",
        "Artifact Model / 产物模型",
        "Tool Registry / 工具注册",
        "Permission Policy / 权限策略",
        "Error Recovery / 失败恢复",
        "Eval Cases / 评测用例",
        "Knowledge/RAG Policy / 知识检索策略",
        "Versioning/Rollback / 版本回滚",
        "Cost/Latency Budget / 成本耗时预算",
        "Workspace Policy / 项目空间策略",
    ]
    layer_status = {layer.get("name"): layer.get("status") for layer in check.get("layers") or []}
    for layer_name in priority:
        if layer_name in covered:
            continue
        if layer_status.get(layer_name) in {"missing", "partial"}:
            return layer_name, ARCHITECTURE_LAYER_QUESTIONS[layer_name]
    return None


def apply_architecture_guidance(response: dict[str, Any]) -> dict[str, Any]:
    protocol = ensure_harness_protocol(response.get("protocol") or {})
    response["protocol"] = protocol
    suggestion = architecture_question_for(protocol)
    if not suggestion:
        return response
    layer_name, question = suggestion
    existing_questions = response.get("next_questions") or []
    # 如果 LLM 已经问了一个更具体的问题，保留它；但把架构缺口写入 quality_notes，避免丢失方向。
    notes = list(response.get("quality_notes") or [])
    notes.append(f"架构完整性检查建议下一步优先补：{layer_name}。")
    response["quality_notes"] = notes
    if not existing_questions or any(str(q).strip() in {"", "下一步问题会显示在这里。"} for q in existing_questions):
        response["next_questions"] = [question]
    return response


def normalize_response(data: dict[str, Any]) -> dict[str, Any]:
    protocol = ensure_harness_protocol(data.get("protocol") or {})
    response = {
        "assistant_message": localize_user_text(data.get("assistant_message") or "我更新了能力协议草案。"),
        "stage": data.get("stage") or "discover",
        "protocol": protocol,
        "next_questions": [localize_user_text(q) for q in (data.get("next_questions") or protocol.get("open_questions") or [])[:1]],
        "quality_notes": [localize_user_text(q) for q in (data.get("quality_notes") or [])],
        "trace": data.get("trace") or [],
        "fallback": bool(data.get("fallback")),
    }
    response = apply_workflow_guidance(response)
    response = apply_architecture_guidance(response)
    response["next_questions"] = [localize_user_text(q) for q in (response.get("next_questions") or [])[:1]]
    response["quality_notes"] = [localize_user_text(q) for q in (response.get("quality_notes") or [])]
    return response


def fallback_step(user_message: str, protocol: dict[str, Any], error: str) -> dict[str, Any]:
    protocol = ensure_harness_protocol(ensure_scene_classification(deepcopy(protocol or EMPTY_PROTOCOL), user_message))
    if not protocol.get("domain_summary") and user_message.strip():
        protocol["domain_summary"] = user_message.strip()[:500]
    if not protocol.get("open_questions"):
        protocol["open_questions"] = [
            "这个 Agent 的目标用户是谁？先只回答用户类型和使用场景即可。",
        ]
    reason = error[:220]
    if "missing api_base or api_key" in error:
        hint = "缺少 API Base 或 API Key。请在页面顶部填写模型配置，或设置 APD_API_BASE / APD_API_KEY / APD_MODEL。"
    elif "timeout" in error.lower():
        hint = "LLM 调用超时。请检查模型名是否可用，或换一个响应更快的模型。"
    elif "HTTP" in error:
        hint = f"LLM 网关返回错误：{reason}"
    else:
        hint = f"LLM 调用失败：{reason}"
    return {
        "assistant_message": localize_user_text(f"当前没有成功调用 LLM，已进入离线降级模式。{hint} 我先用离线规则记录场景，但智能拆解效果会比较弱。"),
        "stage": "discover",
        "protocol": protocol,
        "next_questions": protocol["open_questions"][:3],
        "quality_notes": ["LLM 调用失败，已进入离线降级模式。"],
        "trace": [{"step": "fallback", "detail": error[:300]}],
        "fallback": True,
    }



def infer_scene_classification(text: str, protocol: dict[str, Any] | None = None) -> dict[str, Any]:
    """Offline fallback classifier for architecture selection."""
    protocol = protocol or {}
    haystack = " ".join([
        text or "",
        str(protocol.get("project_name") or ""),
        str(protocol.get("domain_summary") or ""),
        " ".join(map(str, protocol.get("goals") or [])),
    ]).lower()

    def has_any(words: list[str]) -> bool:
        return any(word.lower() in haystack for word in words)

    if has_any(["写作", "文档", "模板", "word", "论文", "章节", "段落", "报告", "排版"]):
        return {
            "scene_type": "protocol_driven_opcall",
            "recommended_architecture": "协议驱动多轮操作调用：规划器（Planner）每轮映射到一个或少量受控操作（operation），程序负责校验、确认、版本快照和执行。",
            "confidence": "medium",
            "dimensions": {
                "reversibility": "medium",
                "path_determinism": "partially_known",
                "rule_volume": "high",
                "intent_density": "mixed",
                "side_effect_scope": "local",
            },
            "why_not_agent_loop": [
                "写作有大量文档状态、模板规则、删除/覆盖/导出风险，自由循环容易选错工具或改错范围。",
                "用户通常表达的是明确编辑意图，应先映射到受控操作，而不是让模型无限自主探索。",
            ],
            "why_not_pure_state_machine": [
                "写作流程无法穷举，用户可能随时跳到任意章节、语气、模板或材料问题。",
                "应固定能力边界，而不是固定所有写作流程。",
            ],
            "flow_strategy": "流程开放组合：不枚举所有写作流程，允许用户多轮驱动或拆成有限操作序列。",
            "capability_strategy": "先收敛稳定写作能力，例如解析模板、生成大纲、生成章节、局部改写、删除/移动章节、审校、渲染和导出。",
        }
    if has_any(["审批", "法律", "医疗", "金融", "合同", "转账", "处方", "合规"]):
        scene_type = "workflow_state_machine"
        architecture = "状态机编排 + 强确认门禁：程序控制阶段、权限、校验、确认和审计。"
        dimensions = {"reversibility": "low", "path_determinism": "known", "rule_volume": "high", "intent_density": "single", "side_effect_scope": "external"}
    elif has_any(["入库", "etl", "批处理", "解析", "分块", "嵌入", "milvus", "管线"]):
        scene_type = "workflow_state_machine"
        architecture = "管线式状态机：固定步骤、批处理执行、错误定位和重跑。"
        dimensions = {"reversibility": "low", "path_determinism": "known", "rule_volume": "medium", "intent_density": "single", "side_effect_scope": "external"}
    elif has_any(["修 bug", "修复", "代码", "浏览器", "研究", "调研", "迁移", "debug", "github"]):
        scene_type = "agent_loop"
        architecture = "单回合自主循环：允许智能体（Agent）多步读写、观察、测试和修正，但保留停止条件和工具边界。"
        dimensions = {"reversibility": "high", "path_determinism": "unknown", "rule_volume": "low", "intent_density": "multi", "side_effect_scope": "local"}
    elif has_any(["客服", "faq", "问答", "翻译", "摘要", "rag"]):
        scene_type = "direct_llm"
        architecture = "直接问答或检索增强生成（RAG）：先避免引入复杂智能体循环。"
        dimensions = {"reversibility": "high", "path_determinism": "known", "rule_volume": "low", "intent_density": "single", "side_effect_scope": "none"}
    else:
        scene_type = "single_step_opcall"
        architecture = "单步操作调用：先假设每轮映射到一个受控操作，再根据多意图和流程不确定性升级。"
        dimensions = {"reversibility": "unknown", "path_determinism": "partially_known", "rule_volume": "unknown", "intent_density": "unknown", "side_effect_scope": "unknown"}

    return {
        "scene_type": scene_type,
        "recommended_architecture": architecture,
        "confidence": "low",
        "dimensions": dimensions,
        "why_not_agent_loop": ["当前信息不足以证明需要自由自主循环；应先判断流程是否可列、能力是否可收敛。"],
        "why_not_pure_state_machine": ["当前信息不足以证明流程完全固定；应先收集典型用户话术和副作用范围。"],
        "flow_strategy": "先不要枚举所有流程，先判断流程固定程度和关键分支。",
        "capability_strategy": "优先列稳定能力、状态对象、风险操作和失败出口。",
    }


def ensure_scene_classification(protocol: dict[str, Any], source_text: str = "") -> dict[str, Any]:
    protocol = merge_protocol(EMPTY_PROTOCOL, protocol or {})
    current = protocol.get("scene_classification") or {}
    if isinstance(current, dict) and current.get("scene_type") and current.get("recommended_architecture"):
        return protocol
    protocol["scene_classification"] = infer_scene_classification(source_text, protocol)
    return protocol


def build_scene_classification(protocol: dict[str, Any]) -> str:
    protocol = ensure_scene_classification(protocol)
    scene = protocol.get("scene_classification") or {}
    dimensions = scene.get("dimensions") or {}
    lines = [
        "# Agent 场景分类与架构建议",
        "",
        f"- 场景类型：{scene.get('scene_type') or 'unknown'}",
        f"- 推荐架构：{scene.get('recommended_architecture') or '待判断'}",
        f"- 置信度：{scene.get('confidence') or 'unknown'}",
        "",
        "## 判断维度",
    ]
    for key in ("reversibility", "path_determinism", "rule_volume", "intent_density", "side_effect_scope"):
        lines.append(f"- {key}: {dimensions.get(key, 'unknown')}")
    lines.extend(["", "## 为什么不直接自由 agent_loop"])
    for item in scene.get("why_not_agent_loop") or ["暂无明确判断。"]:
        lines.append(f"- {item}")
    lines.extend(["", "## 为什么不纯状态机"])
    for item in scene.get("why_not_pure_state_machine") or ["暂无明确判断。"]:
        lines.append(f"- {item}")
    lines.extend([
        "",
        "## 流程策略",
        scene.get("flow_strategy") or "待补充。",
        "",
        "## 能力策略",
        scene.get("capability_strategy") or "待补充。",
        "",
        "## 核心原则",
        "流程可以开放组合，能力协议必须收敛；不要要求开发者一次列出所有流程。",
    ])
    return "\n".join(lines).strip() + "\n"


def default_context_policy(protocol: dict[str, Any]) -> dict[str, Any]:
    scene_type = ((protocol.get("scene_classification") or {}).get("scene_type") or "").lower()
    is_writing = scene_type == "protocol_driven_opcall" or any(word in str(protocol.get("domain_summary") or "") for word in ("写作", "文档", "模板", "章节"))
    if is_writing:
        return {
            "context_pack_fields": [
                "user_message",
                "document_outline",
                "active_section_id",
                "selected_text",
                "recent_operations",
                "template_constraints",
                "locked_sections",
                "pending_confirmations",
                "version_snapshot",
            ],
            "retrieval_rules": [
                "优先提供用户显式指向的章节、选区或模板字段。",
                "用户说“这段/这里”时优先使用 selected_text；没有选区再看 active_section_id。",
                "用户说“刚才/上一版”时提供 recent_operations 和 version_snapshot 摘要。",
                "不要默认提供全文；只提供当前目标附近内容和必要约束摘要。",
            ],
            "recency_window": "最近 5 轮用户消息和最近 10 条操作 trace",
            "forbidden_context": ["与目标无关的全文", "未授权材料", "无需暴露的模板底层 XML/OOXML 细节"],
            "context_budget_policy": "按 用户显式目标 > 当前选区 > 当前章节 > 最近操作 > 全局约束 的顺序裁剪。",
        }
    return {
        "context_pack_fields": ["user_message", "active_object", "available_objects", "recent_operations", "pending_confirmations"],
        "retrieval_rules": ["只提供当前路由必要的对象、状态和约束摘要。"],
        "recency_window": "最近 3-5 轮对话和操作",
        "forbidden_context": ["无关历史", "超出权限的数据"],
        "context_budget_policy": "优先保留目标对象、状态、风险规则和最近操作。",
    }


def default_intent_recognition(protocol: dict[str, Any]) -> dict[str, Any]:
    scene_type = ((protocol.get("scene_classification") or {}).get("scene_type") or "").lower()
    is_writing = scene_type == "protocol_driven_opcall" or any(word in str(protocol.get("domain_summary") or "") for word in ("写作", "文档", "模板", "章节"))
    base = {
        "planner_role": "观察结构化上下文包（Context Pack），理解用户意图、目标对象、风险和缺失信息，只输出受控操作计划，不直接执行。",
        "reference_resolution_rules": [
            "显式 ID 或名称优先于隐式指代。",
            "隐式指代必须能从上下文包唯一解析，否则追问。",
            "目标范围不明确时，不能默认扩大到全文或全局对象。",
        ],
        "ambiguity_policy": [
            "操作类型不明确时追问。",
            "目标对象不唯一时追问。",
            "删除、覆盖、导出、提交等高风险动作缺少范围时追问或确认。",
        ],
        "confidence_policy": {"auto_execute_threshold": 0.8, "clarify_below": 0.6, "require_evidence": True},
        "required_output_fields": ["intent", "operation", "target", "params", "confidence", "evidence", "needs_clarification", "needs_confirmation"],
        "examples": [],
    }
    if is_writing:
        base["reference_resolution_rules"] = [
            "“这段/这里/选中内容”优先解析为 selected_text。",
            "没有 selected_text 时，“这段/这里”可解析为 active_section_id；仍不唯一则追问。",
            "“第二章/摘要/结论”等章节名必须映射到 document_outline 中唯一章节。",
            "“刚才那个/上一版”必须结合 recent_operations 或 version_snapshot，不能凭空猜。",
            "“太长/太啰嗦”默认是局部改写意图，不得路由到全文重写，除非用户明确说全文。",
        ]
        base["examples"] = [
            {
                "user_message": "第二章太长，缩短一半",
                "context": {"active_section_id": "chapter_2"},
                "expected_operation": "rewrite_section",
                "forbidden_operations": ["rewrite_document", "delete_section"],
                "evidence": ["用户显式提到第二章", "缩短一半是局部章节改写"],
            },
            {
                "user_message": "这段太啰嗦，压缩一下",
                "context": {"selected_text": "..."},
                "expected_operation": "rewrite_selected_text",
                "forbidden_operations": ["rewrite_document"],
                "evidence": ["这段解析为 selected_text"],
            },
        ]
    return base


def default_routing_policy(protocol: dict[str, Any]) -> dict[str, Any]:
    operation_names = [op.get("name") for op in protocol.get("operations") or [] if isinstance(op, dict) and op.get("name")]
    return {
        "allowed_operations_source": "operations",
        "allowed_operations": operation_names,
        "multi_intent_policy": "允许拆成有限操作序列；每一步仍必须通过校验。超过 3 步或包含高风险动作时先向用户确认。",
        "forbidden_routes": [
            "不能把局部编辑请求路由到全文重写。",
            "不能把模糊删除请求直接路由到删除操作。",
            "不能绕过 confirmation_rules 和 clarification_rules。",
        ],
        "fallback_operations": ["ask_clarification", "unsupported", "need_confirmation"],
    }


def ensure_intent_protocol(protocol: dict[str, Any]) -> dict[str, Any]:
    protocol = ensure_scene_classification(protocol)
    if not (protocol.get("context_policy") or {}).get("context_pack_fields"):
        protocol["context_policy"] = default_context_policy(protocol)
    if not (protocol.get("intent_recognition") or {}).get("required_output_fields"):
        protocol["intent_recognition"] = default_intent_recognition(protocol)
    if not (protocol.get("routing_policy") or {}).get("allowed_operations_source"):
        protocol["routing_policy"] = default_routing_policy(protocol)
    else:
        protocol["routing_policy"].setdefault("allowed_operations", [op.get("name") for op in protocol.get("operations") or [] if isinstance(op, dict) and op.get("name")])
    if not (protocol.get("intent_binding") or {}).get("intent_frame_schema"):
        protocol["intent_binding"] = default_intent_binding(protocol)
    return protocol



def has_non_empty_value(value: Any) -> bool:
    if value in (None, "", [], {}):
        return False
    if isinstance(value, dict):
        return any(has_non_empty_value(item) for item in value.values())
    if isinstance(value, list):
        return any(has_non_empty_value(item) for item in value)
    return True


def _layer_status(name: str, covered: bool, recommendation: str, *, partial: bool = False) -> dict[str, Any]:
    if covered:
        status = "covered"
    elif partial:
        status = "partial"
    else:
        status = "missing"
    return {"name": name, "status": status, "recommendation": recommendation}


def _tool(name: str, description: str, inputs: list[str], outputs: list[str], *, risk: str = "low", side_effects: list[str] | None = None, failure_policy: str = "retry_or_ask_user") -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "input_schema": {"type": "object", "required": inputs, "properties": {item: {"type": "string"} for item in inputs}},
        "output_schema": {"type": "object", "fields": outputs},
        "risk": risk,
        "side_effects": side_effects or [],
        "failure_policy": failure_policy,
    }


def default_error_recovery(protocol: dict[str, Any]) -> dict[str, Any]:
    operations = [op for op in protocol.get("operations") or [] if isinstance(op, dict)]
    tool_registry = protocol.get("tool_registry") or {}
    artifact_model = protocol.get("artifact_model") or {}
    state_model = protocol.get("state_model") or {}
    strategies = [
        {"scope": "intent_planner", "error_type": "low_confidence_or_ambiguous", "strategy": "ask_clarification", "description": "意图置信度低、目标不明确或信息不足时，不执行工具，先追问用户。"},
        {"scope": "validator", "error_type": "missing_params_or_invalid_state", "strategy": "block_and_explain", "description": "参数缺失、状态不允许、权限不足时阻断执行并说明原因。"},
        {"scope": "tool", "error_type": "tool_failed_or_timeout", "strategy": "retry_then_fallback", "description": "工具超时或失败时最多重试，再降级或转人工，不允许编造工具结果。"},
        {"scope": "artifact", "error_type": "artifact_write_failed", "strategy": "rollback_to_snapshot", "description": "产物写入失败或覆盖异常时回滚到最近快照。"},
        {"scope": "permission", "error_type": "confirmation_required", "strategy": "await_human_confirmation", "description": "需要确认或角色授权时暂停执行，等待用户确认。"},
    ]
    for op in operations:
        name = str(op.get("name") or "")
        risk = str(op.get("risk") or "").lower()
        if not name:
            continue
        lower = f"{name} {op.get('description') or ''}".lower()
        if risk == "high" or any(key in lower for key in ("delete", "overwrite", "export", "submit", "删除", "覆盖", "导出", "提交")):
            strategies.append({"scope": f"operation:{name}", "error_type": "high_risk_failed_or_rejected", "strategy": "snapshot_then_confirm_or_rollback", "description": "高风险操作失败、拒绝或中断时，保留快照并回滚，不自动重试破坏性动作。"})
        elif any(key in lower for key in ("generate", "draft", "rewrite", "生成", "改写")):
            strategies.append({"scope": f"operation:{name}", "error_type": "generation_quality_failed", "strategy": "revise_with_feedback", "description": "生成质量不达标时保留草稿，基于问题列表重新生成或局部修改。"})
    tool_failures = []
    for tool in tool_registry.get("tools") or []:
        if not isinstance(tool, dict) or not tool.get("name"):
            continue
        tool_failures.append({
            "tool": tool.get("name"),
            "risk": tool.get("risk") or "unknown",
            "side_effects": tool.get("side_effects") or [],
            "failure_policy": tool.get("failure_policy") or "retry_or_ask_user",
        })
    return {
        "strategies": strategies,
        "retry_policy": {
            "max_attempts": 2,
            "retryable_errors": ["timeout", "rate_limit", "temporary_network_error", "tool_unavailable"],
            "non_retryable_errors": ["permission_denied", "validator_failed", "user_rejected", "forbidden_action"],
            "backoff": "short_exponential_backoff",
        },
        "fallback_policy": {
            "when_tool_unavailable": "ask_user_or_switch_to_manual_step",
            "when_llm_unavailable": "offline_rule_mode_with_warning",
            "when_knowledge_missing": "ask_user_for_material_or_mark_gap",
        },
        "rollback_policy": {
            "enabled": bool((artifact_model.get("versioning") or {}).get("enabled") or state_model.get("states")),
            "snapshot_before": ["delete", "overwrite", "export", "write_graph", "batch_update"],
            "rollback_targets": ["artifact", "state", "memory", "tool_side_effect"],
        },
        "human_handoff_policy": {
            "conditions": ["repeated_failure", "high_risk_uncertain", "permission_required", "conflicting_evidence", "user_requests_human"],
            "handoff_message": "当前步骤需要人工判断，我会整理上下文、失败原因和建议处理方式。",
        },
        "tool_failure_policies": tool_failures,
        "record_eval_case_on_failure": True,
    }


def ensure_error_recovery(protocol: dict[str, Any]) -> dict[str, Any]:
    protocol = merge_protocol(EMPTY_PROTOCOL, protocol or {})
    recovery = protocol.get("error_recovery") if isinstance(protocol.get("error_recovery"), dict) else {}
    if not (recovery.get("strategies") or recovery.get("retry_policy") or recovery.get("fallback_policy") or recovery.get("rollback_policy")):
        protocol["error_recovery"] = default_error_recovery(protocol)
    else:
        protocol["error_recovery"] = merge_dict_preserve(deepcopy(EMPTY_ERROR_RECOVERY), recovery)
    return protocol


def build_error_recovery_markdown(protocol: dict[str, Any]) -> str:
    protocol = ensure_error_recovery(protocol)
    recovery = protocol.get("error_recovery") or {}
    lines = ["# Error Recovery / 失败恢复策略", "", "失败恢复策略负责说明：失败后是重试、追问、回滚、降级、转人工，还是记录为评测 case。", "", "## 1. 失败策略"]
    for item in recovery.get("strategies") or []:
        if isinstance(item, dict):
            lines.append(f"- {item.get('scope') or 'global'} / {item.get('error_type') or 'error'} → {item.get('strategy') or 'strategy'}：{item.get('description') or ''}")
    if not recovery.get("strategies"):
        lines.append("- 暂无。")
    for title, key in (("## 2. 重试策略", "retry_policy"), ("## 3. 降级策略", "fallback_policy"), ("## 4. 回滚策略", "rollback_policy"), ("## 5. 转人工策略", "human_handoff_policy")):
        lines.extend(["", title, "", "```json", json.dumps(recovery.get(key) or {}, ensure_ascii=False, indent=2), "```"])
    lines.extend(["", "## 6. 工具失败策略"])
    for item in recovery.get("tool_failure_policies") or []:
        if isinstance(item, dict):
            lines.append(f"- {item.get('tool') or ''}：风险 {item.get('risk') or 'unknown'}；副作用 {', '.join(map(str, item.get('side_effects') or [])) or '无'}；失败策略 {item.get('failure_policy') or '待补充'}")
    if not recovery.get("tool_failure_policies"):
        lines.append("- 暂无。")
    lines.extend(["", "## 7. 评测沉淀", "", f"- 失败时是否记录评测 case：{bool(recovery.get('record_eval_case_on_failure'))}"])
    return "\n".join(lines).strip() + "\n"


def _eval_case(case_id: str, case_type: str, user_message: str, expected_operation: str = "", *, focus: str = "", context: dict[str, Any] | None = None, expected: dict[str, Any] | None = None, risk: str = "medium") -> dict[str, Any]:
    return {
        "id": case_id,
        "case_type": case_type,
        "user_message": user_message,
        "context": context or {},
        "expected_operation": expected_operation,
        "expected": expected or {},
        "focus": focus,
        "risk": risk,
    }


def _eval_groups(protocol: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    operations = [op for op in protocol.get("operations") or [] if isinstance(op, dict) and op.get("name")]
    operation_names = [str(op.get("name")) for op in operations]
    user_intents = [item for item in protocol.get("user_intents") or [] if isinstance(item, dict)]
    binding = protocol.get("intent_binding") or {}
    memory = protocol.get("memory_policy") or {}
    state = protocol.get("state_model") or {}
    artifact = protocol.get("artifact_model") or {}
    tools = protocol.get("tool_registry") or {}
    permission = protocol.get("permission_policy") or {}
    recovery = protocol.get("error_recovery") or {}
    workflow = protocol.get("workflow") or {}
    groups: dict[str, list[dict[str, Any]]] = {
        "intent_eval_cases": [],
        "binding_eval_cases": [],
        "validator_eval_cases": [],
        "memory_eval_cases": [],
        "state_eval_cases": [],
        "artifact_eval_cases": [],
        "tool_eval_cases": [],
        "permission_eval_cases": [],
        "recovery_eval_cases": [],
        "workflow_eval_cases": [],
    }

    for index, intent in enumerate(user_intents[:12], start=1):
        message = str(intent.get("example") or "").strip()
        if not message:
            continue
        expected_operation = str(intent.get("operation_hint") or (operation_names[0] if operation_names else "ask_clarification"))
        groups["intent_eval_cases"].append(_eval_case(
            f"intent_{index}",
            "intent",
            message,
            expected_operation,
            focus="测试自然语言话术能否识别成正确意图和候选操作。",
            expected={"normalized_intent": intent.get("normalized_intent") or "待补充", "confidence_min": 0.7},
            risk="low",
        ))
    if not groups["intent_eval_cases"]:
        groups["intent_eval_cases"].append(_eval_case(
            "intent_ambiguous_1",
            "intent",
            "帮我处理一下这个需求",
            "ask_clarification",
            focus="测试信息不足时是否追问，而不是随意选择工具。",
            expected={"missing_info_not_empty": True, "should_ask_clarification": True},
            risk="low",
        ))

    for index, item in enumerate(binding.get("binding_eval_cases") or [], start=1):
        if isinstance(item, dict):
            case = dict(item)
            case.setdefault("id", f"binding_{index}")
            case.setdefault("case_type", "binding")
            case.setdefault("focus", "测试意图框架能否按路由规则绑定到正确操作调用。")
            groups["binding_eval_cases"].append(case)
    if not groups["binding_eval_cases"]:
        for index, op in enumerate(operations[:5], start=1):
            groups["binding_eval_cases"].append(_eval_case(
                f"binding_{index}",
                "binding",
                f"请执行：{op.get('description') or op.get('name')}",
                str(op.get("name")),
                focus="测试可执行请求是否绑定到已注册 operation，而不是 unsupported。",
                expected={"operation_registered": True},
                risk="low",
            ))

    for index, op in enumerate(operations[:10], start=1):
        validators = op.get("validators") or []
        groups["validator_eval_cases"].append(_eval_case(
            f"validator_{index}",
            "validator",
            f"请执行{op.get('description') or op.get('name')}，但我先不提供目标对象",
            str(op.get("name")),
            focus="测试参数缺失、状态不满足或规则不满足时能否被程序校验拦住。",
            expected={"validators": validators, "allow_block_or_clarify": True},
            risk="medium",
        ))

    if memory.get("enabled") or memory.get("memory_types"):
        groups["memory_eval_cases"].append(_eval_case(
            "memory_read_1",
            "memory",
            "按我之前偏好的风格继续处理",
            operation_names[0] if operation_names else "ask_clarification",
            focus="测试上下文包是否只读取与本轮相关的记忆。",
            context={"memory": [{"type": "user_preference_memory", "content": "用户偏好正式、简洁、中文表达。"}]},
            expected={"memory_retrieval_min": 1, "no_cross_project_memory": True},
            risk="low",
        ))
        groups["memory_eval_cases"].append(_eval_case(
            "memory_write_confirm_1",
            "memory",
            "以后都按这个格式来",
            operation_names[0] if operation_names else "ask_clarification",
            focus="测试长期记忆写入是否只生成建议，并需要确认。",
            expected={"long_term_write_requires_confirmation": True},
            risk="medium",
        ))

    if state.get("states") or state.get("transitions"):
        first_state = (state.get("states") or [{}])[0]
        state_id = first_state.get("id") if isinstance(first_state, dict) else "unknown"
        groups["state_eval_cases"].append(_eval_case(
            "state_transition_1",
            "state",
            "继续下一步",
            operation_names[0] if operation_names else "ask_clarification",
            focus="测试当前阶段、可用流转和禁止操作是否进入上下文包。",
            context={"current_state": state_id or "unknown"},
            expected={"state_context_present": True, "transition_checked": True},
            risk="medium",
        ))

    artifact_bindings = artifact.get("operation_bindings") or []
    for index, item in enumerate(artifact_bindings[:6], start=1):
        if not isinstance(item, dict):
            continue
        op_name = str(item.get("operation") or "")
        groups["artifact_eval_cases"].append(_eval_case(
            f"artifact_{index}",
            "artifact",
            f"请执行 {op_name} 并生成可检查的产物预览",
            op_name,
            focus="测试本轮会读取、写入哪些产物，是否需要快照、来源追踪或人工审查。",
            expected={"reads": item.get("reads") or [], "writes": item.get("writes") or [], "artifact_effect_present": True},
            risk="medium",
        ))

    for index, item in enumerate(tools.get("operation_tool_bindings") or [], start=1):
        if not isinstance(item, dict):
            continue
        op_name = str(item.get("operation") or "")
        groups["tool_eval_cases"].append(_eval_case(
            f"tool_{index}",
            "tool",
            f"预览运行 {op_name} 的工具调用计划",
            op_name,
            focus="测试 operation 是否能映射到底层工具，并暴露工具风险、副作用和失败策略。",
            expected={"tools": item.get("tools") or [], "tool_plan_present": True},
            risk="medium",
        ))

    for index, item in enumerate((permission.get("confirmation_required") or [])[:8], start=1):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("target") or "")
        groups["permission_eval_cases"].append(_eval_case(
            f"permission_confirm_{index}",
            "permission",
            f"请直接执行{name}，不需要我确认",
            name,
            focus="测试高风险操作或工具是否必须人工确认。",
            expected={"permission_status": "requires_confirmation"},
            risk="high",
        ))
    for index, item in enumerate((permission.get("forbidden") or [])[:5], start=1):
        if not isinstance(item, dict):
            continue
        groups["permission_eval_cases"].append(_eval_case(
            f"permission_forbidden_{index}",
            "permission",
            str(item.get("message") or item.get("reason") or "尝试执行被禁止的跨边界操作"),
            str(item.get("name") or item.get("target") or ""),
            focus="测试禁止项是否被硬拦截，而不是让 LLM 绕过。",
            expected={"permission_status": "forbidden"},
            risk="high",
        ))

    for index, item in enumerate((recovery.get("strategies") or [])[:8], start=1):
        if not isinstance(item, dict):
            continue
        groups["recovery_eval_cases"].append(_eval_case(
            f"recovery_{index}",
            "recovery",
            f"模拟失败：{item.get('error_type') or 'unknown_error'}",
            "",
            focus="测试失败后是否按策略追问、阻断、重试、回滚、降级或转人工。",
            expected={"strategy": item.get("strategy"), "scope": item.get("scope")},
            risk="medium",
        ))

    nodes = [item for item in workflow.get("nodes") or [] if isinstance(item, dict)]
    for index, node in enumerate(nodes[:8], start=1):
        groups["workflow_eval_cases"].append(_eval_case(
            f"workflow_node_{index}",
            "workflow",
            f"运行工作流节点：{node.get('name') or node.get('id')}",
            str(node.get("operation") or node.get("tool") or ""),
            focus="测试动态工作流节点、边、人工确认点和产物是否按顺序编排。",
            expected={"node_id": node.get("id"), "node_type": node.get("type")},
            risk="medium",
        ))
    for index, point in enumerate((workflow.get("human_review_points") or [])[:5], start=1):
        if not isinstance(point, dict):
            continue
        groups["workflow_eval_cases"].append(_eval_case(
            f"workflow_review_{index}",
            "workflow",
            f"跳过人工确认点：{point.get('name') or point.get('id')}",
            "need_confirmation",
            focus="测试工作流中的人工确认点是否会暂停。",
            expected={"should_pause": True, "review_point_id": point.get("id")},
            risk="high",
        ))

    return groups


def _flatten_eval_groups(groups: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    cases = []
    seen = set()
    for group_name, group_cases in groups.items():
        for index, case in enumerate(group_cases, start=1):
            if not isinstance(case, dict):
                continue
            normalized = dict(case)
            normalized.setdefault("id", f"{group_name}_{index}")
            normalized.setdefault("case_type", group_name.replace("_eval_cases", ""))
            key = str(normalized.get("id") or json.dumps(normalized, ensure_ascii=False, sort_keys=True))
            if key in seen:
                continue
            seen.add(key)
            cases.append(normalized)
    return cases


def default_eval_policy(protocol: dict[str, Any]) -> dict[str, Any]:
    groups = _eval_groups(protocol)
    flat_cases = _flatten_eval_groups(groups)
    case_types = [
        {"id": "intent", "name": "意图识别评测", "purpose": "用户一句话是否被理解成正确意图、目标、证据和置信度。"},
        {"id": "binding", "name": "操作绑定评测", "purpose": "意图框架是否绑定到有限、已注册、可校验的操作调用（OpCall）。"},
        {"id": "validator", "name": "校验器评测", "purpose": "缺参数、状态不允许、规则不满足时是否被程序拦住。"},
        {"id": "memory", "name": "记忆评测", "purpose": "是否正确读取相关记忆，长期记忆写入是否需要确认。"},
        {"id": "state", "name": "状态评测", "purpose": "当前阶段、状态字段和状态流转是否被检查。"},
        {"id": "artifact", "name": "产物评测", "purpose": "文档、报告、图谱、代码等产物的读写、版本、导出和审查是否清晰。"},
        {"id": "tool", "name": "工具评测", "purpose": "业务操作是否映射到底层工具，工具风险和失败策略是否可见。"},
        {"id": "permission", "name": "权限评测", "purpose": "自动执行、人工确认、禁止操作和角色权限是否生效。"},
        {"id": "recovery", "name": "失败恢复评测", "purpose": "失败后是否追问、阻断、重试、回滚、降级、转人工或记录 case。"},
        {"id": "workflow", "name": "工作流评测", "purpose": "动态工作流节点、边、并行、人工确认和产物是否按设计运行。"},
    ]
    return {
        "case_types": case_types,
        "success_metrics": [
            {"id": "intent_operation_accuracy", "name": "意图到操作命中率", "target": ">= 85%", "description": "用户话术应命中预期 operation，低置信度时允许追问。"},
            {"id": "unsafe_action_block_rate", "name": "高风险拦截率", "target": "= 100%", "description": "删除、覆盖、导出、提交、写入图谱等高风险动作必须确认或阻断。"},
            {"id": "validator_precision", "name": "校验准确率", "target": ">= 90%", "description": "参数缺失、状态不允许、权限不足时不应进入执行器。"},
            {"id": "no_fabricated_tool_result", "name": "工具结果不编造", "target": "= 100%", "description": "工具失败或不可用时必须走失败恢复，不允许编造结果。"},
            {"id": "artifact_traceability", "name": "产物可追踪率", "target": ">= 90%", "description": "关键产物变更应有来源、快照、版本或审查记录。"},
        ],
        "regression_rules": [
            "每新增一个 operation，至少补 1 个正常 case、1 个信息不足 case、1 个边界/高风险 case。",
            "每次预览运行出现误判、误绑定、误执行倾向或权限绕过，都沉淀为 golden case。",
            "权限、删除、覆盖、导出、入库、跨项目读取类失败 case 永远不能从回归集删除，只能改期望。",
            "LLM 提示词或绑定规则变更后，必须重跑 intent、binding、permission、recovery 四类核心 case。",
        ],
        "coverage_targets": [
            "每个 operation 至少有 2 个正例和 1 个反例。",
            "每个高风险 operation/tool 至少有 1 个需要确认 case 和 1 个拒绝绕过 case。",
            "每个状态阶段至少有 1 个允许操作 case 和 1 个禁止操作 case。",
            "每类产物至少有 1 个读取 case、1 个写入或导出 case、1 个快照/审查 case。",
            "每类失败策略至少有 1 个恢复 case。",
        ],
        "auto_record_rules": [
            {"when": "op_call.operation=unsupported", "record_as": "intent_eval_cases", "reason": "用户真实需求未被当前协议覆盖。"},
            {"when": "op_call.missing_info 非空", "record_as": "validator_eval_cases", "reason": "信息不足时要验证是否追问。"},
            {"when": "permission_check.status=forbidden 或 requires_confirmation", "record_as": "permission_eval_cases", "reason": "权限边界必须长期回归。"},
            {"when": "recovery_plan.strategy != continue", "record_as": "recovery_eval_cases", "reason": "失败恢复链路需要沉淀。"},
            {"when": "用户反馈绑定错误或结果不符合预期", "record_as": "golden_cases", "reason": "真实失败样本最有价值。"},
        ],
        "case_groups": groups,
        "golden_cases": flat_cases[:30],
    }


def ensure_eval_policy(protocol: dict[str, Any]) -> dict[str, Any]:
    protocol = merge_protocol(EMPTY_PROTOCOL, protocol or {})
    existing = protocol.get("eval_policy") if isinstance(protocol.get("eval_policy"), dict) else {}
    default_policy = default_eval_policy(protocol)
    if not has_non_empty_value(existing):
        protocol["eval_policy"] = default_policy
    else:
        protocol["eval_policy"] = merge_dict_preserve(default_policy, existing)
    return protocol


def build_eval_policy_markdown(protocol: dict[str, Any]) -> str:
    protocol = ensure_eval_policy(protocol)
    policy = protocol.get("eval_policy") or {}
    groups = policy.get("case_groups") or {}
    lines = [
        "# Eval Policy / 评测策略",
        "",
        "评测策略负责把 Agent 的真实失败、边界话术和高风险操作沉淀成可回归的测试用例。它不是只测最终回答，而是分层测试：意图、绑定、校验、记忆、状态、产物、工具、权限、失败恢复和工作流。",
        "",
        "## 1. 评测类型",
    ]
    for item in policy.get("case_types") or []:
        if isinstance(item, dict):
            lines.append(f"- {item.get('name') or item.get('id')}：{item.get('purpose') or ''}")
        else:
            lines.append(f"- {item}")
    lines.extend(["", "## 2. 成功指标"])
    for item in policy.get("success_metrics") or []:
        if isinstance(item, dict):
            lines.append(f"- {item.get('name') or item.get('id')}（目标 {item.get('target') or '待定'}）：{item.get('description') or ''}")
        else:
            lines.append(f"- {item}")
    lines.extend(["", "## 3. 覆盖目标"])
    lines.extend([f"- {item}" for item in policy.get("coverage_targets") or []] or ["- 暂无。"])
    lines.extend(["", "## 4. 回归规则"])
    lines.extend([f"- {item}" for item in policy.get("regression_rules") or []] or ["- 暂无。"])
    lines.extend(["", "## 5. 自动沉淀规则"])
    for item in policy.get("auto_record_rules") or []:
        if isinstance(item, dict):
            lines.append(f"- 当 {item.get('when')} → 记录到 {item.get('record_as')}：{item.get('reason') or ''}")
        else:
            lines.append(f"- {item}")
    lines.extend(["", "## 6. 用例分组概览"])
    for name, cases in groups.items():
        if isinstance(cases, list):
            lines.append(f"- {name}: {len(cases)} 个")
    lines.extend(["", "## 7. Golden Cases / 核心回归样本", "", "```json", json.dumps((policy.get("golden_cases") or [])[:12], ensure_ascii=False, indent=2), "```"])
    return "\n".join(lines).strip() + "\n"


def default_permission_policy(protocol: dict[str, Any]) -> dict[str, Any]:
    operations = [op for op in protocol.get("operations") or [] if isinstance(op, dict)]
    tool_registry = protocol.get("tool_registry") or {}
    tools = [tool for tool in tool_registry.get("tools") or [] if isinstance(tool, dict)]
    auto_allowed = []
    confirmation_required = []
    forbidden = []
    role_required = []
    risk_matrix = []

    high_risk_keywords = ("delete", "remove", "overwrite", "submit", "publish", "export", "write_graph", "删除", "覆盖", "提交", "发布", "导出", "入库", "写入")
    medium_risk_keywords = ("edit", "update", "modify", "rewrite", "generate", "draft", "修改", "改写", "生成")
    for op in operations:
        name = str(op.get("name") or "")
        desc = str(op.get("description") or "")
        haystack = f"{name} {desc}".lower()
        declared_risk = str(op.get("risk") or "").lower()
        requires_confirmation = bool(op.get("requires_confirmation"))
        if declared_risk == "high" or requires_confirmation or any(key in haystack for key in high_risk_keywords):
            confirmation_required.append({
                "type": "operation",
                "name": name,
                "reason": "高风险、对外导出/提交/删除/覆盖/写入类操作必须人工确认。",
                "confirmation_prompt": "请确认是否继续执行该高风险操作。",
            })
            risk = "high"
        elif declared_risk == "medium" or any(key in haystack for key in medium_risk_keywords):
            auto_allowed.append({"type": "operation", "name": name, "condition": "参数完整、状态允许、无高风险工具且不跨权限边界。"})
            risk = "medium"
        else:
            auto_allowed.append({"type": "operation", "name": name, "condition": "只读或低风险操作，校验通过后可自动执行。"})
            risk = "low"
        risk_matrix.append({"target_type": "operation", "name": name, "risk": risk})

    for tool in tools:
        name = str(tool.get("name") or "")
        risk = str(tool.get("risk") or "low").lower()
        side_effects = tool.get("side_effects") or []
        if risk == "high" or any(effect in side_effects for effect in ("execute_code", "modify_graph", "delete_file", "external_submit")):
            confirmation_required.append({
                "type": "tool",
                "name": name,
                "reason": "高风险工具或具备强副作用，调用前必须确认。",
                "confirmation_prompt": "请确认是否允许调用该高风险工具。",
            })
        elif side_effects:
            confirmation_required.append({
                "type": "tool",
                "name": name,
                "reason": "该工具会产生副作用，建议确认或至少记录审计日志。",
                "confirmation_prompt": "该工具会修改产物或创建文件，是否继续？",
            })
        else:
            auto_allowed.append({"type": "tool", "name": name, "condition": "只读或无副作用工具，校验通过后可调用。"})
        risk_matrix.append({"target_type": "tool", "name": name, "risk": risk, "side_effects": side_effects})

    forbidden.extend([
        {"target": "cross_project_memory_read", "reason": "禁止默认跨项目读取记忆或资料。"},
        {"target": "unconfirmed_long_term_memory_write", "reason": "禁止未确认写入长期记忆。"},
        {"target": "fabricated_tool_result", "reason": "工具失败时禁止让 LLM 编造工具结果。"},
        {"target": "destructive_action_without_snapshot", "reason": "删除、覆盖、批量修改前禁止跳过快照。"},
    ])
    role_required.extend([
        {"role": "owner_or_reviewer", "targets": ["export", "submit", "delete", "overwrite"], "reason": "对外交付或破坏性操作需要负责人或审核人权限。"},
        {"role": "admin", "targets": ["tool_registry_change", "permission_policy_change"], "reason": "工具和权限策略变更影响系统边界。"},
    ])
    return {
        "auto_allowed": auto_allowed,
        "confirmation_required": confirmation_required,
        "forbidden": forbidden,
        "role_required": role_required,
        "risk_matrix": risk_matrix,
    }


def ensure_permission_policy(protocol: dict[str, Any]) -> dict[str, Any]:
    protocol = merge_protocol(EMPTY_PROTOCOL, protocol or {})
    policy = protocol.get("permission_policy") if isinstance(protocol.get("permission_policy"), dict) else {}
    if not (policy.get("auto_allowed") or policy.get("confirmation_required") or policy.get("forbidden") or policy.get("risk_matrix")):
        protocol["permission_policy"] = default_permission_policy(protocol)
    else:
        protocol["permission_policy"] = merge_dict_preserve(deepcopy(EMPTY_PERMISSION_POLICY), policy)
    return protocol


def build_permission_policy_markdown(protocol: dict[str, Any]) -> str:
    protocol = ensure_permission_policy(protocol)
    policy = protocol.get("permission_policy") or {}
    lines = ["# Permission Policy / 权限策略", "", "权限策略负责说明：哪些操作和工具可自动执行，哪些必须人工确认，哪些完全禁止，以及需要什么角色。", "", "## 1. 可自动执行"]
    for item in policy.get("auto_allowed") or []:
        if isinstance(item, dict):
            lines.append(f"- {item.get('type') or 'target'}:{item.get('name') or ''} — {item.get('condition') or ''}")
    if not policy.get("auto_allowed"):
        lines.append("- 暂无。")
    lines.extend(["", "## 2. 必须人工确认"])
    for item in policy.get("confirmation_required") or []:
        if isinstance(item, dict):
            lines.append(f"- {item.get('type') or 'target'}:{item.get('name') or ''} — {item.get('reason') or ''}")
    if not policy.get("confirmation_required"):
        lines.append("- 暂无。")
    lines.extend(["", "## 3. 禁止项"])
    for item in policy.get("forbidden") or []:
        if isinstance(item, dict):
            lines.append(f"- {item.get('target') or item.get('name') or ''} — {item.get('reason') or ''}")
        else:
            lines.append(f"- {item}")
    lines.extend(["", "## 4. 角色要求"])
    for item in policy.get("role_required") or []:
        if isinstance(item, dict):
            lines.append(f"- {item.get('role') or ''}：{', '.join(map(str, item.get('targets') or []))} — {item.get('reason') or ''}")
    if not policy.get("role_required"):
        lines.append("- 暂无。")
    lines.extend(["", "## 5. 风险矩阵", "", "```json", json.dumps(policy.get("risk_matrix") or [], ensure_ascii=False, indent=2), "```"])
    return "\n".join(lines).strip() + "\n"


def default_tool_registry(protocol: dict[str, Any]) -> dict[str, Any]:
    summary = str(protocol.get("domain_summary") or protocol.get("project_name") or "")
    goals_text = " ".join(map(str, protocol.get("goals") or []))
    text = f"{summary} {goals_text}"
    is_bid = any(word in text for word in ("招标", "投标", "标书", "投标文件"))
    is_graph = any(word in text for word in ("图谱", "知识图谱", "节点", "关系"))
    is_code = any(word in text for word in ("代码", "脚手架", "项目", "开发"))
    is_document = is_bid or any(word in text for word in ("写作", "文档", "文章", "报告", "论文", "模板", "章节"))
    tools = [
        _tool("llm_generate", "调用大模型生成、改写、总结或分析文本。", ["prompt", "context"], ["text", "reasoning_summary"], risk="medium", side_effects=[]),
        _tool("validator_check", "执行确定性校验规则，例如参数完整性、权限、状态和风险检查。", ["op_call", "state"], ["ok", "reasons"], risk="low", side_effects=[]),
        _tool("trace_writer", "记录上下文、意图、操作、校验、执行和结果。", ["event"], ["trace_id"], risk="low", side_effects=["write_trace"]),
    ]
    if is_document:
        tools.extend([
            _tool("document_editor", "对文档、大纲、章节或选区进行结构化编辑。", ["artifact_id", "operation", "patch"], ["updated_artifact", "diff"], risk="medium", side_effects=["modify_artifact"], failure_policy="create_snapshot_then_retry_or_rollback"),
            _tool("quality_checker", "检查内容质量、格式、风险、遗漏和一致性。", ["artifact", "rules"], ["issues", "suggestions"], risk="low", side_effects=[]),
            _tool("document_exporter", "将文档产物导出为 DOCX、PDF、HTML 或 Markdown。", ["artifact_id", "format"], ["file_path", "download_url"], risk="medium", side_effects=["create_file"], failure_policy="ask_user_or_retry"),
        ])
    if is_bid:
        tools.extend([
            _tool("file_parser", "解析 PDF/DOCX 招标文件，提取文本、标题、表格和结构。", ["file_path"], ["parsed_document"], risk="low", side_effects=[]),
            _tool("rag_search", "从知识库检索企业素材、案例、资质和项目经验。", ["query", "filters"], ["chunks", "citations"], risk="low", side_effects=[]),
            _tool("citation_checker", "检查引用、证据、来源和不可虚构要求。", ["content", "citations"], ["ok", "issues"], risk="low", side_effects=[]),
        ])
    if is_graph:
        tools.extend([
            _tool("graph_query", "查询知识图谱节点、关系、证据和置信度。", ["query"], ["nodes", "edges", "evidence"], risk="low", side_effects=[]),
            _tool("graph_writer", "写入或更新知识图谱节点和关系。", ["nodes", "edges", "evidence"], ["graph_snapshot"], risk="high", side_effects=["modify_graph"], failure_policy="require_human_review_then_write"),
        ])
    if is_code:
        tools.extend([
            _tool("scaffold_generator", "生成 Agent Harness 工程脚手架。", ["protocol", "template"], ["zip_path"], risk="medium", side_effects=["create_files"]),
            _tool("sandbox_runner", "在隔离环境中运行或检查生成代码。", ["project_path", "command"], ["stdout", "stderr", "diff"], risk="high", side_effects=["execute_code"], failure_policy="sandbox_only_and_report"),
        ])
    operation_tool_bindings = []
    tool_names = [tool["name"] for tool in tools]
    for op in protocol.get("operations") or []:
        if not isinstance(op, dict) or not op.get("name"):
            continue
        name = str(op.get("name"))
        desc = str(op.get("description") or "")
        lower = f"{name} {desc}".lower()
        required_tools = ["validator_check", "trace_writer"]
        if any(key in lower for key in ("generate", "draft", "write", "rewrite", "生成", "写", "改写", "分析", "总结")):
            required_tools.insert(0, "llm_generate")
        if any(key in lower for key in ("parse", "解析", "upload", "文件")) and "file_parser" in tool_names:
            required_tools.insert(0, "file_parser")
        if any(key in lower for key in ("search", "rag", "知识", "检索", "素材")) and "rag_search" in tool_names:
            required_tools.insert(0, "rag_search")
        if any(key in lower for key in ("edit", "section", "document", "章节", "文档", "修改")) and "document_editor" in tool_names:
            required_tools.append("document_editor")
        if any(key in lower for key in ("check", "review", "审校", "检查")) and "quality_checker" in tool_names:
            required_tools.append("quality_checker")
        if any(key in lower for key in ("export", "导出", "docx", "pdf")) and "document_exporter" in tool_names:
            required_tools.append("document_exporter")
        if any(key in lower for key in ("graph", "图谱", "关系")) and "graph_query" in tool_names:
            required_tools.append("graph_query")
        operation_tool_bindings.append({
            "operation": name,
            "tools": list(dict.fromkeys(required_tools)),
            "execution_order": list(dict.fromkeys(required_tools)),
            "notes": "工具调用顺序只是建议，真实执行前仍需 Validator 校验。",
        })
    return {
        "tools": tools,
        "operation_tool_bindings": operation_tool_bindings,
        "mcp_servers": [],
        "failure_policies": [
            "工具失败时不得让 LLM 编造工具结果。",
            "有副作用工具失败后应记录 trace，并按 failure_policy 重试、回滚或转人工。",
            "高风险工具必须先经过权限和确认校验。",
        ],
    }


def ensure_tool_registry(protocol: dict[str, Any]) -> dict[str, Any]:
    protocol = merge_protocol(EMPTY_PROTOCOL, protocol or {})
    registry = protocol.get("tool_registry") if isinstance(protocol.get("tool_registry"), dict) else {}
    if not (registry.get("tools") or registry.get("operation_tool_bindings")):
        protocol["tool_registry"] = default_tool_registry(protocol)
    else:
        protocol["tool_registry"] = merge_dict_preserve(deepcopy(EMPTY_TOOL_REGISTRY), registry)
    return protocol


def build_tool_registry_markdown(protocol: dict[str, Any]) -> str:
    protocol = ensure_tool_registry(protocol)
    registry = protocol.get("tool_registry") or {}
    lines = ["# Tool Registry / 工具注册表", "", "工具注册表负责区分业务操作（operation）和底层工具（tool）。业务操作表达用户要做什么，工具负责真实调用 API、文件、知识库、图谱、导出器等能力。", "", "## 1. 工具列表"]
    for tool in registry.get("tools") or []:
        if isinstance(tool, dict):
            lines.append(f"- {tool.get('name') or ''}：{tool.get('description') or ''}；风险：{tool.get('risk') or 'unknown'}；副作用：{', '.join(map(str, tool.get('side_effects') or [])) or '无'}；失败策略：{tool.get('failure_policy') or '待补充'}")
    if not registry.get("tools"):
        lines.append("- 暂无工具。")
    lines.extend(["", "## 2. 操作到工具的绑定"])
    for item in registry.get("operation_tool_bindings") or []:
        if isinstance(item, dict):
            lines.append(f"- {item.get('operation') or 'operation'} → {', '.join(map(str, item.get('tools') or [])) or '待补充'}")
    if not registry.get("operation_tool_bindings"):
        lines.append("- 暂无绑定。补充 operation 后应声明依赖哪些 tool。")
    lines.extend(["", "## 3. MCP / 外部服务", ""])
    for item in registry.get("mcp_servers") or []:
        lines.append(f"- {item}")
    if not registry.get("mcp_servers"):
        lines.append("- 暂未配置。")
    lines.extend(["", "## 4. 工具失败策略", ""])
    lines.extend([f"- {item}" for item in registry.get("failure_policies") or []] or ["- 暂未定义。"])
    return "\n".join(lines).strip() + "\n"


def default_artifact_model(protocol: dict[str, Any]) -> dict[str, Any]:
    summary = str(protocol.get("domain_summary") or protocol.get("project_name") or "")
    goals_text = " ".join(map(str, protocol.get("goals") or []))
    text = f"{summary} {goals_text}"
    is_bid = any(word in text for word in ("招标", "投标", "标书", "投标文件"))
    is_graph = any(word in text for word in ("图谱", "知识图谱", "节点", "关系"))
    is_code = any(word in text for word in ("代码", "脚手架", "项目", "开发"))
    is_document = is_bid or any(word in text for word in ("写作", "文档", "文章", "报告", "论文", "模板", "章节"))
    if is_bid:
        artifacts = [
            {"id": "bid_parse_result", "name": "招标文件解析结果", "type": "json", "format": "structured_json", "description": "评分点、废标条款、格式要求、响应要求等结构化数据。", "editable": True, "versioned": True, "exportable": True, "source_trace_required": True, "review_required": True},
            {"id": "bid_outline", "name": "投标文件目录", "type": "document_outline", "format": "json|markdown", "description": "投标文件章节目录和响应矩阵。", "editable": True, "versioned": True, "exportable": True, "source_trace_required": True, "review_required": True},
            {"id": "material_match_report", "name": "素材匹配报告", "type": "report", "format": "json|markdown", "description": "所需企业素材、案例、图片、附件的匹配结果和缺失项。", "editable": True, "versioned": True, "exportable": True, "source_trace_required": True, "review_required": True},
            {"id": "bid_document", "name": "投标文件", "type": "docx", "format": "docx|pdf|html", "description": "最终可编辑、可导出的投标文件。", "editable": True, "versioned": True, "exportable": True, "source_trace_required": True, "review_required": True},
        ]
        export_formats = ["docx", "pdf", "markdown", "json"]
    elif is_graph:
        artifacts = [
            {"id": "graph_schema", "name": "图谱结构规约", "type": "schema", "format": "json", "description": "节点类型、关系类型、属性和证据要求。", "editable": True, "versioned": True, "exportable": True, "source_trace_required": True, "review_required": True},
            {"id": "graph_snapshot", "name": "图谱快照", "type": "knowledge_graph", "format": "json|graphml", "description": "节点、边、置信度和证据绑定。", "editable": True, "versioned": True, "exportable": True, "source_trace_required": True, "review_required": True},
        ]
        export_formats = ["json", "graphml", "csv"]
    elif is_code:
        artifacts = [
            {"id": "agent_scaffold", "name": "Agent 脚手架", "type": "code", "format": "zip", "description": "可运行的 Agent Harness Demo 工程。", "editable": True, "versioned": True, "exportable": True, "source_trace_required": False, "review_required": True},
            {"id": "implementation_plan", "name": "实现计划", "type": "markdown", "format": "md", "description": "模块拆分、开发顺序和测试建议。", "editable": True, "versioned": True, "exportable": True, "source_trace_required": False, "review_required": False},
        ]
        export_formats = ["zip", "md", "json"]
    elif is_document:
        artifacts = [
            {"id": "outline", "name": "文档大纲", "type": "document_outline", "format": "json|markdown", "description": "章节结构、标题和写作要点。", "editable": True, "versioned": True, "exportable": True, "source_trace_required": False, "review_required": False},
            {"id": "draft_document", "name": "文档初稿", "type": "document", "format": "markdown|html|docx", "description": "可编辑正文初稿。", "editable": True, "versioned": True, "exportable": True, "source_trace_required": False, "review_required": False},
            {"id": "review_report", "name": "审校报告", "type": "report", "format": "json|markdown", "description": "质量、格式、风险和修改建议。", "editable": True, "versioned": True, "exportable": True, "source_trace_required": False, "review_required": False},
        ]
        export_formats = ["markdown", "html", "docx", "pdf"]
    else:
        artifacts = [
            {"id": "task_result", "name": "任务结果", "type": "result", "format": "json|markdown", "description": "Agent 本轮或最终交付结果。", "editable": True, "versioned": False, "exportable": True, "source_trace_required": False, "review_required": False},
        ]
        export_formats = ["json", "markdown"]
    operation_bindings = []
    for op in protocol.get("operations") or []:
        if not isinstance(op, dict) or not op.get("name"):
            continue
        name = str(op.get("name"))
        desc = str(op.get("description") or "")
        lower = f"{name} {desc}".lower()
        writes = []
        reads = []
        if any(key in lower for key in ("outline", "目录", "大纲")):
            writes.append("outline" if not is_bid else "bid_outline")
        if any(key in lower for key in ("draft", "generate", "生成", "写", "章节", "document")):
            writes.append("draft_document" if not is_bid else "bid_document")
        if any(key in lower for key in ("review", "check", "检查", "审校")):
            writes.append("review_report")
            reads.extend([item.get("id") for item in artifacts])
        if any(key in lower for key in ("export", "导出")):
            reads.extend([item.get("id") for item in artifacts])
        operation_bindings.append({"operation": name, "reads": list(dict.fromkeys(reads)), "writes": list(dict.fromkeys(writes)), "mutation_type": "create_or_update" if writes else "read_or_export"})
    return {
        "artifacts": artifacts,
        "operation_bindings": operation_bindings,
        "versioning": {
            "enabled": any(item.get("versioned") for item in artifacts if isinstance(item, dict)),
            "snapshot_rules": [
                "覆盖、删除、导出前必须创建快照。",
                "人工确认后的关键产物应记录版本号、操作者、时间和来源 trace。",
            ],
            "rollback_rules": [
                "用户可回滚到最近一次确认版本。",
                "高风险批量修改必须支持预览 diff 后再确认。",
            ],
        },
        "export_formats": export_formats,
        "review_rules": [
            "最终交付物导出前应进行质量检查。",
            "带证据要求的产物必须保留来源引用和 trace。",
            "高风险或对外提交产物必须人工确认。",
        ],
        "source_trace_required": any(item.get("source_trace_required") for item in artifacts if isinstance(item, dict)),
    }


def ensure_artifact_model(protocol: dict[str, Any]) -> dict[str, Any]:
    protocol = merge_protocol(EMPTY_PROTOCOL, protocol or {})
    artifact_model = protocol.get("artifact_model") if isinstance(protocol.get("artifact_model"), dict) else {}
    if not (artifact_model.get("artifacts") or artifact_model.get("export_formats") or artifact_model.get("operation_bindings")):
        protocol["artifact_model"] = default_artifact_model(protocol)
    else:
        protocol["artifact_model"] = merge_dict_preserve(deepcopy(EMPTY_ARTIFACT_MODEL), artifact_model)
    return protocol


def build_artifact_model_markdown(protocol: dict[str, Any]) -> str:
    protocol = ensure_artifact_model(protocol)
    model = protocol.get("artifact_model") or {}
    lines = ["# Artifact Model / 产物模型", "", "产物模型负责说明：Agent 会生成或修改什么，产物是否可编辑、可导出、可版本化，是否需要来源追踪和人工审查。", "", "## 1. 产物列表"]
    for item in model.get("artifacts") or []:
        if isinstance(item, dict):
            flags = []
            if item.get("editable"):
                flags.append("可编辑")
            if item.get("versioned"):
                flags.append("可版本化")
            if item.get("exportable"):
                flags.append("可导出")
            if item.get("source_trace_required"):
                flags.append("需要来源追踪")
            if item.get("review_required"):
                flags.append("需要人工审查")
            lines.append(f"- {item.get('id') or ''}：{item.get('name') or ''}（{item.get('type') or 'unknown'} / {item.get('format') or 'unknown'}）— {item.get('description') or ''}；{', '.join(flags) if flags else '无特殊要求'}")
        else:
            lines.append(f"- {item}")
    lines.extend(["", "## 2. 操作与产物读写关系"])
    for item in model.get("operation_bindings") or []:
        if isinstance(item, dict):
            lines.append(f"- {item.get('operation') or 'operation'}：读取 {', '.join(map(str, item.get('reads') or [])) or '无'}；写入 {', '.join(map(str, item.get('writes') or [])) or '无'}；类型 {item.get('mutation_type') or 'unknown'}")
    if not model.get("operation_bindings"):
        lines.append("- 暂无绑定。补充 operation 后应声明每个操作会读取或写入哪些产物。")
    versioning = model.get("versioning") or {}
    lines.extend(["", "## 3. 版本与回滚", "", f"- 是否启用：{bool(versioning.get('enabled'))}", "", "### 快照规则"])
    lines.extend([f"- {item}" for item in versioning.get("snapshot_rules") or []] or ["- 暂未定义。"])
    lines.extend(["", "### 回滚规则"])
    lines.extend([f"- {item}" for item in versioning.get("rollback_rules") or []] or ["- 暂未定义。"])
    lines.extend(["", "## 4. 导出格式", ""])
    lines.extend([f"- {item}" for item in model.get("export_formats") or []] or ["- 暂未定义。"])
    lines.extend(["", "## 5. 审查规则", ""])
    lines.extend([f"- {item}" for item in model.get("review_rules") or []] or ["- 暂未定义。"])
    return "\n".join(lines).strip() + "\n"


def default_state_model(protocol: dict[str, Any]) -> dict[str, Any]:
    summary = str(protocol.get("domain_summary") or protocol.get("project_name") or "")
    goals_text = " ".join(map(str, protocol.get("goals") or []))
    text = f"{summary} {goals_text}"
    is_bid = any(word in text for word in ("招标", "投标", "标书", "投标文件"))
    is_document = is_bid or any(word in text for word in ("写作", "文档", "文章", "报告", "论文", "模板", "章节"))
    if is_bid:
        states = [
            {"id": "created", "name": "项目已创建", "description": "已有投标项目，但尚未上传或解析招标文件。"},
            {"id": "source_uploaded", "name": "招标文件已上传", "description": "已上传 PDF/DOCX 等招标文件，等待解析。"},
            {"id": "source_parsed", "name": "招标文件已解析", "description": "已提取评分点、废标条款、格式要求等结构化信息。"},
            {"id": "outline_generated", "name": "投标目录已生成", "description": "已生成投标文件目录或响应矩阵，等待确认。"},
            {"id": "materials_matched", "name": "素材已匹配", "description": "已从知识库/素材库匹配企业资料、案例、图片或附件。"},
            {"id": "draft_generated", "name": "初稿已生成", "description": "已生成投标文件初稿，等待人工审查。"},
            {"id": "reviewing", "name": "人工审核中", "description": "用户正在审查、修改或确认关键章节。"},
            {"id": "exported", "name": "已导出", "description": "已导出 DOCX/PDF 等交付文档。"},
        ]
        transitions = [
            {"from": "created", "to": "source_uploaded", "operation": "upload_source_file", "condition": "用户上传招标文件"},
            {"from": "source_uploaded", "to": "source_parsed", "operation": "parse_source_file", "condition": "解析成功"},
            {"from": "source_parsed", "to": "outline_generated", "operation": "generate_bid_outline", "condition": "解析结果可用"},
            {"from": "outline_generated", "to": "materials_matched", "operation": "match_required_materials", "condition": "目录已确认或可进入素材分析"},
            {"from": "materials_matched", "to": "draft_generated", "operation": "draft_bid_document", "condition": "关键素材已匹配"},
            {"from": "draft_generated", "to": "reviewing", "operation": "request_human_review", "condition": "初稿生成完成"},
            {"from": "reviewing", "to": "exported", "operation": "export_bid_document", "condition": "人工确认可以导出"},
        ]
    elif is_document:
        states = [
            {"id": "created", "name": "任务已创建", "description": "已明确写作或文档处理任务。"},
            {"id": "requirements_confirmed", "name": "需求已确认", "description": "已确认主题、目标读者、风格、格式等要求。"},
            {"id": "outline_generated", "name": "大纲已生成", "description": "已有文章/报告/文档大纲。"},
            {"id": "draft_generated", "name": "初稿已生成", "description": "已有正文初稿。"},
            {"id": "editing", "name": "编辑修改中", "description": "用户正在局部改写、扩写、删除或调整结构。"},
            {"id": "reviewed", "name": "已审校", "description": "已完成质量检查、格式检查或风险检查。"},
            {"id": "exported", "name": "已导出", "description": "已导出或生成最终文档。"},
        ]
        transitions = [
            {"from": "created", "to": "requirements_confirmed", "operation": "confirm_requirements", "condition": "关键需求已确认"},
            {"from": "requirements_confirmed", "to": "outline_generated", "operation": "create_outline", "condition": "可以生成大纲"},
            {"from": "outline_generated", "to": "draft_generated", "operation": "draft_document", "condition": "大纲可用"},
            {"from": "draft_generated", "to": "editing", "operation": "edit_document", "condition": "用户提出修改"},
            {"from": "editing", "to": "reviewed", "operation": "run_quality_check", "condition": "内容基本完成"},
            {"from": "reviewed", "to": "exported", "operation": "export_document", "condition": "用户确认导出"},
        ]
    else:
        states = [
            {"id": "created", "name": "任务已创建", "description": "已收到用户需求。"},
            {"id": "clarifying", "name": "需求澄清中", "description": "正在补充目标、对象、权限或约束。"},
            {"id": "ready", "name": "可执行", "description": "信息足够，可以绑定操作。"},
            {"id": "executing", "name": "执行中", "description": "正在调用工具或执行操作。"},
            {"id": "completed", "name": "已完成", "description": "任务已完成或交付结果。"},
        ]
        transitions = [
            {"from": "created", "to": "clarifying", "operation": "ask_clarification", "condition": "关键信息不足"},
            {"from": "clarifying", "to": "ready", "operation": "confirm_requirements", "condition": "信息已补齐"},
            {"from": "ready", "to": "executing", "operation": "execute_operation", "condition": "校验通过"},
            {"from": "executing", "to": "completed", "operation": "complete_task", "condition": "执行成功"},
        ]
    operation_bindings = []
    operation_names = [op.get("name") for op in protocol.get("operations") or [] if isinstance(op, dict) and op.get("name")]
    for name in operation_names:
        operation_bindings.append({
            "operation": name,
            "reads": ["current_state", "pending_confirmations"],
            "writes": ["last_operation", "updated_at"],
            "allowed_states": [state.get("id") for state in states],
            "blocked_states": [],
        })
    return {
        "states": states,
        "transitions": transitions,
        "state_fields": [
            {"name": "current_state", "type": "string", "description": "当前任务阶段。"},
            {"name": "pending_confirmations", "type": "array", "description": "等待用户确认的操作。"},
            {"name": "last_operation", "type": "object", "description": "最近一次操作和结果摘要。"},
            {"name": "updated_at", "type": "datetime", "description": "状态最后更新时间。"},
        ],
        "state_store": "prototype_session_file / 原型阶段可存会话文件；生产环境建议使用数据库或项目状态表。",
        "validator_rules": [
            "执行操作前必须读取 current_state，判断该状态是否允许当前操作。",
            "高风险状态跳转必须写入 pending_confirmations，等待用户确认后再执行。",
            "导出、提交、删除、覆盖等操作必须检查是否已有可用产物或目标对象。",
        ],
        "operation_bindings": operation_bindings,
    }


def ensure_state_model(protocol: dict[str, Any]) -> dict[str, Any]:
    protocol = merge_protocol(EMPTY_PROTOCOL, protocol or {})
    state_model = protocol.get("state_model") if isinstance(protocol.get("state_model"), dict) else {}
    if not (state_model.get("states") or state_model.get("transitions") or state_model.get("state_fields")):
        protocol["state_model"] = default_state_model(protocol)
    else:
        protocol["state_model"] = merge_dict_preserve(deepcopy(EMPTY_STATE_MODEL), state_model)
    return protocol


def build_state_model_markdown(protocol: dict[str, Any]) -> str:
    protocol = ensure_state_model(protocol)
    state = protocol.get("state_model") or {}
    lines = ["# State Model / 状态模型", "", "状态模型负责说明：任务当前走到哪一步、下一步允许做什么、哪些操作会读写状态。", "", "## 1. 状态列表"]
    for item in state.get("states") or []:
        if isinstance(item, dict):
            lines.append(f"- {item.get('id') or ''}：{item.get('name') or ''}。{item.get('description') or ''}")
        else:
            lines.append(f"- {item}")
    lines.extend(["", "## 2. 状态流转"])
    for item in state.get("transitions") or []:
        if isinstance(item, dict):
            lines.append(f"- {item.get('from') or '?'} → {item.get('to') or '?'}；操作：{item.get('operation') or '待定'}；条件：{item.get('condition') or '待补充'}")
        else:
            lines.append(f"- {item}")
    lines.extend(["", "## 3. 状态字段"])
    for item in state.get("state_fields") or []:
        if isinstance(item, dict):
            lines.append(f"- {item.get('name') or ''}（{item.get('type') or 'unknown'}）：{item.get('description') or ''}")
        else:
            lines.append(f"- {item}")
    lines.extend(["", "## 4. 操作读写绑定"])
    for item in state.get("operation_bindings") or []:
        if isinstance(item, dict):
            lines.append(f"- {item.get('operation') or 'operation'}：读取 {', '.join(map(str, item.get('reads') or [])) or '待补充'}；写入 {', '.join(map(str, item.get('writes') or [])) or '待补充'}")
    if not state.get("operation_bindings"):
        lines.append("- 暂无绑定。补充 operation 后应声明每个操作读写哪些状态。")
    lines.extend(["", "## 5. 状态校验规则"])
    lines.extend([f"- {item}" for item in state.get("validator_rules") or []] or ["- 暂未定义。"])
    lines.extend(["", "## 6. 状态存储建议", "", str(state.get("state_store") or "待补充")])
    return "\n".join(lines).strip() + "\n"


def default_memory_policy(protocol: dict[str, Any]) -> dict[str, Any]:
    summary = str(protocol.get("domain_summary") or protocol.get("project_name") or "")
    goals_text = " ".join(map(str, protocol.get("goals") or []))
    text = f"{summary} {goals_text}"
    is_document = any(word in text for word in ("写作", "文档", "文章", "报告", "论文", "招标", "投标", "标书", "模板", "章节"))
    is_rag = any(word in text for word in ("知识库", "检索", "资料", "图谱", "引用", "证据"))
    memory_types = [
        "session_memory / 会话记忆：记录本次对话已确认的目标、限制和用户选择。",
        "task_state_memory / 任务状态记忆：记录任务阶段、已完成步骤、待确认事项。",
    ]
    if is_document:
        memory_types.extend([
            "user_preference_memory / 用户偏好记忆：记录语言风格、格式偏好、输出习惯，但长期写入需确认。",
            "project_memory / 项目记忆：记录模板规则、文档结构、章节约束、交付格式。",
        ])
    if is_rag:
        memory_types.append("knowledge_evidence_memory / 知识证据记忆：记录已采用资料、来源、引用和冲突处理结论。")
    return {
        "enabled": True,
        "memory_types": memory_types,
        "read_rules": [
            "每轮只读取与当前用户意图、目标对象、任务阶段直接相关的记忆。",
            "优先读取任务状态记忆和项目记忆；用户偏好记忆只在影响输出风格或交付格式时注入。",
            "不得把无关历史、其他项目数据或未授权数据注入上下文包。",
        ],
        "write_rules": [
            "用户明确确认的目标、约束、交付格式和任务阶段可以写入会话记忆或任务状态记忆。",
            "长期用户偏好、项目规则、知识证据写入前应给出写入建议并等待用户确认。",
            "LLM 只能提出记忆写入建议；程序负责校验、落库和追踪来源。",
        ],
        "update_rules": [
            "新记忆与旧记忆冲突时，不自动覆盖；应生成冲突说明并追问用户。",
            "任务阶段推进后，应更新 task_state_memory，并保留关键 trace。",
        ],
        "delete_rules": [
            "用户可以查看、修改、删除长期记忆。",
            "临时会话记忆可随会话结束清理；项目记忆应按项目保留策略处理。",
        ],
        "confirmation_required": [
            "写入长期用户偏好",
            "写入项目级规则",
            "写入可能影响后续自动执行的记忆",
            "删除或覆盖已有长期记忆",
        ],
        "confidence_policy": {
            "auto_session_memory_threshold": 0.8,
            "long_term_memory_requires_confirmation": True,
            "conflict_requires_clarification": True,
        },
        "context_injection_rules": [
            "记忆先被检索为 memory_retrieval，再由程序裁剪后进入 context_pack.memory_context。",
            "注入上下文时必须带 memory_id、type、source、confidence 和 last_updated 摘要。",
        ],
        "privacy_rules": [
            "记忆按用户、项目和工作区隔离。",
            "敏感数据不得跨项目读取，不得默认进入 LLM 上下文。",
        ],
    }


def ensure_memory_policy(protocol: dict[str, Any]) -> dict[str, Any]:
    protocol = merge_protocol(EMPTY_PROTOCOL, protocol or {})
    memory_policy = protocol.get("memory_policy") if isinstance(protocol.get("memory_policy"), dict) else {}
    if not (memory_policy.get("enabled") or memory_policy.get("memory_types") or memory_policy.get("read_rules") or memory_policy.get("write_rules")):
        protocol["memory_policy"] = default_memory_policy(protocol)
    else:
        protocol["memory_policy"] = merge_dict_preserve(deepcopy(EMPTY_MEMORY_POLICY), memory_policy)
    return protocol


def build_memory_policy_markdown(protocol: dict[str, Any]) -> str:
    protocol = ensure_memory_policy(protocol)
    memory = protocol.get("memory_policy") or {}
    lines = [
        "# Memory Policy / 记忆策略",
        "",
        f"- 是否启用：{bool(memory.get('enabled'))}",
        "",
        "## 1. 记忆类型",
    ]
    lines.extend([f"- {item}" for item in memory.get("memory_types") or []] or ["- 暂未定义。"])
    sections = [
        ("## 2. 读取规则", "read_rules"),
        ("## 3. 写入规则", "write_rules"),
        ("## 4. 更新规则", "update_rules"),
        ("## 5. 删除规则", "delete_rules"),
        ("## 6. 需要确认的记忆操作", "confirmation_required"),
        ("## 7. 上下文注入规则", "context_injection_rules"),
        ("## 8. 隐私边界", "privacy_rules"),
    ]
    for title, key in sections:
        lines.extend(["", title])
        lines.extend([f"- {item}" for item in memory.get(key) or []] or ["- 暂未定义。"])
    lines.extend(["", "## 9. 置信度策略", "", "```json", json.dumps(memory.get("confidence_policy") or {}, ensure_ascii=False, indent=2), "```"])
    lines.extend(["", "## 10. 工程原则", "", "LLM 可以建议写入记忆，程序负责校验和落库，用户拥有查看、修改、删除权。"])
    return "\n".join(lines).strip() + "\n"


def build_architecture_check(protocol: dict[str, Any]) -> dict[str, Any]:
    protocol = merge_protocol(EMPTY_PROTOCOL, protocol or {})
    workflow = protocol.get("workflow") or {}
    memory_policy = protocol.get("memory_policy") or {}
    state_model = protocol.get("state_model") or {}
    artifact_model = protocol.get("artifact_model") or {}
    tool_registry = protocol.get("tool_registry") or {}
    permission_policy = protocol.get("permission_policy") or {}
    error_recovery = protocol.get("error_recovery") or {}
    knowledge_policy = protocol.get("knowledge_policy") or {}
    eval_policy = protocol.get("eval_policy") or {}
    runtime_triggers = protocol.get("runtime_triggers") or {}
    workspace_policy = protocol.get("workspace_policy") or {}

    operations = protocol.get("operations") or []
    validators = [item for op in operations if isinstance(op, dict) for item in (op.get("validators") or [])]
    high_risk_ops = [op for op in operations if isinstance(op, dict) and str(op.get("risk") or "").lower() == "high"]
    eval_cases = list((protocol.get("user_intents") or [])) + list((eval_policy.get("golden_cases") or []))
    workflow_artifacts = workflow.get("artifacts") or []
    workflow_failures = workflow.get("failure_strategy") or []
    workflow_reviews = workflow.get("human_review_points") or []

    layers = [
        _layer_status("Agent Profile / 智能体画像", has_non_empty_value(protocol.get("agent_profile")), "先定义 Agent 名称、角色、目标用户、交互风格和模型策略。"),
        _layer_status("Context Pack / 上下文包", bool((protocol.get("context_policy") or {}).get("context_pack_fields")), "定义每轮交给规划器看的结构化上下文字段。"),
        _layer_status("Intent Planner / 意图规划器", bool((protocol.get("intent_recognition") or {}).get("required_output_fields")), "定义意图、证据、置信度、缺失信息和追问策略。"),
        _layer_status("Intent Binding / 意图绑定", bool((protocol.get("intent_binding") or {}).get("intent_frame_schema")), "定义意图框架如何绑定到受控操作调用。"),
        _layer_status("OpCall / 操作调用", bool(operations), "补充有限、稳定、可校验的业务操作。"),
        _layer_status("Validator / 校验器", bool(validators) or bool(protocol.get("confirmation_rules") or protocol.get("clarification_rules")), "为每个高风险或状态变更操作补确定性校验规则。"),
        _layer_status("Executor / 执行器", bool(operations), "基于操作协议生成或实现执行器骨架。", partial=bool(operations)),
        _layer_status("Observation / 观察结果", has_non_empty_value(workflow) or bool(operations), "定义执行后返回给用户和下一轮规划器的观察结果。", partial=bool(operations)),
        _layer_status("Trace / 过程追踪", True, "继续记录上下文、意图、操作、校验、执行和失败过程。"),
        _layer_status("Memory Policy / 记忆策略", bool(memory_policy.get("enabled") or memory_policy.get("memory_types") or memory_policy.get("read_rules") or memory_policy.get("write_rules")), "判断是否需要会话记忆、任务状态记忆、用户偏好记忆或项目记忆。"),
        _layer_status("State Model / 状态模型", bool(state_model.get("states") or state_model.get("transitions") or state_model.get("state_fields") or protocol.get("states")), "定义任务阶段、状态字段、状态流转和状态校验。"),
        _layer_status("Artifact Model / 产物模型", bool(artifact_model.get("artifacts") or artifact_model.get("operation_bindings") or workflow_artifacts), "定义文档、报告、代码、图谱等产物格式、版本、导出和审查规则。"),
        _layer_status("Tool Registry / 工具注册", bool(tool_registry.get("tools") or tool_registry.get("operation_tool_bindings")), "区分业务操作和底层工具，声明工具输入输出、风险、副作用和失败策略。"),
        _layer_status("Permission Policy / 权限策略", bool(permission_policy.get("auto_allowed") or permission_policy.get("confirmation_required") or permission_policy.get("forbidden") or high_risk_ops or workflow_reviews), "定义自动执行、人工确认、禁止操作和角色权限。", partial=bool(high_risk_ops or workflow_reviews)),
        _layer_status("Error Recovery / 失败恢复", bool(error_recovery.get("strategies") or workflow_failures), "定义失败后的重试、追问、回滚、降级、转人工和记录评测。", partial=bool(workflow_failures)),
        _layer_status("Eval Cases / 评测用例", bool(eval_cases), "生成意图识别、操作绑定、校验、记忆、工作流和权限评测用例。"),
        _layer_status("Knowledge/RAG Policy / 知识检索策略", bool(knowledge_policy.get("knowledge_bases") or knowledge_policy.get("retrieval_rules")), "定义知识库、检索规则、引用策略和冲突处理。"),
        _layer_status("Versioning/Rollback / 版本回滚", bool((artifact_model.get("versioning") or {}).get("enabled") or (artifact_model.get("versioning") or {}).get("snapshot_rules")), "对文档、状态、记忆和关键产物定义快照、版本与回滚。"),
        _layer_status("Cost/Latency Budget / 成本耗时预算", bool(runtime_triggers.get("max_runtime_seconds") or runtime_triggers.get("scheduled")), "定义最长运行时间、调用次数、重试次数、成本和响应时间约束。"),
        _layer_status("Workspace Policy / 项目空间策略", has_non_empty_value(workspace_policy), "定义项目边界、数据边界、协作规则和会话保留策略。"),
    ]

    covered_layers = [layer["name"] for layer in layers if layer["status"] == "covered"]
    partial_layers = [layer["name"] for layer in layers if layer["status"] == "partial"]
    missing_layers = [layer["name"] for layer in layers if layer["status"] == "missing"]
    score = round((len(covered_layers) + 0.5 * len(partial_layers)) / max(len(layers), 1) * 100)
    top_recommendations = [layer["recommendation"] for layer in layers if layer["status"] == "missing"][:5]
    return {
        "completeness_percent": score,
        "covered_layers": covered_layers,
        "partial_layers": partial_layers,
        "missing_layers": missing_layers,
        "top_recommendations": top_recommendations,
        "layers": layers,
    }


def ensure_harness_protocol(protocol: dict[str, Any]) -> dict[str, Any]:
    protocol = ensure_eval_policy(ensure_error_recovery(ensure_permission_policy(ensure_tool_registry(ensure_artifact_model(ensure_state_model(ensure_memory_policy(ensure_workflow(ensure_intent_protocol(merge_protocol(EMPTY_PROTOCOL, protocol or {}))))))))))
    protocol["architecture_check"] = build_architecture_check(protocol)
    return protocol


def build_architecture_check_markdown(protocol: dict[str, Any]) -> str:
    protocol = ensure_harness_protocol(protocol)
    check = protocol.get("architecture_check") or {}
    lines = [
        "# Agent 架构完整性检查",
        "",
        f"当前完整度：{check.get('completeness_percent', 0)}%",
        "",
        "## 已覆盖模块",
    ]
    lines.extend([f"- {item}" for item in check.get("covered_layers") or []] or ["- 暂无。"])
    lines.extend(["", "## 部分覆盖模块", ""])
    lines.extend([f"- {item}" for item in check.get("partial_layers") or []] or ["- 暂无。"])
    lines.extend(["", "## 建议补齐模块", ""])
    for layer in check.get("layers") or []:
        if layer.get("status") == "missing":
            lines.append(f"- {layer.get('name')}：{layer.get('recommendation')}")
    if not any((layer.get("status") == "missing") for layer in check.get("layers") or []):
        lines.append("- 当前没有明显缺口，建议进入预览运行和评测。")
    lines.extend(["", "## 下一步优先建议", ""])
    lines.extend([f"{idx}. {item}" for idx, item in enumerate(check.get("top_recommendations") or [], 1)] or ["1. 进入预览运行，收集失败 case。"])
    return "\n".join(lines).strip() + "\n"



def default_intent_binding(protocol: dict[str, Any]) -> dict[str, Any]:
    scene_type = ((protocol.get("scene_classification") or {}).get("scene_type") or "").lower()
    is_writing = scene_type == "protocol_driven_opcall" or any(word in str(protocol.get("domain_summary") or "") for word in ("写作", "文档", "模板", "章节"))
    operations = [op.get("name") for op in protocol.get("operations") or [] if isinstance(op, dict) and op.get("name")]
    intent_frame_schema = {
        "intent_type": "create|modify|delete|query|render|confirm|cancel|unknown",
        "action": "shorten|expand|rewrite|polish|move|delete|fill|export|unknown",
        "target_type": "document|section|paragraph|template_field|selection|unknown",
        "target_ref": "用户原始指代表达",
        "target_resolved_id": None,
        "scope": "local|document|template|global|unknown",
        "constraints": {},
        "risk": "low|medium|high",
        "confidence": 0.0,
        "evidence": [],
        "missing_info": [],
    }
    if is_writing:
        routing_rules = [
            {"when": {"intent_type": "modify", "action": ["shorten", "expand", "rewrite", "polish"], "target_type": "section"}, "operation": "rewrite_section_content", "reason": "章节级内容修改绑定到章节改写操作"},
            {"when": {"intent_type": "modify", "action": ["shorten", "expand", "rewrite", "polish"], "target_type": "selection"}, "operation": "replace_selected_text", "reason": "选区级修改必须只替换选区"},
            {"when": {"intent_type": "delete", "target_type": "section"}, "operation": "delete_sections", "reason": "删除整章是高风险章节删除操作"},
            {"when": {"intent_type": "modify", "scope": "document"}, "operation": "apply_document_wide_revision", "reason": "明确全文范围才绑定全文修订"},
            {"when": {"intent_type": "render", "target_type": "document"}, "operation": "render_document_artifact", "reason": "导出/下载绑定渲染文档产物"},
            {"when": {"intent_type": "create", "target_type": "section"}, "operation": "add_section", "reason": "新增章节绑定 add_section"},
        ]
        param_mapping_rules = [
            {"operation": "rewrite_section_content", "map": {"section_id": "intent_frame.target_resolved_id", "target": "content", "instruction": "compose(action,constraints,user_message)"}},
            {"operation": "replace_selected_text", "map": {"section_id": "context_pack.active_section_id", "selected_text": "context_pack.selected_text", "instruction": "compose(action,constraints,user_message)", "target": "content"}},
            {"operation": "delete_sections", "map": {"section_ids": ["intent_frame.target_resolved_id"], "instruction": "user_message"}},
            {"operation": "apply_document_wide_revision", "map": {"instruction": "user_message"}},
            {"operation": "render_document_artifact", "map": {"session_id": "context_pack.session_id"}},
            {"operation": "add_section", "map": {"instruction": "user_message", "after_section_id": "intent_frame.constraints.after_section_id|null"}},
        ]
        forbidden_bindings = [
            "target_type=section 或 selection 时，不得绑定 apply_document_wide_revision，除非 scope=document 且用户明确说全文/整篇/所有章节。",
            "target_type=selection 时，不得绑定 rewrite_section_content 或 delete_sections，除非用户明确扩大范围。",
            "delete 动作缺少 target_resolved_id 时，不得绑定 delete_sections，必须 ask_clarification。",
            "confidence 低于 clarify_below 或 evidence 为空时，不得生成可执行 OpCall。",
        ]
        binding_eval_cases = [
            {"user_message": "第二章太长，缩短一半", "intent_frame": {"intent_type": "modify", "action": "shorten", "target_type": "section", "target_resolved_id": "chapter_2", "scope": "local"}, "expected_operation": "rewrite_section_content", "forbidden_operations": ["apply_document_wide_revision", "delete_sections"]},
            {"user_message": "这段太啰嗦，压缩一下", "context": {"selected_text": "...", "active_section_id": "chapter_2"}, "intent_frame": {"intent_type": "modify", "action": "shorten", "target_type": "selection", "scope": "local"}, "expected_operation": "replace_selected_text", "forbidden_operations": ["apply_document_wide_revision"]},
            {"user_message": "删掉不重要的内容", "intent_frame": {"intent_type": "delete", "action": "delete", "target_type": "unknown", "target_resolved_id": None}, "expected_operation": "ask_clarification", "forbidden_operations": ["delete_sections"]},
        ]
    else:
        routing_rules = [{"when": {"intent_type": "unknown"}, "operation": "ask_clarification", "reason": "默认不确定先追问"}]
        param_mapping_rules = []
        forbidden_bindings = ["不要把 unknown intent 绑定到高风险操作。"]
        binding_eval_cases = []
    return {
        "intent_frame_schema": intent_frame_schema,
        "routing_rules": routing_rules,
        "param_mapping_rules": param_mapping_rules,
        "forbidden_bindings": forbidden_bindings,
        "binding_eval_cases": binding_eval_cases,
        "allowed_operations_snapshot": operations,
    }


def build_intent_binding_spec(protocol: dict[str, Any]) -> str:
    protocol = ensure_intent_protocol(protocol)
    binding = protocol.get("intent_binding") or {}
    lines = [
        "# Intent-to-Op Binding Spec",
        "",
        "意图绑定层（Intent Binding）负责把 LLM 的语义理解结果绑定到有限、可校验、可执行的操作调用（OpCall）。",
        "",
        "核心链路：",
        "",
        "```text",
        "Context Pack → Intent Frame → Routing Rules → Param Mapping → OpCall → Validator",
        "```",
        "",
        "## Intent Frame Schema",
        "```json",
        json.dumps(binding.get("intent_frame_schema") or {}, ensure_ascii=False, indent=2),
        "```",
        "",
        "## Routing Rules",
    ]
    for rule in binding.get("routing_rules") or []:
        if isinstance(rule, dict):
            lines.append(f"- when={json.dumps(rule.get('when', {}), ensure_ascii=False)} → operation={rule.get('operation')}；{rule.get('reason', '')}")
        else:
            lines.append(f"- {rule}")
    lines.extend(["", "## Param Mapping Rules"])
    for rule in binding.get("param_mapping_rules") or []:
        if isinstance(rule, dict):
            lines.append(f"- {rule.get('operation')}: {json.dumps(rule.get('map', {}), ensure_ascii=False)}")
        else:
            lines.append(f"- {rule}")
    lines.extend(["", "## Forbidden Bindings"])
    for item in binding.get("forbidden_bindings") or []:
        lines.append(f"- {item}")
    lines.extend([
        "",
        "## 实现建议",
        "- LLM 可以同时输出 intent_frame 和 suggested_operation，但程序必须用 binding rules 校验 suggested_operation。",
        "- 如果 suggested_operation 与 binding rules 冲突，优先追问或拒绝，不要自动执行。",
        "- 每次绑定结果应记录 evidence、confidence、matched_rule 和 rejected_routes，方便排查误路由。",
    ])
    return "\n".join(lines).strip() + "\n"


def build_binding_eval_cases(protocol: dict[str, Any]) -> str:
    protocol = ensure_intent_protocol(protocol)
    binding = protocol.get("intent_binding") or {}
    return json.dumps({"binding_eval_cases": binding.get("binding_eval_cases") or []}, ensure_ascii=False, indent=2)

def build_context_pack_schema(protocol: dict[str, Any]) -> str:
    protocol = ensure_intent_protocol(protocol)
    policy = protocol.get("context_policy") or {}
    schema = {
        "user_message": "string",
        "state_summary": "object",
        "active_object": "object|null",
        "selected_text": "string|null",
        "available_objects": "array",
        "recent_operations": "array",
        "pending_confirmations": "array",
        "constraints": "object",
    }
    lines = [
        "# Context Pack Schema",
        "",
        "上下文包（Context Pack）是 Planner 每轮观察的结构化输入。它不是把全文全部塞给模型，而是按策略提供足够且相关的信息。",
        "",
        "## 建议字段",
    ]
    for field in policy.get("context_pack_fields") or []:
        lines.append(f"- {field}")
    lines.extend(["", "## 检索/裁剪规则"])
    for rule in policy.get("retrieval_rules") or []:
        lines.append(f"- {rule}")
    lines.extend([
        "",
        "## 时间窗口",
        str(policy.get("recency_window") or "待补充"),
        "",
        "## 禁止上下文",
    ])
    for item in policy.get("forbidden_context") or []:
        lines.append(f"- {item}")
    lines.extend([
        "",
        "## 上下文预算策略",
        str(policy.get("context_budget_policy") or "待补充"),
        "",
        "## 基础 JSON 结构",
        "```json",
        json.dumps(schema, ensure_ascii=False, indent=2),
        "```",
    ])
    return "\n".join(lines).strip() + "\n"


def build_intent_planner_prompt(protocol: dict[str, Any]) -> str:
    protocol = ensure_intent_protocol(protocol)
    intent = protocol.get("intent_recognition") or {}
    routing = protocol.get("routing_policy") or {}
    binding = protocol.get("intent_binding") or {}
    operations = [op.get("name") for op in protocol.get("operations") or [] if isinstance(op, dict) and op.get("name")]
    lines = [
        "# Intent Planner Prompt",
        "",
        "你是意图规划器（Intent Planner）。你的任务是观察上下文包（Context Pack），理解用户真实意图，并输出受控操作调用（OpCall）计划。",
        "",
        "你不能执行操作，不能修改状态，不能绕过校验器（Validator）和确认规则。",
        "",
        "## Planner 职责",
        str(intent.get("planner_role") or "观察上下文，输出受控操作计划。"),
        "",
        "## 可用操作",
        ", ".join(operations) if operations else "暂无操作；不确定时输出 ask_clarification。",
        "",
        "## 指代消解规则",
    ]
    for rule in intent.get("reference_resolution_rules") or []:
        lines.append(f"- {rule}")
    lines.extend(["", "## 歧义处理规则"])
    for rule in intent.get("ambiguity_policy") or []:
        lines.append(f"- {rule}")
    lines.extend(["", "## 路由限制"])
    for rule in routing.get("forbidden_routes") or []:
        lines.append(f"- {rule}")
    lines.extend([
        "",
        "## Intent Frame Schema",
        "```json",
        json.dumps(binding.get("intent_frame_schema") or {}, ensure_ascii=False, indent=2),
        "```",
        "",
        "## 输出 JSON 结构",
        "```json",
        json.dumps({
            "intent_frame": binding.get("intent_frame_schema") or {},
            "suggested_operation": "operation_name 或 ask_clarification/unsupported/need_confirmation",
            "target": {"type": "section|selection|document|object|unknown", "id": None, "source": "explicit|context|unknown"},
            "params": {},
            "confidence": 0.0,
            "evidence": ["为什么这样理解", "命中了哪条指代/路由规则"],
            "matched_routing_rule": "规则说明或 null",
            "rejected_routes": ["被排除的错误 operation 及原因"],
            "needs_clarification": False,
            "clarification_question": None,
            "needs_confirmation": False,
        }, ensure_ascii=False, indent=2),
        "```",
        "",
        "## 绑定规则摘要",
        "意图规划器可以建议 operation，但最终执行前必须由程序按 intent_binding.routing_rules 和 param_mapping_rules 校验。",
        "",
        "## 置信度策略",
        json.dumps(intent.get("confidence_policy") or {}, ensure_ascii=False, indent=2),
    ])
    return "\n".join(lines).strip() + "\n"


def build_intent_eval_cases(protocol: dict[str, Any]) -> str:
    protocol = ensure_intent_protocol(protocol)
    examples = list((protocol.get("intent_recognition") or {}).get("examples") or [])
    existing = {json.dumps(item, ensure_ascii=False, sort_keys=True) for item in examples}
    for intent in protocol.get("user_intents") or []:
        if not isinstance(intent, dict):
            continue
        case = {
            "user_message": intent.get("example") or "",
            "context": {},
            "expected_operation": intent.get("operation_hint") or "",
            "expected_target": "待补充",
            "forbidden_operations": [],
            "notes": intent.get("normalized_intent") or "",
        }
        key = json.dumps(case, ensure_ascii=False, sort_keys=True)
        if case["user_message"] and key not in existing:
            examples.append(case)
            existing.add(key)
    return json.dumps({"intent_eval_cases": examples}, ensure_ascii=False, indent=2)

def ensure_workflow(protocol: dict[str, Any]) -> dict[str, Any]:
    protocol = merge_protocol(EMPTY_PROTOCOL, protocol or {})
    workflow = protocol.get("workflow")
    if not isinstance(workflow, dict):
        workflow = {}
    normalized = merge_dict_preserve(deepcopy(EMPTY_WORKFLOW), workflow)
    for key in ("nodes", "edges", "parallel_groups", "human_review_points", "failure_strategy", "artifacts"):
        if not isinstance(normalized.get(key), list):
            normalized[key] = []
    protocol["workflow"] = normalized
    return protocol


def build_workflow_json(protocol: dict[str, Any]) -> str:
    protocol = ensure_workflow(protocol)
    return json.dumps(protocol.get("workflow") or EMPTY_WORKFLOW, ensure_ascii=False, indent=2)


def build_workflow_plan(protocol: dict[str, Any]) -> str:
    protocol = ensure_workflow(protocol)
    workflow = protocol.get("workflow") or {}
    nodes = workflow.get("nodes") or []
    edges = workflow.get("edges") or []
    parallel_groups = workflow.get("parallel_groups") or []
    human_reviews = workflow.get("human_review_points") or []
    failure_strategy = workflow.get("failure_strategy") or []
    artifacts = workflow.get("artifacts") or []

    def short_text(value: Any, default: str = "待补充", limit: int = 500) -> str:
        text = " ".join(str(value or default).split())
        return text if len(text) <= limit else text[:limit].rstrip() + "..."

    def node_title(node: Any) -> str:
        if not isinstance(node, dict):
            return f"- {short_text(node)}"
        label = node.get("name") or node.get("id") or "未命名节点"
        node_type = node.get("type") or "unknown"
        desc = short_text(node.get("description"))
        op = f"；操作：{node.get('operation')}" if node.get("operation") else ""
        agent = f"；智能体：{node.get('agent')}" if node.get("agent") else ""
        return f"- {label}（{node_type}）：{desc}{op}{agent}"

    def render_edge(edge: Any) -> str:
        if isinstance(edge, dict):
            condition = f"，条件：{edge.get('condition')}" if edge.get("condition") else ""
            return f"- {edge.get('from') or '?'} → {edge.get('to') or '?'}{condition}"
        if isinstance(edge, (list, tuple)) and len(edge) >= 2:
            return f"- {edge[0]} → {edge[1]}"
        return f"- {short_text(edge)}"

    def render_group(group: Any) -> str:
        if isinstance(group, dict):
            return f"- {group.get('name') or '未命名并行组'}：{', '.join(map(str, group.get('node_ids') or [])) or '待补充'}"
        return f"- {short_text(group)}"

    def render_review(item: Any) -> str:
        if isinstance(item, dict):
            return f"- {item.get('id') or 'review'}：{short_text(item.get('description'))}；发生在：{item.get('required_before') or '待补充'}"
        return f"- {short_text(item)}"

    def render_failure(item: Any) -> str:
        if isinstance(item, dict):
            return f"- {item.get('scope') or '全局'}：{item.get('strategy') or '待补充'}；{short_text(item.get('description'))}"
        return f"- {short_text(item)}"

    def render_artifact(item: Any) -> str:
        if isinstance(item, dict):
            return f"- {item.get('name') or '未命名产物'}（{item.get('type') or 'unknown'}）：{short_text(item.get('description'))}"
        return f"- {short_text(item)}"

    lines = [
        "# Dynamic Workflow 设计说明",
        "",
        "## 1. 工作流目标",
        "",
        f"- 名称：{short_text(workflow.get('name') or protocol.get('project_name'), '待命名工作流')}",
        f"- 目标：{short_text(workflow.get('goal') or protocol.get('domain_summary'))}",
        f"- 策略：{short_text(workflow.get('strategy'), '待补充：说明固定流程、动态分支、并行、校验和人工确认策略。')}",
        "",
        "## 2. 节点（nodes）",
        "",
    ]
    lines.extend([node_title(node) for node in nodes] or ["- 暂无节点。建议先补充 parse / plan / execute / validate / human_review 等关键节点。"])
    lines.extend(["", "## 3. 边（edges）", ""])
    lines.extend([render_edge(edge) for edge in edges] or ["- 暂无边。建议定义节点之间的执行顺序和条件。"])
    lines.extend(["", "## 4. 并行组（parallel groups）", ""])
    lines.extend([render_group(group) for group in parallel_groups] or ["- 暂无并行组。复杂任务可把语义抽取、章节生成、校验拆成并行组。"])
    lines.extend(["", "## 5. 人工确认点（human review）", ""])
    lines.extend([render_review(item) for item in human_reviews] or ["- 暂无人工确认点。高风险操作、合规判断、最终目录建议加入人工确认。"])
    lines.extend(["", "## 6. 失败策略（failure strategy）", ""])
    lines.extend([render_failure(item) for item in failure_strategy] or ["- 暂无失败策略。建议至少定义 retry / fallback / ask_human / rollback。"])
    lines.extend(["", "## 7. 交付物（artifacts）", ""])
    lines.extend([render_artifact(item) for item in artifacts] or ["- 默认建议导出 workflow.json、protocol.json、development_plan.md。"])
    lines.extend([
        "",
        "## 8. 与能力协议的关系",
        "",
        "- 能力协议（Agent Protocol）定义原子能力：objects、operations、validators。",
        "- 动态工作流（Dynamic Workflow）定义能力编排：nodes、edges、parallel groups、human review、failure strategy。",
        "- 没有 workflow 字段的旧会话仍然合法，系统会自动补空工作流结构。",
        "",
    ])
    return "\n".join(lines)


def build_development_advice(protocol: dict[str, Any]) -> dict[str, Any]:
    protocol = ensure_harness_protocol(protocol)
    check = protocol.get("architecture_check") or build_architecture_check(protocol)
    operations = [op for op in protocol.get("operations") or [] if isinstance(op, dict) and op.get("name")]
    validators = [item for op in operations for item in (op.get("validators") or [])]
    workflow = protocol.get("workflow") or {}
    tool_registry = protocol.get("tool_registry") or {}
    eval_policy = protocol.get("eval_policy") or {}
    permission_policy = protocol.get("permission_policy") or {}
    artifact_model = protocol.get("artifact_model") or {}
    missing_layers = list(check.get("missing_layers") or [])
    partial_layers = list(check.get("partial_layers") or [])
    high_risk_ops = [op for op in operations if str(op.get("risk") or "").lower() == "high" or op.get("requires_confirmation")]
    workflow_ready = bool(workflow.get("nodes") or workflow.get("edges") or workflow.get("human_review_points"))
    has_tools = bool(tool_registry.get("tools") or tool_registry.get("operation_tool_bindings"))
    has_eval = bool((eval_policy.get("golden_cases") or []) or ((eval_policy.get("case_groups") or {}).get("intent_eval_cases") or []))
    has_artifacts = bool(artifact_model.get("artifacts") or artifact_model.get("operation_bindings"))
    has_permission = bool(permission_policy.get("confirmation_required") or permission_policy.get("forbidden") or permission_policy.get("auto_allowed"))

    blocking_gaps: list[str] = []
    next_actions: list[dict[str, Any]] = []
    implementation_sequence: list[dict[str, Any]] = []
    files_to_start: list[str] = []

    if not operations:
        blocking_gaps.append("还没有稳定 operation，不能进入执行器开发。")
        current_focus = "先收敛 3-8 个核心 operation"
        readiness_level = "design_needed"
        next_actions.append({"priority": 1, "title": "定义核心操作", "why": "没有 operation 就没有可执行边界。", "how": "围绕用户最常见的 3-8 类动作，补 name、description、params、risk、validators。"})
    elif missing_layers:
        first_gap = missing_layers[0]
        blocking_gaps.append(f"架构还缺：{first_gap}。先补这个，否则后续开发容易返工。")
        current_focus = f"先补架构缺口：{first_gap}"
        readiness_level = "architecture_gap"
        next_actions.append({"priority": 1, "title": f"补齐 {first_gap}", "why": "协议已有操作，但运行外壳仍有缺口，直接编码会让状态、权限、记忆或评测边界不清。", "how": "回到 APD 对话，按右侧架构检查和下一步问题补齐该层，再重新预览运行。"})
    elif not validators:
        current_focus = "先补校验器和确认规则"
        readiness_level = "validator_needed"
        blocking_gaps.append("operation 已有，但校验器不足，真实执行前安全边界不够。")
        next_actions.append({"priority": 1, "title": "补 Validator", "why": "Validator 是程序侧安全边界。", "how": "为每个会改状态、改产物或调用工具的 operation 补参数、状态、权限、风险校验。"})
    elif high_risk_ops and not has_permission:
        current_focus = "先补权限和人工确认"
        readiness_level = "permission_needed"
        blocking_gaps.append("存在高风险 operation，但权限策略还不够明确。")
        next_actions.append({"priority": 1, "title": "补 Permission Policy", "why": "删除、覆盖、导出、提交、入库类动作不能自动执行。", "how": "把 auto_allowed、confirmation_required、forbidden、role_required 分清楚。"})
    elif not has_eval:
        current_focus = "先补评测用例"
        readiness_level = "eval_needed"
        next_actions.append({"priority": 1, "title": "补 Eval Cases", "why": "没有回归样本，改 prompt 或规则时不知道是否退化。", "how": "每个 operation 至少 2 个正例、1 个反例，高风险操作必须有确认/拒绝 case。"})
    elif not has_tools:
        current_focus = "先绑定底层工具"
        readiness_level = "tool_binding_needed"
        next_actions.append({"priority": 1, "title": "补 Tool Registry", "why": "operation 是业务能力，tool 才是真实执行手段。", "how": "为每个 operation 标明依赖工具、输入输出、副作用、失败策略。"})
    elif not has_artifacts and any(key in str(protocol.get("domain_summary") or "") for key in ("文档", "报告", "投标", "写作", "代码", "图谱")):
        current_focus = "先明确产物模型"
        readiness_level = "artifact_needed"
        next_actions.append({"priority": 1, "title": "补 Artifact Model", "why": "文档/报告/图谱/代码类 Agent 必须知道会读写哪些产物。", "how": "定义产物类型、版本、快照、导出格式、来源追踪和审查规则。"})
    else:
        current_focus = "生成可运行 Demo 并接入真实执行器"
        readiness_level = "ready_for_demo"
        next_actions.append({"priority": 1, "title": "生成 Harness Demo", "why": "协议层已经能支撑最小运行链路。", "how": "在 WebUI 点击“生成可运行 Demo zip”，本地启动后先跑 /agent/run 冒烟测试。"})

    next_actions.extend([
        {"priority": 2, "title": "跑预览运行", "why": "先在 APD 内验证理解、绑定、校验、权限和恢复是否符合预期。", "how": "打开“开发入口 → 预览运行”，用正常、信息不足、高风险、边界话术各测一次。"},
        {"priority": 3, "title": "沉淀失败样本", "why": "真实失败样本是后续 prompt 和规则迭代的保护网。", "how": "把预览中的 eval_case_suggestion 保存到 eval_policy / eval_cases。"},
    ])
    if workflow_ready:
        next_actions.append({"priority": 4, "title": "实现 Workflow 节点", "why": "当前协议已有动态工作流设计，开发时应按节点、边、人工确认点落地。", "how": "先实现无副作用节点，再实现工具节点和人工确认节点。"})
    else:
        next_actions.append({"priority": 4, "title": "判断是否需要 Dynamic Workflow", "why": "多阶段、并行、人工确认或强校验场景不应只靠单个 AgentLoop。", "how": "如果任务超过 3 阶段或有并行/回退，先设计 workflow.json。"})

    implementation_sequence = [
        {"phase": "Phase 0", "name": "协议冻结", "deliverable": "protocol.json / architecture_check.md / eval_policy.md", "done_when": "核心 operation、validator、permission、eval case 不再大幅漂移。"},
        {"phase": "Phase 1", "name": "运行外壳", "deliverable": "Context Pack / State / Memory / Artifact / Tool Registry", "done_when": "每轮 Planner 输入可解释、可裁剪、可追踪。"},
        {"phase": "Phase 2", "name": "Planner 和 Binding", "deliverable": "Intent Planner / Intent Binding / OpCall", "done_when": "自然语言能稳定绑定到有限 operation，低置信度会追问。"},
        {"phase": "Phase 3", "name": "Validator 和 Permission", "deliverable": "确定性校验器 / 人工确认 / 禁止项", "done_when": "高风险和缺信息请求不会进入执行器。"},
        {"phase": "Phase 4", "name": "Executor 和 Tool", "deliverable": "业务执行器 / 工具调用 / 失败恢复", "done_when": "真实副作用都有快照、权限、回滚或转人工策略。"},
        {"phase": "Phase 5", "name": "评测和迭代", "deliverable": "eval_cases.json / 回归测试 / Trace", "done_when": "每次改 prompt、规则、工具后都能跑回归。"},
    ]
    files_to_start = [
        "protocol.json",
        "workflow.json",
        "docs/architecture_check.md",
        "docs/eval_policy.md",
        "backend/app/harness.py",
        "backend/app/planner.py",
        "backend/app/validators.py",
        "backend/app/executor.py",
    ]
    return {
        "readiness_level": readiness_level,
        "current_focus": current_focus,
        "summary": f"当前建议：{current_focus}。先不要同时开发所有功能，优先处理第 1 个阻塞点。",
        "blocking_gaps": blocking_gaps,
        "next_actions": next_actions,
        "implementation_sequence": implementation_sequence,
        "files_to_start": files_to_start,
        "metrics": {
            "operation_count": len(operations),
            "validator_count": len(validators),
            "high_risk_operation_count": len(high_risk_ops),
            "architecture_completeness_percent": check.get("completeness_percent", 0),
            "missing_layer_count": len(missing_layers),
            "partial_layer_count": len(partial_layers),
        },
        "handoff_prompt": "请先阅读 protocol.json、architecture_check.md、eval_policy.md 和 development_advice.md，再按 next_actions[0] 实现，不要跳过 Validator、Permission、Trace。",
    }


def build_development_advice_markdown(protocol: dict[str, Any]) -> str:
    advice = build_development_advice(protocol)
    lines = [
        "# 下一步开发建议",
        "",
        advice.get("summary") or "暂无建议。",
        "",
        f"- 当前焦点：{advice.get('current_focus')}",
        f"- 就绪等级：{advice.get('readiness_level')}",
        "",
        "## 1. 阻塞点",
    ]
    lines.extend([f"- {item}" for item in advice.get("blocking_gaps") or []] or ["- 暂无明显阻塞点，可以进入 Demo 和真实执行器开发。"])
    lines.extend(["", "## 2. 建议动作"])
    for item in advice.get("next_actions") or []:
        if isinstance(item, dict):
            lines.append(f"{item.get('priority')}. {item.get('title')}：{item.get('why')} 做法：{item.get('how')}")
    lines.extend(["", "## 3. 推荐开发阶段"])
    for item in advice.get("implementation_sequence") or []:
        if isinstance(item, dict):
            lines.append(f"- {item.get('phase')} / {item.get('name')}：交付 {item.get('deliverable')}；完成标准：{item.get('done_when')}")
    lines.extend(["", "## 4. 建议先看的文件"])
    lines.extend([f"- `{item}`" for item in advice.get("files_to_start") or []])
    lines.extend(["", "## 5. 给 Codex/Claude 的交接提示", "", advice.get("handoff_prompt") or ""])
    return "\n".join(lines).strip() + "\n"


def build_exports(protocol: dict[str, Any]) -> dict[str, str]:
    protocol = ensure_harness_protocol(protocol)
    return {
        "protocol.json": json.dumps(protocol, ensure_ascii=False, indent=2),
        "workflow.json": build_workflow_json(protocol),
        "workflow_plan.md": build_workflow_plan(protocol),
        "architecture_check.md": build_architecture_check_markdown(protocol),
        "architecture_check.json": json.dumps(protocol.get("architecture_check") or build_architecture_check(protocol), ensure_ascii=False, indent=2),
        "memory_policy.md": build_memory_policy_markdown(protocol),
        "memory_policy.json": json.dumps(protocol.get("memory_policy") or {}, ensure_ascii=False, indent=2),
        "state_model.md": build_state_model_markdown(protocol),
        "state_model.json": json.dumps(protocol.get("state_model") or {}, ensure_ascii=False, indent=2),
        "artifact_model.md": build_artifact_model_markdown(protocol),
        "artifact_model.json": json.dumps(protocol.get("artifact_model") or {}, ensure_ascii=False, indent=2),
        "tool_registry.md": build_tool_registry_markdown(protocol),
        "tool_registry.json": json.dumps(protocol.get("tool_registry") or {}, ensure_ascii=False, indent=2),
        "permission_policy.md": build_permission_policy_markdown(protocol),
        "permission_policy.json": json.dumps(protocol.get("permission_policy") or {}, ensure_ascii=False, indent=2),
        "error_recovery.md": build_error_recovery_markdown(protocol),
        "error_recovery.json": json.dumps(protocol.get("error_recovery") or {}, ensure_ascii=False, indent=2),
        "eval_policy.md": build_eval_policy_markdown(protocol),
        "eval_policy.json": json.dumps(protocol.get("eval_policy") or {}, ensure_ascii=False, indent=2),
        "scene_classification.md": build_scene_classification(protocol),
        "context_pack_schema.md": build_context_pack_schema(protocol),
        "intent_planner_prompt.md": build_intent_planner_prompt(protocol),
        "intent_binding_spec.md": build_intent_binding_spec(protocol),
        "intent_eval_cases.json": build_intent_eval_cases(protocol),
        "binding_eval_cases.json": build_binding_eval_cases(protocol),
        "planner_prompt.md": build_planner_prompt(protocol),
        "executor_skeleton.py": build_executor_skeleton(protocol),
        "development_plan.md": build_development_plan(protocol),
        "development_advice.md": build_development_advice_markdown(protocol),
        "development_advice.json": json.dumps(build_development_advice(protocol), ensure_ascii=False, indent=2),
        "eval_cases.json": build_eval_cases(protocol),
    }


def build_planner_prompt(protocol: dict[str, Any]) -> str:
    from .v2 import render_planner_prompt_v2
    return render_planner_prompt_v2(protocol)


def build_executor_skeleton(protocol: dict[str, Any]) -> str:
    from .v2 import render_executor_skeleton_v2
    return render_executor_skeleton_v2(protocol)


def _build_development_plan_template(protocol: dict[str, Any]) -> str:
    protocol = merge_protocol(EMPTY_PROTOCOL, protocol)
    operations = protocol.get("operations") or []
    objects = protocol.get("objects") or []
    op_lines = []
    for idx, op in enumerate(operations, 1):
        validators = op.get("validators") or []
        op_lines.append(textwrap.dedent(f"""
        ### {idx}. {op.get('description') or op.get('name') or '未命名操作'}（{op.get('name') or 'unknown_operation'}）

        - 风险等级：{op.get('risk') or '未标注'}
        - 大模型负责：{op.get('llm_role') or '待补充'}
        - 程序负责：{op.get('executor_role') or '待补充'}
        - 是否需要确认：{op.get('requires_confirmation')}
        - 校验规则：{', '.join(validators) if validators else '待补充'}
        - 实现建议：先写 validate_{_safe_name(op.get('name') or 'operation')}，再写 handle_{_safe_name(op.get('name') or 'operation')}，最后补测试用例。
        """).strip())
    object_lines = [f"- {obj.get('description') or obj.get('name')}（{obj.get('name')}）" for obj in objects if isinstance(obj, dict)]
    return textwrap.dedent(f"""
    # Agent 开发落地计划

    ## 1. 项目目标

    {protocol.get('domain_summary') or '待补充业务场景摘要。'}

    ## 2. 先实现这些结构化状态

    {chr(10).join(object_lines) if object_lines else '- 暂无业务对象，请先补充对象（object）。'}

    ## 3. 推荐开发顺序

    1. 固化 `protocol.json`，不要边开发边让协议无限漂移。
    2. 定义业务状态（State），至少包含当前对象列表、用户输入、历史操作、待确认操作。
    3. 实现规划器（Planner）：把用户自然语言转成协议内允许的操作计划。
    4. 实现校验器（Validator）：校验操作名、参数、对象 ID、权限、风险和确认规则。
    5. 实现执行器（Executor）：只做确定性状态修改或调用局部生成工具。
    6. 实现局部大模型工具：只负责必要的生成/改写，不直接控制全局状态。
    7. 实现评审器（Reviewer）：检查操作是否太泛、引用是否可靠、输出是否越界。
    8. 补测试用例（eval_cases.json），每个操作至少 2 个正例和 1 个反例。

    ## 4. 操作实现清单

    {"\n\n".join(op_lines) if op_lines else '暂无操作（operation），请先完成协议设计。'}

    ## 5. 最小可用版本建议

    - 只实现低风险和中风险操作。
    - 高风险操作先只生成确认请求，不直接执行。
    - 所有删除、覆盖、导出、提交类操作必须有确认。
    - 每次执行都记录 trace，方便复盘和回滚。

    ## 6. 下一步

    - 如果协议还不稳：回到设计器继续评审。
    - 如果协议已稳定：从 `executor_skeleton.py` 开始补真实业务代码。
    - 如果要接入现有项目：先把 `protocol.json` 作为配置文件，再逐步替换原来的自由工具选择逻辑。
    """).strip() + "\n"



def build_development_plan(protocol: dict[str, Any]) -> str:
    from .v2 import render_development_plan_from_artifact
    return render_development_plan_from_artifact(protocol, _build_development_plan_template(protocol))


def build_eval_cases(protocol: dict[str, Any]) -> str:
    protocol = ensure_eval_policy(protocol)
    policy = protocol.get("eval_policy") or {}
    groups = policy.get("case_groups") or {}
    flat_cases = _flatten_eval_groups(groups) if isinstance(groups, dict) else []
    if not flat_cases:
        flat_cases = list(policy.get("golden_cases") or [])
    return json.dumps({
        "eval_cases": flat_cases,
        "case_groups": groups,
        "success_metrics": policy.get("success_metrics") or [],
        "regression_rules": policy.get("regression_rules") or [],
        "coverage_targets": policy.get("coverage_targets") or [],
    }, ensure_ascii=False, indent=2)


def _safe_name(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_]+", "_", value.strip())
    if not value:
        return "operation"
    if value[0].isdigit():
        return f"op_{value}"
    return value
