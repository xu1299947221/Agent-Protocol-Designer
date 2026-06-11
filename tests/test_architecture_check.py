from protocol_designer.core import (
    EMPTY_PROTOCOL,
    architecture_question_for,
    build_exports,
    ensure_harness_protocol,
    normalize_response,
)


def test_harness_defaults_and_exports_include_architecture_check():
    protocol = ensure_harness_protocol(EMPTY_PROTOCOL)

    for key in (
        "agent_profile",
        "memory_policy",
        "state_model",
        "artifact_model",
        "tool_registry",
        "permission_policy",
        "error_recovery",
        "knowledge_policy",
        "eval_policy",
        "runtime_triggers",
        "workspace_policy",
        "architecture_check",
    ):
        assert key in protocol

    assert protocol["architecture_check"]["completeness_percent"] >= 0
    exports = build_exports(protocol)
    assert "architecture_check.md" in exports
    assert "architecture_check.json" in exports
    assert "当前完整度" in exports["architecture_check.md"]


def test_architecture_guidance_asks_one_question_for_first_gap():
    response = normalize_response({
        "assistant_message": "已记录。",
        "protocol": {"domain_summary": "我想做一个写作 Agent。"},
        "next_questions": [],
    })

    assert len(response["next_questions"]) == 1
    assert "角色" in response["next_questions"][0]
    assert any("Agent Profile" in note or "智能体画像" in note for note in response["quality_notes"])


def test_architecture_question_moves_to_next_gap_after_eval_policy_defaults():
    protocol = ensure_harness_protocol({
        "agent_profile": {
            "agent_name": "写作助手",
            "role": "帮助用户写作和修改文档",
        }
    })

    suggestion = architecture_question_for(protocol)
    assert suggestion is not None
    layer_name, question = suggestion
    assert "Knowledge/RAG" in layer_name
    assert "知识库" in question or "检索" in question
