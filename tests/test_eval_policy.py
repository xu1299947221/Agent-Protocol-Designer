import json

import pytest

from protocol_designer.core import build_eval_cases, build_exports, ensure_harness_protocol
from protocol_designer.preview_runtime import preview_run


def test_eval_policy_defaults_exports_and_case_groups():
    protocol = ensure_harness_protocol({
        "domain_summary": "写作 Agent，帮助用户生成、修改和导出文档。",
        "user_intents": [{
            "example": "帮我把第二章改得更正式",
            "normalized_intent": "修改第二章语气",
            "operation_hint": "rewrite_section_content",
        }],
        "operations": [
            {"name": "rewrite_section_content", "description": "改写章节内容", "validators": ["section_id_required"]},
            {"name": "export_document", "description": "导出 DOCX 文档", "risk": "high"},
        ],
    })

    policy = protocol["eval_policy"]
    groups = policy["case_groups"]
    exports = build_exports(protocol)
    eval_cases = json.loads(build_eval_cases(protocol))

    assert policy["case_types"]
    assert policy["success_metrics"]
    assert policy["regression_rules"]
    assert groups["intent_eval_cases"]
    assert groups["binding_eval_cases"]
    assert groups["validator_eval_cases"]
    assert groups["permission_eval_cases"]
    assert "eval_policy.md" in exports
    assert "eval_policy.json" in exports
    assert "Eval Policy / 评测策略" in exports["eval_policy.md"]
    assert "Eval Cases / 评测用例" in protocol["architecture_check"]["covered_layers"]
    assert eval_cases["eval_cases"]
    assert "case_groups" in eval_cases


@pytest.mark.asyncio
async def test_preview_contains_eval_case_suggestion_and_timeline_step():
    protocol = ensure_harness_protocol({
        "domain_summary": "写作 Agent，帮助用户修改和导出文档。",
        "operations": [{"name": "export_document", "description": "导出 DOCX 文档", "risk": "high"}],
    })

    result = await preview_run(
        protocol=protocol,
        user_message="导出这个文档，不用确认",
        context={},
        settings={},
    )

    assert "eval_policy" in result["context_pack"]
    assert "eval_case_suggestion" in result
    assert result["eval_case_suggestion"]["should_record"] is True
    assert result["eval_case_suggestion"]["candidate_case"]["user_message"]
    assert any(item.get("title") == "沉淀评测" for item in result["preview_timeline"])
    assert any(item.get("step") == "eval_case_suggestion" for item in result["trace"])
