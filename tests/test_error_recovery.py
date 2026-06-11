import pytest

from protocol_designer.core import build_exports, ensure_harness_protocol
from protocol_designer.preview_runtime import preview_run


def test_error_recovery_defaults_and_exports_for_high_risk_operation():
    protocol = ensure_harness_protocol({
        "domain_summary": "写作 Agent，帮助用户修改和导出文档。",
        "operations": [{"name": "delete_document", "description": "删除文档", "risk": "high"}],
    })
    recovery = protocol["error_recovery"]

    assert recovery["strategies"]
    assert recovery["retry_policy"]["max_attempts"] >= 1
    assert recovery["rollback_policy"]["enabled"] is True
    exports = build_exports(protocol)
    assert "error_recovery.md" in exports
    assert "error_recovery.json" in exports
    assert "Error Recovery / 失败恢复策略" in exports["error_recovery.md"]
    assert "Error Recovery / 失败恢复" in protocol["architecture_check"]["covered_layers"]


@pytest.mark.asyncio
async def test_preview_contains_recovery_context_plan_and_timeline_step():
    protocol = ensure_harness_protocol({
        "domain_summary": "写作 Agent，帮助用户修改和导出文档。",
        "operations": [{"name": "export_document", "description": "导出 DOCX 文档", "risk": "high"}],
    })

    result = await preview_run(
        protocol=protocol,
        user_message="导出这个文档",
        context={},
        settings={},
    )

    assert "recovery_context" in result["context_pack"]
    assert "recovery_plan" in result
    assert result["recovery_plan"]["strategy"] == "await_human_confirmation"
    assert any(item.get("title") == "失败恢复" for item in result["preview_timeline"])
    assert any("失败恢复建议" in item for item in result["diagnostics"]["problems"])
