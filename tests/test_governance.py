from protocol_designer.governance import (
    create_audit_event,
    detect_sensitive_data,
    list_audit_events,
    save_audit_event,
    summarize_audit_events,
)


def test_detect_sensitive_data_by_key_and_value():
    findings = detect_sensitive_data({"api_key": "sk-xxx", "contact": "user@example.com", "phone": "13800138000"})
    types = {item["type"] for item in findings}

    assert "sensitive_key" in types
    assert "email" in types
    assert "phone" in types


def test_create_audit_event_sets_high_risk_for_sensitive_payload():
    event = create_audit_event(action="tool_test", scope="tool", resource_id="run1", payload={"password": "secret"})

    assert event["risk"] == "high"
    assert event["sensitive_findings"]
    assert event["retention_policy"]["retain_days"] == 180
    assert "LLM" in event["responsibility_boundary"]


def test_save_and_summarize_audit_events(tmp_path):
    save_audit_event(tmp_path, create_audit_event(action="runtime_run", scope="runtime", resource_id="job1", payload={}))
    save_audit_event(tmp_path, create_audit_event(action="artifact_rollback", scope="artifact", resource_id="artifact1", payload={"token": "abc"}))

    events = list_audit_events(tmp_path)
    summary = summarize_audit_events(tmp_path)

    assert len(events) == 2
    assert summary["total"] == 2
    assert summary["by_scope"]["runtime"] == 1
    assert summary["by_risk"]["high"] >= 1
    assert summary["sensitive_finding_count"] >= 1
