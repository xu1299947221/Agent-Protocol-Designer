from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .core import ensure_harness_protocol


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _tool_map(protocol: dict[str, Any]) -> dict[str, dict[str, Any]]:
    protocol = ensure_harness_protocol(protocol or {})
    return {
        str(tool.get("name")): tool
        for tool in ((protocol.get("tool_registry") or {}).get("tools") or [])
        if isinstance(tool, dict) and tool.get("name")
    }


def _sample_value(field: str) -> Any:
    lower = field.lower()
    if "path" in lower or "file" in lower:
        return "/tmp/apd-sample-input.txt"
    if "format" in lower:
        return "json"
    if "query" in lower or "prompt" in lower:
        return "APD dry-run sample query"
    if "context" in lower or "filters" in lower or "state" in lower:
        return {}
    if "artifact" in lower:
        return "artifact_sample"
    return f"sample_{field}"


def build_tool_test_input(tool: dict[str, Any]) -> dict[str, Any]:
    inputs = tool.get("inputs") or []
    if isinstance(inputs, dict):
        inputs = list((inputs.get("properties") or {}).keys())
    return {str(field): _sample_value(str(field)) for field in inputs if field}


def build_tool_test_cases(protocol: dict[str, Any], selected_tools: list[str] | None = None) -> list[dict[str, Any]]:
    tools = _tool_map(protocol)
    names = selected_tools or list(tools)
    cases = []
    for name in names:
        tool = tools.get(str(name))
        if not tool:
            cases.append({"tool": str(name), "status": "missing", "input": {}, "expected": {"should_exist": True}})
            continue
        cases.append({
            "tool": name,
            "status": "ready",
            "input": build_tool_test_input(tool),
            "expected": {
                "outputs": tool.get("outputs") or [],
                "risk": tool.get("risk") or "unknown",
                "side_effects": tool.get("side_effects") or [],
                "failure_policy": tool.get("failure_policy") or "retry_or_ask_user",
            },
        })
    return cases


def dry_run_tool(protocol: dict[str, Any], tool_name: str, tool_input: dict[str, Any] | None = None) -> dict[str, Any]:
    tools = _tool_map(protocol)
    tool = tools.get(tool_name)
    started = _now()
    if not tool:
        return {
            "tool": tool_name,
            "status": "failed",
            "ok": False,
            "started_at": started,
            "finished_at": _now(),
            "input": tool_input or {},
            "output": {},
            "error": "tool_not_registered",
            "failure_policy": "ask_user_or_register_tool",
            "trace": [{"step": "tool_lookup", "ok": False, "detail": "工具未注册。"}],
        }
    expected_input = build_tool_test_input(tool)
    merged_input = {**expected_input, **(tool_input or {})}
    outputs = tool.get("outputs") or []
    output = {str(field): f"dry_run_{field}" for field in outputs if field}
    risk = str(tool.get("risk") or "unknown").lower()
    side_effects = tool.get("side_effects") or []
    requires_confirmation = risk == "high" or bool(side_effects)
    return {
        "tool": tool_name,
        "status": "needs_confirmation" if requires_confirmation else "passed",
        "ok": True,
        "dry_run": True,
        "started_at": started,
        "finished_at": _now(),
        "input": merged_input,
        "output": output,
        "risk": risk,
        "side_effects": side_effects,
        "requires_confirmation": requires_confirmation,
        "failure_policy": tool.get("failure_policy") or "retry_or_ask_user",
        "trace": [
            {"step": "tool_lookup", "ok": True, "detail": "工具已在 Tool Registry 中注册。"},
            {"step": "input_shape", "ok": True, "detail": "已生成 dry-run 输入，不访问真实文件或外部服务。"},
            {"step": "risk_check", "ok": True, "detail": "高风险或有副作用工具只做 dry-run，需要确认后才能真实执行。"},
            {"step": "mock_output", "ok": True, "detail": "已按 outputs 生成模拟输出。"},
        ],
    }


def run_tool_tests(protocol: dict[str, Any], selected_tools: list[str] | None = None) -> dict[str, Any]:
    cases = build_tool_test_cases(protocol, selected_tools)
    results = []
    for case in cases:
        if case.get("status") == "missing":
            results.append({
                "tool": case.get("tool"),
                "status": "failed",
                "ok": False,
                "error": "tool_not_registered",
                "input": {},
                "output": {},
                "trace": [{"step": "tool_lookup", "ok": False, "detail": "工具未注册。"}],
            })
        else:
            results.append(dry_run_tool(protocol, str(case.get("tool")), case.get("input") or {}))
    passed = sum(1 for item in results if item.get("ok"))
    needs_confirmation = sum(1 for item in results if item.get("status") == "needs_confirmation")
    failed = len(results) - passed
    return {
        "run_id": secrets.token_hex(8),
        "created_at": _now(),
        "dry_run": True,
        "summary": {
            "total": len(results),
            "passed": passed,
            "failed": failed,
            "needs_confirmation": needs_confirmation,
        },
        "results": results,
        "next_action": "高风险或有副作用工具只能 dry-run；真实执行前必须接入权限确认、沙箱和失败恢复策略。",
    }


def save_tool_run(run_dir: Path, run: dict[str, Any], *, session_id: str = "") -> dict[str, Any]:
    run_dir.mkdir(parents=True, exist_ok=True)
    run = dict(run)
    run["session_id"] = session_id
    path = run_dir / f"{run['run_id']}.json"
    path.write_text(json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8")
    return run


def list_tool_runs(run_dir: Path, session_id: str = "") -> list[dict[str, Any]]:
    runs = []
    for path in sorted(run_dir.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            run = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if session_id and run.get("session_id") != session_id:
            continue
        runs.append({
            "run_id": run.get("run_id"),
            "session_id": run.get("session_id"),
            "created_at": run.get("created_at"),
            "dry_run": run.get("dry_run"),
            "summary": run.get("summary") or {},
        })
    return runs
