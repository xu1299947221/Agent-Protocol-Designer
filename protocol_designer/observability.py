from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _load_json_files(directory: Path) -> list[dict[str, Any]]:
    items = []
    if not directory.exists():
        return items
    for path in directory.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(data, dict):
            items.append(data)
    return items


def _runtime_metrics(jobs: list[dict[str, Any]]) -> dict[str, Any]:
    statuses: dict[str, int] = {}
    trace_events = 0
    artifact_count = 0
    waiting = 0
    rollback_points = 0
    for job in jobs:
        status = str(job.get("status") or "unknown")
        statuses[status] = statuses.get(status, 0) + 1
        if job.get("waiting_for") or ((job.get("result") or {}).get("waiting_for")):
            waiting += 1
        result = job.get("result") or {}
        trace_events += len(result.get("trace") or [])
        artifact_count += len(result.get("artifacts") or [])
        rollback_points += len(((result.get("artifact_versions") or {}).get("rollback_points") or []))
    return {
        "total": len(jobs),
        "statuses": statuses,
        "waiting_for_human": waiting,
        "trace_events": trace_events,
        "artifact_count": artifact_count,
        "rollback_points": rollback_points,
    }


def _tool_metrics(runs: list[dict[str, Any]]) -> dict[str, Any]:
    total_tools = passed = failed = needs_confirmation = 0
    for run in runs:
        summary = run.get("summary") or {}
        total_tools += int(summary.get("total") or 0)
        passed += int(summary.get("passed") or 0)
        failed += int(summary.get("failed") or 0)
        needs_confirmation += int(summary.get("needs_confirmation") or 0)
    return {
        "runs": len(runs),
        "tool_tests": total_tools,
        "passed": passed,
        "failed": failed,
        "needs_confirmation": needs_confirmation,
        "failure_rate": round(failed / max(1, total_tools), 3),
    }


def _eval_metrics(replays: list[dict[str, Any]]) -> dict[str, Any]:
    total_cases = passed = failed = 0
    for replay in replays:
        summary = replay.get("summary") or {}
        total_cases += int(summary.get("total") or 0)
        passed += int(summary.get("passed") or 0)
        failed += int(summary.get("failed") or 0)
    return {
        "runs": len(replays),
        "cases": total_cases,
        "passed": passed,
        "failed": failed,
        "pass_rate": round(passed / max(1, total_cases), 3),
    }


def _registry_metrics(agents: list[dict[str, Any]]) -> dict[str, Any]:
    version_count = sum(len(agent.get("versions") or []) for agent in agents)
    return {"agents": len(agents), "versions": version_count}


def build_observability_summary(
    *,
    runtime_dir: Path,
    tool_run_dir: Path,
    eval_replay_dir: Path,
    agent_registry_dir: Path,
) -> dict[str, Any]:
    runtime_jobs = _load_json_files(runtime_dir)
    tool_runs = _load_json_files(tool_run_dir)
    eval_replays = _load_json_files(eval_replay_dir)
    agents = _load_json_files(agent_registry_dir)
    runtime = _runtime_metrics(runtime_jobs)
    tools = _tool_metrics(tool_runs)
    evals = _eval_metrics(eval_replays)
    registry = _registry_metrics(agents)
    recommendations = []
    if runtime["waiting_for_human"]:
        recommendations.append("存在等待人工确认的 Runtime Job，建议先处理确认点。")
    if tools["needs_confirmation"]:
        recommendations.append("存在高风险或有副作用工具，真实执行前需要权限确认和沙箱策略。")
    if evals["failed"]:
        recommendations.append("Eval 回放存在失败 case，应回到协议、workflow、tool binding 或 validator 修正。")
    if not runtime_jobs:
        recommendations.append("还没有 Runtime Job，建议先运行一次 Runtime。")
    if not recommendations:
        recommendations.append("当前观测指标正常，可以继续沉淀更多 Runtime Job、Tool Run 和 Eval Replay。")
    return {
        "runtime_jobs": runtime,
        "tool_runs": tools,
        "eval_replays": evals,
        "agent_registry": registry,
        "health": {
            "runtime_has_waiting": bool(runtime["waiting_for_human"]),
            "tool_failure_rate": tools["failure_rate"],
            "eval_pass_rate": evals["pass_rate"],
        },
        "recommendations": recommendations,
        "raw_counts": {
            "runtime_job_files": len(runtime_jobs),
            "tool_run_files": len(tool_runs),
            "eval_replay_files": len(eval_replays),
            "agent_registry_files": len(agents),
        },
    }
