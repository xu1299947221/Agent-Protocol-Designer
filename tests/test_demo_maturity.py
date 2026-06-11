from webui_server import build_demo_maturity_report


def test_demo_maturity_explains_mock_gap_and_next_task():
    report = build_demo_maturity_report(
        {
            "operations": [{"name": "draft_section"}],
            "workflow": {"nodes": [{"id": "draft"}]},
            "tool_registry": {"tools": [{"name": "doc_writer"}]},
        },
        {
            "op_call": {"operation": "draft_section"},
            "validator_results": [],
            "permission_check": {"status": "allowed"},
            "tool_results": [{"mode": "dry_run"}],
            "artifact_versions": [{"id": "artifact_1"}],
            "trace": [{"step": "context_pack"}],
        },
        {"node_states": {"draft": {"status": "completed"}}},
        {"trace_events": [{"step": "context_pack"}], "artifacts": [{"id": "artifact_1"}]},
        {"tools": [{"name": "doc_writer"}]},
    )

    assert report["score"] < 100
    assert report["stage"] in {"可运行骨架", "半成品 Agent", "接近可体验 Agent"}
    assert any(not item["done"] and item["id"] == "real_llm" for item in report["checks"])
    assert report["recommended_next"]["priority"] == "P0"
    assert "planner.py" in report["recommended_next"]["files"][0]
