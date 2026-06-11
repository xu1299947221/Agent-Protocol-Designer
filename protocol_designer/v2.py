from __future__ import annotations

import copy
import json
import re
import secrets
import textwrap
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .core import EMPTY_PROTOCOL, dedupe_rules, merge_protocol, normalize_rule_text, strip_meta_prefix


def operation_map(protocol: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {op.get("name"): op for op in protocol.get("operations", []) or [] if isinstance(op, dict) and op.get("name")}


def object_map(protocol: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {obj.get("name"): obj for obj in protocol.get("objects", []) or [] if isinstance(obj, dict) and obj.get("name")}


def _question_key(text: str) -> str:
    text = strip_meta_prefix(text)
    for prefix in ("下一步建议", "下一轮建议", "建议先", "建议继续", "当前", "本轮"):
        text = text.replace(prefix, "")
    return normalize_rule_text(text)


def normalize_open_questions(session: dict[str, Any], *, max_age_turns: int = 3) -> None:
    protocol = session.setdefault("protocol", copy.deepcopy(EMPTY_PROTOCOL))
    current_turn = len(session.get("turns") or [])
    existing_meta = protocol.get("open_question_meta") or []
    old_questions = protocol.get("open_questions") or []
    new_questions = (session.get("last_response") or {}).get("next_questions") or []
    by_key: dict[str, dict[str, Any]] = {}

    for idx, question in enumerate(old_questions):
        key = _question_key(str(question))
        if not key:
            continue
        meta = next((item for item in existing_meta if item.get("key") == key), None) or {}
        by_key[key] = {
            "text": strip_meta_prefix(str(question)),
            "key": key,
            "created_turn": meta.get("created_turn", max(0, current_turn - 1)),
            "last_seen_turn": meta.get("last_seen_turn", max(0, current_turn - 1)),
        }

    for question in new_questions:
        key = _question_key(str(question))
        if not key:
            continue
        if key in by_key:
            by_key[key]["text"] = strip_meta_prefix(str(question))
            by_key[key]["last_seen_turn"] = current_turn
        else:
            by_key[key] = {
                "text": strip_meta_prefix(str(question)),
                "key": key,
                "created_turn": current_turn,
                "last_seen_turn": current_turn,
            }

    active = []
    closed = list(protocol.get("closed_open_questions") or [])
    for item in by_key.values():
        if current_turn - int(item.get("last_seen_turn", current_turn)) > max_age_turns:
            closed.append({**item, "closed_turn": current_turn, "reason": "stale"})
        else:
            active.append(item)
    active.sort(key=lambda item: (item.get("last_seen_turn", 0), item.get("created_turn", 0)), reverse=True)
    protocol["open_question_meta"] = active
    protocol["open_questions"] = [item["text"] for item in active]
    protocol["closed_open_questions"] = closed[-50:]


def ensure_snapshot(session: dict[str, Any], protocol: dict[str, Any] | None = None, change_summary: dict[str, Any] | None = None) -> None:
    session.setdefault("snapshots", [])
    turn_index = len(session.get("turns") or [])
    if session["snapshots"] and session["snapshots"][-1].get("turn_index") == turn_index:
        session["snapshots"][-1]["protocol"] = copy.deepcopy(protocol or session.get("protocol") or {})
        session["snapshots"][-1]["change_summary"] = change_summary or {}
        return
    session["snapshots"].append({
        "turn_index": turn_index,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "protocol": copy.deepcopy(protocol or session.get("protocol") or {}),
        "change_summary": change_summary or {},
    })


def protocol_after_turn(session: dict[str, Any], turn_index: int) -> dict[str, Any]:
    if turn_index <= 0:
        return copy.deepcopy(EMPTY_PROTOCOL)
    for turn in session.get("turns") or []:
        if int(turn.get("turn_index") or 0) == turn_index:
            return copy.deepcopy(turn.get("protocol_after") or {})
    for snap in session.get("snapshots") or []:
        if int(snap.get("turn_index") or 0) == turn_index:
            return copy.deepcopy(snap.get("protocol") or {})
    raise KeyError(f"turn not found: {turn_index}")


def append_revert_turn(session: dict[str, Any], *, target_turn: int, protocol: dict[str, Any], reason: str) -> None:
    before = copy.deepcopy(session.get("protocol") or {})
    session["protocol"] = copy.deepcopy(protocol)
    turn = {
        "turn_index": len(session.get("turns") or []) + 1,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "stage_before": (session.get("last_response") or {}).get("stage") or "discover",
        "stage_after": "reverted",
        "user": reason,
        "assistant": f"已回滚到第 {target_turn} 轮之后的协议快照。",
        "next_questions": [],
        "quality_notes": [],
        "changes": {"reverted_to_turn": target_turn},
        "protocol_before": before,
        "protocol_after": copy.deepcopy(protocol),
        "revert_to_turn": target_turn,
    }
    session.setdefault("turns", []).append(turn)
    session["last_response"] = {
        "assistant_message": turn["assistant"],
        "stage": "reverted",
        "protocol": copy.deepcopy(protocol),
        "next_questions": [],
        "quality_notes": [],
        "trace": [{"step": "revert", "target_turn": target_turn}],
        "fallback": False,
    }
    session.setdefault("history", []).append({"role": "user", "content": reason})
    session["history"].append({"role": "assistant", "content": turn["assistant"]})
    ensure_snapshot(session, protocol, {"reverted_to_turn": target_turn})


@dataclass
class ValidationIssue:
    level: str
    message: str
    suggestion: str = ""


@dataclass
class ValidationReport:
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def fails(self) -> list[ValidationIssue]:
        return [item for item in self.issues if item.level == "FAIL"]

    @property
    def warns(self) -> list[ValidationIssue]:
        return [item for item in self.issues if item.level == "WARN"]

    def add(self, level: str, message: str, suggestion: str = "") -> None:
        self.issues.append(ValidationIssue(level, message, suggestion))

    def to_dict(self) -> dict[str, Any]:
        return {"issues": [item.__dict__ for item in self.issues], "fail": len(self.fails), "warn": len(self.warns)}

    def text(self) -> str:
        if not self.issues:
            return "✓ 协议形式校验通过\nTotal: 0 fail, 0 warn"
        lines = []
        for item in self.issues:
            mark = "✗ FAIL" if item.level == "FAIL" else "✗ WARN"
            lines.append(f"{mark}: {item.message}")
            if item.suggestion:
                lines.append(f"        Suggest: {item.suggestion}")
        lines.append(f"Total: {len(self.fails)} fail, {len(self.warns)} warn")
        return "\n".join(lines)


def validate_protocol(protocol: dict[str, Any]) -> ValidationReport:
    report = ValidationReport()
    ops = operation_map(protocol)
    objs = object_map(protocol)
    object_names_lower = {name.lower() for name in objs}

    for name, op in ops.items():
        if not (op.get("validators") or []):
            report.add("FAIL", f"operation {name} has no validators", "add at least one deterministic validator")
        schema = op.get("input_schema") or {}
        if not schema or not schema.get("properties"):
            report.add("FAIL", f"operation {name} has empty input_schema", "define input_schema.properties and required fields")
        if not op.get("failure_policy"):
            report.add("FAIL", f"operation {name} has empty failure_policy", "describe retry/clarification/rejection behavior")
        if str(op.get("risk") or "").lower() == "high" and not op.get("requires_confirmation"):
            desc = (op.get("description") or "") + (op.get("failure_policy") or "")
            if "无需确认" not in desc and "不要求确认" not in desc and "例外" not in desc:
                report.add("FAIL", f"high risk operation {name} requires_confirmation is false", "set requires_confirmation=true or document an explicit exception")

        for validator in op.get("validators") or []:
            v = str(validator)
            if "pending_confirmation" in v and not any("pending" in obj and "confirmation" in obj for obj in object_names_lower):
                report.add("FAIL", f"{name}.validators references {v!r} but no PendingConfirmation object defined", "add PendingConfirmation object or remove/rename the validator")

    text_blob = "\n".join([str(x) for x in (protocol.get("confirmation_rules") or []) + (protocol.get("clarification_rules") or [])])
    for name in re.findall(r"[a-z][a-z0-9_]{3,}", text_blob):
        if name.endswith("_sections") or name.endswith("_artifact") or name in ops:
            if name not in ops:
                report.add("WARN", f"rule may reference missing operation {name}", "check rule text or add matching operation")

    for name, obj in objs.items():
        haystack = json.dumps(protocol.get("operations", []), ensure_ascii=False) + "\n" + text_blob
        if name not in haystack and name.lower() not in haystack.lower():
            report.add("WARN", f"object {name} appears unused", "reference it from operation schemas/rules or remove it")

    if not report.issues:
        report.add("OK", "All P0 validation checks passed")
        report.issues = []
    return report


def render_planner_prompt_v2(protocol: dict[str, Any]) -> str:
    ops = protocol.get("operations") or []
    op_blocks = []
    for op in ops:
        op_blocks.append(textwrap.dedent(f"""
        ## {op.get('name')}
        - 说明：{op.get('description') or ''}
        - 风险：{op.get('risk') or 'unknown'}
        - 需要确认：{op.get('requires_confirmation')}
        - 参数结构：
        ```json
        {json.dumps(op.get('input_schema') or {}, ensure_ascii=False, indent=2)}
        ```
        """).strip())
    confirmation = "\n".join(f"- {rule}" for rule in protocol.get("confirmation_rules") or []) or "- 无"
    clarification = "\n".join(f"- {rule}" for rule in protocol.get("clarification_rules") or []) or "- 无"
    state_fields = sorted({field for obj in protocol.get("objects") or [] for field in (obj.get("key_fields") or [])})
    context_policy = protocol.get("context_policy") or {}
    intent_policy = protocol.get("intent_recognition") or {}
    routing_policy = protocol.get("routing_policy") or {}
    context_fields = "\n".join(f"- {field}" for field in context_policy.get("context_pack_fields", []) or []) or "- 调用方提供的最小 state 摘要"
    reference_rules = "\n".join(f"- {rule}" for rule in intent_policy.get("reference_resolution_rules", []) or []) or "- 指代不清时必须追问"
    ambiguity_rules = "\n".join(f"- {rule}" for rule in intent_policy.get("ambiguity_policy", []) or []) or "- 目标不唯一时必须追问"
    forbidden_routes = "\n".join(f"- {rule}" for rule in routing_policy.get("forbidden_routes", []) or []) or "- 不要绕过确认和追问规则"
    return textwrap.dedent(f"""
    # Planner Prompt v2

    你是受控规划器（Planner）。你的任务是把用户自然语言转换成协议允许的一个操作（operation）计划。
    你不能执行工具，不能修改状态，只能输出 JSON。

    ## 可读取状态字段
    {', '.join(state_fields) if state_fields else '暂无明确字段；只能读取调用方提供的 state 摘要。'}

    ## 上下文包字段
    {context_fields}

    ## 指代消解规则
    {reference_rules}

    ## 歧义处理规则
    {ambiguity_rules}

    ## 禁止路由
    {forbidden_routes}

    ## 可用操作
    {chr(10).join(op_blocks) if op_blocks else '暂无操作。'}

    ## 必须先创建确认请求的条件
    {confirmation}

    ## 必须先追问而不是直接路由的条件
    {clarification}

    ## 输出 JSON 结构
    ```json
    {{
      "intent": "用户意图摘要",
      "operation": "operation_name 或 ask_clarification",
      "target": {{"type": "section|selection|document|object|unknown", "id": null, "source": "explicit|context|unknown"}},
      "params": {{}},
      "confidence": 0.0,
      "evidence": ["为什么这样理解"],
      "confirmation_required": false,
      "clarification_question": null,
      "reason": "为什么这样路由"
    }}
    ```

    ## 规则
    - operation 必须来自可用操作列表；不确定时输出 ask_clarification。
    - 命中确认规则时，confirmation_required 必须为 true，不能直接执行。
    - 命中追问规则时，operation 必须为 ask_clarification，并填写 clarification_question。
    - 不要编造 state 中不存在的对象 ID。
    - Planner 可以充分理解和推理，但最终只能输出协议允许的操作（operation）或追问/拒绝出口。
    - 局部编辑请求不得默认扩大为全文重写；除非用户明确说明全文范围。
    """).strip() + "\n"


def render_executor_skeleton_v2(protocol: dict[str, Any]) -> str:
    ops = operation_map(protocol)
    state_fields = sorted({field for obj in protocol.get("objects") or [] for field in (obj.get("key_fields") or [])})
    dataclass_fields = [f"    {safe_py_name(field)}: Any = None" for field in state_fields]
    if not dataclass_fields:
        dataclass_fields = ["    data: dict[str, Any] = field(default_factory=dict)"]

    handler_blocks: list[str] = []
    mapping_lines: list[str] = []
    for name in ops:
        py_name = safe_py_name(name)
        handler_block = (
            "def handle_{py_name}(state: State, params: dict[str, Any]) -> tuple[State, dict[str, Any]]:\n"
            "    failed = run_validators(PROTOCOL, {name_json}, state, params)\n"
            "    if failed:\n"
            "        return state, {{\"status\": \"validator_failed\", \"failed\": failed}}\n"
            "    clarification = check_clarification_rules(PROTOCOL, {name_json}, state, params)\n"
            "    if clarification.required:\n"
            "        return state, {{\"status\": \"clarification_required\", \"rule\": clarification.rule}}\n"
            "    confirmation = check_confirmation_rules(PROTOCOL, {name_json}, state, params)\n"
            "    if confirmation.required:\n"
            "        return state, {{\"status\": \"confirmation_required\", \"rule\": confirmation.rule}}\n"
            "    raise NotImplementedError(\"Fill in actual operation logic for {name}\")"
        ).format(py_name=py_name, name_json=json.dumps(name), name=name)
        handler_blocks.append(handler_block)
        mapping_lines.append(f'    {json.dumps(name)}: handle_{py_name},')
    if not handler_blocks:
        handler_blocks.append('def handle_noop(state: State, params: dict[str, Any]) -> tuple[State, dict[str, Any]]:\n    return state, {"status": "noop"}')

    skeleton = (
        "from __future__ import annotations\n\n"
        "import json\n"
        "from dataclasses import dataclass, field\n"
        "from pathlib import Path\n"
        "from typing import Any\n\n"
        "PROTOCOL = json.loads(Path(\"protocol.json\").read_text(encoding=\"utf-8\"))\n\n\n"
        "@dataclass\n"
        "class RuleDecision:\n"
        "    required: bool = False\n"
        "    rule: str = \"\"\n\n\n"
        "@dataclass\n"
        "class State:\n"
        + "\n".join(dataclass_fields)
        + "\n\n\n"
        "def operation_spec(protocol: dict[str, Any], operation_name: str) -> dict[str, Any]:\n"
        "    for op in protocol.get(\"operations\", []):\n"
        "        if op.get(\"name\") == operation_name:\n"
        "            return op\n"
        "    raise KeyError(f\"Unknown operation: {operation_name}\")\n\n\n"
        "def run_validators(protocol: dict[str, Any], operation_name: str, state: State, params: dict[str, Any]) -> list[str]:\n"
        "    op = operation_spec(protocol, operation_name)\n"
        "    failed: list[str] = []\n"
        "    schema = op.get(\"input_schema\") or {}\n"
        "    required = schema.get(\"required\") or []\n"
        "    for field_name in required:\n"
        "        if field_name not in params or params.get(field_name) in (None, \"\"):\n"
        "            failed.append(f\"missing required param: {field_name}\")\n"
        "    # TODO: map protocol validator names to deterministic Python checks.\n"
        "    return failed\n\n\n"
        "def _rule_mentions_operation(rule: str, operation_name: str) -> bool:\n"
        "    return operation_name in rule or operation_name.replace(\"_\", \" \") in rule\n\n\n"
        "def check_confirmation_rules(protocol: dict[str, Any], operation_name: str, state: State, params: dict[str, Any]) -> RuleDecision:\n"
        "    op = operation_spec(protocol, operation_name)\n"
        "    if op.get(\"requires_confirmation\"):\n"
        "        return RuleDecision(True, \"operation requires confirmation\")\n"
        "    for rule in protocol.get(\"confirmation_rules\", []):\n"
        "        if _rule_mentions_operation(str(rule), operation_name):\n"
        "            return RuleDecision(True, str(rule))\n"
        "    return RuleDecision(False, \"\")\n\n\n"
        "def check_clarification_rules(protocol: dict[str, Any], operation_name: str, state: State, params: dict[str, Any]) -> RuleDecision:\n"
        "    for rule in protocol.get(\"clarification_rules\", []):\n"
        "        if _rule_mentions_operation(str(rule), operation_name):\n"
        "            return RuleDecision(True, str(rule))\n"
        "    return RuleDecision(False, \"\")\n\n\n"
        + "\n\n".join(handler_blocks)
        + "\n\n\nHANDLERS = {\n"
        + "\n".join(mapping_lines)
        + "\n}\n\n\n"
        "def dispatch(operation_name: str, state: State, params: dict[str, Any]) -> tuple[State, dict[str, Any]]:\n"
        "    handler = HANDLERS.get(operation_name)\n"
        "    if not handler:\n"
        "        return state, {\"status\": \"unsupported_operation\", \"operation\": operation_name}\n"
        "    return handler(state, params)\n"
    )
    return skeleton

def safe_py_name(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_]+", "_", str(value).strip())
    if not value:
        return "operation"
    if value[0].isdigit():
        return f"op_{value}"
    return value


def render_development_plan_from_artifact(protocol: dict[str, Any], fallback: str) -> str:
    artifact = ((protocol.get("artifacts") or {}).get("development_plan") or {})
    content = artifact.get("content")
    if not content:
        return fallback
    if isinstance(content, str):
        return content if content.endswith("\n") else content + "\n"
    if isinstance(content, dict) and content.get("phases"):
        lines = ["# Agent 开发落地计划", ""]
        for phase in content.get("phases") or []:
            lines.extend([f"## {phase.get('id', '')} {phase.get('name', '')}".strip(), ""])
            for task in phase.get("tasks") or []:
                lines.append(f"### {task.get('id', '')} {task.get('title', '')}".strip())
                for key in ("scope", "preconditions", "deliverables", "acceptance_criteria", "risk", "estimated_days"):
                    val = task.get(key)
                    if isinstance(val, list):
                        val = "; ".join(map(str, val))
                    lines.append(f"- {key}: {val if val not in (None, '') else '待补充'}")
                lines.append("")
        return "\n".join(lines).strip() + "\n"
    return json.dumps(content, ensure_ascii=False, indent=2) + "\n"
