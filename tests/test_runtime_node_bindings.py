from protocol_designer.workflow_runtime import build_runtime_plan, run_workflow_once


def test_runtime_binds_nodes_to_tool_validator_rag_llm_and_human_review():
    protocol = {
        "domain_summary": "招投标 Agent，解析招标文件，检索素材，生成投标目录。",
        "operations": [
            {"name": "parse_tender", "description": "解析招标文件", "validators": ["file_required"]},
            {"name": "generate_outline", "description": "生成投标目录", "llm_role": "根据需求矩阵生成目录 JSON"},
        ],
        "tool_registry": {
            "tools": [
                {"name": "file_parser", "description": "解析文件"},
                {"name": "rag_search", "description": "检索知识库"},
            ],
            "operation_tool_bindings": [
                {"operation": "parse_tender", "tools": ["file_parser"]},
            ],
        },
        "knowledge_policy": {
            "knowledge_bases": [{"name": "企业素材库"}],
            "rag_required_operations": ["retrieve_materials"],
        },
        "workflow": {
            "nodes": [
                {"id": "parse", "type": "tool", "name": "解析招标文件", "operation": "parse_tender"},
                {"id": "validate", "type": "validator", "name": "校验解析结果", "validators": ["file_required"], "depends_on": ["parse"]},
                {"id": "retrieve", "type": "rag", "name": "检索企业素材", "depends_on": ["validate"]},
                {"id": "outline", "type": "agent", "name": "生成目录", "operation": "generate_outline", "depends_on": ["retrieve"]},
                {"id": "confirm", "type": "human_review", "name": "确认目录", "depends_on": ["outline"]},
            ],
        },
    }

    plan = build_runtime_plan(protocol)
    bindings = {item["node_id"]: item for item in plan["implementation_bindings"]}

    assert bindings["parse"]["binding_type"] == "tool"
    assert bindings["parse"]["target"] == ["file_parser"]
    assert bindings["validate"]["binding_type"] == "validator"
    assert bindings["retrieve"]["binding_type"] == "rag"
    assert bindings["outline"]["binding_type"] == "llm_prompt"
    assert bindings["confirm"]["binding_type"] == "human_review"


def test_runtime_result_contains_implementation_binding_and_execution_preview():
    protocol = {
        "operations": [{"name": "generate_outline", "description": "生成目录", "llm_role": "输出目录 JSON"}],
        "workflow": {"nodes": [{"id": "outline", "type": "agent", "name": "生成目录", "operation": "generate_outline"}]},
    }

    result = run_workflow_once(protocol=protocol, user_message="生成目录")

    state = result["node_states"]["outline"]
    assert result["implementation_bindings"]
    assert state["implementation_binding"]["binding_type"] == "llm_prompt"
    assert state["execution_preview"]["mode"] == "llm_call"
    assert result["trace"][0]["implementation_binding"]["binding_type"] == "llm_prompt"
