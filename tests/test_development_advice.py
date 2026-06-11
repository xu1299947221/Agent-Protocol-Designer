import json

from protocol_designer.core import build_development_advice, build_exports, ensure_harness_protocol


def test_development_advice_for_empty_protocol_points_to_operation_design():
    advice = build_development_advice({})

    assert advice["readiness_level"] == "design_needed"
    assert "operation" in advice["current_focus"]
    assert advice["next_actions"]
    assert advice["blocking_gaps"]


def test_development_advice_exports_for_runnable_demo_stage():
    protocol = ensure_harness_protocol({
        "domain_summary": "写作 Agent，帮助用户生成和导出文档。",
        "operations": [
            {"name": "draft_section", "description": "生成章节内容", "validators": ["section_required"]},
            {"name": "export_document", "description": "导出 DOCX 文档", "risk": "high"},
        ],
    })
    exports = build_exports(protocol)
    advice_json = json.loads(exports["development_advice.json"])

    assert "development_advice.md" in exports
    assert "development_advice.json" in exports
    assert "下一步开发建议" in exports["development_advice.md"]
    assert advice_json["next_actions"]
    assert advice_json["implementation_sequence"]
    assert advice_json["files_to_start"]
    assert advice_json["metrics"]["operation_count"] == 2
