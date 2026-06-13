from __future__ import annotations

import asyncio
import json
import fcntl
import os
import pty
import select
import shlex
import signal
import shutil
import socket
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .developer_runner import OPEN_CLAUDE_ROOT, mask_secret


@dataclass
class CliSession:
    session_id: str
    workspace_id: str
    runner: str
    command: list[str] | str
    cwd: Path
    master_fd: int
    process: subprocess.Popen[bytes]
    api_key: str = ""
    created_at: float = field(default_factory=time.time)
    last_read_at: float = field(default_factory=time.time)
    buffer: str = ""

    def status(self) -> str:
        return "running" if self.process.poll() is None else "stopped"

    def to_summary(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "workspace_id": self.workspace_id,
            "runner": self.runner,
            "cwd": str(self.cwd),
            "command_preview": _command_preview(self.command),
            "status": self.status(),
            "pid": self.process.pid,
            "created_at": self.created_at,
            "last_read_at": self.last_read_at,
        }


class InteractiveCliManager:
    def __init__(self) -> None:
        self.sessions: dict[str, CliSession] = {}

    def start(
        self,
        *,
        workspace_id: str,
        runner: str,
        project_root: Path,
        work_dir: str = "",
        cli_root: str = "",
        base_url: str = "",
        api_key: str = "",
        model: str = "",
        initial_prompt: str = "",
        command_template: str = "",
    ) -> dict[str, Any]:
        runner = str(runner or "open_claude").strip() or "open_claude"
        cwd = Path(work_dir).expanduser().resolve() if work_dir else project_root.resolve()
        if not cwd.exists() or not cwd.is_dir():
            raise FileNotFoundError(f"工作目录不存在：{cwd}")
        command = _build_interactive_command(runner, cwd, cli_root, initial_prompt, command_template)
        env = os.environ.copy()
        env.pop("ANTHROPIC_BASE_URL", None)
        env.pop("ANTHROPIC_AUTH_TOKEN", None)
        if base_url.strip():
            env["ANTHROPIC_BASE_URL"] = base_url.strip()
            env["OPENAI_BASE_URL"] = base_url.strip()
        if api_key.strip():
            env["ANTHROPIC_API_KEY"] = api_key.strip()
            env["ANTHROPIC_AUTH_TOKEN"] = api_key.strip()
            env["OPENAI_API_KEY"] = api_key.strip()
        if model.strip():
            env["ANTHROPIC_MODEL"] = model.strip()
            env["OPENAI_MODEL"] = model.strip()
        master_fd, slave_fd = pty.openpty()
        _set_nonblocking(master_fd)
        process = subprocess.Popen(
            command,
            cwd=str(cwd),
            env=env,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            close_fds=True,
            shell=isinstance(command, str),
        )
        os.close(slave_fd)
        cli_session = CliSession(
            session_id=f"cli-{uuid.uuid4().hex[:12]}",
            workspace_id=workspace_id,
            runner=runner,
            command=command,
            cwd=cwd,
            master_fd=master_fd,
            process=process,
            api_key=api_key,
        )
        self.sessions[cli_session.session_id] = cli_session
        time.sleep(0.2)
        output = self.read(cli_session.session_id).get("output", "")
        return {**cli_session.to_summary(), "output": output}

    def read(self, session_id: str) -> dict[str, Any]:
        cli_session = self._get(session_id)
        chunks: list[str] = []
        while True:
            ready, _, _ = select.select([cli_session.master_fd], [], [], 0)
            if not ready:
                break
            try:
                data = os.read(cli_session.master_fd, 8192)
            except BlockingIOError:
                break
            except OSError:
                break
            if not data:
                break
            chunks.append(data.decode("utf-8", errors="replace"))
        output = "".join(chunks)
        if output:
            output = mask_secret(output, cli_session.api_key)
            cli_session.buffer += output
            cli_session.last_read_at = time.time()
        return {**cli_session.to_summary(), "output": output, "buffer_tail": cli_session.buffer[-20000:]}

    def send(self, session_id: str, text: str, *, raw: bool = False) -> dict[str, Any]:
        cli_session = self._get(session_id)
        if cli_session.status() != "running":
            return {**cli_session.to_summary(), "output": "", "error": "CLI session already stopped"}
        payload = str(text or "")
        if not raw and not payload.endswith("\n"):
            payload += "\n"
        os.write(cli_session.master_fd, payload.encode("utf-8"))
        time.sleep(0.02 if raw else 0.1)
        return self.read(session_id)

    def stop(self, session_id: str) -> dict[str, Any]:
        cli_session = self._get(session_id)
        if cli_session.process.poll() is None:
            try:
                cli_session.process.terminate()
                cli_session.process.wait(timeout=3)
            except Exception:
                try:
                    cli_session.process.kill()
                except Exception:
                    pass
        try:
            os.close(cli_session.master_fd)
        except OSError:
            pass
        return cli_session.to_summary()

    def list(self, workspace_id: str = "") -> list[dict[str, Any]]:
        items = []
        for session_id, cli_session in list(self.sessions.items()):
            if workspace_id and cli_session.workspace_id != workspace_id:
                continue
            items.append(cli_session.to_summary())
        return sorted(items, key=lambda item: item.get("created_at") or 0, reverse=True)

    def _get(self, session_id: str) -> CliSession:
        cli_session = self.sessions.get(session_id)
        if not cli_session:
            raise KeyError(session_id)
        return cli_session


