from protocol_designer.demo_playground import DemoPlaygroundManager


def test_demo_playground_starts_runs_and_stops_generated_demo(tmp_path):
    manager = DemoPlaygroundManager(tmp_path)
    session = {
        "session_id": "demo-session",
        "title": "Demo Playground Agent",
        "protocol": {
            "project_name": "demo-playground-agent",
            "domain_summary": "测试 Demo Playground。",
            "operations": [
                {"name": "draft_section", "description": "生成章节内容", "validators": ["section_required"]},
            ],
            "tool_registry": {
                "tools": [{"name": "doc_writer", "description": "写文档"}],
                "operation_tool_bindings": [{"operation": "draft_section", "tools": ["doc_writer"]}],
            },
            "workflow": {"nodes": [{"id": "draft", "name": "生成章节", "operation": "draft_section"}]},
        },
    }

    demo = manager.start_demo(session)
    try:
        assert demo["status"] == "running"
        agent = manager.run_agent(demo["demo_id"], "请生成章节内容", {"current_state": "drafting"})
        workflow = manager.run_workflow(demo["demo_id"], "请生成章节内容", {"current_state": "drafting"}, 2)
        store = manager.get_store_snapshot(demo["demo_id"])
        tools = manager.get_tools(demo["demo_id"])

        assert agent["tool_results"]
        assert agent["artifact_versions"]
        assert workflow["node_states"]
        assert store["trace_events"]
        assert tools["tools"][0]["name"] == "doc_writer"
    finally:
        stopped = manager.stop_demo(demo["demo_id"])
        assert stopped["status"] == "stopped"
