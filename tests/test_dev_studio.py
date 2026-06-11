from protocol_designer.dev_studio import DevStudioManager


def test_dev_studio_workspace_version_run_download(tmp_path):
    manager = DevStudioManager(tmp_path)
    session = {
        "session_id": "studio-session",
        "title": "开发台测试 Agent",
        "protocol": {
            "project_name": "studio-agent",
            "domain_summary": "测试 Agent 开发台工作区。",
            "operations": [
                {"name": "draft_section", "description": "生成章节内容", "validators": ["section_required"]},
            ],
            "tool_registry": {
                "tools": [{"name": "doc_writer", "description": "写文档"}],
                "operation_tool_bindings": [{"operation": "draft_section", "tools": ["doc_writer"]}],
            },
            "workflow": {"nodes": [{"id": "draft", "name": "生成章节", "operation": "draft_section"}]},
        },
    }

    workspace = manager.create_workspace(session, name="studio-agent")
    workspace_id = workspace["workspace_id"]

    assert workspace["current_version"] == "v1"
    assert workspace["versions"][0]["version_id"] == "v1"
    assert workspace["summary"]["file_count"] > 0
    assert manager.list_workspaces("studio-session")[0]["workspace_id"] == workspace_id

    run = manager.run_workspace(workspace_id, message="请生成章节内容", context={"current_state": "drafting"})
    assert run["agent_run"]["tool_results"]
    assert run["agent_run"]["artifact_versions"]
    assert run["workflow_run"]["node_states"]
    assert run["store_snapshot"]["trace_events"]

    version = manager.save_version(workspace_id, "运行通过后的版本")
    assert version["version_id"] == "v2"

    rolled_back = manager.rollback(workspace_id, "v1")
    assert rolled_back["current_version"] == "v1"

    data, filename = manager.download_zip(workspace_id)
    assert filename.endswith("current.zip")
    assert b"README.md" in data or len(data) > 1000



def test_dev_studio_quick_reply_changes_runtime_output(tmp_path):
    manager = DevStudioManager(tmp_path)
    session = {
        "session_id": "studio-edit-session",
        "title": "开发台编辑 Agent",
        "protocol": {
            "project_name": "studio-edit-agent",
            "operations": [{"name": "draft_section", "description": "生成章节内容"}],
            "workflow": {"nodes": [{"id": "draft", "name": "生成章节", "operation": "draft_section"}]},
        },
    }
    workspace = manager.create_workspace(session, name="studio-edit-agent")
    workspace_id = workspace["workspace_id"]

    files = manager.list_editable_files(workspace_id)
    assert any(item["path"] == "backend/app/executor.py" for item in files)

    original = manager.read_file(workspace_id, "backend/app/executor.py")
    assert "Demo 已模拟执行" in original["content"]

    new_reply = "这是我在 APD 开发台里边改边看到的实时回复。"
    edit = manager.update_demo_reply(workspace_id, new_reply)
    assert edit["replacement_count"] >= 1

    run = manager.run_workspace(workspace_id, message="请生成章节内容", context={})
    assert run["agent_run"]["assistant_message"] == new_reply

    file_data = manager.read_file(workspace_id, "backend/app/executor.py")
    manager.write_file(workspace_id, "backend/app/executor.py", file_data["content"])



def test_dev_studio_open_claude_runner_executes_with_custom_cli_root(tmp_path):
    manager = DevStudioManager(tmp_path / "workspaces")
    session = {
        "session_id": "open-claude-session",
        "title": "Open Claude Agent",
        "protocol": {
            "project_name": "open-claude-agent",
            "operations": [{"name": "draft_section", "description": "生成章节内容"}],
        },
    }
    workspace = manager.create_workspace(session, name="open-claude-agent")
    workspace_id = workspace["workspace_id"]
    cli_root = tmp_path / "fake-open-claude"
    (cli_root / "dist").mkdir(parents=True)
    cli = cli_root / "dist" / "cli.js"
    cli.write_text(
        """
const fs = require('fs');
const path = require('path');
const target = path.join(process.cwd(), 'OPEN_CLAUDE_MARKER.txt');
fs.writeFileSync(target, 'changed by fake open_claude\\n', 'utf8');
console.log('fake open_claude completed');
""".strip(),
        encoding="utf-8",
    )

    result = manager.run_open_claude(
        workspace_id,
        task="创建一个标记文件",
        cli_root=str(cli_root),
        timeout_seconds=60,
    )

    assert result["status"] == "completed"
    assert "fake open_claude completed" in result["stdout"]
    assert any(item["path"] == "OPEN_CLAUDE_MARKER.txt" for item in result["changed_files"])


def test_dev_studio_builds_agent_engineering_plan_and_runs_unified_runner(tmp_path):
    manager = DevStudioManager(tmp_path / "workspaces")
    session = {
        "session_id": "developer-runner-session",
        "title": "Developer Runner Agent",
        "protocol": {
            "project_name": "developer-runner-agent",
            "operations": [{"name": "draft_section", "description": "生成章节内容"}],
            "workflow": {"nodes": [{"id": "draft", "name": "生成章节", "operation": "draft_section"}]},
        },
    }
    workspace = manager.create_workspace(session, name="developer-runner-agent")
    workspace_id = workspace["workspace_id"]
    cli_root = tmp_path / "fake-open-claude"
    (cli_root / "dist").mkdir(parents=True)
    (cli_root / "dist" / "cli.js").write_text(
        """
const fs = require('fs');
const path = require('path');
fs.writeFileSync(path.join(process.cwd(), 'DEVELOPER_RUNNER_MARKER.txt'), 'ok\\n', 'utf8');
console.log(process.argv.join(' ').includes('Knowledge') || process.argv.join(' ').includes('知识') ? 'task contains architecture context' : 'task received');
""".strip(),
        encoding="utf-8",
    )

    plan = manager.build_engineering_plan(workspace_id, session, "生成章节前先检索知识库素材")
    assert "RAG Tool Use Agent" in plan["agent_types"]
    assert any("Knowledge" in item["name"] for item in plan["architecture_layers"])
    assert "APD Agent 工程开发任务包" in plan["runner_task"]

    result = manager.run_developer_task(
        workspace_id,
        session=session,
        request="生成章节前先检索知识库素材",
        runner="open_claude",
        cli_root=str(cli_root),
        timeout_seconds=60,
    )
    runner_result = result["runner_result"]
    assert runner_result["status"] == "completed"
    assert result["plan"]["agent_types"]
    assert any(item["path"] == "DEVELOPER_RUNNER_MARKER.txt" for item in runner_result["changed_files"])
