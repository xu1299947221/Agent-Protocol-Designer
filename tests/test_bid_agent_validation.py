from pathlib import Path

from webui_server import BID_VALIDATION_PATH


def test_bid_agent_validation_report_exists_and_covers_apd_layers():
    text = Path("docs/bid_agent_apd_validation.md").read_text(encoding="utf-8")

    assert BID_VALIDATION_PATH.exists()
    assert "第 17 项验证通过" in text
    assert "Dynamic Workflow" in text
    assert "Knowledge/RAG Policy" in text
    assert "Artifact" in text or "产物" in text
    assert "人工确认" in text
    assert "Agent Runtime" in text
