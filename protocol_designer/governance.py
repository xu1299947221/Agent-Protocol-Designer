from __future__ import annotations

import json
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SENSITIVE_KEYWORDS = ("api_key", "apikey", "secret", "token", "password", "passwd", "authorization", "cookie", "私钥", "密码", "密钥", "令牌")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?<!\d)(?:1[3-9]\d{9}|\d{3,4}-\d{7,8})(?!\d)")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def detect_sensitive_data(value: Any, path: str = "") -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            key_path = f"{path}.{key}" if path else str(key)
            lowered = str(key).lower()
            if any(word in lowered for word in SENSITIVE_KEYWORDS):
                findings.append({"path": key_path, "type": "sensitive_key", "severity": "high", "hint": "字段名疑似敏感信息，应脱敏或避免进入 LLM 上下文。"})
            findings.extend(detect_sensitive_data(item, key_path))
    elif isinstance(value, list):
        for index, item in enumerate(value[:50]):
            findings.extend(detect_sensitive_data(item, f"{path}[{index}]"))
    elif isinstance(value, str):
        if EMAIL_RE.search(value):
            findings.append({"path": path, "type": "email", "severity": "medium", "hint": "文本包含邮箱，进入外部模型前建议脱敏。"})
        if PHONE_RE.search(value):
            findings.append({"path": path, "type": "phone", "severity": "medium", "hint": "文本包含手机号或电话，进入外部模型前建议脱敏。"})
    return findings


def classify_action_risk(action: str, payload: Any | None = None) -> str:
    text = action.lower()
    if any(word in text for word in ("delete", "rollback", "execute", "tool_test", "sandbox", "export", "write")):
        return "high"
    if detect_sensitive_data(payload or {}):
        return "medium"
    if any(word in text for word in ("register", "version", "clone", "runtime", "eval")):
        return "medium"
    return "low"


def create_audit_event(
    *,
    action: str,
    scope: str,
    resource_id: str = "",
    actor: str = "system",
    payload: Any | None = None,
    risk: str = "",
    result: str = "ok",
) -> dict[str, Any]:
    findings = detect_sensitive_data(payload or {})
    resolved_risk = risk or classify_action_risk(action, payload)
    if any(item.get("severity") == "high" for item in findings):
        resolved_risk = "high"
    return {
        "event_id": secrets.token_hex(8),
        "created_at": _now(),
        "actor": actor,
        "scope": scope,
        "action": action,
        "resource_id": resource_id,
        "risk": resolved_risk,
        "result": result,
        "sensitive_findings": findings,
        "retention_policy": retention_policy_for_scope(scope, resolved_risk),
        "responsibility_boundary": responsibility_boundary(scope, action),
    }


def retention_policy_for_scope(scope: str, risk: str = "medium") -> dict[str, Any]:
    if risk == "high":
        days = 180
    elif scope in {"runtime", "tool", "artifact"}:
        days = 90
    else:
        days = 30
    return {
        "retain_days": days,
        "delete_allowed": True,
        "export_allowed": scope in {"runtime", "artifact", "eval"},
        "note": "原型阶段只记录策略，不自动删除文件；后续应接入定时清理和用户可见删除入口。",
    }


def responsibility_boundary(scope: str, action: str) -> str:
    if scope == "tool":
        return "LLM 只能建议工具调用；程序负责权限校验、dry-run、真实执行和失败恢复。"
    if scope == "artifact":
        return "LLM 只能生成修改建议；程序负责版本、快照、确认和回滚。"
    if scope == "runtime":
        return "Runtime 负责节点状态、暂停恢复和 Trace；高风险节点必须人工确认。"
    if scope == "memory":
        return "LLM 只能建议写入记忆；用户拥有查看、修改和删除权。"
    return "程序负责记录审计事件，用户和开发者负责确认业务边界。"


def save_audit_event(audit_dir: Path, event: dict[str, Any]) -> dict[str, Any]:
    audit_dir.mkdir(parents=True, exist_ok=True)
    (audit_dir / f"{event['event_id']}.json").write_text(json.dumps(event, ensure_ascii=False, indent=2), encoding="utf-8")
    return event


def list_audit_events(audit_dir: Path, limit: int = 100) -> list[dict[str, Any]]:
    events = []
    if not audit_dir.exists():
        return events
    for path in sorted(audit_dir.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)[:limit]:
        try:
            event = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        events.append(event)
    return events


def summarize_audit_events(audit_dir: Path) -> dict[str, Any]:
    events = list_audit_events(audit_dir, limit=500)
    by_scope: dict[str, int] = {}
    by_risk: dict[str, int] = {}
    sensitive_count = 0
    for event in events:
        scope = str(event.get("scope") or "unknown")
        risk = str(event.get("risk") or "unknown")
        by_scope[scope] = by_scope.get(scope, 0) + 1
        by_risk[risk] = by_risk.get(risk, 0) + 1
        sensitive_count += len(event.get("sensitive_findings") or [])
    recommendations = []
    if by_risk.get("high"):
        recommendations.append("存在高风险审计事件，建议检查是否需要人工确认、沙箱或回滚。")
    if sensitive_count:
        recommendations.append("检测到敏感字段，进入 LLM 或外部工具前应脱敏。")
    if not events:
        recommendations.append("暂无审计事件；建议先运行 Runtime、Tool 测试或 Eval 回放。")
    return {
        "total": len(events),
        "by_scope": by_scope,
        "by_risk": by_risk,
        "sensitive_finding_count": sensitive_count,
        "recent_events": events[:20],
        "recommendations": recommendations,
    }
