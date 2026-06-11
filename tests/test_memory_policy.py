import pytest

from protocol_designer.core import build_exports, ensure_harness_protocol
from protocol_designer.preview_runtime import preview_run


def test_memory_policy_defaults_and_exports():
    protocol = ensure_harness_protocol({"domain_summary": "写作 Agent，帮助用户生成和修改文档。"})
    memory = protocol["memory_policy"]

    assert memory["enabled"] is True
    assert memory["memory_types"]
    assert any("用户偏好" in item for item in memory["memory_types"])
    assert "Memory Policy / 记忆策略" in build_exports(protocol)["memory_policy.md"]
    assert "memory_policy.json" in build_exports(protocol)
    assert "Memory Policy / 记忆策略" in protocol["architecture_check"]["covered_layers"]


@pytest.mark.asyncio
async def test_preview_contains_memory_retrieval_and_write_proposal():
    protocol = ensure_harness_protocol({
        "domain_summary": "写作 Agent，帮助用户生成文档。",
        "operations": [{
            "name": "draft_section",
            "description": "生成章节内容",
            "validators": ["section_required"],
        }],
    })

    result = await preview_run(
        protocol=protocol,
        user_message="帮我写第二章",
        context={"memory": [{"type": "user_preference_memory", "content": "用户偏好正式中文风格"}]},
        settings={},
    )

    assert "memory_retrieval" in result
    assert result["memory_retrieval"]["enabled"] is True
    assert result["memory_retrieval"]["retrieved_memories"]
    assert "memory_write_proposal" in result
    assert "preview_timeline" in result
    assert "diagnostics" in result
    assert any(item.get("title") == "读取记忆" for item in result["preview_timeline"])
    assert any(item.get("step") == "memory_retrieval" for item in result["trace"])
