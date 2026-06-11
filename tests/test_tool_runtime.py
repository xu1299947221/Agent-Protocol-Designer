from protocol_designer.tool_runtime import build_tool_test_cases, dry_run_tool, list_tool_runs, run_tool_tests, save_tool_run


def _protocol():
    return {
        "tool_registry": {
            "tools": [
                {"name": "file_parser", "description": "解析文件", "inputs": ["file_path"], "outputs": ["parsed_document"], "risk": "low", "side_effects": []},
                {"name": "sandbox_runner", "description": "运行代码", "inputs": ["project_path", "command"], "outputs": ["stdout"], "risk": "high", "side_effects": ["execute_code"], "failure_policy": "sandbox_only"},
            ]
        }
    }


def test_build_tool_test_cases_from_registry():
    cases = build_tool_test_cases(_protocol())

    assert len(cases) == 2
    assert cases[0]["tool"] == "file_parser"
    assert cases[0]["input"]["file_path"] == "/tmp/apd-sample-input.txt"


def test_dry_run_tool_records_output_risk_and_trace():
    result = dry_run_tool(_protocol(), "sandbox_runner")

    assert result["ok"] is True
    assert result["dry_run"] is True
    assert result["status"] == "needs_confirmation"
    assert result["requires_confirmation"] is True
    assert result["failure_policy"] == "sandbox_only"
    assert result["trace"]


def test_run_tool_tests_and_persist_history(tmp_path):
    run = run_tool_tests(_protocol())
    saved = save_tool_run(tmp_path, run, session_id="session_1")
    runs = list_tool_runs(tmp_path, "session_1")

    assert saved["summary"]["total"] == 2
    assert saved["summary"]["needs_confirmation"] == 1
    assert runs[0]["run_id"] == saved["run_id"]


def test_missing_tool_fails_with_failure_policy():
    result = dry_run_tool(_protocol(), "missing_tool")

    assert result["ok"] is False
    assert result["error"] == "tool_not_registered"
    assert result["failure_policy"] == "ask_user_or_register_tool"