@dataclass
class TtydSession:
    session_id: str
    workspace_id: str
    runner: str
    command: list[str] | str
    cwd: Path
    port: int
    process: subprocess.Popen[bytes]
    tmux_session: str = ""
    api_key: str = ""
    created_at: float = field(default_factory=time.time)

    def status(self) -> str:
        return "running" if self.process.poll() is None else "stopped"

    def to_summary(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "workspace_id": self.workspace_id,
            "runner": self.runner,
            "cwd": str(self.cwd),
            "port": self.port,
            "url": f"http://0.0.0.0:{self.port}/",
            "command_preview": _command_preview(self.command),
            "status": self.status(),
            "pid": self.process.pid,
            "tmux_session": self.tmux_session,
            "created_at": self.created_at,
        }


class TtydCliManager:
    def __init__(self) -> None:
        self.sessions: dict[str, TtydSession] = {}

    def cleanup(self, workspace_id: str = "", *, all_apd: bool = False) -> dict[str, Any]:
        workspace_id = str(workspace_id or "").strip()
        stopped_sessions: list[dict[str, Any]] = []
        killed_ttyd_pids: list[int] = []
        killed_tmux_sessions: list[str] = []
        errors: list[str] = []

        for session_id, session in list(self.sessions.items()):
            if not all_apd and workspace_id and session.workspace_id != workspace_id:
                continue
            if not all_apd and not workspace_id:
                continue
            try:
                stopped_sessions.append(self.stop(session_id))
            except Exception as exc:
                errors.append(f"stop {session_id}: {exc}")
            finally:
                self.sessions.pop(session_id, None)

        workspace_marker = f"/data/dev_workspaces/{workspace_id}/" if workspace_id else "/data/dev_workspaces/"
        try:
            for pid, args in _iter_process_args():
                if "ttyd" not in args or "agent-protocol-designer/data/dev_workspaces" not in args:
                    continue
                if not all_apd and workspace_marker not in args:
                    continue
                if _terminate_pid(pid):
                    killed_ttyd_pids.append(pid)
        except Exception as exc:
            errors.append(f"cleanup ttyd: {exc}")

        tmux_bin = shutil.which("tmux")
        if tmux_bin:
            try:
                for name in _list_apd_tmux_sessions(tmux_bin):
                    if not all_apd and workspace_id:
                        current_path = _tmux_pane_current_path(tmux_bin, name)
                        if workspace_marker not in current_path:
                            continue
                    if not all_apd and not workspace_id:
                        continue
                    subprocess.run([tmux_bin, "kill-session", "-t", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    killed_tmux_sessions.append(name)
            except Exception as exc:
                errors.append(f"cleanup tmux: {exc}")

        return {
            "ok": True,
            "workspace_id": workspace_id,
            "all_apd": all_apd,
            "stopped_sessions": stopped_sessions,
            "killed_ttyd_pids": killed_ttyd_pids,
            "killed_tmux_sessions": killed_tmux_sessions,
            "errors": errors,
        }

    def start(
        self,
        *,
        workspace_id: str,
        runner: str,
        project_root: Path,
        work_dir: str = "",
        cli_root: str = "",
        base_url: str = "",
        api_key: str = "",
        model: str = "",
        initial_prompt: str = "",
        command_template: str = "",
        host: str = "",
    ) -> dict[str, Any]:
        ttyd_bin = shutil.which("ttyd")
        if not ttyd_bin:
            raise FileNotFoundError("未找到 ttyd 命令，请先安装 ttyd")
        runner = str(runner or "open_claude").strip() or "open_claude"
        cwd = Path(work_dir).expanduser().resolve() if work_dir else project_root.resolve()
        if not cwd.exists() or not cwd.is_dir():
            raise FileNotFoundError(f"工作目录不存在：{cwd}")
        command = _build_interactive_command(runner, cwd, cli_root, initial_prompt, command_template)
        child_command = command if isinstance(command, list) else ["sh", "-lc", command]
        port = _find_free_port()
        env = os.environ.copy()
        env.pop("ANTHROPIC_BASE_URL", None)
        env.pop("ANTHROPIC_AUTH_TOKEN", None)
        if base_url.strip():
            env["ANTHROPIC_BASE_URL"] = base_url.strip()
            env["OPENAI_BASE_URL"] = base_url.strip()
        if api_key.strip():
            env["ANTHROPIC_API_KEY"] = api_key.strip()
            env["ANTHROPIC_AUTH_TOKEN"] = api_key.strip()
            env["OPENAI_API_KEY"] = api_key.strip()
        if model.strip():
            env["ANTHROPIC_MODEL"] = model.strip()
            env["OPENAI_MODEL"] = model.strip()
        tmux_bin = shutil.which("tmux")
        tmux_session = ""
        ttyd_child_command = child_command
        if tmux_bin:
            tmux_session = f"apd_{uuid.uuid4().hex[:12]}"
            child_command_line = child_command if isinstance(child_command, str) else " ".join(shlex.quote(str(part)) for part in child_command)
            subprocess.run(
                [tmux_bin, "new-session", "-d", "-s", tmux_session, "-c", str(cwd), child_command_line],
                cwd=str(cwd),
                env=env,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            ttyd_child_command = [tmux_bin, "attach-session", "-t", tmux_session]
        ttyd_command = [
            ttyd_bin,
            "-i",
            "0.0.0.0",
            "-p",
            str(port),
            "-W",
            "-m",
            "2",
            "-w",
            str(cwd),
            "-t",
            "fontSize=14",
            "-t",
            'theme={"background":"#020617","foreground":"#e5e7eb"}', 
            *(ttyd_child_command if isinstance(ttyd_child_command, list) else ["sh", "-lc", ttyd_child_command]),
        ]
        process = subprocess.Popen(
            ttyd_command,
            cwd=str(cwd),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
        time.sleep(0.3)
        if process.poll() is not None:
            raise RuntimeError("ttyd 启动失败，请检查命令和工作目录")
        session = TtydSession(
            session_id=f"ttyd-{uuid.uuid4().hex[:12]}",
            workspace_id=workspace_id,
            runner=runner,
            command=command,
            cwd=cwd,
            port=port,
            process=process,
            tmux_session=tmux_session,
            api_key=api_key,
        )
        self.sessions[session.session_id] = session
        summary = session.to_summary()
        public_host = str(host or "").split(":", 1)[0] or "127.0.0.1"
        if public_host in {"127.0.0.1", "localhost", "0.0.0.0"}:
            public_host = _detect_lan_host() or public_host
        summary["url"] = f"http://{public_host}:{port}/"
        return summary

    async def send(self, session_id: str, text: str, *, raw: bool = False, columns: int = 120, rows: int = 32) -> dict[str, Any]:
        session = self._get(session_id)
        if session.status() != "running":
            return {**session.to_summary(), "sent": False, "error": "ttyd session already stopped"}
        payload = str(text or "")
        if not payload:
            return {**session.to_summary(), "sent": False, "error": "empty input"}
        if session.tmux_session:
            return await asyncio.to_thread(self._send_to_tmux, session, payload, raw=raw)
        return {**session.to_summary(), "sent": False, "error": "当前 ttyd 会话不是 tmux 共享模式，请停止并重新启动终端"}

    def _send_to_tmux(self, session: TtydSession, payload: str, *, raw: bool = False) -> dict[str, Any]:
        tmux_bin = shutil.which("tmux")
        if not tmux_bin:
            return {**session.to_summary(), "sent": False, "error": "未找到 tmux，无法写入可见 ttyd 会话"}
        buffer_name = f"apd_send_{uuid.uuid4().hex[:10]}"
        try:
            subprocess.run(
                [tmux_bin, "load-buffer", "-b", buffer_name, "-"],
                input=payload.encode("utf-8"),
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            subprocess.run(
                [tmux_bin, "paste-buffer", "-b", buffer_name, "-t", session.tmux_session],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if not raw:
                subprocess.run(
                    [tmux_bin, "send-keys", "-t", session.tmux_session, "Enter"],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
        except Exception as exc:
            return {**session.to_summary(), "sent": False, "error": str(exc) or exc.__class__.__name__}
        finally:
            try:
                subprocess.run([tmux_bin, "delete-buffer", "-b", buffer_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                pass
        return {**session.to_summary(), "sent": True, "sent_chars": len(payload)}

    def stop(self, session_id: str) -> dict[str, Any]:
        session = self._get(session_id)
        if session.process.poll() is None:
            try:
                session.process.terminate()
                session.process.wait(timeout=3)
            except Exception:
                try:
                    session.process.kill()
                except Exception:
                    pass
        if session.tmux_session:
            tmux_bin = shutil.which("tmux")
            if tmux_bin:
                try:
                    subprocess.run([tmux_bin, "kill-session", "-t", session.tmux_session], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except Exception:
                    pass
        return session.to_summary()

    def _get(self, session_id: str) -> TtydSession:
        session = self.sessions.get(session_id)
        if not session:
            raise KeyError(session_id)
        return session


def _build_interactive_command(runner: str, cwd: Path, cli_root: str, initial_prompt: str, command_template: str) -> list[str] | str:
    prompt = str(initial_prompt or "").strip()
    if command_template.strip():
        return command_template.format(
            prompt=shlex.quote(prompt),
            project_root=shlex.quote(str(cwd)),
        )
    if runner == "open_claude":
        effective_cli_root = Path(cli_root).expanduser().resolve() if cli_root else OPEN_CLAUDE_ROOT.resolve()
        cli = effective_cli_root / "dist" / "cli.js"
        if not cli.exists():
            raise FileNotFoundError(f"open_claude CLI 不存在：{cli}")
        command = [
            "node",
            "--enable-source-maps",
            str(cli),
            "--dangerously-skip-permissions",
            "--add-dir",
            str(cwd),
        ]
        if prompt:
            command.append(prompt)
        return command
    if runner == "codex":
        command = ["codex", "--skip-git-repo-check"]
        if prompt:
            command.append(prompt)
        return command
    if runner == "claude_code":
        command = ["claude", "--dangerously-skip-permissions"]
        if prompt:
            command.append(prompt)
        return command
    if runner == "custom_shell":
        raise ValueError("custom_shell 交互模式需要填写命令模板")
    raise ValueError(f"未知 Runner：{runner}")


def _set_nonblocking(fd: int) -> None:
    flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("0.0.0.0", 0))
        return int(sock.getsockname()[1])


def _detect_lan_host() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            host = str(sock.getsockname()[0] or "")
            if host and not host.startswith("127."):
                return host
    except Exception:
        return ""
    return ""


def _command_preview(command: list[str] | str) -> str:
    if isinstance(command, str):
        return command
    return " ".join(shlex.quote(str(part)) for part in command)


def _iter_process_args() -> list[tuple[int, str]]:
    result = subprocess.run(["ps", "-eo", "pid=,args="], check=False, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    items: list[tuple[int, str]] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        pid_text, _, args = line.partition(" ")
        try:
            pid = int(pid_text)
        except ValueError:
            continue
        items.append((pid, args.strip()))
    return items


def _terminate_pid(pid: int) -> bool:
    if pid <= 1 or pid == os.getpid():
        return False
    try:
        os.kill(pid, signal.SIGTERM)
        time.sleep(0.05)
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        os.kill(pid, signal.SIGKILL)
        return True
    except ProcessLookupError:
        return True
    except PermissionError:
        return False


def _list_apd_tmux_sessions(tmux_bin: str) -> list[str]:
    result = subprocess.run([tmux_bin, "list-sessions", "-F", "#{session_name}"], check=False, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    return [line.strip() for line in result.stdout.splitlines() if line.strip().startswith("apd_")]


def _tmux_pane_current_path(tmux_bin: str, session_name: str) -> str:
    result = subprocess.run([tmux_bin, "display-message", "-p", "-t", session_name, "#{pane_current_path}"], check=False, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    return result.stdout.strip()
