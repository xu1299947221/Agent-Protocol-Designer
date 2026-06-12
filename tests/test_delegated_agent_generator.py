import sys
import zipfile
from pathlib import Path

from protocol_designer.delegated_generator import generate_delegated_agent_project
from protocol_designer.delegated_playground import DelegatedPlaygroundManager


def create_fake_open_claude(root: Path) -> Path:
    source = root / "Openclaude-openclaude"
    (source / "dist").mkdir(parents=True)
    (source / "src").mkdir()
    (source / "bin").mkdir()
    (source / "node_modules" / "ignored").mkdir(parents=True)
    (source / ".git").mkdir()
    (source / "dist" / "cli.js").write_text(
        """#!/usr/bin/env node
const fs = require('fs');
const path = require('path');
console.log('open_claude thinking: received task pack');
fs.mkdirSync(path.join(process.cwd(), 'artifacts'), { recursive: true });
fs.writeFileSync(path.join(process.cwd(), 'artifacts', 'report.md'), '# Real Runner Smoke\\n\\nopen_claude thinking captured.\\n');
fs.writeFileSync(path.join(process.cwd(), 'artifacts', 'result.json'), JSON.stringify({
  status: 'completed',
  summary: 'real runner smoke completed',
  artifacts: ['report.md', 'result.json'],
  next_actions: []
}, null, 2));
""",
        encoding="utf-8",
    )
    (source / "src" / "index.ts").write_text("export {}\n", encoding="utf-8")
    (source / "bin" / "cli.js").write_text("#!/usr/bin/env node\n", encoding="utf-8")
    (source / "package.json").write_text('{"name":"openclaude"}\n', encoding="utf-8")
    (source / "README.md").write_text("# OpenClaude\n", encoding="utf-8")
    (source / "node_modules" / "ignored" / "x.js").write_text("ignored\n", encoding="utf-8")
    (source / ".git" / "config").write_text("ignored\n", encoding="utf-8")
    return source


def test_delegated_agent_zip_contains_runtime_and_bundled_runner(tmp_path):
    source = create_fake_open_claude(tmp_path)

    data, manifest = generate_delegated_agent_project(
        protocol={"project_name": "写作 Agent", "operations": [{"name": "draft"}]},
        project_name="test delegated agent",
        agent_name="测试委托 Agent",
        agent_goal="验证委托执行链路",
        default_task="生成 report.md 和 result.json",
        open_claude_source=source,
    )

    zip_path = tmp_path / "delegated.zip"
    zip_path.write_bytes(data)
    names = set(zipfile.ZipFile(zip_path).namelist())

    assert manifest["mode"] == "delegated_agent"
    assert "test-delegated-agent/backend/app/main.py" in names
    assert "test-delegated-agent/backend/app/runtime/openclaude_runner.py" in names
    assert "test-delegated-agent/backend/app/runtime/runner_worker.py" in names
    assert "test-delegated-agent/backend/app/templates/task_pack_template.md" in names
    assert "test-delegated-agent/frontend/index.html" in names
    assert "test-delegated-agent/.env.example" in names
    assert "test-delegated-agent/Dockerfile" in names
    assert "test-delegated-agent/runner/open_claude/Openclaude-openclaude/dist/cli.js" in names
    assert "test-delegated-agent/runner/open_claude_manifest.json" in names
    assert not any("node_modules" in name for name in names)
    assert not any("/.git/" in name for name in names)
    runner_source = zipfile.ZipFile(zip_path).read("test-delegated-agent/backend/app/runtime/openclaude_runner.py").decode("utf-8")
    assert "pty.openpty()" in runner_source
    assert "stdin=slave_fd" in runner_source
    assert "runner_screen" in runner_source


