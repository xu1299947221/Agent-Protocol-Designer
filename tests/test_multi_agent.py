from protocol_designer.multi_agent import analyze_multi_agent_collaboration


def test_multi_agent_infers_roles_handoffs_conflict_and_trace():
    protocol = {
        "domain_summary": "招投标 Agent，解析招标文件，检索企业素材，生成投标目录和 DOCX 文档。",
        "operations": [
            {"name": "parse_tender", "description": "解析招标文件"},
            {"name": "retrieve_materials", "description": "检索知识库素材"},
            {"name": "generate_outline", "description": "生成投标目录"},
        ],
        "tool_registry": {"tools": [{"name": "file_parser"}, {"name": "rag_search"}]},
        "permission_policy": {"high_risk_operations": ["export_docx"]},
        "artifact_model": {"artifacts": [{"id": "bid_doc", "review_required": True}]},
        "workflow": {
            "nodes": [
                {"id": "parse", "type": "tool", "name": "解析招标文件"},
                {"id": "retrieve", "type": "rag", "name": "检索企业素材", "depends_on": ["parse"]},
                {"id": "outline", "type": "agent", "name": "生成投标目录", "depends_on": ["retrieve"]},
                {"id": "review", "type": "human_review", "name": "人工确认目录", "depends_on": ["outline"]},
            ]
        },
    }

    result = analyze_multi_agent_collaboration(protocol, "根据招标文件生成投标文件")
    role_ids = {role["id"] for role in result["roles"]}

    assert "coordinator" in role_ids
    assert "researcher" in role_ids
    assert "writer" in role_ids
    assert result["summary"]["role_count"] >= 4
    assert result["summary"]["handoff_count"] == 4
    assert result["message_protocol"]["allowed_message_types"]
    assert result["handoff_contract"]["handoffs"][1]["to_agent"] == "researcher"
    assert result["conflict_policy"]["programmatic_guards"]
    assert result["collaboration_trace"][0]["event"] == "collaboration_started"


def test_multi_agent_reports_gaps_without_workflow_tools_permission_and_artifacts():
    result = analyze_multi_agent_collaboration({"domain_summary": "普通客服 Agent"}, "处理用户咨询")

    assert result["summary"]["recommended_mode"] in {"single_agent_with_review", "multi_agent_coordination"}
    assert any("workflow" in gap for gap in result["gaps"])
    assert any("工具注册表" in gap for gap in result["gaps"])
    assert any("权限策略" in gap for gap in result["gaps"])
    assert any("产物模型" in gap for gap in result["gaps"])
