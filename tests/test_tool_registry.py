import pytest

from protocol_designer.core import build_exports, ensure_harness_protocol
from protocol_designer.preview_runtime import preview_run


def test_tool_registry_defaults_and_exports_for_bid_scene():
    protocol = ensure_harness_protocol({
        "domain_summary": "招投标 Agent，解析招标文件并检索知识库生成投标文件。",
        "operations": [
            {"name": "parse_source_file", "description": "解析招标文件"},
            {"name": "draft_bid_document", "description": "检索知识库并生成投标文件"},
        ],
    })
    registry = protocol["tool_registry"]

    tool_names = {item.get("name") for item in registry["tools"]}
    assert "file_parser" in tool_names
    assert "rag_search" in tool_names
    assert registry["operation_tool_bindings"]
    exports = build_exports(protocol)
    assert "tool_registry.md" in exports
    assert "tool_registry.json" in exports
    assert "Tool Registry / 工具注册表" in exports["tool_registry.md"]
    assert "Tool Registry / 工具注册" in protocol["architecture_check"]["covered_layers"]


@pytest.mark.asyncio
async def test_preview_contains_tool_context_plan_and_timeline_step():
    protocol = ensure_harness_protocol({
        "domain_summary": "写作 Agent，帮助用户生成文档。",
        "operations": [{"name": "draft_document", "description": "生成文档初稿"}],
    })

    result = await preview_run(
        protocol=protocol,
        user_message="生成文档初稿",
        context={},
        settings={},
    )

    assert "tool_context" in result["context_pack"]
    assert "tool_plan" in result
    assert result["tool_plan"]["tool_names"]
    assert "tool_plan" in [item.get("step") for item in result["trace"]]
    assert any(item.get("title") == "规划工具" for item in result["preview_timeline"])
