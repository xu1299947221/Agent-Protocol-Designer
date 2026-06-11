from pathlib import Path

from webui_server import AGENTOS_EVALUATION_PATH


def test_agentos_evaluation_report_exists_and_sets_boundary():
    text = Path("docs/agentos_evaluation.md").read_text(encoding="utf-8")

    assert AGENTOS_EVALUATION_PATH.exists()
    assert "第 19 项" in text
    assert "不应该立刻宣称自己是完整 AgentOS" in text
    assert "Agent Harness Designer / Generator" in text
    assert "Agent Runtime 平台" in text
    assert "Runtime 状态持久化" in text
    assert "Agent Registry" in text
