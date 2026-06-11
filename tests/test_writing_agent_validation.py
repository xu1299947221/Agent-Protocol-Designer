from pathlib import Path

from webui_server import writing_case_payload


def test_writing_agent_validation_report_is_exposed_in_case_payload():
    report = Path("docs/writing_agent_apd_validation.md")
    assert report.exists()
    text = report.read_text(encoding="utf-8")
    assert "第 16 项通过" in text
    assert "Context Pack" in text
    assert "Intent Frame" in text
    assert "OpCall" in text

    payload = writing_case_payload()
    assert payload["validation_summary"]
    assert payload["validation_points"]
    assert "第 16 项验证" in payload["validation_summary"]
    assert "OpCall Runtime" in payload["validation_markdown"]