def test_generated_backend_fake_runner_runs_end_to_end(tmp_path, monkeypatch):
    source = create_fake_open_claude(tmp_path)
    data, _ = generate_delegated_agent_project(
        protocol={"project_name": "demo"},
        project_name="demo-agent",
        agent_name="Demo Agent",
        agent_goal="Run fake delegated job",
        open_claude_source=source,
    )
    zip_path = tmp_path / "delegated.zip"
    zip_path.write_bytes(data)
    with zipfile.ZipFile(zip_path) as archive:
        archive.extractall(tmp_path / "out")

    project = tmp_path / "out" / "demo-agent"
    for path in (project / "backend" / "app").rglob("*.py"):
        __import__("py_compile").compile(str(path), doraise=True)

    monkeypatch.setenv("OPEN_CLAUDE_FAKE", "1")
    monkeypatch.setenv("DATA_DIR", str(project / "data"))
    sys.path.insert(0, str(project / "backend"))
    try:
        from app.runtime import runner_worker, workspace_manager

        job = workspace_manager.create_job("请生成测试报告")
        runner_worker.run_job(job["job_id"])
        detail = workspace_manager.read_job(job["job_id"])
        artifacts = project / "data" / "jobs" / job["job_id"] / "artifacts"
    finally:
        sys.path.remove(str(project / "backend"))
        for name in list(sys.modules):
            if name == "app" or name.startswith("app."):
                sys.modules.pop(name, None)

    assert detail["status"] == "completed"
    assert detail["result"]["summary"] == "fake runner completed"
    assert (artifacts / "report.md").exists()
    assert (artifacts / "result.json").exists()


def test_delegated_playground_starts_and_runs_fake_job(tmp_path):
    source = create_fake_open_claude(tmp_path)
    manager = DelegatedPlaygroundManager(tmp_path / "playground")
    session = {
        "session_id": "session-abc",
        "title": "Delegated Online",
        "protocol": {"project_name": "delegated-online", "domain_summary": "online debug"},
    }

    item = manager.start(
        session,
        project_name="delegated-online",
        open_claude_source=source,
        fake_runner=True,
    )
    try:
        created = manager.create_job(item["delegated_id"], "请生成 report.md 和 result.json")
        detail = {}
        for _ in range(50):
            detail = manager.get_job(item["delegated_id"], created["job_id"])
            if detail["status"] in {"completed", "failed", "timeout"}:
                break
        artifacts = manager.artifacts(item["delegated_id"], created["job_id"])
        report = manager.artifact_text(item["delegated_id"], created["job_id"], "report.md")
    finally:
        manager.stop(item["delegated_id"])

    assert detail["status"] == "completed"
    assert detail["summary"] == "fake runner completed"
    assert any(item["name"] == "report.md" for item in artifacts["artifacts"])
    assert "Fake Runner" in report


def test_delegated_playground_real_runner_captures_pty_output(tmp_path):
    source = create_fake_open_claude(tmp_path)
    manager = DelegatedPlaygroundManager(tmp_path / "playground")
    session = {
        "session_id": "session-real",
        "title": "Delegated Real",
        "protocol": {"project_name": "delegated-real", "domain_summary": "real debug"},
    }

    item = manager.start(
        session,
        project_name="delegated-real",
        open_claude_source=source,
        fake_runner=False,
    )
    try:
        created = manager.create_job(item["delegated_id"], "请生成 report.md 和 result.json")
        detail = {}
        events = {}
        for _ in range(50):
            detail = manager.get_job(item["delegated_id"], created["job_id"])
            events = manager.events(item["delegated_id"], created["job_id"])
            if detail["status"] in {"completed", "failed", "timeout"}:
                break
        logs = manager.job_logs(item["delegated_id"], created["job_id"])
    finally:
        manager.stop(item["delegated_id"])

    event_types = {event["type"] for event in events["events"]}
    assert detail["status"] == "completed"
    assert detail["summary"] == "real runner smoke completed"
    assert "runner_process_started" in event_types
    assert "runner_output" in event_types or "runner_screen" in event_types
    assert "open_claude thinking" in logs["stdout"]
