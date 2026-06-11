import asyncio
import importlib
import py_compile
import sys

from protocol_designer.generator import generate_project_scaffold


def test_generated_scaffold_is_runnable_harness_demo(tmp_path):
    session = {
        "title": "写作 Agent Demo",
        "protocol": {
            "project_name": "writing-agent-demo",
            "domain_summary": "写作 Agent，帮助用户生成、修改和导出文档。",
            "operations": [
                {"name": "draft_section", "description": "生成章节内容", "validators": ["section_required"]},
                {"name": "export_document", "description": "导出 DOCX 文档", "risk": "high"},
            ],
            "tool_registry": {
                "tools": [{"name": "doc_writer", "description": "写入文档草稿"}],
                "operation_tool_bindings": [{"operation": "draft_section", "tools": ["doc_writer"]}],
            },
            "workflow": {
                "nodes": [
                    {"id": "draft", "name": "生成章节", "type": "agent", "operation": "draft_section"},
                    {"id": "export", "name": "导出文档", "type": "tool", "operation": "export_document", "depends_on": ["draft"]},
                ]
            },
        },
    }

    written = generate_project_scaffold(session, tmp_path, project_name="writing-agent-demo", force=True)
    project = tmp_path / "writing-agent-demo"

    assert written
    assert (project / "backend/app/harness.py").exists()
    assert (project / "backend/app/store.py").exists()
    assert (project / "backend/app/tools.py").exists()
    assert (project / "backend/app/workflow.py").exists()
    assert (project / "backend/requirements.txt").exists()
    assert (project / "docs/eval_policy.md").exists()
    assert (project / "docs/development_advice.md").exists()
    assert "Agent Harness Demo" in (project / "README.md").read_text(encoding="utf-8")

    for path in (project / "backend/app").glob("*.py"):
        py_compile.compile(str(path), doraise=True)

    sys.path.insert(0, str(project / "backend"))
    try:
        runtime = importlib.import_module("app.runtime")
        workflow = importlib.import_module("app.workflow")
        store = importlib.import_module("app.store")
        tools = importlib.import_module("app.tools")
        models = importlib.import_module("app.models")
        normal = asyncio.run(runtime.run_agent(models.AgentRequest(message="请生成章节内容", context={"current_state": "drafting"})))
        high_risk = asyncio.run(runtime.run_agent(models.AgentRequest(message="导出 DOCX 文档", context={})))
        workflow_result = asyncio.run(workflow.run_workflow("请生成章节内容", {"current_state": "drafting"}, max_steps=2))
        store_snapshot = store.STORE.snapshot()
        tool_list = tools.list_tools()
    finally:
        sys.path.remove(str(project / "backend"))
        for name in list(sys.modules):
            if name == "app" or name.startswith("app."):
                sys.modules.pop(name, None)

    normal_data = normal.model_dump() if hasattr(normal, "model_dump") else normal.dict()
    high_risk_data = high_risk.model_dump() if hasattr(high_risk, "model_dump") else high_risk.dict()

    assert normal_data["context_pack"]
    assert normal_data["permission_check"]["status"] == "allowed"
    assert normal_data["next_action"] == "simulated"
    assert normal_data["trace"]
    assert normal_data["tool_results"]
    assert normal_data["state_snapshot"]
    assert normal_data["artifact_versions"]
    assert workflow_result["node_states"]
    assert store_snapshot["trace_events"]
    assert tool_list and tool_list[0]["name"] == "doc_writer"
    assert high_risk_data["permission_check"]["status"] == "requires_confirmation"
    assert high_risk_data["eval_case_suggestion"]["should_record"] is True
