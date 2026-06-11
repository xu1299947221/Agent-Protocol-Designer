import pytest

from protocol_designer.core import build_exports, ensure_harness_protocol
from protocol_designer.preview_runtime import preview_run


def test_state_model_defaults_and_exports_for_bid_scene():
    protocol = ensure_harness_protocol({"domain_summary": "招投标 Agent，上传招标文件后生成投标文档。"})
    state = protocol["state_model"]

    assert state["states"]
    assert any(item.get("id") == "source_uploaded" for item in state["states"])
    assert state["transitions"]
    exports = build_exports(protocol)
    assert "state_model.md" in exports
    assert "state_model.json" in exports
    assert "State Model / 状态模型" in exports["state_model.md"]
    assert "State Model / 状态模型" in protocol["architecture_check"]["covered_layers"]


@pytest.mark.asyncio
async def test_preview_contains_state_context_and_timeline_step():
    protocol = ensure_harness_protocol({
        "domain_summary": "写作 Agent，帮助用户生成文档。",
        "operations": [{"name": "draft_document", "description": "生成文档初稿"}],
    })

    result = await preview_run(
        protocol=protocol,
        user_message="继续生成正文",
        context={"current_state": "outline_generated"},
        settings={},
    )

    state_context = result["context_pack"]["state_context"]
    assert state_context["current_state"] == "outline_generated"
    assert "state_context" in [item.get("step") for item in result["trace"]]
    assert any(item.get("title") == "检查状态" for item in result["preview_timeline"])
