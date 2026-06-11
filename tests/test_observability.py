import json

from protocol_designer.observability import build_observability_summary


def _write(directory, name, data):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_observability_summary_counts_runtime_tool_eval_and_registry(tmp_path):
    runtime_dir = tmp_path / "runtime"
    tool_dir = tmp_path / "tools"
    eval_dir = tmp_path / "evals"
    registry_dir = tmp_path / "agents"
    _write(runtime_dir, "job1.json", {
        "status": "awaiting_human",
        "waiting_for": {"node_id": "confirm"},
        "result": {
            "trace": [{"step": "a"}, {"step": "b"}],
            "artifacts": [{"id": "a1"}],
            "artifact_versions": {"rollback_points": [{"artifact_id": "a1"}]},
        },
    })
    _write(tool_dir, "run1.json", {"summary": {"total": 3, "passed": 2, "failed": 1, "needs_confirmation": 1}})
    _write(eval_dir, "eval1.json", {"summary": {"total": 4, "passed": 3, "failed": 1}})
    _write(registry_dir, "agent1.json", {"versions": [{"version_id": "v1"}, {"version_id": "v2"}]})

    summary = build_observability_summary(
        runtime_dir=runtime_dir,
        tool_run_dir=tool_dir,
        eval_replay_dir=eval_dir,
        agent_registry_dir=registry_dir,
    )

    assert summary["runtime_jobs"]["total"] == 1
    assert summary["runtime_jobs"]["waiting_for_human"] == 1
    assert summary["runtime_jobs"]["trace_events"] == 2
    assert summary["runtime_jobs"]["rollback_points"] == 1
    assert summary["tool_runs"]["failure_rate"] == 0.333
    assert summary["eval_replays"]["pass_rate"] == 0.75
    assert summary["agent_registry"]["versions"] == 2
    assert summary["recommendations"]
