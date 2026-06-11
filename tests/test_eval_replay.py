from protocol_designer.eval_replay import collect_eval_cases, replay_eval_case, replay_eval_cases


def test_collect_eval_cases_from_eval_policy_groups():
    protocol = {
        "operations": [{"name": "draft_doc", "description": "生成文档"}],
        "eval_policy": {
            "case_groups": {
                "binding_eval_cases": [
                    {"id": "case_1", "case_type": "binding", "user_message": "生成文档", "expected_operation": "draft_doc"}
                ]
            }
        },
    }

    cases = collect_eval_cases(protocol, limit=5)

    assert cases
    assert cases[0]["id"] == "case_1"
    assert cases[0]["expected_operation"] == "draft_doc"


def test_replay_eval_case_compares_expected_operation_and_trace():
    protocol = {
        "operations": [{"name": "draft_doc", "description": "生成文档", "llm_role": "输出文档 JSON"}],
        "workflow": {"nodes": [{"id": "draft", "type": "agent", "operation": "draft_doc", "outputs": ["draft_document"]}]},
    }
    case = {"id": "case_1", "case_type": "binding", "user_message": "生成文档", "expected_operation": "draft_doc"}

    result = replay_eval_case(protocol, case)

    assert result["passed"] is True
    assert result["runtime_status"] == "completed"
    assert result["implementation_bindings"]
    assert result["artifact_versions"]["versions"]


def test_replay_eval_cases_summary_counts_failures():
    protocol = {
        "operations": [{"name": "draft_doc", "description": "生成文档"}],
        "workflow": {"nodes": [{"id": "draft", "type": "agent", "operation": "draft_doc"}]},
    }
    cases = [
        {"id": "ok", "user_message": "生成文档", "expected_operation": "draft_doc"},
        {"id": "bad", "user_message": "删除文档", "expected_operation": "delete_doc"},
    ]

    result = replay_eval_cases(protocol, cases=cases)

    assert result["summary"]["total"] == 2
    assert result["summary"]["passed"] == 1
    assert result["summary"]["failed"] == 1
