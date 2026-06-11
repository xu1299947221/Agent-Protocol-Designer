from __future__ import annotations

import json
import secrets
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .core import ensure_harness_protocol


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_id(value: str) -> str:
    safe = "".join(ch for ch in str(value or "") if ch.isalnum() or ch in {"-", "_"})
    return safe or secrets.token_hex(8)


def registry_path(registry_dir: Path, agent_id: str) -> Path:
    return registry_dir / f"{_safe_id(agent_id)}.json"


def summarize_protocol(protocol: dict[str, Any]) -> dict[str, Any]:
    protocol = ensure_harness_protocol(protocol or {})
    workflow = protocol.get("workflow") or {}
    architecture = protocol.get("architecture_check") or {}
    return {
        "project_name": protocol.get("project_name") or "",
        "domain_summary": str(protocol.get("domain_summary") or "")[:500],
        "operation_count": len([op for op in protocol.get("operations") or [] if isinstance(op, dict)]),
        "workflow_node_count": len([node for node in workflow.get("nodes") or [] if isinstance(node, dict)]),
        "workflow_name": workflow.get("name") or "",
        "completeness_percent": architecture.get("completeness_percent"),
        "covered_layers": architecture.get("covered_layers") or [],
    }


def make_registry_version(session: dict[str, Any], *, version: str, notes: str = "") -> dict[str, Any]:
    protocol = ensure_harness_protocol(deepcopy(session.get("protocol") or {}))
    return {
        "version": version,
        "version_id": version,
        "notes": notes,
        "source_session_id": session.get("session_id") or session.get("id") or "",
        "created_at": _now(),
        "summary": summarize_protocol(protocol),
        "protocol": protocol,
    }


def make_agent_entry(session: dict[str, Any], *, name: str = "", description: str = "", agent_id: str = "") -> dict[str, Any]:
    protocol = ensure_harness_protocol(deepcopy(session.get("protocol") or {}))
    resolved_name = name or protocol.get("project_name") or session.get("title") or "未命名 Agent"
    entry_id = _safe_id(agent_id or secrets.token_hex(8))
    version = make_registry_version({**session, "protocol": protocol}, version="v1", notes="初始注册版本")
    return {
        "agent_id": entry_id,
        "name": str(resolved_name)[:120],
        "description": description or protocol.get("domain_summary") or "",
        "status": "active",
        "created_at": _now(),
        "updated_at": _now(),
        "current_version": "v1",
        "tags": [],
        "summary": summarize_protocol(protocol),
        "versions": [version],
    }


def save_agent(registry_dir: Path, agent: dict[str, Any]) -> None:
    registry_dir.mkdir(parents=True, exist_ok=True)
    registry_path(registry_dir, agent["agent_id"]).write_text(json.dumps(agent, ensure_ascii=False, indent=2), encoding="utf-8")


def load_agent(registry_dir: Path, agent_id: str) -> dict[str, Any]:
    path = registry_path(registry_dir, agent_id)
    if not path.exists():
        raise KeyError(agent_id)
    return json.loads(path.read_text(encoding="utf-8"))


def list_agents(registry_dir: Path) -> list[dict[str, Any]]:
    agents = []
    for path in sorted(registry_dir.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            agent = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        agents.append({
            "agent_id": agent.get("agent_id"),
            "name": agent.get("name"),
            "description": agent.get("description"),
            "status": agent.get("status"),
            "current_version": agent.get("current_version"),
            "version_count": len(agent.get("versions") or []),
            "summary": agent.get("summary") or {},
            "created_at": agent.get("created_at"),
            "updated_at": agent.get("updated_at"),
        })
    return agents


def register_agent(registry_dir: Path, session: dict[str, Any], *, name: str = "", description: str = "") -> dict[str, Any]:
    agent = make_agent_entry(session, name=name, description=description)
    save_agent(registry_dir, agent)
    return agent


def add_agent_version(registry_dir: Path, agent_id: str, session: dict[str, Any], *, notes: str = "") -> dict[str, Any]:
    agent = load_agent(registry_dir, agent_id)
    next_index = len(agent.get("versions") or []) + 1
    version_id = f"v{next_index}"
    version = make_registry_version(session, version=version_id, notes=notes or f"版本 {version_id}")
    agent.setdefault("versions", []).append(version)
    agent["current_version"] = version_id
    agent["summary"] = version.get("summary") or agent.get("summary") or {}
    agent["updated_at"] = _now()
    save_agent(registry_dir, agent)
    return agent


def get_agent_version(agent: dict[str, Any], version_id: str = "") -> dict[str, Any]:
    versions = [item for item in agent.get("versions") or [] if isinstance(item, dict)]
    if not versions:
        raise KeyError("version")
    target = version_id or agent.get("current_version") or versions[-1].get("version_id")
    for version in versions:
        if version.get("version_id") == target or version.get("version") == target:
            return version
    raise KeyError(target)


def session_from_agent(agent: dict[str, Any], *, session_id: str, version_id: str = "") -> dict[str, Any]:
    version = get_agent_version(agent, version_id)
    protocol = ensure_harness_protocol(deepcopy(version.get("protocol") or {}))
    return {
        "session_id": session_id,
        "protocol": protocol,
        "history": [
            {"role": "assistant", "content": f"已从 Agent Registry 复制：{agent.get('name')} / {version.get('version_id')}"}
        ],
        "last_response": None,
        "title": f"{agent.get('name') or 'Agent'} 副本",
        "created_at": _now(),
        "updated_at": _now(),
        "registry_source": {"agent_id": agent.get("agent_id"), "version_id": version.get("version_id")},
    }
