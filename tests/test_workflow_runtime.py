from protocol_designer.workflow_runtime import build_runtime_plan, run_workflow_once


def test_runtime_plan_uses_workflow_nodes():
    protocol = {
        "project_name": "招投标 Agent",
        "workflow": {
            "name": "投标文件生成流程",
            "nodes": [
                {"id": "parse_tender", "type": "tool", "name": "解析招标文件", "outputs": ["parsed_tender"]},
                {"id": "confirm_matrix", "type": "human_review", "name": "确认需求矩阵", "depends_on": ["parse_tender"]},
            ],
            "human_review_points": [
                {"id": "review_matrix", "description": "确认评分项和废标项", "required_before": "confirm_matrix"}
            ],
        },
    }

    plan = build_runtime_plan(protocol, "生成投标文件")

    assert plan["runtime_name"] == "投标文件生成流程"
    assert len(plan["nodes"]) == 2
    assert plan["nodes"][1]["requires_confirmation"] is True


def test_runtime_pauses_on_human_review_node():
    protocol = {
        "workflow": {
            "nodes": [
                {"id": "parse_tender", "type": "tool", "name": "解析招标文件", "outputs": ["parsed_tender"]},
                {"id": "confirm_matrix", "type": "human_review", "name": "确认需求矩阵", "depends_on": ["parse_tender"]},
            ],
            "human_review_points": [
                {"id": "review_matrix", "description": "确认评分项和废标项", "required_before": "confirm_matrix"}
            ],
        },
    }

    result = run_workflow_once(protocol=protocol, user_message="生成投标文件")

    assert result["status"] == "awaiting_human"
    assert result["node_states"]["parse_tender"]["status"] == "completed"
    assert result["node_states"]["confirm_matrix"]["status"] == "awaiting_human"
    assert result["waiting_for"]["node_id"] == "confirm_matrix"
    assert result["artifacts"]


def test_runtime_continues_when_approval_is_provided():
    protocol = {
        "workflow": {
            "nodes": [
                {"id": "parse_tender", "type": "tool", "name": "解析招标文件", "outputs": ["parsed_tender"]},
                {"id": "confirm_matrix", "type": "human_review", "name": "确认需求矩阵", "depends_on": ["parse_tender"], "outputs": ["confirmed_matrix"]},
            ],
            "human_review_points": [
                {"id": "review_matrix", "description": "确认评分项和废标项", "required_before": "confirm_matrix"}
            ],
        },
    }

    result = run_workflow_once(
        protocol=protocol,
        user_message="生成投标文件",
        approvals={"confirm_matrix": True},
    )

    assert result["status"] == "completed"
    assert result["summary"]["completed_nodes"] == 2
    assert result["waiting_for"] is None


def test_runtime_resume_skips_completed_nodes_after_confirmation():
    protocol = {
        "workflow": {
            "nodes": [
                {"id": "parse_tender", "type": "tool", "name": "解析招标文件", "outputs": ["parsed_tender"]},
                {"id": "confirm_matrix", "type": "human_review", "name": "确认需求矩阵", "depends_on": ["parse_tender"], "outputs": ["confirmed_matrix"]},
                {"id": "generate_outline", "type": "agent", "name": "生成目录", "depends_on": ["confirm_matrix"], "outputs": ["bid_outline"]},
            ],
            "human_review_points": [
                {"id": "review_matrix", "description": "确认评分项和废标项", "required_before": "confirm_matrix"}
            ],
        },
    }
    paused = run_workflow_once(protocol=protocol, user_message="生成投标文件")

    resumed = run_workflow_once(
        protocol=protocol,
        user_message="生成投标文件",
        approvals={"confirm_matrix": True},
        existing_result=paused,
    )

    assert resumed["status"] == "completed"
    assert resumed["node_states"]["parse_tender"]["status"] == "completed"
    assert resumed["node_states"]["confirm_matrix"]["status"] == "completed"
    assert resumed["node_states"]["generate_outline"]["status"] == "completed"
    assert len([item for item in resumed["artifacts"] if item.get("producer_node") == "parse_tender"]) == 1
