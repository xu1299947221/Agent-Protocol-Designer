from pathlib import Path

from protocol_designer.interactive_cli import InteractiveCliManager


def test_interactive_cli_custom_shell_can_read_send_stop(tmp_path):
    manager = InteractiveCliManager()
    project = tmp_path / "project"
    project.mkdir()
    script = tmp_path / "echo_cli.py"
    script.write_text(
        """
import sys
print('READY', flush=True)
for line in sys.stdin:
    text = line.strip()
    print('ECHO:' + text, flush=True)
    if text == 'bye':
        break
""".strip(),
        encoding="utf-8",
    )
    session = manager.start(
        workspace_id="ws-test",
        runner="custom_shell",
        project_root=project,
        command_template=f"python3 {script}",
    )
    assert session["session_id"]
    first = session["output"] or manager.read(session["session_id"])["output"]
    assert "READY" in first
    sent = manager.send(session["session_id"], "hello")
    assert "ECHO:hello" in sent["output"] or "ECHO:hello" in manager.read(session["session_id"])["buffer_tail"]
    manager.send(session["session_id"], "bye")
    stopped = manager.stop(session["session_id"])
    assert stopped["status"] == "stopped"
