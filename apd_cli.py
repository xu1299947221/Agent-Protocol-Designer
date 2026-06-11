#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import copy
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from protocol_designer.env import load_dotenv
load_dotenv()

from protocol_designer.core import EMPTY_PROTOCOL, build_exports, design_step, ensure_intent_protocol, merge_protocol
from protocol_designer.generator import generate_project_scaffold
from protocol_designer.v2 import (
    append_revert_turn,
    ensure_snapshot,
    normalize_open_questions,
    protocol_after_turn,
    validate_protocol,
)
from webui_server import (
    derive_session_title,
    list_saved_sessions,
    load_session_from_file,
    new_session,
    save_session,
    session_path,
)


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def print_json(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def read_message(args: argparse.Namespace) -> str:
    if getattr(args, "message", ""):
        return args.message
    parts = getattr(args, "message_parts", None) or []
    if parts == ["-"] or getattr(args, "stdin", False):
        return sys.stdin.read()
    if parts:
        return " ".join(parts)
    return ""


def load_or_new(session_id: str | None) -> dict[str, Any]:
    if session_id:
        session = load_session_from_file(session_id)
        if session:
            ensure_session_shape(session)
            session["session_id"] = session_id
            return session
        raise SystemExit(f"会话不存在：{session_id}")
    session = new_session()
    ensure_session_shape(session)
    return session


def ensure_session_shape(session: dict[str, Any]) -> None:
    session["protocol"] = merge_protocol(EMPTY_PROTOCOL, session.get("protocol") or {})
    session.setdefault("history", [])
    session.setdefault("turns", [])
    session.setdefault("snapshots", [])


def settings_from_args(args: argparse.Namespace) -> dict[str, Any]:
    settings: dict[str, Any] = {}
    for key in ("api_base", "api_key", "model", "timeout", "server_timeout", "max_tokens", "response_format"):
        value = getattr(args, key, None)
        if value not in (None, ""):
            settings[key] = value
    return settings


def compact_text(text: str, *, max_lines: int = 3, max_chars: int = 500) -> str:
    lines = [line.strip() for line in (text or "").strip().splitlines() if line.strip()]
    compact = "\n".join(lines[:max_lines])
    if len(lines) > max_lines:
        compact += "\n..."
    if len(compact) > max_chars:
        compact = compact[: max_chars - 3] + "..."
    return compact


def operation_map(protocol: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {op.get("name"): op for op in protocol.get("operations", []) or [] if isinstance(op, dict) and op.get("name")}


def object_map(protocol: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {obj.get("name"): obj for obj in protocol.get("objects", []) or [] if isinstance(obj, dict) and obj.get("name")}


def summarize_protocol_changes(before: dict[str, Any] | None, after: dict[str, Any] | None) -> dict[str, Any]:
    before = before or {}
    after = after or {}
    b_ops, a_ops = operation_map(before), operation_map(after)
    b_objs, a_objs = object_map(before), object_map(after)
    before_confirmation = before.get("confirmation_rules", []) or []
    after_confirmation = after.get("confirmation_rules", []) or []
    before_clarification = before.get("clarification_rules", []) or []
    after_clarification = after.get("clarification_rules", []) or []
    changes: dict[str, Any] = {
        "added_operations": sorted(set(a_ops) - set(b_ops)),
        "removed_operations": sorted(set(b_ops) - set(a_ops)),
        "changed_operations": [],
        "added_objects": sorted(set(a_objs) - set(b_objs)),
        "removed_objects": sorted(set(b_objs) - set(a_objs)),
        "changed_objects": [],
        "confirmation_rules_added": max(0, len(after_confirmation) - len(before_confirmation)),
        "clarification_rules_added": max(0, len(after_clarification) - len(before_clarification)),
        "confirmation_rules": {
            "added": [rule for rule in after_confirmation if rule not in before_confirmation],
            "removed": [rule for rule in before_confirmation if rule not in after_confirmation],
            "modified": [],
            "deduplicated": [],
        },
        "clarification_rules": {
            "added": [rule for rule in after_clarification if rule not in before_clarification],
            "removed": [rule for rule in before_clarification if rule not in after_clarification],
            "modified": [],
            "deduplicated": [],
        },
    }
    for name in sorted(set(a_ops) & set(b_ops)):
        if a_ops[name] != b_ops[name]:
            changes["changed_operations"].append(name)
    for name in sorted(set(a_objs) & set(b_objs)):
        if a_objs[name] != b_objs[name]:
            changes["changed_objects"].append(name)
    return changes


def compact_changes(changes: dict[str, Any]) -> str:
    parts = []
    if changes.get("added_operations"):
        parts.append(f"+{len(changes['added_operations'])} operation")
    if changes.get("removed_operations"):
        parts.append(f"-{len(changes['removed_operations'])} operation")
    if changes.get("changed_operations"):
        parts.append(f"~{len(changes['changed_operations'])} operation")
    if changes.get("added_objects"):
        parts.append(f"+{len(changes['added_objects'])} object")
    if changes.get("removed_objects"):
        parts.append(f"-{len(changes['removed_objects'])} object")
    if changes.get("changed_objects"):
        parts.append(f"~{len(changes['changed_objects'])} object")
    if changes.get("confirmation_rules_added"):
        parts.append(f"+{changes['confirmation_rules_added']} confirmation_rules")
    if changes.get("clarification_rules_added"):
        parts.append(f"+{changes['clarification_rules_added']} clarification_rules")
    return ", ".join(parts)


def append_turn(session: dict[str, Any], user_message: str, result: dict[str, Any], before: dict[str, Any], after: dict[str, Any]) -> None:
    ensure_session_shape(session)
    changes = summarize_protocol_changes(before, after)
    turn = {
        "turn_index": len(session["turns"]) + 1,
        "timestamp": now_iso(),
        "stage_before": (session.get("last_response") or {}).get("stage") or "discover",
        "stage_after": result.get("stage") or "discover",
        "user": user_message,
        "assistant": result.get("assistant_message") or "",
        "next_questions": result.get("next_questions") or [],
        "quality_notes": result.get("quality_notes") or [],
        "changes": changes,
        "protocol_before": before,
        "protocol_after": after,
    }
    session["turns"].append(turn)
    ensure_snapshot(session, after, changes)


def ask_output(session: dict[str, Any], result: dict[str, Any], changes: dict[str, Any]) -> str:
    lines = [f"[session] {session['session_id']}", f"[stage] {result.get('stage') or 'discover'}"]
    assistant = compact_text(result.get("assistant_message") or "")
    if assistant:
        lines.append(f"[assistant] {assistant}")
    questions = result.get("next_questions") or []
    lines.append(f"[next_question] {questions[0] if questions else ''}")
    notes = result.get("quality_notes") or []
    if notes:
        lines.append("[quality_notes]")
        for note in notes[:5]:
            lines.append(f"- {compact_text(str(note), max_lines=1, max_chars=180)}")
    open_questions = (session.get("protocol") or {}).get("open_questions") or []
    if open_questions:
        lines.append("[open_questions]")
        for question in open_questions[:5]:
            lines.append(f"- {compact_text(str(question), max_lines=1, max_chars=180)}")
    change_text = compact_changes(changes)
    if change_text:
        lines.append(f"[changes] {change_text}")
    lines.append(f"[done] {'yes' if (result.get('stage') == 'final') else 'no'}")
    return "\n".join(lines)


async def ask_session(args: argparse.Namespace, *, concise: bool) -> dict[str, Any]:
    message = read_message(args)
    if not message.strip():
        raise SystemExit("请提供消息，或使用 - 从 stdin 读取")
    session = load_or_new(args.session)
    before = copy.deepcopy(session.get("protocol") or EMPTY_PROTOCOL)
    session["history"].append({"role": "user", "content": message})
    result = await design_step(message, protocol=session.get("protocol"), history=session.get("history"), settings=settings_from_args(args))
    after = merge_protocol(session.get("protocol"), result.get("protocol"))
    session["protocol"] = after
    session["last_response"] = result
    session["history"].append({"role": "assistant", "content": result.get("assistant_message", "")})
    append_turn(session, message, result, before, after)
    normalize_open_questions(session)
    save_session(session)
    return {"session": session, "result": result, "changes": summarize_protocol_changes(before, after)}


async def cmd_ask(args: argparse.Namespace) -> None:
    data = await ask_session(args, concise=True)
    print(ask_output(data["session"], data["result"], data["changes"]))


async def run_message(args: argparse.Namespace) -> None:
    data = await ask_session(args, concise=False)
    session, result = data["session"], data["result"]
    if args.json:
        print_json({"session_id": session["session_id"], "response": result, "protocol": session["protocol"]})
        return
    print(f"会话：{session['session_id']}｜标题：{derive_session_title(session)}")
    print("\n设计器：")
    print(result.get("assistant_message") or "")
    if result.get("next_questions"):
        print("\n下一步问题：")
        for item in result.get("next_questions") or []:
            print(f"- {item}")
    print("\n当前阶段：", result.get("stage") or "discover")


async def run_interactive(args: argparse.Namespace) -> None:
    session = load_or_new(args.session)
    settings = settings_from_args(args)
    created_empty = not args.session and not (session.get("history") or [])
    print("Agent Protocol Designer CLI")
    print(f"会话：{session['session_id']}｜标题：{derive_session_title(session)}")
    print("输入 /help 查看命令，/exit 退出。")
    while True:
        try:
            message = input("\n你> ").strip()
        except (EOFError, KeyboardInterrupt):
            if created_empty and not (session.get("history") or []):
                session_path(session["session_id"]).unlink(missing_ok=True)
            print("\n退出。")
            return
        if not message:
            continue
        if message in {"/exit", "/quit"}:
            if created_empty and not (session.get("history") or []):
                session_path(session["session_id"]).unlink(missing_ok=True)
            return
        if message == "/help":
            print("命令：/protocol 查看协议，/export DIR 导出，/history 查看历史，/next 查看下一步，/diff 查看变化，/exit 退出")
            continue
        if message == "/protocol":
            print_json(session.get("protocol") or EMPTY_PROTOCOL)
            continue
        if message == "/history":
            print_history(session, show_diff=False)
            continue
        if message == "/next":
            print(next_question(session))
            continue
        if message == "/diff":
            print(diff_text(session))
            continue
        if message.startswith("/export"):
            parts = message.split(maxsplit=1)
            out = Path(parts[1] if len(parts) > 1 else f"exports/{session['session_id']}")
            export_session(session, out)
            print(f"已导出到：{out}")
            continue
        before = copy.deepcopy(session.get("protocol") or EMPTY_PROTOCOL)
        session["history"].append({"role": "user", "content": message})
        result = await design_step(message, protocol=session.get("protocol"), history=session.get("history"), settings=settings)
        after = merge_protocol(session.get("protocol"), result.get("protocol"))
        session["protocol"] = after
        session["last_response"] = result
        session["history"].append({"role": "assistant", "content": result.get("assistant_message", "")})
        append_turn(session, message, result, before, after)
        normalize_open_questions(session)
        save_session(session)
        print("\n设计器>", result.get("assistant_message") or "")
        for question in result.get("next_questions") or []:
            print("下一步>", question)


def export_session(session: dict[str, Any], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    exports = build_exports(session.get("protocol") or {})
    for name, content in exports.items():
        (out_dir / name).write_text(content, encoding="utf-8")
    (out_dir / "session.json").write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")


def cmd_list(args: argparse.Namespace) -> None:
    sessions = list_saved_sessions()
    if args.json:
        print_json({"sessions": sessions})
        return
    if not sessions:
        print("暂无历史会话。")
        return
    for item in sessions:
        print(f"{item['session_id']}\t{item.get('updated_at','')}\t{item.get('stage','')}\t{item.get('title','未命名')}")


def print_history(session: dict[str, Any], *, show_diff: bool) -> None:
    turns = session.get("turns") or []
    if turns:
        for turn in turns:
            print(f"=== Turn {turn.get('turn_index')} ({turn.get('timestamp')}) [stage: {turn.get('stage_before')} → {turn.get('stage_after')}] ===")
            print(f"[user] {compact_text(turn.get('user') or '', max_lines=4, max_chars=500)}")
            print(f"[assistant] {compact_text(turn.get('assistant') or '', max_lines=4, max_chars=500)}")
            if show_diff:
                text = compact_changes(turn.get("changes") or {})
                print(f"[{text or 'no protocol changes'}]")
            print()
        return
    for item in session.get("history") or []:
        print(f"[{item.get('role')}] {item.get('content')}")


def cmd_show(args: argparse.Namespace) -> None:
    session = load_session_from_file(args.session)
    if not session:
        raise SystemExit(f"会话不存在：{args.session}")
    ensure_session_shape(session)
    if args.history:
        print_history(session, show_diff=args.diff)
        return
    if args.json:
        print_json(session)
        return
    print(f"会话：{session['session_id']}｜标题：{derive_session_title(session)}")
    print(f"更新时间：{session.get('updated_at')}")
    print(f"阶段：{(session.get('last_response') or {}).get('stage') or 'discover'}")
    print("\n最近对话：")
    for item in (session.get("history") or [])[-10:]:
        print(f"{item.get('role')}: {compact_text(item.get('content') or '', max_lines=2)}")


def cmd_delete(args: argparse.Namespace) -> None:
    path = session_path(args.session)
    if path.exists():
        path.unlink()
        print(f"已删除：{args.session}")
    else:
        print(f"会话不存在：{args.session}")


def cmd_export(args: argparse.Namespace) -> None:
    session = load_session_from_file(args.session)
    if not session:
        raise SystemExit(f"会话不存在：{args.session}")
    out = Path(args.out or f"exports/{args.session}")
    export_session(session, out)
    print(f"已导出到：{out}")

def cmd_workflow(args: argparse.Namespace) -> None:
    session = load_session_from_file(args.session)
    if not session:
        raise SystemExit(f"会话不存在：{args.session}")
    exports = build_exports(session.get("protocol") or {})
    name = "workflow.json" if args.json else "workflow_plan.md"
    content = exports.get(name, "")
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(content, encoding="utf-8")
        print(f"已导出：{out}")
        return
    print(content)

def cmd_generate(args: argparse.Namespace) -> None:
    session = load_session_from_file(args.session)
    if not session:
        raise SystemExit(f"会话不存在：{args.session}")
    out = Path(args.out or "generated")
    try:
        written = generate_project_scaffold(session, out, template=args.template, force=args.force, project_name=args.name)
    except FileExistsError as exc:
        raise SystemExit(str(exc)) from exc
    root = written[0].parents[0] if written else out
    # written[0] may be README.md under project root; normalize for display
    project_root = out
    if written:
        for candidate in written[0].parents:
            if (candidate / "README.md").exists() and (candidate / "backend").exists():
                project_root = candidate
                break
    print(f"已生成 Agent 项目脚手架：{project_root}")
    print(f"文件数：{len(written)}")
    print("下一步：")
    print(f"  cd {project_root}")
    print("  cat README.md")


def next_question(session: dict[str, Any]) -> str:
    response = session.get("last_response") or {}
    questions = response.get("next_questions") or []
    stage = response.get("stage") or "discover"
    if questions:
        return str(questions[0])
    return f"(no pending question; stage={stage})"


def cmd_next(args: argparse.Namespace) -> None:
    session = load_session_from_file(args.session)
    if not session:
        raise SystemExit(f"会话不存在：{args.session}")
    print(next_question(session))


def diff_value_lines(prefix: str, before: Any, after: Any) -> list[str]:
    lines: list[str] = []
    if isinstance(before, list) and isinstance(after, list):
        if len(after) > len(before):
            lines.append(f"~ {prefix}: +{len(after) - len(before)}")
        elif len(after) < len(before):
            lines.append(f"~ {prefix}: -{len(before) - len(after)}")
        elif before != after:
            lines.append(f"~ {prefix}: changed")
    elif before != after:
        lines.append(f"~ {prefix}: {before!r} → {after!r}")
    return lines


def protocol_diff_lines(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    b_ops, a_ops = operation_map(before), operation_map(after)
    for name in sorted(set(a_ops) - set(b_ops)):
        op = a_ops[name]
        lines.append(f"+ operations: {name} (risk={op.get('risk')}, confirm={op.get('requires_confirmation')})")
    for name in sorted(set(b_ops) - set(a_ops)):
        lines.append(f"- operations: {name}")
    for name in sorted(set(a_ops) & set(b_ops)):
        b, a = b_ops[name], a_ops[name]
        for key in sorted(set(b) | set(a)):
            if b.get(key) != a.get(key):
                lines.extend(diff_value_lines(f"operations.{name}.{key}", b.get(key), a.get(key)))
    b_objs, a_objs = object_map(before), object_map(after)
    for name in sorted(set(a_objs) - set(b_objs)):
        obj = a_objs[name]
        lines.append(f"+ objects: {name} (key_fields={len(obj.get('key_fields') or [])})")
    for name in sorted(set(b_objs) - set(a_objs)):
        lines.append(f"- objects: {name}")
    for key in ("confirmation_rules", "clarification_rules"):
        b_len = len(before.get(key, []) or [])
        a_len = len(after.get(key, []) or [])
        if a_len > b_len:
            lines.append(f"+ {key}: {a_len - b_len} 条新规则")
        elif a_len < b_len:
            lines.append(f"- {key}: {b_len - a_len} 条规则")
        elif before.get(key) != after.get(key):
            lines.append(f"~ {key}: changed")
    return lines or ["(no protocol changes)"]


def get_turn_protocols(session: dict[str, Any], turn: int | None) -> tuple[dict[str, Any], dict[str, Any]]:
    turns = session.get("turns") or []
    if not turns:
        return {}, session.get("protocol") or {}
    if turn is None:
        selected = turns[-1]
    else:
        idx = turn if turn >= 0 else len(turns) + turn + 1
        idx = max(1, min(len(turns), idx))
        selected = turns[idx - 1]
    return selected.get("protocol_before") or {}, session.get("protocol") or selected.get("protocol_after") or {}


def diff_text(session: dict[str, Any], turn: int | None = None) -> str:
    before, after = get_turn_protocols(session, turn)
    return "\n".join(protocol_diff_lines(before, after))


def cmd_diff(args: argparse.Namespace) -> None:
    session = load_session_from_file(args.session)
    if not session:
        raise SystemExit(f"会话不存在：{args.session}")
    before, after = get_turn_protocols(session, args.turn)
    if args.json:
        print_json({"diff": protocol_diff_lines(before, after), "changes": summarize_protocol_changes(before, after)})
    else:
        print("\n".join(protocol_diff_lines(before, after)))


def resolve_path(protocol: dict[str, Any], path: str) -> Any:
    if not path:
        return protocol
    current: Any = protocol
    parts = path.split(".")
    idx = 0
    while idx < len(parts):
        part = parts[idx]
        if part == "operations" and idx + 1 < len(parts):
            current = operation_map(protocol).get(parts[idx + 1])
            if current is None:
                raise KeyError(path)
            idx += 2
            continue
        if part == "objects" and idx + 1 < len(parts):
            current = object_map(protocol).get(parts[idx + 1])
            if current is None:
                raise KeyError(path)
            idx += 2
            continue
        if isinstance(current, dict) and part in current:
            current = current[part]
            idx += 1
            continue
        if isinstance(current, list):
            try:
                current = current[int(part)]
                idx += 1
                continue
            except Exception as exc:
                raise KeyError(path) from exc
        raise KeyError(path)
    return current


def yaml_like(value: Any, indent: int = 0) -> str:
    pad = " " * indent
    if isinstance(value, dict):
        lines = []
        for key, val in value.items():
            if isinstance(val, (dict, list)):
                if isinstance(val, list) and all(not isinstance(x, (dict, list)) for x in val):
                    lines.append(f"{pad}{key}: {', '.join(map(str, val))}")
                else:
                    lines.append(f"{pad}{key}: ...")
            else:
                lines.append(f"{pad}{key}: {val}")
        return "\n".join(lines)
    if isinstance(value, list):
        return "\n".join(json.dumps(item, ensure_ascii=False) if isinstance(item, (dict, list)) else str(item) for item in value)
    return str(value)


def cmd_get(args: argparse.Namespace) -> None:
    session = load_session_from_file(args.session)
    if not session:
        raise SystemExit(f"会话不存在：{args.session}")
    protocol = ensure_intent_protocol(session.get("protocol") or {})
    if args.path == "operations":
        value = list(operation_map(protocol))
    elif args.path == "objects":
        value = list(object_map(protocol))
    else:
        try:
            value = resolve_path(protocol, args.path)
        except KeyError:
            raise SystemExit(f"路径不存在：{args.path}")
    if args.json:
        print_json(value)
    else:
        print(yaml_like(value))


def cmd_validate(args: argparse.Namespace) -> None:
    session = load_session_from_file(args.session)
    if not session:
        raise SystemExit(f"会话不存在：{args.session}")
    report = validate_protocol(session.get("protocol") or {})
    if args.json:
        print_json(report.to_dict())
    else:
        print(report.text())


def cmd_revert(args: argparse.Namespace) -> None:
    session = load_session_from_file(args.session)
    if session:
        session["session_id"] = args.session
    if not session:
        raise SystemExit(f"会话不存在：{args.session}")
    ensure_session_shape(session)
    try:
        protocol = protocol_after_turn(session, args.to_turn)
    except KeyError as exc:
        raise SystemExit(str(exc)) from exc
    append_revert_turn(session, target_turn=args.to_turn, protocol=protocol, reason=f"CLI revert to turn {args.to_turn}")
    normalize_open_questions(session)
    save_session(session)
    print(f"已回滚到第 {args.to_turn} 轮之后：{session['session_id']}")


def cmd_rollback(args: argparse.Namespace) -> None:
    session = load_session_from_file(args.session)
    if session:
        session["session_id"] = args.session
    if not session:
        raise SystemExit(f"会话不存在：{args.session}")
    ensure_session_shape(session)
    turns = session.get("turns") or []
    if not turns:
        raise SystemExit("没有可撤销的历史轮次")
    last_turn = int(turns[-1].get("turn_index") or len(turns))
    target_turn = max(0, last_turn - 1)
    try:
        protocol = protocol_after_turn(session, target_turn)
    except KeyError as exc:
        raise SystemExit(str(exc)) from exc
    append_revert_turn(session, target_turn=target_turn, protocol=protocol, reason=f"CLI rollback last turn {last_turn}")
    normalize_open_questions(session)
    save_session(session)
    print(f"已撤销最近一轮，当前回到第 {target_turn} 轮之后：{session['session_id']}")


def cmd_artifact(args: argparse.Namespace) -> None:
    session = load_session_from_file(args.session)
    if session:
        session["session_id"] = args.session
    if not session:
        raise SystemExit(f"会话不存在：{args.session}")
    ensure_session_shape(session)
    protocol = session.setdefault("protocol", copy.deepcopy(EMPTY_PROTOCOL))
    artifacts = protocol.setdefault("artifacts", {})
    if args.set:
        key, value = args.set
        parts = key.split(".")
        if len(parts) != 2:
            raise SystemExit("--set 需要形如：development_plan.structure phase_grouped_tasks")
        artifact_name, field_name = parts
        artifact = artifacts.setdefault(artifact_name, {})
        before = copy.deepcopy(protocol)
        artifact[field_name] = value
        session["protocol"] = protocol
        ensure_snapshot(session, protocol, summarize_protocol_changes(before, protocol))
        save_session(session)
        print(f"已设置 artifacts.{artifact_name}.{field_name} = {value}")
        return
    print_json(artifacts)



def add_model_args(command: argparse.ArgumentParser) -> None:
    command.add_argument("--api-base", default="")
    command.add_argument("--api-key", default="")
    command.add_argument("--model", default="")
    command.add_argument("--timeout", type=float, default=None)
    command.add_argument("--server-timeout", type=float, default=None)
    command.add_argument("--max-tokens", type=int, default=None)
    command.add_argument("--response-format", default="")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Agent Protocol Designer CLI")
    parser.add_argument("--api-base", default="")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--timeout", type=float, default=None, help="LLM 请求超时秒")
    parser.add_argument("--server-timeout", type=float, default=None, help="保留兼容字段")
    parser.add_argument("--max-tokens", type=int, default=None)
    parser.add_argument("--response-format", default="0")
    sub = parser.add_subparsers(dest="cmd", required=True)

    ask = sub.add_parser("ask", help="交互友好：在 session 上推进一轮，只输出简短状态")
    add_model_args(ask)
    ask.add_argument("message_parts", nargs="*")
    ask.add_argument("--message", "-m", default="")
    ask.add_argument("--session", "-s", default="")
    ask.add_argument("--new", action="store_true", help="显式创建新会话（默认未传 -s 也会新建）")
    ask.set_defaults(func=lambda a: asyncio.run(cmd_ask(a)))

    run = sub.add_parser("run", help="发送单条消息（向后兼容）")
    add_model_args(run)
    run.add_argument("message_parts", nargs="*")
    run.add_argument("--message", "-m", default="")
    run.add_argument("--session", "-s", default="")
    run.add_argument("--json", action="store_true")
    run.set_defaults(func=lambda a: asyncio.run(run_message(a)))

    chat = sub.add_parser("chat", help="进入交互模式")
    add_model_args(chat)
    chat.add_argument("--session", "-s", default="")
    chat.set_defaults(func=lambda a: asyncio.run(run_interactive(a)))

    ls = sub.add_parser("list", help="列出历史会话")
    ls.add_argument("--json", action="store_true")
    ls.set_defaults(func=cmd_list)

    show = sub.add_parser("show", help="查看会话")
    show.add_argument("session", nargs="?")
    show.add_argument("--session", "-s", dest="session_opt", default="")
    show.add_argument("--json", action="store_true")
    show.add_argument("--history", action="store_true")
    show.add_argument("--diff", action="store_true")
    show.set_defaults(func=lambda a: (setattr(a, "session", a.session or a.session_opt), cmd_show(a))[1])

    delete = sub.add_parser("delete", help="删除会话")
    delete.add_argument("session")
    delete.set_defaults(func=cmd_delete)

    export = sub.add_parser("export", help="导出会话产物")
    export.add_argument("session")
    export.add_argument("--out", "-o", default="")
    export.set_defaults(func=cmd_export)

    workflow = sub.add_parser("workflow", help="查看或导出 Dynamic Workflow 设计")
    workflow.add_argument("session")
    workflow.add_argument("--json", action="store_true", help="输出 workflow.json，默认输出 workflow_plan.md")
    workflow.add_argument("--out", "-o", default="", help="写入指定文件")
    workflow.set_defaults(func=cmd_workflow)

    generate = sub.add_parser("generate", help="根据 session 生成 Agent 项目脚手架")
    generate.add_argument("session")
    generate.add_argument("--template", default="fastapi-vue", help="脚手架模板，当前支持 fastapi-vue")
    generate.add_argument("--out", "-o", default="generated", help="输出目录")
    generate.add_argument("--name", default="", help="指定生成项目目录名，避免使用会话标题")
    generate.add_argument("--force", action="store_true", help="如果输出目录已存在则覆盖")
    generate.set_defaults(func=cmd_generate)

    diff = sub.add_parser("diff", help="显示协议变化")
    diff.add_argument("--session", "-s", required=True)
    diff.add_argument("--turn", type=int, default=None)
    diff.add_argument("--json", action="store_true")
    diff.set_defaults(func=cmd_diff)

    get = sub.add_parser("get", help="查询协议局部")
    get.add_argument("--session", "-s", required=True)
    get.add_argument("path")
    get.add_argument("--json", action="store_true")
    get.set_defaults(func=cmd_get)

    nxt = sub.add_parser("next", help="输出下一步问题")
    nxt.add_argument("--session", "-s", required=True)
    nxt.set_defaults(func=cmd_next)

    validate = sub.add_parser("validate", help="校验协议自一致性")
    validate.add_argument("--session", "-s", required=True)
    validate.add_argument("--json", action="store_true")
    validate.set_defaults(func=cmd_validate)

    revert = sub.add_parser("revert", help="把协议回滚到指定轮次之后")
    revert.add_argument("--session", "-s", required=True)
    revert.add_argument("--to-turn", type=int, required=True)
    revert.set_defaults(func=cmd_revert)

    rollback = sub.add_parser("rollback", help="撤销最近一轮协议变化")
    rollback.add_argument("--session", "-s", required=True)
    rollback.add_argument("--last", action="store_true", help="撤销最近一轮")
    rollback.set_defaults(func=cmd_rollback)

    artifact = sub.add_parser("artifact", help="查看或设置协议产物 schema")
    artifact.add_argument("--session", "-s", required=True)
    artifact.add_argument("--set", nargs=2, metavar=("KEY", "VALUE"), help="例如：development_plan.structure phase_grouped_tasks")
    artifact.set_defaults(func=cmd_artifact)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if getattr(args, "cmd", "") == "show" and not (getattr(args, "session", None) or getattr(args, "session_opt", None)):
        raise SystemExit("请提供 session id")
    args.func(args)


if __name__ == "__main__":
    main()
