import pytest

from protocol_designer.core import build_exports, ensure_harness_protocol
from protocol_designer.preview_runtime import preview_run


def test_permission_policy_defaults_and_exports_for_high_risk_operation():
    protocol = ensure_harness_protocol({
        "domain_summary": "写作 Agent，帮助用户修改和导出文档。",
        "operations": [
            {"name": "delete_document", "description": "删除文档", "risk": "high"},
            {"name": "draft_document", "description": "生成文档初稿", "risk": "medium"},
        ],
    })
    policy = protocol["permission_policy"]

    assert policy["confirmation_required"]
    assert any(item.get("name") == "delete_document" for item in policy["confirmation_required"])
    exports = build_exports(protocol)
    assert "permission_policy.md" in exports
    assert "permission_policy.json" in exports
    assert "Permission Policy / 权限策略" in exports["permission_policy.md"]
    assert "Permission Policy / 权限策略" in protocol["architecture_check"]["covered_layers"]


@pytest.mark.asyncio
async def test_preview_contains_permission_context_check_and_timeline_step():
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

    assert "permission_context" in result["context_pack"]
    assert "permission_check" in result
    assert result["permission_check"]["requires_confirmation"] is True
    assert "permission_check" in [item.get("step") for item in result["trace"]]
    assert any(item.get("title") == "权限判断" for item in result["preview_timeline"])
