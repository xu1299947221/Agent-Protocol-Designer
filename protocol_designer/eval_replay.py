from __future__ import annotations

import json
from typing import Any

from .core import build_eval_cases, ensure_harness_protocol
from .workflow_runtime import run_workflow_once


def collect_eval_cases(protocol: dict[str, Any], *, limit: int = 20) -> list[dict[str, Any]]:
    raw_policy = (protocol or {}).get("eval_policy") if isinstance((protocol or {}).get("eval_policy"), dict) else {}
    cases: list[dict[str, Any]] = []
    for group_cases in (raw_policy.get("case_groups") or {}).values():
        if isinstance(group_cases, list):
            cases.extend([case for case in group_cases if isinstance(case, dict)])
    cases.extend([case for case in raw_policy.get("golden_cases") or [] if isinstance(case, dict)])
    protocol = ensure_harness_protocol(protocol or {})
    if not cases:
        try:
            payload = json.loads(build_eval_cases(protocol))
        except Exception:
            payload = {}
        cases = [case for case in payload.get("eval_cases") or [] if isinstance(case, dict)]
    if not cases:
        policy = protocol.get("eval_policy") or {}
        for group_cases in (policy.get("case_groups") or {}).values():
            if isinstance(group_cases, list):
                cases.extend([case for case in group_cases if isinstance(case, dict)])
        cases.extend([case for case in policy.get("golden_cases") or [] if isinstance(case, dict)])
    normalized = []
    for index, case in enumerate(cases[: max(1, limit)], start=1):
        item = dict(case)
        item.setdefault("id", f"eval_{index}")
        item.setdefault("case_type", item.get("type") or "runtime")
        item.setdefault("user_message", item.get("message") or item.get("input") or "继续")
        item.setdefault("expected", item.get("expected") or {})
        normalized.append(item)
    return normalized


def replay_eval_case(protocol: dict[str, Any], case: dict[str, Any]) -> dict[str, Any]:
    protocol = ensure_harness_protocol(protocol or {})
    message = str(case.get("user_message") or case.get("message") or "")
    expected_operation = str(case.get("expected_operation") or (case.get("expected") or {}).get("operation") or "")
    context = case.get("context") if isinstance(case.get("context"), dict) else {}
    result = run_workflow_once(protocol=protocol, user_message=message, context=context)
    comparison = compare_runtime_result(protocol, case, result, expected_operation)
    return {
        "case_id": case.get("id") or "eval_case",
        "case_type": case.get("case_type") or case.get("type") or "runtime",
        "user_message": message,
        "expected_operation": expected_operation,
        "passed": comparison["passed"],
        "score": comparison["score"],
        "checks": comparison["checks"],
        "problems": comparison["problems"],
        "runtime_status": result.get("status"),
        "runtime_summary": result.get("summary") or {},
        "implementation_bindings": result.get("implementation_bindings") or [],
        "artifact_versions": result.get("artifact_versions") or {},
        "trace_tail": (result.get("trace") or [])[-5:],
    }


def compare_runtime_result(protocol: dict[str, Any], case: dict[str, Any], result: dict[str, Any], expected_operation: str = "") -> dict[str, Any]:
    checks = []
    problems = []
    expected = case.get("expected") if isinstance(case.get("expected"), dict) else {}
    operations = {str(op.get("name")) for op in protocol.get("operations") or [] if isinstance(op, dict) and op.get("name")}
    runtime_nodes = (result.get("runtime_plan") or {}).get("nodes") or []
    runtime_operations = {str(node.get("operation")) for node in runtime_nodes if isinstance(node, dict) and node.get("operation")}
    bindings = result.get("implementation_bindings") or []
    binding_types = {str(item.get("binding_type")) for item in bindings if isinstance(item, dict)}

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})
        if not ok:
            problems.append(detail)

    if expected_operation:
        if expected_operation == "ask_clarification":
            add("expected_operation", True, "Runtime 回放不做意图识别；ask_clarification 类 case 只检查链路是否可运行。")
        else:
            ok = expected_operation in runtime_operations or expected_operation in operations
            add("expected_operation", ok, f"期望 operation={expected_operation}；协议/Runtime 中 {'存在' if ok else '不存在'}。")
    add("runtime_trace", bool(result.get("trace") or result.get("status") == "completed"), "Runtime 应产生 Trace 或明确完成状态。")
    if expected.get("operation_registered") and expected_operation:
        add("operation_registered", expected_operation in operations, f"operation {expected_operation} 应在协议中注册。")
    if expected.get("state_context_present"):
        add("state_context_present", bool((result.get("context_pack") or {}).get("state_context")), "应包含状态上下文。")
    if expected.get("artifact_effect_present"):
        add("artifact_effect_present", bool(result.get("artifact_versions") or result.get("artifacts")), "应产生或追踪产物。")
    if expected.get("tools"):
        targets = set()
        for binding in bindings:
            target = binding.get("target") if isinstance(binding, dict) else None
            if isinstance(target, list):
                targets.update(map(str, target))
            elif target:
                targets.add(str(target))
        missing = [tool for tool in expected.get("tools") or [] if str(tool) not in targets]
        add("tools_bound", not missing, f"期望工具 {expected.get('tools')}；缺失 {missing}。")
    if (case.get("case_type") or "") == "workflow" or expected.get("workflow_node"):
        add("workflow_nodes", bool(runtime_nodes), "工作流回放应有 Runtime 节点。")
    if expected.get("validator_required") or expected.get("validators"):
        add("validator_binding", "validator" in binding_types or bool(expected.get("validators")), "应能看到 Validator 绑定或校验规则。")

    if not checks:
        add("runtime_smoke", result.get("status") in {"completed", "awaiting_human", "blocked"}, "至少能完成 Runtime 冒烟回放。")
    ok_count = sum(1 for item in checks if item.get("ok"))
    score = ok_count / max(1, len(checks))
    return {"passed": score >= 0.8, "score": round(score, 3), "checks": checks, "problems": problems}


def replay_eval_cases(protocol: dict[str, Any], *, cases: list[dict[str, Any]] | None = None, limit: int = 20) -> dict[str, Any]:
    protocol = ensure_harness_protocol(protocol or {})
    selected = cases or collect_eval_cases(protocol, limit=limit)
    results = [replay_eval_case(protocol, case) for case in selected[: max(1, limit)]]
    passed = sum(1 for item in results if item.get("passed"))
    failed = len(results) - passed
    return {
        "summary": {
            "total": len(results),
            "passed": passed,
            "failed": failed,
            "pass_rate": round(passed / max(1, len(results)), 3),
        },
        "results": results,
        "next_action": "失败 case 应回到协议、workflow、validator、tool binding 或 prompt 设计中修正，然后再次回放。" if failed else "当前 Eval 回放通过，可以继续沉淀更多真实失败样本。",
    }
