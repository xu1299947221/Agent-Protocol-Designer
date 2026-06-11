import pytest

from protocol_designer.core import build_exports, ensure_harness_protocol
from protocol_designer.preview_runtime import preview_run


def test_artifact_model_defaults_and_exports_for_bid_scene():
    protocol = ensure_harness_protocol({"domain_summary": "招投标 Agent，解析招标文件并生成投标文件。"})
    model = protocol["artifact_model"]

    assert model["artifacts"]
    assert any(item.get("id") == "bid_document" for item in model["artifacts"])
    assert model["versioning"]["enabled"] is True
    exports = build_exports(protocol)
    assert "artifact_model.md" in exports
    assert "artifact_model.json" in exports
    assert "Artifact Model / 产物模型" in exports["artifact_model.md"]
    assert "Artifact Model / 产物模型" in protocol["architecture_check"]["covered_layers"]


@pytest.mark.asyncio
async def test_preview_contains_artifact_context_effect_and_timeline_step():
    protocol = ensure_harness_protocol({
        "domain_summary": "写作 Agent，帮助用户生成文档。",
        "operations": [{"name": "draft_document", "description": "生成文档初稿"}],
    })

    result = await preview_run(
        protocol=protocol,
        user_message="生成文档初稿",
        context={"existing_artifacts": [{"id": "outline", "version": "v1"}]},
        settings={},
    )

    assert "artifact_context" in result["context_pack"]
    assert "artifact_effect" in result
    assert "artifact_context" in [item.get("step") for item in result["trace"]]
    assert any(item.get("title") == "检查产物" for item in result["preview_timeline"])
