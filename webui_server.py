from __future__ import annotations

import asyncio
import copy
import hmac
import hashlib
import io
import json
import os
import secrets
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from protocol_designer.core import EMPTY_PROTOCOL, build_exports, design_step, fallback_step, merge_protocol
from protocol_designer.delegated_generator import DEFAULT_OPEN_CLAUDE_SOURCE, generate_delegated_agent_project
from protocol_designer.delegated_playground import DelegatedPlaygroundManager
from protocol_designer.demo_playground import DemoPlaygroundManager
from protocol_designer.dev_studio import DevStudioManager
from protocol_designer.generator import generate_project_scaffold, safe_project_name
from protocol_designer.interactive_cli import InteractiveCliManager, TtydCliManager
from protocol_designer.sandbox import run_docker_sandbox
from protocol_designer.tool_runtime import list_tool_runs, run_tool_tests, save_tool_run
from protocol_designer.env import load_dotenv
from protocol_designer.agent_registry import add_agent_version, list_agents as list_registered_agents, load_agent as load_registered_agent, register_agent, session_from_agent
from protocol_designer.eval_replay import replay_eval_cases
from protocol_designer.governance import create_audit_event, list_audit_events, save_audit_event, summarize_audit_events
from protocol_designer.llm import LLMError, chat_json_result, resolve_config
from protocol_designer.multi_agent import analyze_multi_agent_collaboration
from protocol_designer.observability import build_observability_summary
from protocol_designer.preview_runtime import generate_preview_examples, preview_run
from protocol_designer.workflow_runtime import build_runtime_plan, rollback_artifact_in_job, run_workflow_once, runtime_result_to_job
from protocol_designer.v2 import ensure_snapshot, normalize_open_questions

load_dotenv()

SESSIONS: dict[str, dict[str, Any]] = {}
BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"
GUIDE_PATH = BASE_DIR / "docs" / "agent_architecture_guide.md"
CASE_PATH = BASE_DIR / "docs" / "writing_agent_case.md"
WRITING_VALIDATION_PATH = BASE_DIR / "docs" / "writing_agent_apd_validation.md"
BID_VALIDATION_PATH = BASE_DIR / "docs" / "bid_agent_apd_validation.md"
AGENTOS_EVALUATION_PATH = BASE_DIR / "docs" / "agentos_evaluation.md"
CAPABILITY_BOUNDARY_PATH = BASE_DIR / "docs" / "apd_capability_boundary.md"
DATA_DIR = BASE_DIR / "data" / "sessions"
DATA_DIR.mkdir(parents=True, exist_ok=True)
RUNTIME_DIR = BASE_DIR / "data" / "runtime_jobs"
RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
AGENT_REGISTRY_DIR = BASE_DIR / "data" / "agent_registry"
AGENT_REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
TOOL_RUN_DIR = BASE_DIR / "data" / "tool_runs"
TOOL_RUN_DIR.mkdir(parents=True, exist_ok=True)
EVAL_REPLAY_DIR = BASE_DIR / "data" / "eval_replays"
EVAL_REPLAY_DIR.mkdir(parents=True, exist_ok=True)
AUDIT_DIR = BASE_DIR / "data" / "audit_events"
AUDIT_DIR.mkdir(parents=True, exist_ok=True)
DEMO_PLAYGROUND_DIR = BASE_DIR / "data" / "demo_playground"
DEMO_PLAYGROUND_DIR.mkdir(parents=True, exist_ok=True)
DEMO_PLAYGROUND = DemoPlaygroundManager(DEMO_PLAYGROUND_DIR)
DELEGATED_PLAYGROUND_DIR = BASE_DIR / "data" / "delegated_playground"
DELEGATED_PLAYGROUND_DIR.mkdir(parents=True, exist_ok=True)
DELEGATED_PLAYGROUND = DelegatedPlaygroundManager(DELEGATED_PLAYGROUND_DIR)
DEV_STUDIO_DIR = BASE_DIR / "data" / "dev_workspaces"
DEV_STUDIO_DIR.mkdir(parents=True, exist_ok=True)
DEV_STUDIO = DevStudioManager(DEV_STUDIO_DIR)
INTERACTIVE_CLI = InteractiveCliManager()
TTYD_CLI = TtydCliManager()
AUTH_COOKIE = "apd_auth"
PUBLIC_PATHS = {"/login", "/api/login"}


def auth_username() -> str:
    return os.getenv("APD_USERNAME", "admin")


def auth_password() -> str:
    return os.getenv("APD_PASSWORD", "apd123456")


def auth_secret() -> str:
    return os.getenv("APD_AUTH_SECRET") or auth_password()


def sign_user(username: str) -> str:
    return hmac.new(auth_secret().encode("utf-8"), username.encode("utf-8"), hashlib.sha256).hexdigest()


def make_auth_token(username: str) -> str:
    return f"{username}:{sign_user(username)}"


def is_authed(request) -> bool:
    token = request.cookies.get(AUTH_COOKIE, "")
    if ":" not in token:
        return False
    username, signature = token.split(":", 1)
    return username == auth_username() and hmac.compare_digest(signature, sign_user(username))


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        path = request.url.path
        if path in PUBLIC_PATHS or path.startswith('/favicon') or path.startswith('/static/'):
            return await call_next(request)
        if is_authed(request):
            return await call_next(request)
        if path.startswith('/api/'):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return RedirectResponse('/login')



def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def session_path(session_id: str) -> Path:
    safe = "".join(ch for ch in session_id if ch.isalnum() or ch in "_-")
    return DATA_DIR / f"{safe}.json"


def save_session(session: dict[str, Any]) -> None:
    refresh_session_title(session)
    session["updated_at"] = now_iso()
    session_path(session["session_id"]).write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")


def load_session_from_file(session_id: str) -> dict[str, Any] | None:
    path = session_path(session_id)
    if not path.exists():
        return None
    try:
        session = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if session.get("session_id"):
        session["protocol"] = merge_protocol(EMPTY_PROTOCOL, session.get("protocol") or {})
        SESSIONS[session["session_id"]] = session
        return session
    return None


def runtime_job_path(job_id: str) -> Path:
    safe = "".join(ch for ch in str(job_id or "") if ch.isalnum() or ch in {"-", "_"})
    if not safe:
        safe = "unknown"
    return RUNTIME_DIR / f"{safe}.json"


def save_runtime_job(job: dict[str, Any]) -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    runtime_job_path(job["job_id"]).write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")


def load_runtime_job(job_id: str) -> dict[str, Any]:
    path = runtime_job_path(job_id)
    if not path.exists():
        raise KeyError(job_id)
    return json.loads(path.read_text(encoding="utf-8"))


def list_runtime_jobs(session_id: str = "") -> list[dict[str, Any]]:
    jobs = []
    for path in sorted(RUNTIME_DIR.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if session_id and job.get("session_id") != session_id:
            continue
        jobs.append({
            "job_id": job.get("job_id"),
            "session_id": job.get("session_id"),
            "title": job.get("title") or job.get("job_id"),
            "status": job.get("status"),
            "summary": job.get("summary") or {},
            "waiting_for": job.get("waiting_for"),
            "created_at": job.get("created_at"),
            "updated_at": job.get("updated_at"),
        })
    return jobs


def save_eval_replay(result: dict[str, Any], session_id: str = "") -> dict[str, Any]:
    EVAL_REPLAY_DIR.mkdir(parents=True, exist_ok=True)
    replay = dict(result)
    replay.setdefault("replay_id", secrets.token_hex(8))
    replay["session_id"] = session_id
    replay["created_at"] = now_iso()
    (EVAL_REPLAY_DIR / f"{replay['replay_id']}.json").write_text(json.dumps(replay, ensure_ascii=False, indent=2), encoding="utf-8")
    return replay


def audit(action: str, scope: str, resource_id: str = "", payload: Any | None = None, result: str = "ok", risk: str = "") -> dict[str, Any]:
    event = create_audit_event(action=action, scope=scope, resource_id=resource_id, payload=payload or {}, result=result, risk=risk)
    return save_audit_event(AUDIT_DIR, event)


def derive_session_title(session: dict[str, Any]) -> str:
    protocol = session.get("protocol") or {}
    title = session.get("title")
    if title == "未命名会话":
        title = ""
    title = title or protocol.get("project_name") or protocol.get("domain_summary")
    if not title:
        for item in session.get("history") or []:
            if item.get("role") == "user" and item.get("content"):
                title = item.get("content")
                break
    return str(title or "未命名会话").strip()[:80]


def refresh_session_title(session: dict[str, Any]) -> None:
    title = derive_session_title(session)
    if title and title != "未命名会话":
        session["title"] = title


def session_summary(session: dict[str, Any]) -> dict[str, Any]:
    title = derive_session_title(session)
    return {
        "session_id": session.get("session_id"),
        "title": str(title)[:80],
        "updated_at": session.get("updated_at") or "",
        "message_count": len(session.get("history") or []),
        "stage": ((session.get("last_response") or {}).get("stage") or "discover"),
    }


def list_saved_sessions(*, include_empty: bool = False) -> list[dict[str, Any]]:
    items = []
    for path in DATA_DIR.glob("*.json"):
        try:
            session = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not include_empty and not (session.get("history") or []):
            continue
        items.append(session_summary(session))
    return sorted(items, key=lambda item: item.get("updated_at") or "", reverse=True)


def new_session() -> dict[str, Any]:
    session_id = secrets.token_hex(8)
    session = {
        "session_id": session_id,
        "protocol": json.loads(json.dumps(EMPTY_PROTOCOL, ensure_ascii=False)),
        "history": [],
        "last_response": None,
        "title": "未命名会话",
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    SESSIONS[session_id] = session
    save_session(session)
    return session


def get_session(session_id: str | None) -> dict[str, Any]:
    if session_id and session_id in SESSIONS:
        SESSIONS[session_id]["protocol"] = merge_protocol(EMPTY_PROTOCOL, SESSIONS[session_id].get("protocol") or {})
        return SESSIONS[session_id]
    if session_id:
        restored = load_session_from_file(session_id)
        if restored:
            return restored
    return new_session()


async def login_page(request):
    return HTMLResponse(LOGIN_HTML)


async def api_login(request):
    payload = await request.json()
    username = str(payload.get("username") or "")
    password = str(payload.get("password") or "")
    if username == auth_username() and password == auth_password():
        response = JSONResponse({"ok": True})
        response.set_cookie(AUTH_COOKIE, make_auth_token(username), httponly=True, samesite="lax", max_age=7 * 24 * 3600)
        return response
    return JSONResponse({"error": "用户名或密码错误"}, status_code=401)


async def api_logout(request):
    response = JSONResponse({"ok": True})
    response.delete_cookie(AUTH_COOKIE)
    return response


async def index(request):
    return HTMLResponse(HTML)


async def runtime_inspector_page(request):
    return HTMLResponse(INSPECTOR_HTML)


async def delegated_inspector_page(request):
    return HTMLResponse(DELEGATED_INSPECTOR_HTML)


async def api_guide(request):
    return PlainTextResponse(GUIDE_PATH.read_text(encoding="utf-8"), media_type="text/plain; charset=utf-8")


def writing_case_payload() -> dict[str, Any]:
    return {
        "title": "写作 Agent 落地案例",
        "subtitle": "把 /home/data/rag/ragyuyan/rag_agent 的真实架构和未来演进路径放在一起看。",
        "current_name": "混合路由式受控写作 Agent（Hybrid OpCall Writing Agent）",
        "current_flow": [
            {"layer": "交互入口", "impl": "chat.py", "desc": "接收用户消息、选中文本、章节 ID、模板状态。"},
            {"layer": "入口路由", "impl": "R5 写作链路判断", "desc": "判断本轮是否进入写作 Agent。"},
            {"layer": "状态与记忆", "impl": "PG / Redis / Pending / TemplateSnapshot", "desc": "保存文档、确认任务、模板快照和会话状态。"},
            {"layer": "上下文包", "impl": "_r5_state", "desc": "当前还未独立成 Context Pack（上下文包）。"},
            {"layer": "意图规划", "impl": "WritingPlanner", "desc": "规则 + LLM 混合识别用户到底想改什么。"},
            {"layer": "操作调用", "impl": "OpCall", "desc": "把意图收束成受控 operation + params。"},
            {"layer": "追问/确认", "impl": "ClarificationGate", "desc": "目标不清或高风险操作先拦截。"},
            {"layer": "校验/执行", "impl": "Validator + Executor + handlers", "desc": "确定性校验后执行生成、改写、删除、导出。"},
            {"layer": "观察反馈", "impl": "SSEEmitter", "desc": "把 thinking/progress/artifact/clarification 推给前端。"},
        ],
        "good_points": [
            "已经从自由 Agent 循环（agent_loop）收敛到受控操作调用（OpCall）。",
            "低风险、低歧义走规则，复杂指代交给 LLM，速度和灵活性兼顾。",
            "追问门（ClarificationGate）和校验器（Validator）能阻止不确定操作直接落库。",
            "删除、全文修订、恢复版本、导出等高风险动作有确认/校验基础。",
            "SSE 事件让用户能看到执行进度，而不是只等最终答案。",
        ],
        "gaps": [
            {"name": "上下文包未独立", "risk": "文档变长后 prompt 容易变大、变乱，也难复盘本轮到底给了 LLM 什么。", "next": "拆 ContextBuilder.build(...)，只给 Planner 本轮需要看的证据。"},
            {"name": "意图框架（Intent Frame）缺失", "risk": "误路由时不知道 LLM 是怎么理解 action/target/scope/confidence 的。", "next": "让 Planner 输出 intent_type、target、scope、evidence、missing_info。"},
            {"name": "意图绑定（Intent Binding）隐式", "risk": "解析、路由、参数组装混在 _parse_intent_response()，后续 operation 多了会黑盒。", "next": "拆成 parse_intent_frame → bind_intent_to_opcall → validate_binding。"},
            {"name": "操作追踪（OpCall Trace）偏薄", "risk": "用户问为什么这样改、线上误判复盘时证据不足。", "next": "OpCall 携带 intent_frame、confidence、matched_rule、rejected_routes。"},
            {"name": "状态写回（State Writer）分散", "risk": "handler 各自写库、版本和缓存，长期可能不一致。", "next": "统一 StateWriter / VersionWriter / TraceWriter。"},
        ],
        "future_flow": [
            "交互入口",
            "入口路由",
            "状态与记忆",
            "上下文构建器（Context Builder）",
            "上下文包（Context Pack）",
            "意图规划器（Intent Planner）",
            "意图框架（Intent Frame）",
            "意图绑定（Intent Binding）",
            "操作调用（OpCall）",
            "追问/确认",
            "校验器/守卫",
            "执行器/工具适配器",
            "状态写回",
            "观察反馈",
        ],
        "evolution": [
            {"phase": "P0", "title": "继续体验测试", "desc": "先记录误路由 case：局部/全文、删除/改写、新增段落/新增章节等。"},
            {"phase": "P1", "title": "扩展 OpCall Trace", "desc": "先不大重构，只给 OpCall 增加 intent_frame、confidence、evidence、matched_rule。"},
            {"phase": "P2", "title": "补意图评测", "desc": "把失败 case 固化成 intent eval / binding eval，防止越改越退化。"},
            {"phase": "P3", "title": "拆意图绑定层", "desc": "当 operation 继续增加时，把隐式 if/else 路由改成显式绑定规则。"},
            {"phase": "P4", "title": "拆上下文构建", "desc": "文档变长或误抓重点时，再独立 ContextBuilder 和 ContextPack。"},
            {"phase": "P5", "title": "统一状态写回", "desc": "handler 多起来后，统一写库、版本、trace、事件。"},
        ],
        "conclusion": "当前架构已经够用，不建议为了理想架构推倒重来。更好的路线是：先上线验证 → 收集失败 case → 补评测 → 针对性演进。",
        "validation_summary": "第 16 项验证通过：真实写作 Agent 已证明 APD 的受控 OpCall Runtime 与 Harness 化路线成立。",
        "validation_points": [
            "真实项目已经落地 WritingPlanner → OpCall → ClarificationGate → Validator → Executor / handlers → SSE Observation。",
            "APD 能解释当前项目为什么变稳定：Planner 不直接执行，程序校验和确认负责安全边界。",
            "APD 也能指出下一步：补 OpCall Trace、Intent Frame、Intent Binding、Context Builder 和 intent/binding eval cases。",
            "不建议推倒重来，应以真实误路由 case 驱动增量重构。",
        ],
        "validation_markdown": WRITING_VALIDATION_PATH.read_text(encoding="utf-8"),
        "markdown": CASE_PATH.read_text(encoding="utf-8"),
    }


async def api_writing_case(request):
    if request.query_params.get("format") == "json":
        return JSONResponse(writing_case_payload())
    return PlainTextResponse(CASE_PATH.read_text(encoding="utf-8"), media_type="text/plain; charset=utf-8")


async def api_bid_validation(request):
    return PlainTextResponse(BID_VALIDATION_PATH.read_text(encoding="utf-8"), media_type="text/plain; charset=utf-8")


async def api_agentos_evaluation(request):
    return PlainTextResponse(AGENTOS_EVALUATION_PATH.read_text(encoding="utf-8"), media_type="text/plain; charset=utf-8")


async def api_capability_boundary(request):
    return PlainTextResponse(CAPABILITY_BOUNDARY_PATH.read_text(encoding="utf-8"), media_type="text/plain; charset=utf-8")


async def api_status(request):
    cfg = resolve_config({})
    return JSONResponse({
        "api_base": cfg["api_base"],
        "model": cfg["model"],
        "has_key": bool(cfg["api_key"]),
        "llm_timeout": os.getenv("APD_LLM_TIMEOUT", "120"),
        "server_timeout": os.getenv("APD_SERVER_TIMEOUT", "150"),
        "max_tokens": os.getenv("APD_MAX_TOKENS", "2000"),
        "response_format": os.getenv("APD_RESPONSE_FORMAT", "0"),
    })


def attach_exports_preview(session: dict[str, Any]) -> dict[str, Any]:
    payload = copy.deepcopy(session)
    exports = build_exports(payload.get("protocol") or {})
    payload["exports_preview"] = {key: value[:2000] for key, value in exports.items()}
    return payload


async def api_reset(request):
    return JSONResponse(attach_exports_preview(new_session()))


async def api_sessions(request):
    include_empty = request.query_params.get("include_empty") == "1"
    return JSONResponse({"sessions": list_saved_sessions(include_empty=include_empty)})


async def api_load_session(request):
    session = get_session(request.path_params.get("session_id"))
    return JSONResponse(attach_exports_preview(session))


def _operation_map(protocol: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {op.get("name"): op for op in protocol.get("operations", []) or [] if isinstance(op, dict) and op.get("name")}


def _object_map(protocol: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {obj.get("name"): obj for obj in protocol.get("objects", []) or [] if isinstance(obj, dict) and obj.get("name")}


def _summarize_changes(before: dict[str, Any] | None, after: dict[str, Any] | None) -> dict[str, Any]:
    before = before or {}
    after = after or {}
    b_ops, a_ops = _operation_map(before), _operation_map(after)
    b_objs, a_objs = _object_map(before), _object_map(after)
    changed_operations = [name for name in sorted(set(a_ops) & set(b_ops)) if a_ops[name] != b_ops[name]]
    changed_objects = [name for name in sorted(set(a_objs) & set(b_objs)) if a_objs[name] != b_objs[name]]
    before_confirmation = before.get("confirmation_rules", []) or []
    after_confirmation = after.get("confirmation_rules", []) or []
    before_clarification = before.get("clarification_rules", []) or []
    after_clarification = after.get("clarification_rules", []) or []
    return {
        "added_operations": sorted(set(a_ops) - set(b_ops)),
        "removed_operations": sorted(set(b_ops) - set(a_ops)),
        "changed_operations": changed_operations,
        "added_objects": sorted(set(a_objs) - set(b_objs)),
        "removed_objects": sorted(set(b_objs) - set(a_objs)),
        "changed_objects": changed_objects,
        "confirmation_rules_added": max(0, len(after_confirmation) - len(before_confirmation)),
        "clarification_rules_added": max(0, len(after_clarification) - len(before_clarification)),
        "confirmation_rules": {"added": [rule for rule in after_confirmation if rule not in before_confirmation], "removed": [rule for rule in before_confirmation if rule not in after_confirmation], "modified": [], "deduplicated": []},
        "clarification_rules": {"added": [rule for rule in after_clarification if rule not in before_clarification], "removed": [rule for rule in before_clarification if rule not in after_clarification], "modified": [], "deduplicated": []},
    }


def _append_turn_snapshot(session: dict[str, Any], user_message: str, result: dict[str, Any], before: dict[str, Any], after: dict[str, Any]) -> None:
    session.setdefault("turns", [])
    session.setdefault("snapshots", [])
    turn = {
        "turn_index": len(session["turns"]) + 1,
        "timestamp": now_iso(),
        "stage_before": (session.get("last_response") or {}).get("stage") or "discover",
        "stage_after": result.get("stage") or "discover",
        "user": user_message,
        "assistant": result.get("assistant_message") or "",
        "next_questions": result.get("next_questions") or [],
        "quality_notes": result.get("quality_notes") or [],
        "changes": _summarize_changes(before, after),
        "protocol_before": before,
        "protocol_after": after,
    }
    session["turns"].append(turn)
    ensure_snapshot(session, after, turn["changes"])



async def api_chat(request):
    payload = await request.json()
    session = get_session(payload.get("session_id"))
    message = str(payload.get("message") or "").strip()
    if not message:
        return JSONResponse({"error": "message is required"}, status_code=400)
    settings = payload.get("settings") or {}
    safe_cfg = resolve_config(settings)
    print("[apd] chat config", {
        "api_base": safe_cfg.get("api_base"),
        "model": safe_cfg.get("model"),
        "has_key": bool(safe_cfg.get("api_key")),
        "llm_timeout": settings.get("timeout") or os.getenv("APD_LLM_TIMEOUT", "120"),
        "server_timeout": settings.get("server_timeout") or os.getenv("APD_SERVER_TIMEOUT", "150"),
        "max_tokens": settings.get("max_tokens") or os.getenv("APD_MAX_TOKENS", "2000"),
        "response_format": settings.get("response_format") or os.getenv("APD_RESPONSE_FORMAT", "0"),
    }, flush=True)

    before_protocol = copy.deepcopy(session.get("protocol") or EMPTY_PROTOCOL)
    session["history"].append({"role": "user", "content": message})
    try:
        result = await asyncio.wait_for(
            design_step(
                message,
                protocol=session.get("protocol"),
                history=session.get("history"),
                settings=settings,
            ),
            timeout=float(settings.get("server_timeout") or os.getenv("APD_SERVER_TIMEOUT", "150")),
        )
    except asyncio.TimeoutError:
        result = fallback_step(message, session.get("protocol"), "server-side design timeout")
    session["protocol"] = merge_protocol(session.get("protocol"), result.get("protocol"))
    _append_turn_snapshot(session, message, result, before_protocol, copy.deepcopy(session["protocol"]))
    session["last_response"] = result
    session["history"].append({"role": "assistant", "content": result.get("assistant_message", "")})
    normalize_open_questions(session)
    save_session(session)

    exports = build_exports(session["protocol"])
    return JSONResponse({
        "session_id": session["session_id"],
        "response": result,
        "protocol": session["protocol"],
        "exports_preview": {key: value[:2000] for key, value in exports.items()},
        "llm_config": {
            "api_base": safe_cfg.get("api_base"),
            "model": safe_cfg.get("model"),
            "has_key": bool(safe_cfg.get("api_key")),
            "llm_timeout": settings.get("timeout") or os.getenv("APD_LLM_TIMEOUT", "120"),
            "server_timeout": settings.get("server_timeout") or os.getenv("APD_SERVER_TIMEOUT", "150"),
            "max_tokens": settings.get("max_tokens") or os.getenv("APD_MAX_TOKENS", "2000"),
            "response_format": settings.get("response_format") or os.getenv("APD_RESPONSE_FORMAT", "0"),
        },
    })


async def api_delete_session(request):
    session_id = request.path_params.get("session_id")
    if session_id in SESSIONS:
        SESSIONS.pop(session_id, None)
    path = session_path(session_id)
    if path.exists():
        path.unlink()
    return JSONResponse({"ok": True, "deleted": session_id})


async def api_export(request):
    session = get_session(request.path_params.get("session_id"))
    name = request.path_params.get("name")
    exports = build_exports(session.get("protocol") or {})
    if name == "all":
        return JSONResponse(exports)
    if name not in exports:
        return JSONResponse({"error": "unknown export", "available": list(exports)}, status_code=404)
    media_type = "application/json" if name.endswith(".json") else "text/plain"
    return PlainTextResponse(exports[name], media_type=media_type)


async def api_scaffold_zip(request):
    session = get_session(request.path_params.get("session_id"))
    template = str(request.query_params.get("template") or "fastapi-vue")
    requested_name = str(request.query_params.get("name") or "").strip()
    protocol = session.get("protocol") or {}
    project_name = safe_project_name(requested_name or protocol.get("project_name") or session.get("title") or "generated-agent")
    try:
        with tempfile.TemporaryDirectory(prefix="apd-scaffold-") as temp_dir:
            root_dir = Path(temp_dir)
            written = generate_project_scaffold(session, root_dir, template=template, force=True, project_name=project_name)
            project_root = root_dir / project_name
            if not project_root.exists() and written:
                for candidate in written[0].parents:
                    if (candidate / "README.md").exists() and (candidate / "backend").exists():
                        project_root = candidate
                        break
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for file_path in sorted(project_root.rglob("*")):
                    if file_path.is_file():
                        archive.write(file_path, file_path.relative_to(project_root.parent))
            buffer.seek(0)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    headers = {"Content-Disposition": 'attachment; filename="apd-scaffold.zip"'}
    return Response(buffer.getvalue(), media_type="application/zip", headers=headers)


async def api_delegated_agent_zip(request):
    session = get_session(request.path_params.get("session_id"))
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    protocol = session.get("protocol") or {}
    project_name = str(payload.get("project_name") or protocol.get("project_name") or session.get("title") or "delegated-agent")
    agent_name = str(payload.get("agent_name") or protocol.get("project_name") or session.get("title") or project_name)
    agent_goal = str(payload.get("agent_goal") or protocol.get("domain_summary") or "接收用户任务，委托内置 open_claude 执行，并返回过程与产物。")
    default_task = str(payload.get("default_task") or "请根据用户输入完成任务，并把最终结果写入 artifacts/report.md 和 artifacts/result.json。")
    bundle_open_claude = bool(payload.get("bundle_open_claude", True))
    open_claude_source = Path(str(payload.get("open_claude_source") or DEFAULT_OPEN_CLAUDE_SOURCE))
    try:
        data, manifest = generate_delegated_agent_project(
            protocol=protocol,
            project_name=project_name,
            agent_name=agent_name,
            agent_goal=agent_goal,
            default_task=default_task,
            open_claude_source=open_claude_source,
            bundle_open_claude=bundle_open_claude,
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    filename = f"{safe_project_name(project_name)}-delegated-agent.zip"
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "X-APD-Delegated-Manifest": json.dumps({"project_name": manifest.get("project_name"), "bundled": manifest.get("bundle_open_claude")}, ensure_ascii=False),
    }
    return Response(data, media_type="application/zip", headers=headers)


async def api_delegated_playground_start(request):
    payload = await request.json()
    session = get_session(payload.get("session_id") or payload.get("session"))
    protocol = session.get("protocol") or {}
    try:
        result = DELEGATED_PLAYGROUND.start(
            session,
            project_name=str(payload.get("project_name") or ""),
            agent_name=str(payload.get("agent_name") or protocol.get("project_name") or session.get("title") or ""),
            agent_goal=str(payload.get("agent_goal") or protocol.get("domain_summary") or ""),
            default_task=str(payload.get("default_task") or ""),
            open_claude_source=str(payload.get("open_claude_source") or DEFAULT_OPEN_CLAUDE_SOURCE),
            fake_runner=bool(payload.get("fake_runner", True)),
        )
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)
    audit("delegated_playground_start", "delegated_playground", result.get("delegated_id") or "", {"session_id": session.get("session_id"), "port": result.get("port")})
    return JSONResponse(result)


async def api_delegated_playground_list(request):
    return JSONResponse({"items": DELEGATED_PLAYGROUND.list_items()})


async def api_delegated_playground_stop(request):
    delegated_id = request.path_params.get("delegated_id") or ""
    try:
        result = DELEGATED_PLAYGROUND.stop(delegated_id)
    except KeyError:
        return JSONResponse({"error": "delegated playground not found"}, status_code=404)
    audit("delegated_playground_stop", "delegated_playground", delegated_id, {"status": result.get("status")})
    return JSONResponse(result)


async def api_delegated_playground_restart(request):
    delegated_id = request.path_params.get("delegated_id") or ""
    try:
        result = DELEGATED_PLAYGROUND.restart(delegated_id)
    except KeyError:
        return JSONResponse({"error": "delegated playground not found"}, status_code=404)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)
    audit("delegated_playground_restart", "delegated_playground", delegated_id, {"status": result.get("status"), "pid": result.get("pid")})
    return JSONResponse(result)


async def api_delegated_playground_chat(request):
    delegated_id = request.path_params.get("delegated_id") or ""
    payload = await request.json()
    message = str(payload.get("message") or "").strip()
    if not message:
        return JSONResponse({"error": "message required"}, status_code=400)
    try:
        created = DELEGATED_PLAYGROUND.create_job(delegated_id, message)
    except KeyError:
        return JSONResponse({"error": "delegated playground not found"}, status_code=404)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)
    return JSONResponse(created)


async def api_delegated_playground_job(request):
    delegated_id = request.path_params.get("delegated_id") or ""
    job_id = request.path_params.get("job_id") or ""
    try:
        snapshot = DELEGATED_PLAYGROUND.get_job_snapshot(delegated_id, job_id)
        job = snapshot.get("job") or {}
        event_items = snapshot.get("events") or []
        artifacts = snapshot.get("artifacts") or []
        report = str(snapshot.get("report") or "")
        logs = snapshot.get("logs") or {"stdout": "", "stderr": ""}
        thinking = build_delegated_thinking(job, event_items, logs)
        agent_reply = build_delegated_agent_reply(event_items, logs, report, job)
        whitebox = build_delegated_whitebox(job, event_items, artifacts, report, logs, thinking, agent_reply)
        diagnosis = build_delegated_diagnosis(whitebox)
        repair_task = build_delegated_repair_task(whitebox, diagnosis)
        return JSONResponse({"job": job, "events": event_items, "artifacts": artifacts, "report": report, "logs": logs, "thinking": thinking, "agent_reply": agent_reply, "whitebox": whitebox, "diagnosis": diagnosis, "repair_task": repair_task})
    except KeyError:
        return JSONResponse({"error": "delegated playground not found"}, status_code=404)
    except FileNotFoundError:
        return JSONResponse({"error": "job not found"}, status_code=404)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


async def api_delegated_playground_llm_diagnose(request):
    delegated_id = request.path_params.get("delegated_id") or ""
    payload = await request.json()
    job_id = str(payload.get("job_id") or "")
    settings = payload.get("settings") if isinstance(payload.get("settings"), dict) else {}
    if not job_id:
        return JSONResponse({"ok": False, "diagnosis": "缺少 job_id，无法诊断。", "error": "job_id required"}, status_code=200)
    try:
        snapshot = DELEGATED_PLAYGROUND.get_job_snapshot(delegated_id, job_id)
        job = snapshot.get("job") or {}
        events = snapshot.get("events") or []
        artifacts = snapshot.get("artifacts") or []
        report = str(snapshot.get("report") or "")
        logs = snapshot.get("logs") or {"stdout": "", "stderr": ""}
        thinking = build_delegated_thinking(job, events, logs)
        agent_reply = build_delegated_agent_reply(events, logs, report, job)
        whitebox = build_delegated_whitebox(job, events, artifacts, report, logs, thinking, agent_reply)
        diagnosis = build_delegated_diagnosis(whitebox)
        compact = {
            "delegated_id": delegated_id,
            "job_id": job_id,
            "diagnosis": diagnosis,
            "whitebox": whitebox,
        }
        messages = [
            {"role": "system", "content": "你是资深 Agent 架构诊断专家。请用中文输出。你诊断的是 Delegated Agent：APD 把用户任务打包成 Task Pack，委托 open_claude 执行，再读取 Job、Events、stdout/stderr、Artifacts 和 report.md。请按白盒层级解释问题，指出根因、建议修改文件、复测话术和可直接交给工程 AI 的修复任务。不要泄露密钥。"},
            {"role": "user", "content": json.dumps({"delegated_whitebox_turn": compact, "output_requirements": ["先给一句话结论", "按 Task Pack / Runner / stdout / 产物 / Agent回复 / 观测性逐层诊断", "说明为什么当前回复是这样", "指出最可能根因", "列出建议修改文件", "给出下一轮测试话术", "给出发给 open_claude/Codex 的修复建议"]}, ensure_ascii=False)[:24000]},
        ]
        result = await chat_json_result(messages, settings=settings)
        return JSONResponse({"ok": True, "diagnosis": result.content, "model": result.get("model"), "usage": result.get("usage"), "finish_reason": result.get("finish_reason")})
    except KeyError:
        return JSONResponse({"ok": False, "diagnosis": "Delegated Agent 实例不存在，请重新生成并启动。", "error": "delegated playground not found"}, status_code=200)
    except FileNotFoundError:
        return JSONResponse({"ok": False, "diagnosis": "Job 文件不存在，请重新发送一轮任务。", "error": "job not found"}, status_code=200)
    except LLMError as exc:
        return JSONResponse({"ok": False, "diagnosis": "当前没有成功调用 LLM 诊断。请在左侧“LLM 诊断配置”填写 api_base、api_key、model 后重试。\n\n离线诊断仍可参考右侧白盒信息。", "error": str(exc)}, status_code=200)
    except Exception as exc:
        return JSONResponse({"ok": False, "diagnosis": "LLM 诊断异常，已保留离线白盒信息。", "error": str(exc)}, status_code=200)


def build_delegated_thinking(job: dict[str, Any], events: list[dict[str, Any]], logs: dict[str, str]) -> dict[str, Any]:
    runner_events = [event for event in events if str(event.get("type") or "").startswith("runner_")]
    output_events = [event for event in runner_events if event.get("type") in {"runner_output", "runner_screen"}]
    latest_text = ""
    latest_type = ""
    if output_events:
        latest = output_events[-1]
        latest_type = str(latest.get("type") or "")
        latest_text = str((latest.get("data") or {}).get("text") or latest.get("message") or "").strip()
    if not latest_text:
        stdout = str((logs or {}).get("stdout") or "").strip()
        if stdout:
            latest_type = "stdout_tail"
            latest_text = "\n".join(stdout.splitlines()[-18:])
    if not latest_text:
        latest = events[-1] if events else {}
        latest_type = str(latest.get("type") or "")
        latest_text = str(latest.get("message") or latest_type or "等待 open_claude 输出").strip()
    process = next((event for event in runner_events if event.get("type") == "runner_process_started"), {})
    heartbeat = next((event for event in reversed(runner_events) if event.get("type") == "runner_heartbeat"), {})
    return {
        "title": "open_claude 深度思考 / CLI 屏幕",
        "status": job.get("status") or "",
        "summary": job.get("summary") or "",
        "text": latest_text,
        "source": latest_type,
        "pid": (process.get("data") or {}).get("pid"),
        "silence_seconds": (heartbeat.get("data") or {}).get("silence_seconds"),
        "runner_event_count": len(runner_events),
        "explain": "这里展示真实 open_claude 子进程在终端里的可见输出。若只有心跳没有文本，通常是 CLI 等待模型、等待首次确认，或当前阶段没有向终端输出。",
    }


def build_delegated_agent_reply(events: list[dict[str, Any]], logs: dict[str, str], report: str, job: dict[str, Any]) -> dict[str, Any]:
    if report.strip():
        return {"text": report.strip(), "source": "report.md", "complete": True}
    result = job.get("result") if isinstance(job, dict) else None
    if isinstance(result, dict) and str(result.get("summary") or "").strip():
        return {"text": str(result.get("summary") or "").strip(), "source": "result.summary", "complete": True}
    for event in reversed(events):
        if event.get("type") in {"runner_reply", "runner_reply_delta"}:
            text = str((event.get("data") or {}).get("text") or event.get("message") or "").strip()
            if text:
                return {"text": text, "source": str(event.get("type")), "complete": event.get("type") == "runner_reply"}
        if event.get("type") == "runner_screen":
            text = str((event.get("data") or {}).get("reply_text") or "").strip()
            if text:
                return {"text": text, "source": "runner_screen.reply_text", "complete": False}
    text = extract_reply_from_stdout(str((logs or {}).get("stdout") or ""))
    if text.strip():
        return {"text": text.strip(), "source": "stdout.stream-json", "complete": False}
    return {"text": "", "source": "", "complete": False}


def build_delegated_whitebox(job: dict[str, Any], events: list[dict[str, Any]], artifacts: list[dict[str, Any]], report: str, logs: dict[str, str], thinking: dict[str, Any], agent_reply: dict[str, Any]) -> dict[str, Any]:
    runner_events = [event for event in events if str(event.get("type") or "").startswith("runner_")]
    start_event = next((event for event in runner_events if event.get("type") == "runner_start"), {})
    done_event = next((event for event in reversed(runner_events) if event.get("type") in {"runner_exit", "runner_failed", "runner_timeout"}), {})
    stdout = str((logs or {}).get("stdout") or "")
    stderr = str((logs or {}).get("stderr") or "")
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    return {
        "job_status": {
            "job_id": job.get("job_id") or "",
            "status": job.get("status") or "",
            "summary": job.get("summary") or result.get("summary") or "",
            "created_at": job.get("created_at") or "",
            "updated_at": job.get("updated_at") or "",
        },
        "task_pack": {
            "path": job.get("task_pack_path") or "",
            "message": job.get("message") or "",
            "explain": "Task Pack 是 APD 发给 delegated agent 的任务输入，相当于本轮 open_claude 要执行的工程任务说明。",
        },
        "runner": {
            "command": (start_event.get("data") or {}).get("command") or "",
            "cwd": (start_event.get("data") or {}).get("cwd") or "",
            "timeout_seconds": (start_event.get("data") or {}).get("timeout_seconds"),
            "pid": thinking.get("pid"),
            "done_event": done_event,
            "runner_event_count": len(runner_events),
            "explain": "Runner 层负责启动 open_claude。这里能判断是否启动成功、卡在哪个进程、是否超时。",
        },
        "llm_and_cli_output": {
            "thinking": thinking,
            "stdout_tail": "\n".join(stdout.splitlines()[-80:]),
            "stderr_tail": "\n".join(stderr.splitlines()[-80:]),
            "stdout_has_stream_json": any(line.startswith("{") for line in stdout.splitlines()),
            "explain": "这一层不是 APD 自己编的回复，而是 open_claude 的 stdout/stderr 和流式输出证据。",
        },
        "agent_reply": {
            **agent_reply,
            "report_size": len(report.encode("utf-8")),
            "explain": "最终用户看到的 Agent 回复优先来自 report.md，其次来自 result.summary 或 stdout 中解析出的 assistant/result。",
        },
        "artifacts": {
            "count": len(artifacts),
            "items": artifacts,
            "has_report_md": any(item.get("name") == "report.md" for item in artifacts),
            "has_result_json": any(item.get("name") == "result.json" for item in artifacts),
            "explain": "产物层用于判断 open_claude 是否真的按约定写入 report.md、result.json 等交付物。",
        },
        "events": {
            "count": len(events),
            "tail": events[-40:],
            "types": sorted({str(event.get("type") or "") for event in events}),
            "explain": "Events 是过程追踪。它能证明 Job 从 queued、running 到 completed/failed 的状态变化。",
        },
    }


def build_delegated_diagnosis(whitebox: dict[str, Any]) -> dict[str, Any]:
    levels: list[dict[str, Any]] = []
    files: set[str] = {"backend/app/main.py", "backend/app/runner_open_claude.py"}
    status = ((whitebox.get("job_status") or {}).get("status") or "").lower()
    runner = whitebox.get("runner") or {}
    cli = whitebox.get("llm_and_cli_output") or {}
    reply = whitebox.get("agent_reply") or {}
    artifacts = whitebox.get("artifacts") or {}
    if status in {"queued", ""}:
        levels.append({"label": "Job 调度", "key": "job", "file": "backend/app/main.py", "reason": "Job 还停留在 queued 或没有状态，说明执行循环可能没有启动。", "action": "检查 /api/jobs 创建后是否启动后台任务，以及事件是否写入。", "confidence": 0.9})
    if status in {"failed", "timeout"}:
        levels.append({"label": "Runner 执行", "key": "runner", "file": "backend/app/runner_open_claude.py", "reason": f"Job 已结束但状态是 {status}，优先看 runner 命令、stdout/stderr 和超时设置。", "action": "检查 open_claude 命令、模型配置、网络、超时和工具权限。", "confidence": 0.88})
    if status == "running" and not str(cli.get("stdout_tail") or "").strip():
        levels.append({"label": "CLI 输出", "key": "stdout", "file": "backend/app/runner_open_claude.py", "reason": "Job 正在运行但 stdout 暂无有效输出，可能在等待模型、首次确认或命令参数不适合非交互模式。", "action": "确认使用 print/stream-json 模式，并检查 stderr、模型网关和超时。", "confidence": 0.82})
    if not str((reply.get("text") or "")).strip():
        levels.append({"label": "Agent 回复", "key": "reply", "file": "backend/app/main.py", "reason": "没有解析到最终 Agent 回复，用户侧只能看到状态或空结果。", "action": "检查 report.md/result.json 写入，以及 stdout 中 assistant/result 的解析逻辑。", "confidence": 0.84})
    if status == "completed" and not artifacts.get("has_report_md"):
        levels.append({"label": "产物协议", "key": "artifact", "file": "backend/app/task_pack.py", "reason": "Job 完成但没有 report.md，说明 Task Pack 对交付物约束不够强，或 runner 没按协议写文件。", "action": "强化 Task Pack 的交付要求，并在完成前校验 report.md/result.json。", "confidence": 0.78})
        files.add("backend/app/task_pack.py")
    if not levels:
        levels.append({"label": "整体链路", "key": "ok", "file": "backend/app/main.py", "reason": "Job、Runner、回复和产物链路都有可见证据。若业务效果仍不对，优先看 Task Pack 是否准确表达用户需求。", "action": "用同类话术继续回归，并保存失败样本。", "confidence": 0.72})
    primary = levels[0]
    files.add(str(primary.get("file") or "backend/app/main.py"))
    return {
        "summary": f"本轮优先看：{primary.get('label')}，建议检查 {primary.get('file')}",
        "primary_level": primary,
        "levels": levels,
        "suggested_files": sorted(files),
        "next_test_messages": ["用同一句话重试，观察 Job 是否从 queued 进入 running/completed。", "要求 Agent 明确写入 artifacts/report.md 和 artifacts/result.json。"],
    }


def build_delegated_repair_task(whitebox: dict[str, Any], diagnosis: dict[str, Any]) -> str:
    job = whitebox.get("job_status") or {}
    task = whitebox.get("task_pack") or {}
    reply = whitebox.get("agent_reply") or {}
    return f"""请修复 Delegated Agent 调试中暴露的问题。

【用户任务】
{task.get('message') or ''}

【当前 Job】
- job_id: {job.get('job_id') or ''}
- status: {job.get('status') or ''}
- summary: {job.get('summary') or ''}

【Agent 当前回复】
{reply.get('text') or '暂无有效回复'}

【APD 诊断】
{diagnosis.get('summary') or ''}

【建议修改文件】
{', '.join(diagnosis.get('suggested_files') or [])}

【请你做】
1. 先阅读上述建议文件和当前 Job 的 trace/stdout/stderr。
2. 只做与本轮问题相关的最小修改。
3. 如果是 queued/running 卡住，优先修 Job 调度或 runner。
4. 如果回复不对，优先修 Task Pack、产物校验或 stdout/report 解析。
5. 修改后说明如何复测。"""


def extract_reply_from_stdout(stdout: str) -> str:
    parts: list[str] = []
    final_text = ""
    for line in stdout.splitlines():
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        event_type = payload.get("type")
        if event_type == "stream_event":
            event = payload.get("event") or {}
            if event.get("type") == "content_block_delta":
                delta = event.get("delta") or {}
                if delta.get("type") == "text_delta" and delta.get("text"):
                    parts.append(str(delta.get("text") or ""))
        elif event_type == "assistant":
            message = payload.get("message") or {}
            if isinstance(message, dict):
                texts = [str(item.get("text") or "") for item in message.get("content") or [] if isinstance(item, dict) and item.get("type") == "text"]
                if texts:
                    final_text = "\n".join(texts).strip()
        elif event_type == "result" and payload.get("result"):
            final_text = str(payload.get("result") or "").strip()
    return final_text or "".join(parts)


async def api_delegated_playground_config(request):
    delegated_id = request.path_params.get("delegated_id") or ""
    try:
        return JSONResponse(DELEGATED_PLAYGROUND.config(delegated_id))
    except KeyError:
        return JSONResponse({"error": "delegated playground not found"}, status_code=404)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


def build_demo_maturity_report(protocol: dict[str, Any], agent: dict[str, Any], workflow: dict[str, Any], store: dict[str, Any], tools: dict[str, Any]) -> dict[str, Any]:
    operations = [op for op in protocol.get("operations") or [] if isinstance(op, dict)]
    workflow_nodes = [node for node in (protocol.get("workflow") or {}).get("nodes") or [] if isinstance(node, dict)]
    tool_registry = (protocol.get("tool_registry") or {}).get("tools") or []
    validators = protocol.get("validators") or []
    permission_policy = protocol.get("permission_policy") or {}
    has_llm_runtime = bool(protocol.get("llm_runtime") or protocol.get("llm_planner") or protocol.get("real_llm_binding"))
    has_real_executor = bool(protocol.get("executor_bindings") or protocol.get("real_executor") or protocol.get("implementation_bindings"))
    has_real_artifact = bool(protocol.get("artifact_storage") or protocol.get("document_editor") or protocol.get("export_pipeline"))
    has_real_knowledge = bool(protocol.get("knowledge_policy") or protocol.get("rag_policy") or protocol.get("graph_policy"))
    agent_trace = agent.get("trace") or []
    artifact_versions = agent.get("artifact_versions") or []
    tool_results = agent.get("tool_results") or []
    node_states = workflow.get("node_states") or {}
    store_trace = store.get("trace_events") or []
    checks = [
        {"id": "protocol", "name": "协议里已有可执行操作", "done": bool(operations), "why": "没有 operation 就无法从用户话术绑定到受控动作。"},
        {"id": "scaffold", "name": "工程骨架能启动", "done": True, "why": "Demo FastAPI 已经启动并返回健康检查。"},
        {"id": "planner", "name": "能把话术绑定到操作", "done": bool((agent.get("op_call") or {}).get("operation")), "why": "当前是规则版 Demo Planner，不是真实 LLM Planner。"},
        {"id": "validator", "name": "有校验链路", "done": bool(agent.get("validator_results") is not None), "why": "校验器用于防止信息不足或非法参数进入执行。"},
        {"id": "permission", "name": "有权限判断", "done": bool(agent.get("permission_check")), "why": "高风险动作需要确认或阻断。"},
        {"id": "tool", "name": "有工具适配器位置", "done": bool(tool_registry or tool_results), "why": "当前多数仍是 dry-run，还没有真实调用业务工具。"},
        {"id": "artifact", "name": "有产物版本记录", "done": bool(artifact_versions), "why": "产物版本用于文档、报告、导出文件的追踪和回滚。"},
        {"id": "workflow", "name": "能跑 Workflow 节点", "done": bool(workflow_nodes or node_states), "why": "复杂 Agent 需要节点级流程，而不是单轮聊天。"},
        {"id": "store", "name": "有状态/记忆/Trace 存储", "done": bool(store_trace), "why": "当前是内存 Store，真实项目应换成数据库或文件/对象存储。"},
        {"id": "real_llm", "name": "接入真实 LLM 业务生成", "done": has_llm_runtime, "why": "目前生成 Demo 默认不调用 LLM，所以业务内容没有真实智能效果。"},
        {"id": "real_executor", "name": "接入真实业务执行器", "done": has_real_executor, "why": "目前 executor 多数是 mock，只证明链路通了。"},
        {"id": "real_artifact", "name": "接入真实文档/导出产物", "done": has_real_artifact, "why": "没有真实文档、编辑器或导出，就很难感受到业务价值。"},
        {"id": "knowledge", "name": "接入真实知识库/RAG/图谱", "done": has_real_knowledge, "why": "需要素材、证据或企业知识时，必须接真实知识来源。"},
    ]
    done_count = sum(1 for item in checks if item["done"])
    score = round(done_count / len(checks) * 100)
    if score < 45:
        stage = "可运行骨架"
        plain = "现在主要证明工程能跑，还没有明显业务效果。"
    elif score < 70:
        stage = "半成品 Agent"
        plain = "已经有流程和追踪，但业务能力还主要停留在 mock。"
    else:
        stage = "接近可体验 Agent"
        plain = "已经接近真实体验，但仍需要继续验证效果和稳定性。"
    next_tasks = []
    if not has_llm_runtime:
        next_tasks.append({"priority": "P0", "title": "接真实 LLM Planner / Writer", "files": ["backend/app/planner.py"], "goal": "把规则匹配替换成 LLM 意图理解和内容生成，让 Demo 有真实智能效果。"})
    if not has_real_executor:
        next_tasks.append({"priority": "P0", "title": "实现一个真实业务 Executor", "files": ["backend/app/executor.py"], "goal": "至少让一个 operation 不再 mock，而是真正生成业务结果。"})
    if tool_registry and not any(item.get("mode") != "dry_run" for item in tool_results):
        next_tasks.append({"priority": "P1", "title": "把 Tool Adapter 从 dry-run 改成真实调用", "files": ["backend/app/tools.py"], "goal": "接入文件解析、RAG、导出、数据库或业务 API。"})
    if not has_real_artifact:
        next_tasks.append({"priority": "P1", "title": "接真实产物文件", "files": ["backend/app/store.py", "backend/app/executor.py"], "goal": "生成真实文档、报告或 JSON 文件，而不是只返回内存里的 artifact_versions。"})
    if workflow_nodes and len(node_states) < len(workflow_nodes):
        next_tasks.append({"priority": "P1", "title": "补 Workflow 节点执行", "files": ["backend/app/workflow.py"], "goal": "让每个 workflow node 都绑定到真实 planner、tool、validator 或 human review。"})
    if not next_tasks:
        next_tasks.append({"priority": "P1", "title": "沉淀评测用例并做真实回归", "files": ["backend/tests/test_protocol_shape.py", "docs/eval_policy.md"], "goal": "用真实失败样本验证 Agent 是否稳定。"})
    return {
        "score": score,
        "stage": stage,
        "plain_summary": plain,
        "done_count": done_count,
        "total_count": len(checks),
        "checks": checks,
        "completed": [item for item in checks if item["done"]],
        "missing": [item for item in checks if not item["done"]],
        "next_tasks": next_tasks[:5],
        "recommended_next": next_tasks[0],
    }


def build_demo_beginner_explanation(agent: dict[str, Any], workflow: dict[str, Any], store: dict[str, Any], tools: dict[str, Any], maturity: dict[str, Any] | None = None) -> dict[str, Any]:
    tool_count = len(agent.get("tool_results") or [])
    artifact_count = len(agent.get("artifact_versions") or [])
    trace_count = len(agent.get("trace") or [])
    node_count = len(workflow.get("node_states") or {})
    memory_count = len(store.get("memory") or [])
    stored_artifact_count = len(store.get("artifacts") or [])
    stage = (maturity or {}).get("stage") or "可运行骨架"
    score = (maturity or {}).get("score") or 0
    return {
        "一句话结论": f"当前 Demo 成熟度约 {score}%，阶段是：{stage}。它能证明工程链路跑通，但是否有真实业务效果，要看 LLM、Executor、Tool 和文档产物是否已经接入。",
        "你刚才点按钮发生了什么": [
            "APD 先根据当前会话生成一个临时 FastAPI Agent 项目。",
            "然后 APD 在服务器内部启动这个 Demo 项目。",
            "接着自动调用单轮 Agent 接口，验证它能不能处理一句用户话术。",
            "再调用 Workflow 接口，验证它能不能按节点流程跑。",
            "最后读取 Store 和 Tools，确认状态、产物、Trace 和工具模板是否存在。",
        ],
        "怎么看结果": [
            f"工具结果 tool_results：{tool_count} 条，表示 Demo 是否经过工具适配器。",
            f"产物版本 artifact_versions：{artifact_count} 条，表示执行后有没有生成可追踪产物。",
            f"过程日志 trace：{trace_count} 条，表示每一步是否可复盘。",
            f"Workflow 节点 node_states：{node_count} 个，表示流程是否跑到节点级。",
            f"Store 里记忆 {memory_count} 条、产物 {stored_artifact_count} 条，表示运行结果有没有落到 Demo 内存。",
        ],
        "如果你看到这些就代表成功": [
            "agent_run.next_action 有值",
            "agent_run.trace 不是空",
            "agent_run.artifact_versions 不是空",
            "workflow_run.node_states 不是空",
            "store_snapshot.trace_events 不是空",
        ],
        "下一步开发应该看哪里": [
            "想有真实智能效果：先接 backend/app/planner.py 的 LLM Planner / Writer。",
            "想有真实业务结果：再改 backend/app/executor.py。",
            "想接业务工具：改 backend/app/tools.py。",
            "想保存真实文档和状态：改 backend/app/store.py。",
            "想跑复杂流程：改 backend/app/workflow.py。",
        ],
        "当前仍然不是生产系统": "它是临时 Demo，用于验证 APD 生成的 Agent 工程骨架能跑通；真实业务逻辑还需要继续实现。",
    }


async def api_demo_playground_start(request):
    payload = await request.json()
    session = get_session(payload.get("session_id") or payload.get("session"))
    try:
        result = DEMO_PLAYGROUND.start_demo(
            session,
            project_name=str(payload.get("project_name") or ""),
            template=str(payload.get("template") or "fastapi-vue"),
        )
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)
    audit("demo_playground_start", "demo_playground", result.get("demo_id") or "", {"session_id": session.get("session_id"), "port": result.get("port")})
    return JSONResponse(result)


async def api_demo_playground_one_click(request):
    payload = await request.json()
    session = get_session(payload.get("session_id") or payload.get("session"))
    message = str(payload.get("message") or "测试一句用户话术")
    context = payload.get("context") if isinstance(payload.get("context"), dict) else {"current_state": "drafting"}
    project_name = str(payload.get("project_name") or "")
    stop_after = bool(payload.get("stop_after", False))
    try:
        demo = DEMO_PLAYGROUND.start_demo(session, project_name=project_name, template=str(payload.get("template") or "fastapi-vue"))
        demo_id = demo.get("demo_id") or ""
        agent = DEMO_PLAYGROUND.run_agent(demo_id, message, context)
        workflow = DEMO_PLAYGROUND.run_workflow(demo_id, message, context, 3)
        store = DEMO_PLAYGROUND.get_store_snapshot(demo_id)
        tools = DEMO_PLAYGROUND.get_tools(demo_id)
        maturity = build_demo_maturity_report(session.get("protocol") or {}, agent, workflow, store, tools)
        explanation = build_demo_beginner_explanation(agent, workflow, store, tools, maturity)
        result = {
            "demo": demo,
            "maturity": maturity,
            "beginner_explanation": explanation,
            "agent_run": agent,
            "workflow_run": workflow,
            "store_snapshot": store,
            "tools": tools,
            "next_action": "先看成熟度和下一步开发任务；如果现在只是可运行骨架，下一步优先接真实 LLM Planner 和真实 Executor。",
        }
        if stop_after:
            result["stopped"] = DEMO_PLAYGROUND.stop_demo(demo_id)
        audit("demo_playground_one_click", "demo_playground", demo_id, {"session_id": session.get("session_id"), "message": message})
        return JSONResponse(result)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


async def api_demo_playground_list(request):
    return JSONResponse({"demos": DEMO_PLAYGROUND.list_demos()})


async def api_demo_playground_stop(request):
    demo_id = request.path_params.get("demo_id") or ""
    try:
        result = DEMO_PLAYGROUND.stop_demo(demo_id)
    except KeyError:
        return JSONResponse({"error": "demo not found"}, status_code=404)
    audit("demo_playground_stop", "demo_playground", demo_id, {"status": result.get("status")})
    return JSONResponse(result)


async def api_demo_playground_agent_run(request):
    demo_id = request.path_params.get("demo_id") or ""
    payload = await request.json()
    try:
        result = DEMO_PLAYGROUND.run_agent(demo_id, str(payload.get("message") or ""), payload.get("context") if isinstance(payload.get("context"), dict) else {})
    except KeyError:
        return JSONResponse({"error": "demo not found"}, status_code=404)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)
    return JSONResponse(result)


async def api_demo_playground_workflow_run(request):
    demo_id = request.path_params.get("demo_id") or ""
    payload = await request.json()
    try:
        max_steps = int(payload.get("max_steps") or 3)
    except Exception:
        max_steps = 3
    try:
        result = DEMO_PLAYGROUND.run_workflow(demo_id, str(payload.get("message") or ""), payload.get("context") if isinstance(payload.get("context"), dict) else {}, max_steps=max(1, min(max_steps, 30)))
    except KeyError:
        return JSONResponse({"error": "demo not found"}, status_code=404)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)
    return JSONResponse(result)


async def api_demo_playground_store(request):
    demo_id = request.path_params.get("demo_id") or ""
    try:
        return JSONResponse(DEMO_PLAYGROUND.get_store_snapshot(demo_id))
    except KeyError:
        return JSONResponse({"error": "demo not found"}, status_code=404)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


async def api_demo_playground_tools(request):
    demo_id = request.path_params.get("demo_id") or ""
    try:
        return JSONResponse(DEMO_PLAYGROUND.get_tools(demo_id))
    except KeyError:
        return JSONResponse({"error": "demo not found"}, status_code=404)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


async def api_dev_studio_create_workspace(request):
    payload = await request.json()
    session = get_session(payload.get("session_id") or payload.get("session"))
    try:
        result = DEV_STUDIO.create_workspace(
            session,
            name=str(payload.get("name") or payload.get("project_name") or ""),
            template=str(payload.get("template") or "fastapi-vue"),
        )
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)
    audit("dev_studio_create_workspace", "dev_studio", result.get("workspace_id") or "", {"session_id": session.get("session_id")})
    return JSONResponse(result)


async def api_dev_studio_workspaces(request):
    session_id = str(request.query_params.get("session_id") or "")
    return JSONResponse({"workspaces": DEV_STUDIO.list_workspaces(session_id=session_id)})


async def api_dev_studio_workspace(request):
    workspace_id = request.path_params.get("workspace_id") or ""
    try:
        return JSONResponse(DEV_STUDIO.get_workspace(workspace_id))
    except KeyError:
        return JSONResponse({"error": "workspace not found"}, status_code=404)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


async def api_dev_studio_files(request):
    workspace_id = request.path_params.get("workspace_id") or ""
    try:
        return JSONResponse({"files": DEV_STUDIO.list_editable_files(workspace_id)})
    except KeyError:
        return JSONResponse({"error": "workspace not found"}, status_code=404)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


async def api_dev_studio_file_read(request):
    workspace_id = request.path_params.get("workspace_id") or ""
    rel_path = str(request.query_params.get("path") or "")
    try:
        return JSONResponse(DEV_STUDIO.read_file(workspace_id, rel_path))
    except KeyError:
        return JSONResponse({"error": "workspace not found"}, status_code=404)
    except FileNotFoundError as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


async def api_dev_studio_file_write(request):
    workspace_id = request.path_params.get("workspace_id") or ""
    payload = await request.json()
    try:
        result = DEV_STUDIO.write_file(workspace_id, str(payload.get("path") or ""), str(payload.get("content") or ""))
    except KeyError:
        return JSONResponse({"error": "workspace not found"}, status_code=404)
    except FileNotFoundError as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    audit("dev_studio_file_write", "dev_studio", workspace_id, {"path": result.get("path")})
    return JSONResponse(result)


async def api_dev_studio_quick_reply(request):
    workspace_id = request.path_params.get("workspace_id") or ""
    payload = await request.json()
    try:
        result = DEV_STUDIO.update_demo_reply(workspace_id, str(payload.get("reply") or ""))
    except KeyError:
        return JSONResponse({"error": "workspace not found"}, status_code=404)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    audit("dev_studio_quick_reply", "dev_studio", workspace_id, {"path": result.get("path")})
    return JSONResponse(result)


async def api_dev_studio_engineering_plan(request):
    workspace_id = request.path_params.get("workspace_id") or ""
    payload = await request.json()
    session = get_session(payload.get("session_id") or payload.get("session"))
    try:
        result = DEV_STUDIO.build_engineering_plan(workspace_id, session, str(payload.get("request") or payload.get("task") or ""))
    except KeyError:
        return JSONResponse({"error": "workspace not found"}, status_code=404)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse(result)


async def api_dev_studio_developer_run(request):
    workspace_id = request.path_params.get("workspace_id") or ""
    payload = await request.json()
    session = get_session(payload.get("session_id") or payload.get("session"))
    try:
        timeout_seconds = int(payload.get("timeout_seconds") or 900)
    except Exception:
        timeout_seconds = 900
    try:
        result = await asyncio.to_thread(
            DEV_STUDIO.run_developer_task,
            workspace_id,
            session=session,
            request=str(payload.get("request") or payload.get("task") or ""),
            runner=str(payload.get("runner") or "open_claude"),
            base_url=str(payload.get("base_url") or ""),
            api_key=str(payload.get("api_key") or ""),
            work_dir=str(payload.get("work_dir") or ""),
            cli_root=str(payload.get("cli_root") or ""),
            model=str(payload.get("model") or ""),
            command_template=str(payload.get("command_template") or ""),
            timeout_seconds=timeout_seconds,
        )
    except KeyError:
        return JSONResponse({"error": "workspace not found"}, status_code=404)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)
    runner_result = result.get("runner_result") or {}
    audit("dev_studio_developer_run", "dev_studio", workspace_id, {"runner": runner_result.get("runner"), "status": runner_result.get("status"), "changed_files": len(runner_result.get("changed_files") or [])})
    return JSONResponse(result)


async def api_dev_studio_cli_start(request):
    workspace_id = request.path_params.get("workspace_id") or ""
    payload = await request.json()
    try:
        workspace = DEV_STUDIO.get_workspace(workspace_id)
        project_root = Path(str(workspace.get("project_root") or ""))
        result = await asyncio.to_thread(
            INTERACTIVE_CLI.start,
            workspace_id=workspace_id,
            runner=str(payload.get("runner") or "open_claude"),
            project_root=project_root,
            work_dir=str(payload.get("work_dir") or ""),
            cli_root=str(payload.get("cli_root") or ""),
            base_url=str(payload.get("base_url") or ""),
            api_key=str(payload.get("api_key") or ""),
            model=str(payload.get("model") or ""),
            initial_prompt=str(payload.get("initial_prompt") or ""),
            command_template=str(payload.get("command_template") or ""),
        )
    except KeyError:
        return JSONResponse({"error": "workspace not found"}, status_code=404)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)
    audit("dev_studio_cli_start", "dev_studio", workspace_id, {"runner": result.get("runner"), "session_id": result.get("session_id")})
    return JSONResponse(result)


async def api_dev_studio_ttyd_start(request):
    workspace_id = request.path_params.get("workspace_id") or ""
    payload = await request.json()
    try:
        workspace = DEV_STUDIO.get_workspace(workspace_id)
        project_root = Path(str(workspace.get("project_root") or ""))
        await asyncio.to_thread(TTYD_CLI.cleanup, workspace_id=workspace_id)
        result = await asyncio.to_thread(
            TTYD_CLI.start,
            workspace_id=workspace_id,
            runner=str(payload.get("runner") or "open_claude"),
            project_root=project_root,
            work_dir=str(payload.get("work_dir") or ""),
            cli_root=str(payload.get("cli_root") or ""),
            base_url=str(payload.get("base_url") or ""),
            api_key=str(payload.get("api_key") or ""),
            model=str(payload.get("model") or ""),
            initial_prompt=str(payload.get("initial_prompt") or ""),
            command_template=str(payload.get("command_template") or ""),
            host=str(request.headers.get("host") or ""),
        )
    except KeyError:
        return JSONResponse({"error": "workspace not found"}, status_code=404)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)
    audit("dev_studio_ttyd_start", "dev_studio", workspace_id, {"runner": result.get("runner"), "session_id": result.get("session_id"), "port": result.get("port")})
    return JSONResponse(result)


async def api_dev_studio_ttyd_stop(request):
    ttyd_session_id = request.path_params.get("ttyd_session_id") or ""
    try:
        result = TTYD_CLI.stop(ttyd_session_id)
    except KeyError:
        return JSONResponse({"error": "ttyd session not found"}, status_code=404)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)
    return JSONResponse(result)


async def api_dev_studio_ttyd_cleanup(request):
    payload = await request.json() if request.headers.get("content-length") not in (None, "0") else {}
    workspace_id = str(payload.get("workspace_id") or "")
    all_apd = bool(payload.get("all_apd", True))
    try:
        result = await asyncio.to_thread(TTYD_CLI.cleanup, workspace_id=workspace_id, all_apd=all_apd)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)
    audit("dev_studio_ttyd_cleanup", "dev_studio", workspace_id or "all", {"all_apd": all_apd, "ttyd": len(result.get("killed_ttyd_pids") or []), "tmux": len(result.get("killed_tmux_sessions") or [])})
    return JSONResponse(result)


async def api_dev_studio_ttyd_send(request):
    ttyd_session_id = request.path_params.get("ttyd_session_id") or ""
    payload = await request.json()
    try:
        result = await TTYD_CLI.send(
            ttyd_session_id,
            str(payload.get("text") or ""),
            raw=bool(payload.get("raw")),
            columns=int(payload.get("columns") or 120),
            rows=int(payload.get("rows") or 32),
        )
    except KeyError:
        return JSONResponse({"error": "ttyd session not found"}, status_code=404)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)
    status_code = 200 if result.get("sent") else 500
    return JSONResponse(result, status_code=status_code)


async def api_dev_studio_cli_read(request):
    cli_session_id = request.path_params.get("cli_session_id") or ""
    try:
        result = INTERACTIVE_CLI.read(cli_session_id)
    except KeyError:
        return JSONResponse({"error": "cli session not found"}, status_code=404)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)
    return JSONResponse(result)


async def api_dev_studio_cli_send(request):
    cli_session_id = request.path_params.get("cli_session_id") or ""
    payload = await request.json()
    try:
        result = INTERACTIVE_CLI.send(cli_session_id, str(payload.get("text") or ""), raw=bool(payload.get("raw")))
    except KeyError:
        return JSONResponse({"error": "cli session not found"}, status_code=404)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)
    return JSONResponse(result)


async def api_dev_studio_cli_stop(request):
    cli_session_id = request.path_params.get("cli_session_id") or ""
    try:
        result = INTERACTIVE_CLI.stop(cli_session_id)
    except KeyError:
        return JSONResponse({"error": "cli session not found"}, status_code=404)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)
    return JSONResponse(result)


async def api_dev_studio_open_claude_run(request):
    workspace_id = request.path_params.get("workspace_id") or ""
    payload = await request.json()
    try:
        timeout_seconds = int(payload.get("timeout_seconds") or 900)
    except Exception:
        timeout_seconds = 900
    try:
        result = await asyncio.to_thread(
            DEV_STUDIO.run_open_claude,
            workspace_id,
            task=str(payload.get("task") or ""),
            base_url=str(payload.get("base_url") or ""),
            api_key=str(payload.get("api_key") or ""),
            work_dir=str(payload.get("work_dir") or ""),
            cli_root=str(payload.get("cli_root") or ""),
            model=str(payload.get("model") or ""),
            timeout_seconds=timeout_seconds,
        )
    except KeyError:
        return JSONResponse({"error": "workspace not found"}, status_code=404)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)
    audit("dev_studio_open_claude_run", "dev_studio", workspace_id, {"status": result.get("status"), "changed_files": len(result.get("changed_files") or [])})
    return JSONResponse(result)


async def _run_debug_turn_from_payload(workspace_id: str, payload: dict[str, Any]) -> JSONResponse:
    try:
        max_steps = int(payload.get("max_steps") or 6)
    except Exception:
        max_steps = 6
    try:
        result = await asyncio.to_thread(
            DEV_STUDIO.debug_turn,
            workspace_id,
            message=str(payload.get("message") or "测试一句用户话术"),
            context=payload.get("context") if isinstance(payload.get("context"), dict) else {},
            max_steps=max_steps,
        )
    except KeyError:
        return JSONResponse({"error": "workspace not found"}, status_code=404)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)
    audit("dev_studio_debug_turn", "dev_studio", workspace_id, {"message": str(payload.get("message") or "")[:200]})
    return JSONResponse(result)


async def api_dev_studio_debug_turn(request):
    workspace_id = request.path_params.get("workspace_id") or ""
    payload = await request.json()
    return await _run_debug_turn_from_payload(workspace_id, payload)


async def api_dev_studio_debug_chat(request):
    workspace_id = request.path_params.get("workspace_id") or ""
    payload = await request.json()
    context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
    scenario = payload.get("scenario") if isinstance(payload.get("scenario"), dict) else {}
    runtime_params = payload.get("runtime_params") if isinstance(payload.get("runtime_params"), dict) else {}
    context = {**context, "scenario": scenario, "runtime_params": runtime_params, "debug_channel": "runtime_inspector_v2"}
    payload = {**payload, "context": context}
    return await _run_debug_turn_from_payload(workspace_id, payload)


async def api_dev_studio_debug_clear(request):
    workspace_id = request.path_params.get("workspace_id") or ""
    try:
        result = await asyncio.to_thread(DEV_STUDIO.clear_debug_session, workspace_id)
    except KeyError:
        return JSONResponse({"error": "workspace not found"}, status_code=404)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)
    audit("dev_studio_debug_clear", "dev_studio", workspace_id, {"status": (result.get("clear") or {}).get("status")})
    return JSONResponse(result)


async def api_dev_studio_debug_testcase(request):
    workspace_id = request.path_params.get("workspace_id") or ""
    payload = await request.json()
    try:
        result = await asyncio.to_thread(DEV_STUDIO.save_debug_testcase, workspace_id, payload)
    except KeyError:
        return JSONResponse({"error": "workspace not found"}, status_code=404)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)
    audit("dev_studio_debug_testcase", "dev_studio", workspace_id, {"case_id": (result.get("testcase") or {}).get("id")})
    return JSONResponse(result)


async def api_dev_studio_debug_testcases(request):
    workspace_id = request.path_params.get("workspace_id") or ""
    try:
        result = await asyncio.to_thread(DEV_STUDIO.list_debug_testcases, workspace_id)
    except KeyError:
        return JSONResponse({"error": "workspace not found"}, status_code=404)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)
    return JSONResponse(result)


async def api_dev_studio_debug_replay(request):
    workspace_id = request.path_params.get("workspace_id") or ""
    case_id = request.path_params.get("case_id") or ""
    try:
        result = await asyncio.to_thread(DEV_STUDIO.replay_debug_testcase, workspace_id, case_id)
    except KeyError as exc:
        return JSONResponse({"error": str(exc) or "not found"}, status_code=404)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)
    audit("dev_studio_debug_replay", "dev_studio", workspace_id, {"case_id": case_id})
    return JSONResponse(result)


async def api_dev_studio_debug_llm_diagnose(request):
    workspace_id = request.path_params.get("workspace_id") or ""
    payload = await request.json()
    turn = payload.get("turn") if isinstance(payload.get("turn"), dict) else {}
    settings = payload.get("settings") if isinstance(payload.get("settings"), dict) else {}
    compact = {
        "workspace_id": workspace_id,
        "message": turn.get("message"),
        "assistant_reply": turn.get("reply"),
        "diagnosis": turn.get("diagnosis"),
        "debug_context": ((turn.get("data") or {}).get("debug_context") if isinstance(turn.get("data"), dict) else {}),
        "agent_run": ((turn.get("data") or {}).get("agent_run") if isinstance(turn.get("data"), dict) else {}),
        "workflow_run": ((turn.get("data") or {}).get("workflow_run") if isinstance(turn.get("data"), dict) else {}),
        "store_snapshot": ((turn.get("data") or {}).get("store_snapshot") if isinstance(turn.get("data"), dict) else {}),
    }
    messages = [
        {"role": "system", "content": "你是资深 Agent 架构诊断专家。请用中文输出，越详细越好，但要结构清晰。你要解释 Agent 本轮为什么这样回复，Context Pack 是否充分，意图识别、OpCall、Validator、Tools、Workflow、Memory、Executor 各层是否有问题，并给出建议修改文件和验收话术。不要泄露密钥。"},
        {"role": "user", "content": json.dumps({"whitebox_turn": compact, "output_requirements": ["先给一句话结论", "按层级逐项诊断", "说明每层输入输出是否合理", "指出最可能根因", "列出建议修改文件", "给出下一轮测试话术", "给出发给工程 AI 的修复建议"]}, ensure_ascii=False)[:20000]},
    ]
    try:
        result = await chat_json_result(messages, settings=settings)
        return JSONResponse({"ok": True, "diagnosis": result.content, "model": result.get("model"), "usage": result.get("usage"), "finish_reason": result.get("finish_reason")})
    except LLMError as exc:
        return JSONResponse({"ok": False, "diagnosis": "当前没有成功调用 LLM 诊断。请在左侧“LLM 诊断配置”填写 api_base、api_key、model 后重试。\n\n离线诊断仍可参考右侧各层白盒信息。", "error": str(exc)}, status_code=200)
    except Exception as exc:
        return JSONResponse({"ok": False, "diagnosis": "LLM 诊断异常，已保留离线白盒信息。", "error": str(exc)}, status_code=200)


async def api_dev_studio_debug_stop(request):
    workspace_id = request.path_params.get("workspace_id") or ""
    try:
        result = await asyncio.to_thread(DEV_STUDIO.stop_debug_session, workspace_id)
    except KeyError:
        return JSONResponse({"error": "workspace not found"}, status_code=404)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)
    return JSONResponse(result)


async def api_dev_studio_debug_restart(request):
    workspace_id = request.path_params.get("workspace_id") or ""
    try:
        result = await asyncio.to_thread(DEV_STUDIO.restart_debug_session, workspace_id)
    except KeyError:
        return JSONResponse({"error": "workspace not found"}, status_code=404)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)
    audit("dev_studio_debug_restart", "dev_studio", workspace_id, {"status": (result.get("restart") or {}).get("status"), "port": (result.get("debug_session") or {}).get("port")})
    return JSONResponse(result)


async def api_dev_studio_run(request):
    workspace_id = request.path_params.get("workspace_id") or ""
    payload = await request.json()
    try:
        max_steps = int(payload.get("max_steps") or 3)
    except Exception:
        max_steps = 3
    try:
        result = await asyncio.to_thread(
            DEV_STUDIO.run_workspace,
            workspace_id,
            message=str(payload.get("message") or "测试一句用户话术"),
            context=payload.get("context") if isinstance(payload.get("context"), dict) else {},
            max_steps=max_steps,
        )
    except KeyError:
        return JSONResponse({"error": "workspace not found"}, status_code=404)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)
    audit("dev_studio_run", "dev_studio", workspace_id, {"message": str(payload.get("message") or "")[:200]})
    return JSONResponse(result)


async def api_dev_studio_save_version(request):
    workspace_id = request.path_params.get("workspace_id") or ""
    payload = await request.json()
    try:
        result = DEV_STUDIO.save_version(workspace_id, str(payload.get("message") or "保存当前版本"))
    except KeyError:
        return JSONResponse({"error": "workspace not found"}, status_code=404)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)
    audit("dev_studio_save_version", "dev_studio", workspace_id, {"version_id": result.get("version_id")})
    return JSONResponse(result)


async def api_dev_studio_rollback(request):
    workspace_id = request.path_params.get("workspace_id") or ""
    payload = await request.json()
    try:
        result = DEV_STUDIO.rollback(workspace_id, str(payload.get("version_id") or ""))
    except KeyError:
        return JSONResponse({"error": "workspace or version not found"}, status_code=404)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)
    audit("dev_studio_rollback", "dev_studio", workspace_id, {"version_id": str(payload.get("version_id") or "")})
    return JSONResponse(result)


async def api_dev_studio_download(request):
    workspace_id = request.path_params.get("workspace_id") or ""
    version_id = str(request.query_params.get("version") or "current")
    try:
        data, filename = DEV_STUDIO.download_zip(workspace_id, version_id=version_id)
    except KeyError:
        return JSONResponse({"error": "workspace or version not found"}, status_code=404)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return Response(data, media_type="application/zip", headers=headers)


async def api_sandbox_run(request):
    payload = await request.json()
    session = get_session(payload.get("session_id") or payload.get("session"))
    try:
        timeout_seconds = int(payload.get("timeout_seconds") or 300)
    except Exception:
        timeout_seconds = 300
    timeout_seconds = max(10, min(timeout_seconds, 3600))
    result = await asyncio.to_thread(
        run_docker_sandbox,
        session=session,
        project_path=str(payload.get("project_path") or ""),
        task=str(payload.get("task") or ""),
        runner=str(payload.get("runner") or "custom"),
        image=str(payload.get("image") or ""),
        command=str(payload.get("command") or ""),
        settings=payload.get("settings") or {},
        timeout_seconds=timeout_seconds,
        memory=str(payload.get("memory") or "2g"),
        cpus=str(payload.get("cpus") or "2"),
        network=str(payload.get("network") or "bridge"),
        keep_job=bool(payload.get("keep_job", True)),
    )
    return JSONResponse(result)


async def api_preview_run(request):
    payload = await request.json()
    session = get_session(payload.get("session_id") or payload.get("session"))
    message = str(payload.get("message") or "").strip()
    if not message:
        return JSONResponse({"error": "message is required"}, status_code=400)
    result = await preview_run(
        protocol=session.get("protocol") or {},
        user_message=message,
        context=payload.get("context") or {},
        settings=payload.get("settings") or {},
    )
    return JSONResponse(result)


async def api_preview_examples(request):
    payload = await request.json()
    session = get_session(payload.get("session_id") or payload.get("session"))
    try:
        count = int(payload.get("count") or 5)
    except Exception:
        count = 5
    result = await generate_preview_examples(
        protocol=session.get("protocol") or {},
        settings=payload.get("settings") or {},
        count=count,
    )
    return JSONResponse(result)


async def api_runtime_plan(request):
    payload = await request.json()
    session = get_session(payload.get("session_id") or payload.get("session"))
    result = build_runtime_plan(
        protocol=session.get("protocol") or {},
        user_message=str(payload.get("message") or ""),
    )
    return JSONResponse(result)


async def api_runtime_run(request):
    payload = await request.json()
    session = get_session(payload.get("session_id") or payload.get("session"))
    job_id = secrets.token_hex(8)
    user_message = str(payload.get("message") or "")
    context = payload.get("context") or {}
    approvals = payload.get("approvals") or {}
    result = run_workflow_once(
        protocol=session.get("protocol") or {},
        user_message=user_message,
        context=context,
        approvals=approvals,
        max_steps=int(payload.get("max_steps") or 30),
    )
    job = runtime_result_to_job(
        job_id=job_id,
        session_id=session.get("session_id") or "",
        result=result,
        user_message=user_message,
        context=context,
        approvals=approvals,
    )
    save_runtime_job(job)
    audit("runtime_run", "runtime", job.get("job_id") or "", {"message": user_message, "context": context, "status": job.get("status")})
    result["job"] = {key: job.get(key) for key in ("job_id", "status", "title", "created_at", "updated_at", "waiting_for")}
    return JSONResponse(result)


async def api_runtime_jobs(request):
    session_id = str(request.query_params.get("session_id") or request.query_params.get("session") or "")
    return JSONResponse({"jobs": list_runtime_jobs(session_id)})


async def api_runtime_job(request):
    try:
        job = load_runtime_job(request.path_params.get("job_id") or "")
    except KeyError:
        return JSONResponse({"error": "runtime job not found"}, status_code=404)
    return JSONResponse(job)


async def api_runtime_continue(request):
    payload = await request.json()
    try:
        job = load_runtime_job(request.path_params.get("job_id") or "")
    except KeyError:
        return JSONResponse({"error": "runtime job not found"}, status_code=404)
    session = get_session(job.get("session_id"))
    approvals = {**(job.get("approvals") or {}), **(payload.get("approvals") or {})}
    waiting = job.get("waiting_for") or ((job.get("result") or {}).get("waiting_for") or {})
    if waiting.get("node_id") and payload.get("approve_waiting", True):
        approvals[str(waiting.get("node_id"))] = True
    result = run_workflow_once(
        protocol=session.get("protocol") or {},
        user_message=job.get("user_message") or "",
        context=job.get("context") or {},
        approvals=approvals,
        max_steps=int(payload.get("max_steps") or 30),
        existing_result=job.get("result") or {},
    )
    updated = runtime_result_to_job(
        job_id=job.get("job_id"),
        session_id=job.get("session_id") or "",
        result=result,
        user_message=job.get("user_message") or "",
        context=job.get("context") or {},
        approvals=approvals,
        created_at=job.get("created_at"),
    )
    save_runtime_job(updated)
    audit("runtime_continue", "runtime", updated.get("job_id") or "", {"approvals": approvals, "status": updated.get("status")})
    result["job"] = {key: updated.get(key) for key in ("job_id", "status", "title", "created_at", "updated_at", "waiting_for")}
    return JSONResponse(result)


async def api_runtime_artifact_rollback(request):
    try:
        job = load_runtime_job(request.path_params.get("job_id") or "")
        updated = rollback_artifact_in_job(job, request.path_params.get("artifact_id") or "")
    except KeyError:
        return JSONResponse({"error": "runtime job or artifact not found"}, status_code=404)
    save_runtime_job(updated)
    audit("artifact_rollback", "artifact", request.path_params.get("artifact_id") or "", {"job_id": updated.get("job_id"), "artifact_id": request.path_params.get("artifact_id")})
    return JSONResponse(updated)


async def api_runtime_eval_replay(request):
    payload = await request.json()
    session = get_session(payload.get("session_id") or payload.get("session"))
    try:
        limit = int(payload.get("limit") or 12)
    except Exception:
        limit = 12
    result = replay_eval_cases(
        session.get("protocol") or {},
        cases=payload.get("cases") if isinstance(payload.get("cases"), list) else None,
        limit=max(1, min(limit, 50)),
    )
    saved = save_eval_replay(result, session.get("session_id") or "")
    audit("eval_replay", "eval", saved.get("replay_id") or "", {"summary": saved.get("summary"), "session_id": session.get("session_id")})
    return JSONResponse(saved)


async def api_registry_agents(request):
    return JSONResponse({"agents": list_registered_agents(AGENT_REGISTRY_DIR)})


async def api_registry_register(request):
    payload = await request.json()
    session = get_session(payload.get("session_id") or payload.get("session"))
    agent = register_agent(
        AGENT_REGISTRY_DIR,
        session,
        name=str(payload.get("name") or ""),
        description=str(payload.get("description") or ""),
    )
    audit("registry_register", "registry", agent.get("agent_id") or "", {"name": agent.get("name"), "summary": agent.get("summary")})
    return JSONResponse(agent)


async def api_registry_agent(request):
    try:
        agent = load_registered_agent(AGENT_REGISTRY_DIR, request.path_params.get("agent_id") or "")
    except KeyError:
        return JSONResponse({"error": "agent not found"}, status_code=404)
    return JSONResponse(agent)


async def api_registry_version(request):
    payload = await request.json()
    session = get_session(payload.get("session_id") or payload.get("session"))
    try:
        agent = add_agent_version(
            AGENT_REGISTRY_DIR,
            request.path_params.get("agent_id") or "",
            session,
            notes=str(payload.get("notes") or ""),
        )
    except KeyError:
        return JSONResponse({"error": "agent not found"}, status_code=404)
    audit("registry_version", "registry", agent.get("agent_id") or "", {"current_version": agent.get("current_version")})
    return JSONResponse(agent)


async def api_registry_clone(request):
    payload = await request.json()
    try:
        agent = load_registered_agent(AGENT_REGISTRY_DIR, request.path_params.get("agent_id") or "")
    except KeyError:
        return JSONResponse({"error": "agent not found"}, status_code=404)
    session_id = secrets.token_hex(8)
    try:
        session = session_from_agent(agent, session_id=session_id, version_id=str(payload.get("version_id") or ""))
    except KeyError:
        return JSONResponse({"error": "version not found"}, status_code=404)
    SESSIONS[session_id] = session
    save_session(session)
    audit("registry_clone", "registry", agent.get("agent_id") or "", {"new_session_id": session_id, "version_id": (session.get("registry_source") or {}).get("version_id")})
    return JSONResponse(attach_exports_preview(session))


async def api_tool_test(request):
    payload = await request.json()
    session = get_session(payload.get("session_id") or payload.get("session"))
    selected = payload.get("tools") if isinstance(payload.get("tools"), list) else None
    if payload.get("tool"):
        selected = [str(payload.get("tool"))]
    run = run_tool_tests(session.get("protocol") or {}, selected_tools=selected)
    saved = save_tool_run(TOOL_RUN_DIR, run, session_id=session.get("session_id") or "")
    audit("tool_test", "tool", saved.get("run_id") or "", {"summary": saved.get("summary"), "session_id": session.get("session_id")}, risk="high" if (saved.get("summary") or {}).get("needs_confirmation") else "")
    return JSONResponse(saved)


async def api_tool_runs(request):
    session_id = str(request.query_params.get("session_id") or request.query_params.get("session") or "")
    return JSONResponse({"runs": list_tool_runs(TOOL_RUN_DIR, session_id)})


async def api_observability_summary(request):
    return JSONResponse(build_observability_summary(
        runtime_dir=RUNTIME_DIR,
        tool_run_dir=TOOL_RUN_DIR,
        eval_replay_dir=EVAL_REPLAY_DIR,
        agent_registry_dir=AGENT_REGISTRY_DIR,
    ))


async def api_multi_agent_analyze(request):
    payload = await request.json()
    session = get_session(payload.get("session_id") or payload.get("session"))
    user_message = str(payload.get("message") or "")
    result = analyze_multi_agent_collaboration(session.get("protocol") or {}, user_message=user_message)
    audit("multi_agent_analyze", "multi_agent", session.get("session_id") or "", {"message": user_message, "summary": result.get("summary")})
    return JSONResponse(result)


async def api_governance_summary(request):
    return JSONResponse(summarize_audit_events(AUDIT_DIR))


async def api_governance_events(request):
    try:
        limit = int(request.query_params.get("limit") or 100)
    except Exception:
        limit = 100
    return JSONResponse({"events": list_audit_events(AUDIT_DIR, max(1, min(limit, 500)))})


routes = [
    Mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static"),
    Route("/login", login_page),
    Route("/api/login", api_login, methods=["POST"]),
    Route("/api/logout", api_logout, methods=["POST"]),
    Route("/", index),
    Route("/runtime-inspector", runtime_inspector_page),
    Route("/delegated-inspector", delegated_inspector_page),
    Route("/api/status", api_status),
    Route("/api/guide", api_guide),
    Route("/api/writing-case", api_writing_case),
    Route("/api/bid-validation", api_bid_validation),
    Route("/api/agentos-evaluation", api_agentos_evaluation),
    Route("/api/capability-boundary", api_capability_boundary),
    Route("/api/reset", api_reset, methods=["POST"]),
    Route("/api/sessions", api_sessions),
    Route("/api/session/{session_id}", api_load_session),
    Route("/api/session/{session_id}", api_delete_session, methods=["DELETE"]),
    Route("/api/chat", api_chat, methods=["POST"]),
    Route("/api/export/{session_id}/{name}", api_export),
    Route("/api/scaffold/{session_id}.zip", api_scaffold_zip),
    Route("/api/delegated-agent/{session_id}.zip", api_delegated_agent_zip, methods=["POST"]),
    Route("/api/delegated-playground/start", api_delegated_playground_start, methods=["POST"]),
    Route("/api/delegated-playground/list", api_delegated_playground_list),
    Route("/api/delegated-playground/{delegated_id}/stop", api_delegated_playground_stop, methods=["POST"]),
    Route("/api/delegated-playground/{delegated_id}/restart", api_delegated_playground_restart, methods=["POST"]),
    Route("/api/delegated-playground/{delegated_id}/chat", api_delegated_playground_chat, methods=["POST"]),
    Route("/api/delegated-playground/{delegated_id}/config", api_delegated_playground_config),
    Route("/api/delegated-playground/{delegated_id}/job/{job_id}", api_delegated_playground_job),
    Route("/api/delegated-playground/{delegated_id}/job/{job_id}/llm-diagnose", api_delegated_playground_llm_diagnose, methods=["POST"]),
    Route("/api/demo-playground/start", api_demo_playground_start, methods=["POST"]),
    Route("/api/demo-playground/one-click", api_demo_playground_one_click, methods=["POST"]),
    Route("/api/demo-playground/list", api_demo_playground_list),
    Route("/api/demo-playground/{demo_id}/stop", api_demo_playground_stop, methods=["POST"]),
    Route("/api/demo-playground/{demo_id}/agent-run", api_demo_playground_agent_run, methods=["POST"]),
    Route("/api/demo-playground/{demo_id}/workflow-run", api_demo_playground_workflow_run, methods=["POST"]),
    Route("/api/demo-playground/{demo_id}/store", api_demo_playground_store),
    Route("/api/demo-playground/{demo_id}/tools", api_demo_playground_tools),
    Route("/api/dev-studio/workspace", api_dev_studio_create_workspace, methods=["POST"]),
    Route("/api/dev-studio/workspaces", api_dev_studio_workspaces),
    Route("/api/dev-studio/workspace/{workspace_id}", api_dev_studio_workspace),
    Route("/api/dev-studio/workspace/{workspace_id}/files", api_dev_studio_files),
    Route("/api/dev-studio/workspace/{workspace_id}/file", api_dev_studio_file_read),
    Route("/api/dev-studio/workspace/{workspace_id}/file", api_dev_studio_file_write, methods=["POST"]),
    Route("/api/dev-studio/workspace/{workspace_id}/quick-reply", api_dev_studio_quick_reply, methods=["POST"]),
    Route("/api/dev-studio/workspace/{workspace_id}/engineering-plan", api_dev_studio_engineering_plan, methods=["POST"]),
    Route("/api/dev-studio/workspace/{workspace_id}/developer-run", api_dev_studio_developer_run, methods=["POST"]),
    Route("/api/dev-studio/workspace/{workspace_id}/cli/start", api_dev_studio_cli_start, methods=["POST"]),
    Route("/api/dev-studio/workspace/{workspace_id}/ttyd/start", api_dev_studio_ttyd_start, methods=["POST"]),
    Route("/api/dev-studio/ttyd/cleanup", api_dev_studio_ttyd_cleanup, methods=["POST"]),
    Route("/api/dev-studio/ttyd/{ttyd_session_id}/stop", api_dev_studio_ttyd_stop, methods=["POST"]),
    Route("/api/dev-studio/ttyd/{ttyd_session_id}/send", api_dev_studio_ttyd_send, methods=["POST"]),
    Route("/api/dev-studio/cli/{cli_session_id}/read", api_dev_studio_cli_read),
    Route("/api/dev-studio/cli/{cli_session_id}/send", api_dev_studio_cli_send, methods=["POST"]),
    Route("/api/dev-studio/cli/{cli_session_id}/stop", api_dev_studio_cli_stop, methods=["POST"]),
    Route("/api/dev-studio/workspace/{workspace_id}/open-claude/run", api_dev_studio_open_claude_run, methods=["POST"]),
    Route("/api/dev-studio/workspace/{workspace_id}/debug/turn", api_dev_studio_debug_turn, methods=["POST"]),
    Route("/api/dev-studio/workspace/{workspace_id}/debug/chat", api_dev_studio_debug_chat, methods=["POST"]),
    Route("/api/dev-studio/workspace/{workspace_id}/debug/restart", api_dev_studio_debug_restart, methods=["POST"]),
    Route("/api/dev-studio/workspace/{workspace_id}/debug/clear", api_dev_studio_debug_clear, methods=["POST"]),
    Route("/api/dev-studio/workspace/{workspace_id}/debug/testcase", api_dev_studio_debug_testcase, methods=["POST"]),
    Route("/api/dev-studio/workspace/{workspace_id}/debug/testcases", api_dev_studio_debug_testcases),
    Route("/api/dev-studio/workspace/{workspace_id}/debug/testcase/{case_id}/replay", api_dev_studio_debug_replay, methods=["POST"]),
    Route("/api/dev-studio/workspace/{workspace_id}/debug/llm-diagnose", api_dev_studio_debug_llm_diagnose, methods=["POST"]),
    Route("/api/dev-studio/workspace/{workspace_id}/debug/stop", api_dev_studio_debug_stop, methods=["POST"]),
    Route("/api/dev-studio/workspace/{workspace_id}/run", api_dev_studio_run, methods=["POST"]),
    Route("/api/dev-studio/workspace/{workspace_id}/version", api_dev_studio_save_version, methods=["POST"]),
    Route("/api/dev-studio/workspace/{workspace_id}/rollback", api_dev_studio_rollback, methods=["POST"]),
    Route("/api/dev-studio/workspace/{workspace_id}/download", api_dev_studio_download),
    Route("/api/sandbox/run", api_sandbox_run, methods=["POST"]),
    Route("/api/preview/run", api_preview_run, methods=["POST"]),
    Route("/api/preview/examples", api_preview_examples, methods=["POST"]),
    Route("/api/runtime/plan", api_runtime_plan, methods=["POST"]),
    Route("/api/runtime/run", api_runtime_run, methods=["POST"]),
    Route("/api/runtime/jobs", api_runtime_jobs),
    Route("/api/runtime/job/{job_id}", api_runtime_job),
    Route("/api/runtime/job/{job_id}/continue", api_runtime_continue, methods=["POST"]),
    Route("/api/runtime/job/{job_id}/artifact/{artifact_id}/rollback", api_runtime_artifact_rollback, methods=["POST"]),
    Route("/api/runtime/eval-replay", api_runtime_eval_replay, methods=["POST"]),
    Route("/api/registry/agents", api_registry_agents),
    Route("/api/registry/register", api_registry_register, methods=["POST"]),
    Route("/api/registry/agent/{agent_id}", api_registry_agent),
    Route("/api/registry/agent/{agent_id}/version", api_registry_version, methods=["POST"]),
    Route("/api/registry/agent/{agent_id}/clone", api_registry_clone, methods=["POST"]),
    Route("/api/tools/test", api_tool_test, methods=["POST"]),
    Route("/api/tools/runs", api_tool_runs),
    Route("/api/observability/summary", api_observability_summary),
    Route("/api/multi-agent/analyze", api_multi_agent_analyze, methods=["POST"]),
    Route("/api/governance/summary", api_governance_summary),
    Route("/api/governance/events", api_governance_events),
]

app = Starlette(debug=True, routes=routes)
app.add_middleware(AuthMiddleware)



INSPECTOR_HTML = r"""
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>Runtime Inspector v2</title>
  <style>
    :root{--bg:#07111f;--panel:#0f172a;--panel2:#111827;--line:#26364e;--text:#e5e7eb;--muted:#94a3b8;--accent:#38bdf8;--ok:#22c55e;--warn:#f59e0b;--bad:#ef4444;--chip:#1e293b}*{box-sizing:border-box}html,body{height:100%;overflow:hidden}body{margin:0;background:linear-gradient(135deg,#020617,#0f172a);color:var(--text);font-family:Inter,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}button,input,textarea,select{font:inherit}button{border:1px solid #334155;background:#172033;color:#e5e7eb;border-radius:10px;padding:8px 11px;cursor:pointer}button.primary{background:#0ea5e9;border-color:#38bdf8;color:#00111f;font-weight:800}button.good{background:#14532d;border-color:#22c55e}button.danger{background:#451a1a;border-color:#ef4444}button:disabled{opacity:.55;cursor:not-allowed}header{height:60px;display:flex;align-items:center;justify-content:space-between;padding:0 16px;border-bottom:1px solid var(--line);background:rgba(2,6,23,.88)}h1{font-size:18px;margin:0}.sub{color:var(--muted);font-size:12px;margin-left:8px}.top-actions{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.status{font-size:12px;color:#bae6fd;max-width:420px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}main{height:calc(100vh - 60px);display:grid;grid-template-columns:300px minmax(420px,1fr) 390px;gap:12px;padding:12px;overflow:hidden}.panel{min-height:0;border:1px solid var(--line);border-radius:18px;background:rgba(15,23,42,.96);display:flex;flex-direction:column;overflow:hidden}.panel-head{padding:12px 14px;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;align-items:center;gap:8px}.panel-head strong{color:#e0f2fe}.hint{color:var(--muted);font-size:12px}.side-body,.diag-body{padding:12px;overflow:auto;display:flex;flex-direction:column;gap:12px}.field label{display:block;color:#cbd5e1;font-size:12px;font-weight:800;margin-bottom:6px}.field textarea,.field input,.field select{width:100%;border:1px solid #334155;background:#020617;color:#e5e7eb;border-radius:12px;padding:9px;min-height:38px}.field textarea{min-height:76px;resize:vertical}.field .mini{min-height:48px}.help{margin-top:6px;color:#94a3b8;font-size:12px;line-height:1.45}.help code{color:#bae6fd}.switch{display:flex;align-items:center;gap:8px;color:#cbd5e1;font-size:13px}.quick-row{display:flex;gap:8px;flex-wrap:wrap}.collab-workspace-console{border:1px solid #334155;border-radius:16px;background:rgba(2,6,23,.45);padding:12px;margin:10px 0;display:grid;gap:10px}.collab-console-head{display:flex;align-items:flex-start;justify-content:space-between;gap:10px}.collab-console-actions{display:flex;gap:8px;flex-wrap:wrap}.collab-workspace-mini-list{display:grid;gap:8px}.collab-workspace-mini-card{border:1px solid #26364e;border-radius:12px;background:#020617;padding:9px}.collab-workspace-mini-card.active{border-color:#38bdf8}.collab-workspace-mini-card .row{display:flex;gap:8px;align-items:center;justify-content:space-between;flex-wrap:wrap}.collab-workspace-mini-card code{color:#bae6fd}.chat-panel{background:rgba(2,6,23,.48)}.chat-list{flex:1;overflow:auto;padding:16px;display:flex;flex-direction:column;gap:12px}.empty{border:1px dashed #334155;border-radius:16px;padding:18px;color:#cbd5e1;background:rgba(15,23,42,.65)}.msg{display:flex;flex-direction:column;gap:6px;max-width:86%}.msg.user{align-self:flex-end}.msg.agent{align-self:flex-start}.bubble{border:1px solid #334155;border-radius:16px;padding:11px 13px;background:#111827;white-space:pre-wrap;line-height:1.55}.user .bubble{background:#1d4ed8;border-color:#60a5fa}.agent .bubble{background:#0f172a;border-color:#334155}.agent .bubble.md-body{white-space:normal}.agent .bubble.md-body h1,.agent .bubble.md-body h2,.agent .bubble.md-body h3{margin:8px 0 6px}.agent .bubble.md-body h1{font-size:18px}.agent .bubble.md-body h2{font-size:16px}.agent .bubble.md-body h3{font-size:14px}.agent .bubble.md-body p{margin:6px 0}.agent .bubble.md-body ul,.agent .bubble.md-body ol{margin:6px 0;padding-left:20px}.agent .bubble.md-body pre{max-height:240px}.thinking{color:#bae6fd;background:rgba(14,165,233,.08);border:1px dashed #38bdf8;border-radius:12px;padding:10px;animation:pulseThinking 1.2s ease-in-out infinite}@keyframes pulseThinking{0%,100%{opacity:.72}50%{opacity:1}}.meta{font-size:12px;color:#94a3b8}.diag-card{border:1px solid #334155;border-radius:14px;background:rgba(2,6,23,.5);padding:10px;margin-top:4px}.diag-card h4{margin:0 0 7px;color:#bae6fd}.summary-grid{display:grid;gap:7px;margin-top:6px}.summary-row{display:grid;grid-template-columns:86px 1fr;gap:8px;align-items:start;font-size:13px}.summary-row b{color:#93c5fd}.summary-row span{color:#e5e7eb}.summary-status{display:inline-flex;width:max-content;border-radius:999px;padding:3px 8px;font-size:12px;border:1px solid #334155}.summary-status.ok{border-color:#22c55e;color:#bbf7d0}.summary-status.warn{border-color:#f59e0b;color:#fde68a}.summary-status.bad{border-color:#ef4444;color:#fecaca}.chips{display:flex;gap:6px;flex-wrap:wrap;margin:7px 0}.chip{border:1px solid #334155;border-radius:999px;padding:4px 8px;background:#0f172a;color:#cbd5e1;font-size:12px}.chip.warn{border-color:#f59e0b;color:#fde68a}.chip.ok{border-color:#22c55e;color:#bbf7d0}.chip.bad{border-color:#ef4444;color:#fecaca}.chat-input{border-top:1px solid var(--line);padding:12px;display:grid;grid-template-columns:1fr auto;gap:10px;background:#08111f}.chat-input textarea{width:100%;min-height:78px;resize:vertical;border:1px solid #334155;border-radius:14px;background:#020617;color:#e5e7eb;padding:11px}.send-col{display:flex;flex-direction:column;gap:8px}.diag-section{border:1px solid #26364e;border-radius:14px;background:#08111f;padding:10px}.diag-section h3{font-size:14px;margin:0 0 8px;color:#e0f2fe}.llm-box{display:grid;gap:10px}.llm-card{border:1px solid #26364e;border-radius:12px;background:#020617;padding:10px}.llm-card h4{margin:0 0 6px;color:#bae6fd;font-size:13px}.llm-card p{margin:0;white-space:pre-wrap;line-height:1.55}.llm-card.primary{border-color:#38bdf8;background:rgba(14,165,233,.08)}.llm-trace-list{display:grid;gap:10px}.llm-trace-card{border:1px solid #334155;border-radius:14px;background:#020617;padding:10px}.llm-trace-card.ok{border-color:#16a34a}.llm-trace-card.fail{border-color:#ef4444}.llm-trace-head{display:flex;align-items:flex-start;justify-content:space-between;gap:10px;margin-bottom:8px}.llm-trace-title{font-weight:800;color:#e0f2fe}.llm-trace-meta{display:flex;gap:6px;flex-wrap:wrap;margin-top:5px}.llm-trace-pill{font-size:12px;border:1px solid #334155;border-radius:999px;padding:3px 7px;color:#cbd5e1;background:#0f172a}.llm-trace-pill.ok{border-color:#22c55e;color:#bbf7d0}.llm-trace-pill.fail{border-color:#ef4444;color:#fecaca}.llm-trace-card details{margin-top:8px}.llm-trace-card pre{max-height:220px}.modal-mask{position:fixed;inset:0;display:none;align-items:center;justify-content:center;background:rgba(2,6,23,.78);z-index:99;padding:24px}.modal-mask.open{display:flex}.llm-modal{width:min(1080px,96vw);height:min(860px,92vh);border:1px solid #334155;border-radius:20px;background:#0f172a;box-shadow:0 24px 90px rgba(0,0,0,.55);display:flex;flex-direction:column;overflow:hidden}.llm-modal-head{padding:13px 16px;border-bottom:1px solid #26364e;display:flex;align-items:center;justify-content:space-between;gap:10px}.llm-modal-body{flex:1;overflow:auto;padding:18px}.md-body{line-height:1.72;color:#e5e7eb}.md-body h1,.md-body h2,.md-body h3{color:#e0f2fe;margin:18px 0 8px}.md-body h1{font-size:22px}.md-body h2{font-size:18px}.md-body h3{font-size:15px}.md-body p{margin:8px 0}.md-body ul,.md-body ol{padding-left:22px}.md-body code{background:#020617;border:1px solid #26364e;border-radius:5px;padding:1px 4px;color:#bae6fd}.md-body pre{background:#020617;border:1px solid #26364e;border-radius:12px;padding:12px;max-height:none}.kv{display:grid;grid-template-columns:92px 1fr;gap:6px;font-size:12px;margin:4px 0}.kv span:first-child{color:#94a3b8}.file-list{display:flex;gap:6px;flex-wrap:wrap}.file{font-size:12px;border:1px solid #1d4ed8;color:#bfdbfe;background:#0f172a;border-radius:999px;padding:4px 8px}details{border:1px solid #26364e;border-radius:12px;padding:9px;background:#020617}summary{cursor:pointer;color:#93c5fd;font-weight:800}pre{white-space:pre-wrap;overflow:auto;max-height:300px;font-size:12px;line-height:1.45}.tabs{display:flex;gap:6px;flex-wrap:wrap}.small{font-size:12px;color:#94a3b8}@media(max-width:1180px){main{grid-template-columns:1fr;overflow:auto}.panel{min-height:420px}.chat-panel{min-height:620px}}
  </style>
</head>
<body>
<header>
  <div><h1>Runtime Inspector v2 <span class="sub">真实 Agent 体验调试台</span></h1></div>
  <div class="top-actions">
    <button onclick="restartSession()">重启当前 Agent</button>
    <button onclick="clearSession()">清空会话</button>
    <button onclick="saveLatestTestCase()">保存本轮样本</button>
    <button onclick="replayLatestTestCase()">回放最新样本</button>
    <button onclick="stopSession()">停止会话</button>
    <span id="pageStatus" class="status"></span>
  </div>
</header>
<main>
  <aside class="panel">
    <div class="panel-head"><strong>测试场景</strong><span class="hint">给 Agent 的真实上下文</span></div>
    <div class="side-body">
      <div class="field"><label>模拟使用者类型</label><select id="userRole"><option>最终用户</option><option>业务操作员</option><option>审核人员</option><option>管理员</option><option>开发测试者</option></select></div>
      <div class="field"><label>业务场景</label><textarea id="businessScenario" placeholder="例如：本次要模拟真实用户在什么业务场景里使用这个 Agent"></textarea><div class="help">告诉 Agent 当前处在什么业务环境里。适合写自然语言，例如“用户正在处理一条售后工单，需要判断是否退款”。</div></div>
      <div class="field"><label>运行参数 JSON</label><textarea id="runtimeParams" class="mini" placeholder='{"language":"zh-CN","mode":"normal"}'></textarea><div class="help">控制本次测试的开关和偏好，必须是 JSON。比如语言、模式、是否需要确认、最大步骤数。不会直接展示给用户，但会进入 Agent 上下文。</div></div>
      <div class="field"><label>测试输入 / 文件 / 业务对象 JSON</label><textarea id="debugAssets" class="mini" placeholder='{"object":"订单A","files":[],"artifacts":[]}'></textarea><div class="help">模拟用户已经提供的业务资料、文件、对象或中间产物，必须是 JSON。比如订单、合同、图片、模板、知识库命中结果。</div></div>
      <label class="switch"><input id="keepContext" type="checkbox" checked /> 保留多轮上下文</label>
      <details><summary>LLM 诊断配置</summary><div class="small" style="margin:8px 0">这里专门用于“LLM 诊断本轮”。不影响 open_claude 终端配置。</div><div class="field"><label>API Base</label><input id="diagApiBase" placeholder="例如：http://192.168.1.156:18888/v1" /></div><div class="field"><label>API Key</label><input id="diagApiKey" type="password" placeholder="sk-..." /></div><div class="field"><label>Model</label><input id="diagModel" placeholder="例如：gpt-5.5" /></div><div class="quick-row"><button onclick="saveDiagLlmSettings()">保存诊断配置</button><button onclick="loadDiagLlmSettings()">读取配置</button></div><div class="help">如果不配置，白盒信息仍可看，但“LLM 诊断本轮”会进入离线说明。</div></details>
      <div class="quick-row"><button onclick="fillGenericScenario()">通用助手场景</button><button onclick="fillRiskScenario()">高风险操作场景</button><button onclick="fillWorkflowScenario()">多步骤流程场景</button></div>
      <div class="diag-section"><h3>怎么用</h3><div class="small">1. 左侧参数可不填，先直接聊天也可以。<br/>2. 如果要测真实业务，就补“业务场景”和 JSON 输入。<br/>3. 看每轮回复下面的诊断。<br/>4. 点“让 AI 修这个问题”。<br/>5. CLI 修完后点“重启当前 Agent”。</div></div>
      <details><summary>这三个字段到底有什么用？</summary><div class="small"><b>业务场景</b>：给 Agent 的自然语言背景。<br/><b>运行参数 JSON</b>：本轮测试的控制开关。<br/><b>测试输入 JSON</b>：模拟文件、业务对象、素材和已有产物。<br/>如果不确定，可以先不填，直接在中间对话框测试。</div></details>
    </div>
  </aside>
  <section class="panel chat-panel">
    <div class="panel-head"><strong>真实 Agent 对话</strong><span id="sessionInfo" class="hint">等待开始</span></div>
    <div id="chatList" class="chat-list"><div class="empty">这里是最终用户视角。请直接输入真实业务话术，APD 会在每轮回复下面绑定诊断和修复入口。</div></div>
    <div class="chat-input">
      <textarea id="message" placeholder="像最终用户一样输入，例如：请根据周报模板生成本周总结"></textarea>
      <div class="send-col"><button class="primary" onclick="sendTurn()">发送</button><button onclick="sendExampleFollowup()">追问示例</button></div>
    </div>
  </section>
  <aside class="panel">
    <div class="panel-head"><strong>诊断与修复</strong><span class="hint">当前选中轮次</span></div>
    <div id="diagBody" class="diag-body"><div class="empty">发送一轮对话后，这里会显示问题层级、建议文件、Trace、Workflow 和原始数据。</div></div>
  </aside>
</main>
<div id="llmDiagnosisModal" class="modal-mask" onclick="closeLlmDiagnosisModal(event)">
  <div class="llm-modal" onclick="event.stopPropagation()">
    <div class="llm-modal-head"><strong>LLM 诊断详情</strong><div class="quick-row"><button class="primary" onclick="sendCurrentLlmDiagnosisFix()">让 AI 修这个问题</button><button onclick="copyCurrentLlmDiagnosis()">复制原文</button><button onclick="closeLlmDiagnosisModal()">关闭</button></div></div>
    <div id="llmDiagnosisModalBody" class="llm-modal-body md-body"></div>
  </div>
</div>
<script>
const params = new URLSearchParams(location.search);
let workspaceId = params.get('workspace_id') || localStorage.getItem('apd_dev_workspace_id') || '';
let debugSessionId = localStorage.getItem('apd_runtime_debug_session_id') || ('debug_' + Date.now().toString(36));
localStorage.setItem('apd_runtime_debug_session_id', debugSessionId);
let turns = [];
let selectedTurn = -1;
function esc(s){return String(s??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));}
function status(msg){const el=document.getElementById('pageStatus'); if(el) el.textContent=msg||'';}
function jsonValue(id, fallback={}){const el=document.getElementById(id); const raw=(el&&el.value||'').trim(); if(!raw) return fallback; try{return JSON.parse(raw);}catch(e){throw new Error(id+' 不是合法 JSON：'+e.message);}}
function replyOf(data){const a=data.agent_run||{};for(const x of[a.assistant_message,a.reply,a.response,a.final_response,a.answer,a.output,a.message])if(typeof x==='string'&&x.trim())return x.trim();return'没有拿到用户可见回复，请看右侧原始返回。';}
function diagnosisOf(data){if(data.diagnosis) return data.diagnosis; const a=data.agent_run||{},w=data.workflow_run||{},s=data.store_snapshot||{}; const op=(a.op_call||{}).operation||''; const files=new Set(['backend/app/executor.py']); const levels=[]; if(!op||op==='ask_clarification'){levels.push({label:'意图识别',key:'planner',file:'backend/app/planner.py',reason:'没有稳定命中操作',action:'优化 planner.py',confidence:.82});files.add('backend/app/planner.py');} if(!(a.tool_results||[]).length){levels.push({label:'工具调用',key:'tools',file:'backend/app/tools.py',reason:'没有工具结果',action:'接入真实工具或 dry-run',confidence:.65});files.add('backend/app/tools.py');} if(!Object.keys(w.node_states||{}).length){levels.push({label:'流程编排',key:'workflow',file:'backend/app/workflow.py',reason:'没有 workflow 节点',action:'补 workflow 状态',confidence:.62});files.add('backend/app/workflow.py');} if(!((s.trace_events||[]).length)){levels.push({label:'可观测性',key:'trace',file:'backend/app/runtime.py',reason:'trace 不足',action:'补 trace',confidence:.7});files.add('backend/app/runtime.py');} if(!levels.length) levels.push({label:'回复/执行',key:'executor',file:'backend/app/executor.py',reason:'如果效果不对，优先看回复组织和执行逻辑',action:'检查 executor.py',confidence:.76}); return {summary:'本轮优先看：'+levels[0].label+'，建议改 '+levels[0].file, primary_level:levels[0], levels, suggested_files:[...files], next_test_messages:['继续追问上一轮内容','要求 Agent 修改刚才的结果']};}
function buildConversationHistory(){return turns.flatMap((t,i)=>[{role:'user',content:t.message,turn:i+1},{role:'assistant',content:t.reply,turn:i+1}]).slice(-12);}
function scenarioContext(){const assets=jsonValue('debugAssets',{}); const runtime=jsonValue('runtimeParams',{}); const llm=llmSettings(); runtime.llm_settings=llm; return {session_id:debugSessionId,user_role:document.getElementById('userRole').value,business_scenario:document.getElementById('businessScenario').value.trim(),assets,runtime_params:runtime,llm_settings:llm,conversation_history:document.getElementById('keepContext').checked?buildConversationHistory():[],source:'runtime_inspector_v2'};}
function diagnosisSummary(d){d=d||{};const levels=d.levels||[];const primary=d.primary_level||levels[0]||{};const confidence=Number(primary.confidence||0);let status='需要关注',cls='warn';if(primary.key==='ok'){status='基本正常';cls='ok';}else if(confidence>=0.82){status='明显需要修';cls='bad';}const file=(d.suggested_files||[])[0]||primary.file||'暂未定位文件';const layer=primary.label||primary.key||'未定位';const reason=primary.reason||d.summary||'需要结合右侧白盒详情继续判断。';const action=primary.action||`优先检查 ${file}，再用同一句话复测。`;return{status,cls,layer,file,reason,action};}
function renderChat(){const box=document.getElementById('chatList'); if(!turns.length){box.innerHTML='<div class="empty">这里是最终用户视角。请直接输入真实业务话术，APD 会在每轮回复下面绑定诊断和修复入口。</div>'; return;} box.innerHTML=turns.map((t,i)=>{const pending=!!t.pending;const d=pending?{summary:'Agent 正在思考中，稍后会生成诊断。',levels:[],suggested_files:[]}:t.diagnosis||diagnosisOf(t.data||{}); const s=diagnosisSummary(d); const replyHtml=pending?'<div class="thinking">🤔 Agent 思考中... 正在调用 Planner、Validator、Executor 和 LLM</div>':renderMarkdownForModal(t.reply); const actions=pending?'<span class="small">等待回复完成后会出现诊断和修复按钮</span>':`<button class="primary" onclick="sendFixToTtyd(${i})">让 AI 修这个问题</button><button onclick="selectTurn(${i})">查看白盒详情</button><button onclick="saveTestCase(${i})">保存样本</button>`; return `<div class="msg user"><div class="meta">你 · 第 ${i+1} 轮</div><div class="bubble">${esc(t.message)}</div></div><div class="msg agent"><div class="meta">Agent 回复 · ${pending?'思考中':'Markdown 预览'}</div><div class="bubble md-body agent-reply">${replyHtml}</div><div class="diag-card"><h4>${pending?'本轮状态':'本轮诊断摘要'}</h4><div class="summary-grid"><div class="summary-row"><b>是否正常</b><span class="summary-status ${pending?'warn':s.cls}">${esc(pending?'运行中':s.status)}</span></div><div class="summary-row"><b>最可能问题</b><span>${esc(pending?'等待 Agent 完成后分析':s.layer+' · 建议看 '+s.file)}</span></div><div class="summary-row"><b>原因</b><span>${esc(pending?'请求已发送，正在等待后端返回。':s.reason)}</span></div><div class="summary-row"><b>下一步</b><span>${esc(pending?'请稍等，完成后右侧会自动展示白盒诊断。':s.action)}</span></div></div>${pending?'':`<details style="margin-top:9px"><summary>展开本轮候选问题层</summary><div class="chips">${(d.levels||[]).map(x=>`<span class="chip ${x.key==='ok'?'ok':(x.confidence>.8?'bad':'warn')}">${esc(x.label||x.key)} · ${esc(String(Math.round((x.confidence||0)*100)))}%</span>`).join('')}</div></details>`}<div class="quick-row" style="margin-top:8px">${actions}</div></div></div>`}).join(''); box.scrollTop=box.scrollHeight; document.getElementById('sessionInfo').textContent=`${turns.length} 轮 · 历史 ${buildConversationHistory().length} 条`;}

function layerHtml(title, desc, payload, defaultOpen=false, html=''){return `<details ${defaultOpen?'open':''}><summary>${esc(title)}：${esc(desc)}</summary>${html||`<pre>${esc(JSON.stringify(payload??{},null,2))}</pre>`}</details>`;}

function briefLlmMeta(call){const res=call.response||{};const usage=res.usage||{};const req=res.request||{};return {model:res.model||req.model||'未知模型',latency:res.latency_ms?`${res.latency_ms}ms`:'未记录耗时',tokens:usage.total_tokens||usage.totalTokens||usage.completion_tokens||usage.prompt_tokens||'未返回 Token',finish:res.finish_reason||'未返回 finish_reason'};}
const llmTraceCopyStore={};
function putLlmTraceCopy(key,value){llmTraceCopyStore[key]=typeof value==='string'?value:JSON.stringify(value??{},null,2);return key;}
function copyLlmTraceValue(key){copyTextToClipboard(llmTraceCopyStore[key]||'');}
function llmCallCard(key,call,turnIndex){call=call||{};const meta=briefLlmMeta(call);const ok=!!call.ok;const messages=call.messages||[];const response=call.response||{};const hasMessages=messages.length>0;const copyBase=`${turnIndex}_${key}_${Math.random().toString(36).slice(2)}`;const promptKey=putLlmTraceCopy(copyBase+'_prompt',messages);const responseKey=putLlmTraceCopy(copyBase+'_response',response);return `<div class="llm-trace-card ${ok?'ok':'fail'}"><div class="llm-trace-head"><div><div class="llm-trace-title">${esc(call.name||key)}</div><div class="small">${esc(call.summary||'一次 LLM 调用')}</div><div class="llm-trace-meta"><span class="llm-trace-pill ${ok?'ok':'fail'}">${ok?'成功':'未成功/未调用'}</span><span class="llm-trace-pill">模型：${esc(meta.model)}</span><span class="llm-trace-pill">耗时：${esc(meta.latency)}</span><span class="llm-trace-pill">Token：${esc(String(meta.tokens))}</span><span class="llm-trace-pill">结束：${esc(meta.finish)}</span></div></div><div class="quick-row"><button onclick="copyLlmTraceValue('${promptKey}')">复制 Prompt</button><button onclick="copyLlmTraceValue('${responseKey}')">复制响应</button><button class="primary" onclick="sendFixToTtyd(${turnIndex})">让 AI 修这个调用</button></div></div><details ${ok?'':'open'}><summary>Prompt / 输入 messages</summary>${hasMessages?`<pre>${esc(JSON.stringify(messages,null,2))}</pre>`:'<div class="small">没有记录 Prompt。可能没有进入 LLM 调用，或工程没有记录 messages。</div>'}</details><details ${ok?'':'open'}><summary>Response / 输出响应</summary><pre>${esc(JSON.stringify(response||{},null,2))}</pre></details></div>`;}

function llmTraceHtml(trace,turnIndex){trace=trace||{};const planner=trace.planner||{};const executor=trace.executor||{};return `<div class="llm-trace-list">${llmCallCard('planner',planner,turnIndex)}${llmCallCard('executor',executor,turnIndex)}</div>`;}
function whiteboxSteps(turn){const data=turn.data||{};const a=data.agent_run||{},w=data.workflow_run||{},s=data.store_snapshot||{};const contextPack=a.context_pack||{};const opCall=a.op_call||{};const llmMessages=a.llm_messages||a.prompt_messages||a.messages||null;const llmResponse=a.llm_response||{};const llmTrace=a.llm_trace||{};const runtimeMode=a.runtime_mode||'unknown';return [
  {title:'0. 运行模式',desc:runtimeMode==='llm_production'?'本轮 Agent 回复已进入生产式 LLM Runtime。':'本轮没有可用 LLM，仍是规则/模拟降级。请检查左侧 LLM 诊断配置是否已保存、模型是否可用。',payload:{runtime_mode:runtimeMode,llm_available:runtimeMode==='llm_production',llm_response:llmResponse},open:true},
  {title:'1. 用户输入',desc:'最终用户本轮真正说的话，所有后续判断都从这里开始。',payload:{message:turn.message, conversation_history:(data.debug_context||{}).conversation_history||[]},open:true},
  {title:'2. Context Pack',desc:'本轮发给规划器/LLM/工具前整理好的上下文，包括业务场景、角色、历史、文件、参数。上下文缺失会导致 Agent 答非所问。',payload:contextPack,open:true},
  {title:'3. LLM Trace / 输入输出总览',desc:'把 Planner 和 Executor 两次 LLM 调用分开看：Prompt、Response、模型、耗时、Token、错误都在这里。',html:llmTraceHtml(llmTrace,selectedTurn),open:true},
  {title:'4. LLM Prompt / 原始 messages',desc:llmMessages?'当前工程返回了真实 LLM messages。':'当前工程没有返回真实 LLM messages，说明没有真正进入 LLM 调用或未记录 prompt。',payload:llmMessages||{notice:'未捕获真实 LLM prompt。要完全白盒，需要检查 backend/app/llm_client.py、planner.py、executor.py。',planner_input:{message:turn.message,context_pack:contextPack}},open:false},
  {title:'5. LLM Response / 意图理解',desc:'这里看 LLM Planner 返回、OpCall 绑定和参数抽取。若 operation 错，优先修 planner.py。',payload:{planner_response:llmResponse.planner||null,op_call:opCall,trace:a.trace||[]},open:true},
  {title:'6. Validator 校验',desc:'程序死逻辑校验这次操作是否合法、参数是否完整、是否需要确认。',payload:{validator_results:a.validator_results||[],permission_check:a.permission_check||{}},open:false},
  {title:'7. Tool Calls / 工具结果',desc:'Agent 调用了哪些工具，拿到了什么结果；如果工具为空，很多能力只是 LLM 回复，没有真实外部动作。',payload:a.tool_results||[],open:false},
  {title:'8. Workflow 节点',desc:'多步骤任务的节点状态。这里能看出是 DAG、循环流程，还是单步执行。',payload:w,open:false},
  {title:'9. Memory / State / Artifact',desc:'本轮后保存了什么记忆、状态和产物版本。连续对话不连贯时优先看这里。',payload:{memory:s.memory||[],state:a.state_snapshot||s.state||{},artifacts:a.artifact_versions||s.artifacts||[]},open:false},
  {title:'10. Executor / Writer LLM 回复',desc:'这里看最终回复是否由 LLM Executor 生成。回复不满意通常改 executor.py 的系统提示词、工具结果使用方式或输出 schema。',payload:{executor_response:llmResponse.executor||null,assistant_message:turn.reply,executor_preview:a.executor_preview||{},next_action:a.next_action},open:true},
  {title:'11. APD 诊断和修复任务',desc:'APD 根据白盒数据生成的修复建议，可直接发给 open_claude/Codex。',payload:{diagnosis:turn.diagnosis||{},repair_task:turn.repair_task||data.repair_task||''},open:false}
];}

function renderDiag(){const root=document.getElementById('diagBody');try{if(!root)return;if(selectedTurn<0||!turns[selectedTurn]){root.innerHTML='<div class="empty">选择一轮 Agent 回复后查看详情。白盒模式会展示 Context Pack、Prompt、OpCall、校验、工具、Workflow、Memory、修复任务。</div>';return;} const t=turns[selectedTurn]; if(t.pending){root.innerHTML=`<div class="diag-section"><h3>本轮正在运行</h3><p>Agent 已收到你的消息，正在按生产式链路执行。</p><div class="chips"><span class="chip warn">Observe / 整理上下文</span><span class="chip warn">Planner / 理解意图</span><span class="chip warn">Validator / 校验</span><span class="chip warn">Executor / 生成回复</span></div><div class="small">完成后这里会自动替换成 LLM Trace、白盒过程、诊断和修复入口。</div></div><div class="diag-section"><h3>当前用户输入</h3><pre>${esc(JSON.stringify({message:t.message,session_id:debugSessionId},null,2))}</pre></div>`;return;} const d=t.diagnosis||diagnosisOf(t.data||{}); const data=t.data||{}; const a=data.agent_run||{}; const layers=whiteboxSteps(t).map(step=>layerHtml(step.title,step.desc,step.payload,step.open,step.html||'')).join(''); root.innerHTML=`<div class="diag-section"><h3>本轮中文结论</h3><p>${esc(d.summary||'暂无诊断')}</p><div class="chips">${(d.levels||[]).map(x=>`<span class="chip ${x.key==='ok'?'ok':(x.confidence>.8?'bad':'warn')}">${esc(x.label||x.key)} · ${esc(String(Math.round((x.confidence||0)*100)))}%</span>`).join('')}</div><div class="small">这不是只看日志，而是把本轮 Agent 运行拆成多层：上下文、Prompt、意图、操作、校验、工具、流程、记忆、执行和修复建议。</div></div><div class="diag-section"><h3>建议修改文件</h3><div class="file-list">${(d.suggested_files||[]).map(f=>`<span class="file">${esc(f)}</span>`).join('')}</div></div><div class="diag-section"><h3>LLM 诊断</h3><div class="quick-row"><button class="primary" onclick="runLlmDiagnosis(${selectedTurn})">LLM 诊断本轮</button><button onclick="copyFixTask(${selectedTurn})">复制修复任务</button><button onclick="sendFixToTtyd(${selectedTurn})">让 AI 修这个问题</button></div><div id="llmDiagnosisBox" class="small" style="margin-top:8px;white-space:pre-wrap">点击后会把本轮白盒数据发给 LLM，让它用中文详细解释问题根因、各层输入输出是否合理、该改哪里和怎么复测。</div></div><div class="diag-section"><h3>白盒过程</h3><div class="small">以下每一层都配了中文说明。JSON 是证据，不是让你自己猜；上面的中文结论会告诉你先看哪一层。</div></div>${layers}`;}catch(e){console.error('renderDiag failed',e);if(root)root.innerHTML=`<div class="diag-section"><h3>诊断渲染失败</h3><p>右侧面板渲染时出现前端错误，但对话结果没有丢。</p><pre>${esc(e&&e.stack?e.stack:String(e))}</pre><div class="quick-row"><button onclick="renderDiag()">重新渲染</button><button onclick="copyTextToClipboard(JSON.stringify(turns[selectedTurn]||{},null,2))">复制当前轮原始数据</button></div></div>`;status('诊断面板渲染失败：'+(e.message||e));}}

function loadDiagLlmSettings(){const apiBase=document.getElementById('diagApiBase');const apiKey=document.getElementById('diagApiKey');const model=document.getElementById('diagModel');if(apiBase)apiBase.value=localStorage.getItem('apd_api_base')||'';if(apiKey)apiKey.value=localStorage.getItem('apd_api_key')||'';if(model)model.value=localStorage.getItem('apd_model')||'';}
function saveDiagLlmSettings(){const apiBase=(document.getElementById('diagApiBase')||{}).value||'';const apiKey=(document.getElementById('diagApiKey')||{}).value||'';const model=(document.getElementById('diagModel')||{}).value||'';localStorage.setItem('apd_api_base',apiBase.trim());if(apiKey.trim())localStorage.setItem('apd_api_key',apiKey.trim());localStorage.setItem('apd_model',model.trim());status('LLM 诊断配置已保存');}
function llmSettings(){const apiBase=(document.getElementById('diagApiBase')||{}).value||localStorage.getItem('apd_api_base')||'';const apiKey=(document.getElementById('diagApiKey')||{}).value||localStorage.getItem('apd_api_key')||'';const model=(document.getElementById('diagModel')||{}).value||localStorage.getItem('apd_model')||'';return {api_base:String(apiBase).trim(),api_key:String(apiKey).trim(),model:String(model).trim(),timeout:Number(localStorage.getItem('apd_timeout')||120),max_tokens:Number(localStorage.getItem('apd_max_tokens')||3000),response_format:localStorage.getItem('apd_response_format')||'0'};}
let currentLlmDiagnosisText='';
let currentLlmDiagnosisTurnIndex=-1;
function mdInline(text){return esc(text).replace(/`([^`]+)`/g,'<code>$1</code>').replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>');}
function renderMarkdownForModal(md){const lines=String(md||'').split(/\n/);let html='',inCode=false,code=[];for(const line of lines){if(line.trim().startsWith('```')){if(inCode){html+='<pre><code>'+esc(code.join('\n'))+'</code></pre>';code=[];inCode=false;}else inCode=true;continue;}if(inCode){code.push(line);continue;}if(/^###\s+/.test(line))html+='<h3>'+mdInline(line.replace(/^###\s+/,''))+'</h3>';else if(/^##\s+/.test(line))html+='<h2>'+mdInline(line.replace(/^##\s+/,''))+'</h2>';else if(/^#\s+/.test(line))html+='<h1>'+mdInline(line.replace(/^#\s+/,''))+'</h1>';else if(/^[-*]\s+/.test(line))html+='<ul><li>'+mdInline(line.replace(/^[-*]\s+/,''))+'</li></ul>';else if(/^\d+\.\s+/.test(line))html+='<ol><li>'+mdInline(line.replace(/^\d+\.\s+/,''))+'</li></ol>';else if(line.trim())html+='<p>'+mdInline(line)+'</p>';else html+='<br/>';}if(inCode)html+='<pre><code>'+esc(code.join('\n'))+'</code></pre>';return html.replace(/<\/ul>\s*<ul>/g,'').replace(/<\/ol>\s*<ol>/g,'');}
function openLlmDiagnosisModal(markdown, turnIndex){currentLlmDiagnosisText=String(markdown||'');if(Number.isInteger(turnIndex))currentLlmDiagnosisTurnIndex=turnIndex;const modal=document.getElementById('llmDiagnosisModal');const body=document.getElementById('llmDiagnosisModalBody');if(body)body.innerHTML=renderMarkdownForModal(currentLlmDiagnosisText);if(modal)modal.classList.add('open');}
function closeLlmDiagnosisModal(event){if(event&&event.target!==document.getElementById('llmDiagnosisModal'))return;document.getElementById('llmDiagnosisModal')?.classList.remove('open');}
function copyCurrentLlmDiagnosis(){navigator.clipboard?.writeText(currentLlmDiagnosisText||'');status('已复制 LLM 诊断原文');}
function sendCurrentLlmDiagnosisFix(){const i=currentLlmDiagnosisTurnIndex>=0?currentLlmDiagnosisTurnIndex:selectedTurn;if(i<0){status('没有可修复的轮次');return;}sendFixToTtyd(i);}
function copyTextToClipboard(text){navigator.clipboard?.writeText(text||'');status('已复制文本');}
function renderLlmDiagnosisText(text, ok, error){const detail=error?`\n\n## 失败原因\n\n${error}`:'';currentLlmDiagnosisText=String(text||'无结果')+detail;return `<div class="llm-card ${ok?'primary':''}"><h4>${esc(ok?'LLM 诊断已完成':'LLM 诊断未成功')}</h4><p>${esc(ok?'完整 Markdown 诊断已在弹窗中打开。':'已生成离线说明和失败原因，点击下方按钮查看。')}</p><div class="quick-row" style="margin-top:8px"><button class="primary" onclick="openLlmDiagnosisModal(currentLlmDiagnosisText)">打开大屏诊断</button><button onclick="copyCurrentLlmDiagnosis()">复制原文</button></div></div>`;}
async function runLlmDiagnosis(i){const box=document.getElementById('llmDiagnosisBox');const turn=turns[i];if(!turn||!workspaceId)return;if(box)box.textContent='LLM 正在读取本轮白盒数据并诊断...';try{const res=await fetch(`/api/dev-studio/workspace/${encodeURIComponent(workspaceId)}/debug/llm-diagnose`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({turn,settings:llmSettings()})});const data=await res.json();if(box){box.innerHTML=renderLlmDiagnosisText(data.diagnosis||'无结果',!!data.ok,data.error||'');openLlmDiagnosisModal(currentLlmDiagnosisText,i);}status(data.ok?'LLM 诊断完成':'LLM 诊断未成功，已显示具体原因');}catch(e){if(box)box.textContent='LLM 诊断请求失败：'+(e.message||e);status('LLM 诊断请求失败');}}
function selectTurn(i){selectedTurn=i; renderDiag();}
async function sendTurn(){try{if(!workspaceId){status('没有拿到工作区，请从 Agent 开发台打开调试页');return;} const input=document.getElementById('message'); const message=input.value.trim(); if(!message){input.focus();return;} input.value=''; const pendingTurn={message,reply:'Agent 思考中...',data:{},diagnosis:null,repair_task:'',pending:true}; turns.push(pendingTurn); const pendingIndex=turns.length-1; selectedTurn=pendingIndex; renderChat(); renderDiag(); status('已发送，Agent 正在思考中...'); const context=scenarioContext(); const res=await fetch(`/api/dev-studio/workspace/${encodeURIComponent(workspaceId)}/debug/chat`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session_id:debugSessionId,message,context,scenario:{user_role:context.user_role,business_scenario:context.business_scenario,assets:context.assets},runtime_params:context.runtime_params,max_steps:8})}); const data=await res.json(); if(!res.ok) throw new Error(data.error||'调试失败'); const completedTurn={message,reply:replyOf(data),data,diagnosis:diagnosisOf(data),repair_task:data.repair_task||'',pending:false}; turns[pendingIndex]=completedTurn; selectedTurn=pendingIndex; renderChat(); renderDiag(); status('本轮完成：已生成回复、诊断和修复任务');}catch(e){const i=(typeof pendingIndex!=="undefined"?pendingIndex:selectedTurn); if(i>=0&&turns[i]&&turns[i].pending){turns[i]={...turns[i],pending:false,reply:'发送失败：'+(e.message||e),data:{error:String(e.message||e)},diagnosis:{summary:'发送失败，未拿到 Agent 运行结果。',levels:[{label:'运行错误',key:'runtime',file:'backend/app/runtime.py',reason:String(e.message||e),action:'检查服务、LLM 配置或后端日志。',confidence:.9}],suggested_files:['backend/app/runtime.py']}}; renderChat(); renderDiag();} status('发送失败：'+(e.message||e));}}

async function restartSession(){if(!workspaceId){status('没有拿到工作区');return;} status('正在重启当前 Agent...'); const res=await fetch(`/api/dev-studio/workspace/${encodeURIComponent(workspaceId)}/debug/restart`,{method:'POST'}); const data=await res.json(); if(!res.ok){status('重启失败：'+(data.error||''));return;} status('已重启，历史上下文保留：'+(((data.debug_context||{}).history_items)||0)+' 条');}
async function clearSession(){if(!workspaceId)return; if(!confirm('确定清空本次调试会话并重启 Agent？'))return; turns=[]; selectedTurn=-1; renderChat(); renderDiag(); const res=await fetch(`/api/dev-studio/workspace/${encodeURIComponent(workspaceId)}/debug/clear`,{method:'POST'}); const data=await res.json(); status(res.ok?'已清空会话并重启 Agent':'清空失败：'+(data.error||''));}
async function stopSession(){if(!workspaceId)return; await fetch(`/api/dev-studio/workspace/${encodeURIComponent(workspaceId)}/debug/stop`,{method:'POST'}); status('调试会话已停止');}
function buildFixTask(i){const t=turns[i]; if(!t)return''; if(t.repair_task)return t.repair_task; const d=t.diagnosis||diagnosisOf(t.data||{}); return `请修复当前 Agent 调试中暴露的问题。\n\n【用户本轮话术】\n${t.message}\n\n【Agent 当前回复】\n${t.reply}\n\n【历史上下文】\n${JSON.stringify(buildConversationHistory(),null,2)}\n\n【APD 诊断】\n${d.summary}\n\n【建议修改文件】\n${(d.suggested_files||[]).join('、')}\n\n【请你做】\n1. 阅读建议文件确认根因。\n2. 只做最小相关修改。\n3. 修改后说明如何验证。`;}
async function copyFixTask(i){const task=buildFixTask(i); if(!task){status('没有可复制的修复任务');return;} try{await navigator.clipboard.writeText(task); status('修复任务已复制');}catch(e){alert(task);}}
async function sendFixToTtyd(i){const task=buildFixTask(i); if(!task){status('没有可发送的修复任务');return;} const ttydSessionId=localStorage.getItem('apd_ttyd_session_id')||''; if(!ttydSessionId){await copyFixTask(i); status('未找到 ttyd，会先复制修复任务；请回开发台启动 CLI');return;} status('正在发送修复任务给 open_claude/Codex...'); try{const res=await fetch(`/api/dev-studio/ttyd/${encodeURIComponent(ttydSessionId)}/send`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:task,raw:false,columns:120,rows:32})}); const data=await res.json(); if(!res.ok||!data.sent)throw new Error(data.error||'发送失败'); status('已发送到 ttyd，请看开发台右侧终端');}catch(e){await copyFixTask(i); status('发送失败，已复制任务：'+(e.message||e));}}
async function saveTestCase(i){const t=turns[i]; if(!t||!workspaceId){status('没有可保存的样本');return;} const payload={message:t.message,assistant_message:t.reply,diagnosis:t.diagnosis||{},context:scenarioContext(),expected:'请在这里补充期望效果'}; const res=await fetch(`/api/dev-studio/workspace/${encodeURIComponent(workspaceId)}/debug/testcase`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}); const data=await res.json(); status(res.ok?'测试样本已保存：'+((data.testcase||{}).id||''):'保存失败：'+(data.error||''));}
async function saveLatestTestCase(){if(!turns.length){status('请先发送一轮对话');return;} await saveTestCase(turns.length-1);}
async function replayLatestTestCase(){if(!workspaceId){status('没有工作区');return;} status('正在查找最新测试样本...'); const listRes=await fetch(`/api/dev-studio/workspace/${encodeURIComponent(workspaceId)}/debug/testcases`); const listData=await listRes.json(); if(!listRes.ok){status('读取样本失败：'+(listData.error||''));return;} const cases=listData.testcases||[]; if(!cases.length){status('还没有保存过测试样本');return;} const latest=cases[cases.length-1]; status('正在回放：'+latest.id); const res=await fetch(`/api/dev-studio/workspace/${encodeURIComponent(workspaceId)}/debug/testcase/${encodeURIComponent(latest.id)}/replay`,{method:'POST'}); const data=await res.json(); if(!res.ok){status('回放失败：'+(data.error||''));return;} const turn={message:'[回放] '+(data.message||latest.message),reply:replyOf(data),data,diagnosis:diagnosisOf(data),repair_task:data.repair_task||''}; turns.push(turn); selectedTurn=turns.length-1; renderChat(); renderDiag(); status('回放完成：'+latest.id);}
function sendExampleFollowup(){const input=document.getElementById('message'); input.value = turns.length ? '请基于上一轮结果继续修改，保持同一个上下文。' : '请先根据当前场景生成一个初版结果。'; input.focus();}
function fillGenericScenario(){document.getElementById('businessScenario').value='模拟一个最终用户正在使用当前 Agent 完成一次普通任务。重点观察：Agent 是否理解目标、是否给出可执行结果、是否保持上下文。';document.getElementById('debugAssets').value=JSON.stringify({inputs:['一段用户提供的业务资料或对象'],files:[],artifacts:[]},null,2);document.getElementById('runtimeParams').value=JSON.stringify({language:'zh-CN',tone:'清晰、直接',mode:'normal'},null,2);}
function fillRiskScenario(){document.getElementById('businessScenario').value='模拟一个用户发起会改变状态、删除、覆盖、提交、导出或审批的高风险操作。重点观察：Agent 是否做权限判断、二次确认和风险提示。';document.getElementById('debugAssets').value=JSON.stringify({target_object:'待操作业务对象',current_state:'draft',risk_level:'high'},null,2);document.getElementById('runtimeParams').value=JSON.stringify({require_confirmation:true,permission_check:true,mode:'risk_control'},null,2);}
function fillWorkflowScenario(){document.getElementById('businessScenario').value='模拟一个需要多步骤完成的复杂任务。重点观察：Agent 是否拆步骤、调用工具、记录节点状态，并在中途缺信息时追问。';document.getElementById('debugAssets').value=JSON.stringify({task_inputs:['输入资料A','输入资料B'],available_tools:['检索','分析','生成','校验'],artifacts:[]},null,2);document.getElementById('runtimeParams').value=JSON.stringify({max_steps:5,workflow_required:true,mode:'multi_step'},null,2);}
if(!workspaceId){status('没有工作区：请从 Agent 开发台点击“打开调试页”');}
loadDiagLlmSettings();
renderChat();renderDiag();
</script>
</body>
</html>
"""


DELEGATED_INSPECTOR_HTML = r"""
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>Delegated Agent Inspector</title>
  <style>
    :root{--bg:#07111f;--panel:#0f172a;--panel2:#111827;--line:#26364e;--text:#e5e7eb;--muted:#94a3b8;--accent:#38bdf8;--ok:#22c55e;--warn:#f59e0b;--bad:#ef4444;--chip:#1e293b}
    *{box-sizing:border-box}html,body{height:100%;overflow:hidden}body{margin:0;background:linear-gradient(135deg,#020617,#0f172a);color:var(--text);font-family:Inter,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}button,input,textarea,select{font:inherit}button{border:1px solid #334155;background:#172033;color:#e5e7eb;border-radius:10px;padding:8px 11px;cursor:pointer}button.primary{background:#0ea5e9;border-color:#38bdf8;color:#00111f;font-weight:800}button.good{background:#14532d;border-color:#22c55e}button.danger{background:#451a1a;border-color:#ef4444}button:disabled{opacity:.55;cursor:not-allowed}
    header{height:60px;display:flex;align-items:center;justify-content:space-between;padding:0 16px;border-bottom:1px solid var(--line);background:rgba(2,6,23,.88)}h1{font-size:18px;margin:0}.sub{color:var(--muted);font-size:12px;margin-left:8px}.top-actions{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.status{font-size:12px;color:#bae6fd;max-width:520px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
    main{height:calc(100vh - 60px);display:grid;grid-template-columns:minmax(560px,1.12fr) minmax(520px,.88fr);gap:12px;padding:12px;overflow:hidden}.panel{min-height:0;border:1px solid var(--line);border-radius:18px;background:rgba(15,23,42,.96);display:flex;flex-direction:column;overflow:hidden}.panel-head{padding:12px 14px;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;align-items:center;gap:8px}.panel-head strong{color:#e0f2fe}.hint{color:var(--muted);font-size:12px}.body{padding:12px;overflow:auto;display:flex;flex-direction:column;gap:12px}.diag-panel{min-height:0;border-top:1px solid var(--line);border-radius:0;background:#07111f;display:flex;flex-direction:column;overflow:hidden}.diag-panel .panel-head{flex:0 0 auto;padding:10px 12px;background:#08111f}.diag-panel .body{flex:1;min-height:0;padding:10px;overflow:auto;display:flex;flex-direction:column;gap:10px}.engineering-panel{min-width:0}.engineering-stack{flex:1;min-height:0;display:grid;grid-template-rows:minmax(360px,62fr) minmax(220px,38fr);overflow:hidden}.terminal-zone{min-height:0;display:flex;flex-direction:column;overflow:hidden}.engineering-toolbar{padding:10px 12px;border-bottom:1px solid var(--line);display:flex;gap:8px;flex-wrap:wrap;background:#08111f}.ttyd-status{color:#93c5fd;font-size:12px;padding:9px 12px;border-bottom:1px solid var(--line);background:#06101f}.ttyd-frame{flex:1;width:100%;min-height:0;border:0;background:#020617;display:none}.engineering-settings{border-radius:0;border-width:1px 0 0 0;margin:0}.terminal-maximized .engineering-panel{position:fixed;inset:72px 12px 12px 12px;z-index:70;box-shadow:0 30px 90px rgba(0,0,0,.55)}.terminal-maximized .engineering-stack{grid-template-rows:minmax(560px,76fr) minmax(160px,24fr)}.terminal-maximized .ttyd-frame{min-height:0}.terminal-maximized .diag-panel .body{padding:8px}.drawer-mask{position:fixed;inset:0;display:none;background:rgba(2,6,23,.72);z-index:80;justify-content:flex-start}.drawer-mask.open{display:flex}.startup-drawer{width:min(460px,92vw);height:100vh;background:#0f172a;border-right:1px solid var(--line);box-shadow:20px 0 70px rgba(0,0,0,.45);display:flex;flex-direction:column}.drawer-head{padding:13px 14px;border-bottom:1px solid var(--line);display:flex;align-items:center;justify-content:space-between;gap:10px}
    .field label{display:block;color:#cbd5e1;font-size:12px;font-weight:800;margin-bottom:6px}.field textarea,.field input,.field select{width:100%;border:1px solid #334155;background:#020617;color:#e5e7eb;border-radius:12px;padding:9px;min-height:38px}.field textarea{min-height:72px;resize:vertical}.help{margin-top:6px;color:#94a3b8;font-size:12px;line-height:1.45}.quick-row{display:flex;gap:8px;flex-wrap:wrap}.empty{border:1px dashed #334155;border-radius:16px;padding:18px;color:#cbd5e1;background:rgba(15,23,42,.65)}
    .chat-panel{background:rgba(2,6,23,.48)}.chat-list{flex:1;overflow:auto;padding:16px;display:flex;flex-direction:column;gap:12px}.msg{display:flex;flex-direction:column;gap:6px;max-width:88%}.msg.user{align-self:flex-end}.msg.agent{align-self:flex-start}.bubble{border:1px solid #334155;border-radius:16px;padding:11px 13px;background:#111827;white-space:pre-wrap;line-height:1.55}.user .bubble{background:#1d4ed8;border-color:#60a5fa}.agent .bubble{background:#0f172a;border-color:#334155}.meta{font-size:12px;color:#94a3b8}.thinking{color:#bae6fd;background:rgba(14,165,233,.08);border:1px dashed #38bdf8;border-radius:12px;padding:10px;animation:pulseThinking 1.2s ease-in-out infinite}@keyframes pulseThinking{0%,100%{opacity:.72}50%{opacity:1}}
    .chat-input{border-top:1px solid var(--line);padding:12px;display:grid;grid-template-columns:1fr auto;gap:10px;background:#08111f}.chat-input textarea{width:100%;min-height:78px;resize:vertical;border:1px solid #334155;border-radius:14px;background:#020617;color:#e5e7eb;padding:11px}.send-col{display:flex;flex-direction:column;gap:8px}
    .card{border:1px solid #26364e;border-radius:14px;background:#08111f;padding:10px}.card h3,.card h4{margin:0 0 8px;color:#e0f2fe}.small{font-size:12px;color:#94a3b8}.chips{display:flex;gap:6px;flex-wrap:wrap;margin:7px 0}.chip{border:1px solid #334155;border-radius:999px;padding:4px 8px;background:#0f172a;color:#cbd5e1;font-size:12px}.chip.ok{border-color:#22c55e;color:#bbf7d0}.chip.warn{border-color:#f59e0b;color:#fde68a}.chip.bad{border-color:#ef4444;color:#fecaca}.summary-grid{display:grid;gap:7px}.summary-row{display:grid;grid-template-columns:92px 1fr;gap:8px;align-items:start;font-size:13px}.summary-row b{color:#93c5fd}.summary-status{display:inline-flex;width:max-content;border-radius:999px;padding:3px 8px;font-size:12px;border:1px solid #334155}.summary-status.ok{border-color:#22c55e;color:#bbf7d0}.summary-status.warn{border-color:#f59e0b;color:#fde68a}.summary-status.bad{border-color:#ef4444;color:#fecaca}
    details{border:1px solid #26364e;border-radius:12px;padding:9px;background:#020617}summary{cursor:pointer;color:#93c5fd;font-weight:800}pre{white-space:pre-wrap;overflow:auto;max-height:300px;font-size:12px;line-height:1.45;background:#020617;border:1px solid #26364e;border-radius:12px;padding:10px}.md-body{line-height:1.72}.md-body h1,.md-body h2,.md-body h3{color:#e0f2fe;margin:12px 0 8px}.md-body p{margin:8px 0}.md-body code{background:#020617;border:1px solid #26364e;border-radius:5px;padding:1px 4px;color:#bae6fd}.file{font-size:12px;border:1px solid #1d4ed8;color:#bfdbfe;background:#0f172a;border-radius:999px;padding:4px 8px}
    @media(max-width:1180px){html,body{overflow:auto}main{grid-template-columns:1fr;height:auto}.panel{min-height:420px}.chat-panel{min-height:620px}}
  </style>
</head>
<body>
<header>
  <div><h1>Delegated Agent Inspector <span class="sub">委托执行型 Agent 在线调试台</span></h1></div>
  <div class="top-actions">
    <button onclick="openStartupDrawer()" class="primary">启动配置</button>
    <button onclick="restartRuntime()">重启当前 Agent</button>
    <button onclick="loadRuntimeConfig()">运行前检查</button>
    <button onclick="stopRuntime()">停止</button>
    <button onclick="location.href='/'">返回 APD</button>
    <span id="pageStatus" class="status"></span>
  </div>
</header>
<main>
  <section class="panel chat-panel runtime-chat-panel">
    <div class="panel-head"><strong>真实 Agent 对话</strong><span id="sessionInfo" class="hint">等待启动</span><div class="quick-row"><button onclick="restartRuntime()">重启当前 Agent</button><button onclick="clearDelegatedSession()">清空会话</button><button onclick="stopDelegatedSession()" class="danger">停止会话</button></div></div>
    <div id="chatList" class="chat-list"><div class="empty">这里是最终用户视角。启动后直接输入任务，例如“请在 artifacts/report.md 写一段 hello delegated agent，并生成 result.json”。</div></div>
    <div class="chat-input">
      <textarea id="message" placeholder="像最终用户一样输入任务"></textarea>
      <div class="send-col"><button class="primary" onclick="sendMessage()">发送</button><button onclick="fillHello()">验收示例</button></div>
    </div>
  </section>
  <aside class="panel engineering-panel">
    <div class="panel-head"><strong>工程开发终端</strong><span id="engineeringWorkspaceInfo" class="hint">等待工作区</span></div>
    <div class="engineering-stack">
      <section class="terminal-zone">
        <div class="engineering-toolbar">
          <button onclick="ensureEngineeringWorkspace()" class="primary">创建/选择工作区</button>
          <button onclick="startEngineeringTtyd()" class="primary">启动 open_claude</button>
          <button onclick="toggleEngineeringMaximize()" id="terminalSizeBtn">放大终端</button>
          <button onclick="sendDelegatedFixToAi()">发送修复任务</button>
          <button onclick="stopEngineeringTtyd()">停止终端</button>
          <button onclick="cleanupEngineeringTtyd()" class="danger">清理旧终端</button>
        </div>
        <div id="engineeringStatus" class="ttyd-status">这里是工程开发侧。真实调试台发现问题后，修复任务会发送到这个 open_claude 终端。</div>
        <iframe id="engineeringTtydFrame" class="ttyd-frame" title="工程开发 ttyd 终端"></iframe>
      </section>
      <section class="diag-panel">
        <div class="panel-head"><strong>过程与诊断</strong><span class="hint">Job / 白盒 / 修复</span></div>
        <div id="diagBody" class="body"><div class="empty">发送一轮任务后，这里会显示 Job 状态、Events、Artifacts、Result、report.md 和白盒诊断。</div></div>
      </section>
    </div>
    <details class="engineering-settings">
      <summary>工程终端设置</summary>
      <div class="field"><label>Runner</label><select id="engRunner"><option value="open_claude">open_claude</option><option value="codex">codex</option><option value="claude">claude</option></select></div>
      <div class="field"><label>API Base</label><input id="engBaseUrl" placeholder="例如：http://192.168.1.156:18888/v1" /></div>
      <div class="field"><label>API Key</label><input id="engApiKey" type="password" placeholder="sk-..." /></div>
      <div class="field"><label>Model</label><input id="engModel" placeholder="例如：gpt-5.5" /></div>
      <div class="field"><label>CLI Root</label><input id="engCliRoot" placeholder="/home/data/rag/open_claude/Openclaude-openclaude" /></div>
      <div class="field"><label>启动提示词</label><textarea id="engInitialPrompt">请先阅读当前项目结构，告诉我这个 Agent 工程的主要文件分别负责什么。先不要修改代码。</textarea></div>
      <div class="quick-row"><button onclick="saveEngineeringSettings()">保存设置</button><button onclick="loadEngineeringSettings()">读取设置</button></div>
    </details>
  </aside>
</main>
<div id="startupDrawerMask" class="drawer-mask" onclick="closeStartupDrawer(event)">
  <aside class="startup-drawer" onclick="event.stopPropagation()">
    <div class="drawer-head"><strong>启动与说明</strong><div class="quick-row"><button onclick="startRuntime()" class="primary">生成并启动</button><button onclick="closeStartupDrawer()">关闭</button></div></div>
    <div class="body">
      <div class="card">
        <h3>怎么用</h3>
        <div class="small">
          1. 默认使用真实 open_claude，点击“生成并启动”。<br/>
          2. 左侧像最终用户一样和真实 Agent 对话。<br/>
          3. 右侧上方是工程开发终端，下方是过程与诊断。<br/>
          4. 如果终端太小，点“放大终端”在当前页面内放大查看。<br/>
          5. 改完代码后，点“重启当前 Agent”验证是否生效。
        </div>
      </div>
      <div class="field"><label>项目名</label><input id="projectName" placeholder="delegated-agent-demo" /></div>
      <div class="field"><label>运行模式</label><select id="fakeRunner"><option value="0">真实 open_claude：调用模型和执行器</option><option value="1">fake runner：只验证链路</option></select><div class="help">默认使用真实 open_claude，更接近生产体验。需要快速验证链路时再切到 fake runner。</div></div>
      <div class="field"><label>Agent 目标</label><textarea id="agentGoal" placeholder="留空自动使用当前协议摘要"></textarea></div>
      <div class="field"><label>默认任务说明</label><textarea id="defaultTask">请根据用户输入完成任务，并把最终结果写入 artifacts/report.md 和 artifacts/result.json。</textarea></div>
      <div class="field"><label>open_claude 路径</label><input id="openClaudeSource" value="/home/data/rag/open_claude/Openclaude-openclaude" /></div>
      <details>
        <summary>LLM 诊断配置</summary>
        <div class="small" style="margin:8px 0">这里用于“LLM 诊断本轮”，不影响 open_claude 自己使用的模型配置。</div>
        <div class="field"><label>API Base</label><input id="diagApiBase" placeholder="例如：http://192.168.1.156:18888/v1" /></div>
        <div class="field"><label>API Key</label><input id="diagApiKey" type="password" placeholder="sk-..." /></div>
        <div class="field"><label>Model</label><input id="diagModel" placeholder="例如：gpt-5.5" /></div>
        <div class="quick-row"><button onclick="saveDiagSettings()">保存诊断配置</button><button onclick="loadDiagSettings()">读取配置</button></div>
      </details>
      <div class="quick-row"><button onclick="startRuntime()" class="primary">生成并启动</button><button onclick="refreshRuntimes()">刷新运行实例</button></div>
      <div id="runtimeList" class="card"><div class="small">暂无运行实例。</div></div>
      <details><summary>真实 open_claude 模式需要什么</summary><div class="small" style="margin-top:8px">生成工程会读取 APD 服务进程环境里的模型配置。真实模式依赖 Node、open_claude 的 dist/cli.js、模型网关、Key、模型名，并可能遇到首次信任目录确认。</div></details>
    </div>
  </aside>
</div>
<script>
const params = new URLSearchParams(location.search);
let sessionId = params.get('session_id') || localStorage.getItem('apd_session_id') || '';
let currentDelegatedId = localStorage.getItem('apd_delegated_id') || '';
let currentJobId = '';
let pollTimer = null;
let turns = [];
let currentJobSnapshot = null;
let currentLlmDiagnosisText = '';
function esc(s){return String(s??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));}
function status(msg){document.getElementById('pageStatus').textContent=msg||'';}
function md(text){let html=esc(text||'');html=html.replace(/^### (.*)$/gm,'<h3>$1</h3>').replace(/^## (.*)$/gm,'<h2>$1</h2>').replace(/^# (.*)$/gm,'<h1>$1</h1>').replace(/\*\*(.*?)\*\*/g,'<strong>$1</strong>').replace(/`([^`]+)`/g,'<code>$1</code>').replace(/\n/g,'<br/>');return html;}
function safeJson(value){try{return JSON.stringify(value,null,2)}catch(e){return String(value)}}
function loadDiagSettings(){diagApiBase.value=localStorage.getItem('apd_api_base')||'';diagApiKey.value=localStorage.getItem('apd_api_key')||'';diagModel.value=localStorage.getItem('apd_model')||'';}
function saveDiagSettings(){localStorage.setItem('apd_api_base',(diagApiBase.value||'').trim());if((diagApiKey.value||'').trim())localStorage.setItem('apd_api_key',diagApiKey.value.trim());localStorage.setItem('apd_model',(diagModel.value||'').trim());status('LLM 诊断配置已保存');}
function llmSettings(){return {api_base:localStorage.getItem('apd_api_base')||'',api_key:localStorage.getItem('apd_api_key')||'',model:localStorage.getItem('apd_model')||''};}

function openStartupDrawer(){document.getElementById('startupDrawerMask').classList.add('open');refreshRuntimes();}
function closeStartupDrawer(event){if(event&&event.target!==document.getElementById('startupDrawerMask'))return;document.getElementById('startupDrawerMask').classList.remove('open');}
function toggleEngineeringMaximize(){const on=document.body.classList.toggle('terminal-maximized');const btn=document.getElementById('terminalSizeBtn');if(btn)btn.textContent=on?'缩小终端':'放大终端';status(on?'终端已放大':'终端已还原');}
let engineeringWorkspaceId=localStorage.getItem('apd_dev_workspace_id')||'';
let engineeringTtydSessionId=localStorage.getItem('apd_ttyd_session_id')||'';
function engEl(id){return document.getElementById(id);}
function engValue(id){const el=engEl(id);return el&&typeof el.value==='string'?el.value:'';}
function setEngValue(id,value){const el=engEl(id);if(el)el.value=value||'';}
function loadEngineeringSettings(){setEngValue('engRunner',localStorage.getItem('apd_cli_runner')||localStorage.getItem('apd_developer_runner')||'open_claude');setEngValue('engBaseUrl',localStorage.getItem('apd_cli_base_url')||localStorage.getItem('apd_developer_base_url')||'');setEngValue('engApiKey',localStorage.getItem('apd_cli_api_key')||localStorage.getItem('apd_developer_api_key')||'');setEngValue('engModel',localStorage.getItem('apd_cli_model')||localStorage.getItem('apd_developer_model')||'');setEngValue('engCliRoot',localStorage.getItem('apd_cli_root')||localStorage.getItem('apd_developer_cli_root')||'/home/data/rag/open_claude/Openclaude-openclaude');setEngValue('engInitialPrompt',localStorage.getItem('apd_cli_initial_prompt')||'请先阅读当前项目结构，告诉我这个 Agent 工程的主要文件分别负责什么。先不要修改代码。');updateEngineeringWorkspaceInfo();}
function saveEngineeringSettings(){localStorage.setItem('apd_cli_runner',engValue('engRunner')||'open_claude');localStorage.setItem('apd_cli_base_url',engValue('engBaseUrl').trim());localStorage.setItem('apd_cli_api_key',engValue('engApiKey').trim());localStorage.setItem('apd_cli_model',engValue('engModel').trim());localStorage.setItem('apd_cli_root',engValue('engCliRoot').trim());localStorage.setItem('apd_cli_initial_prompt',engValue('engInitialPrompt').trim());status('工程终端设置已保存');}
function updateEngineeringWorkspaceInfo(){const el=document.getElementById('engineeringWorkspaceInfo');if(el)el.textContent=engineeringWorkspaceId?'工作区：'+engineeringWorkspaceId:'等待工作区';}
async function fetchJson(url,options={}){const res=await fetch(url,options);let data={};try{data=await res.json();}catch(e){data={error:'接口没有返回 JSON'};}return {res,data};}
async function validateEngineeringWorkspace(wid){if(!wid)return false;try{const {res}=await fetchJson(`/api/dev-studio/workspace/${encodeURIComponent(wid)}`);return res.ok;}catch(e){return false;}}
async function ensureEngineeringWorkspace(){
  const statusEl=document.getElementById('engineeringStatus');
  if(engineeringWorkspaceId&&await validateEngineeringWorkspace(engineeringWorkspaceId)){updateEngineeringWorkspaceInfo();return engineeringWorkspaceId;}
  if(engineeringWorkspaceId){localStorage.removeItem('apd_dev_workspace_id');engineeringWorkspaceId='';}
  if(statusEl)statusEl.textContent='正在准备工程工作区...';
  const url=sessionId?`/api/dev-studio/workspaces?session_id=${encodeURIComponent(sessionId)}`:'/api/dev-studio/workspaces';
  const {res:listRes,data:listData}=await fetchJson(url);
  if(!listRes.ok)throw new Error(listData.error||'读取工作区列表失败');
  const list=listData.workspaces||[];
  if(list.length){engineeringWorkspaceId=list[0].workspace_id||'';}
  else{
    const {res:createRes,data:created}=await fetchJson('/api/dev-studio/workspace',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session_id:sessionId,name:localStorage.getItem('apd_delegated_project_name')||'delegated-agent-workspace'})});
    if(!createRes.ok)throw new Error(created.error||'创建工作区失败');
    engineeringWorkspaceId=created.workspace_id||'';
  }
  if(!engineeringWorkspaceId)throw new Error('没有拿到工程工作区 ID');
  localStorage.setItem('apd_dev_workspace_id',engineeringWorkspaceId);
  updateEngineeringWorkspaceInfo();
  return engineeringWorkspaceId;
}
function showEngineeringFrame(url){
  const frame=document.getElementById('engineeringTtydFrame');
  if(!frame)return;
  frame.style.display='block';
  frame.src='about:blank';
  setTimeout(()=>{frame.src=url;},40);
}
async function startEngineeringTtyd(){
  const statusEl=document.getElementById('engineeringStatus');
  try{
    saveEngineeringSettings();
    if(statusEl)statusEl.textContent='正在启动工程开发终端：准备工作区、启动 ttyd、挂载 open_claude...';
    const wid=await ensureEngineeringWorkspace();
    if(!wid)throw new Error('没有可用工程工作区');
    const {res,data}=await fetchJson(`/api/dev-studio/workspace/${encodeURIComponent(wid)}/ttyd/start`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({runner:engValue('engRunner')||'open_claude',base_url:engValue('engBaseUrl').trim(),api_key:engValue('engApiKey').trim(),model:engValue('engModel').trim(),cli_root:engValue('engCliRoot').trim(),work_dir:'',command_template:'',initial_prompt:engValue('engInitialPrompt').trim()})});
    if(!res.ok)throw new Error(data.error||'ttyd 启动失败');
    engineeringTtydSessionId=data.session_id||'';
    localStorage.setItem('apd_ttyd_session_id',engineeringTtydSessionId);
    showEngineeringFrame(data.url);
    if(statusEl)statusEl.innerHTML=`工程终端已启动。若右侧没有画面，点击备用入口：<a href="${esc(data.url)}" target="_blank" rel="noopener">新窗口打开 ttyd</a>`;
    status('工程开发终端已启动');
  }catch(e){
    const msg=e.message||String(e);
    if(statusEl)statusEl.innerHTML=`工程终端启动失败：${esc(msg)}<br/><span class="hint">已自动清理旧工作区缓存。请再点一次“启动 open_claude”；如果仍失败，检查 ttyd/open_claude 路径。</span>`;
    localStorage.removeItem('apd_ttyd_session_id');
    status('工程终端启动失败：'+msg);
  }
}
async function stopEngineeringTtyd(){const frame=document.getElementById('engineeringTtydFrame');if(!engineeringTtydSessionId){localStorage.removeItem('apd_ttyd_session_id');if(frame){frame.src='about:blank';frame.style.display='none';}status('没有运行中的工程终端');return;}try{await fetch(`/api/dev-studio/ttyd/${encodeURIComponent(engineeringTtydSessionId)}/stop`,{method:'POST'});}catch(e){}engineeringTtydSessionId='';localStorage.removeItem('apd_ttyd_session_id');if(frame){frame.src='about:blank';frame.style.display='none';}const statusEl=document.getElementById('engineeringStatus');if(statusEl)statusEl.textContent='工程终端已停止。';status('工程终端已停止');}
async function cleanupEngineeringTtyd(){const statusEl=document.getElementById('engineeringStatus');const frame=document.getElementById('engineeringTtydFrame');if(!confirm('确定清理 APD 创建的旧 ttyd/tmux 终端？这不会停止左侧真实 Agent，只会关闭工程开发终端。'))return;try{if(statusEl)statusEl.textContent='正在清理旧终端...';const res=await fetch('/api/dev-studio/ttyd/cleanup',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({all_apd:true})});const data=await res.json();if(!res.ok)throw new Error(data.error||'清理失败');engineeringTtydSessionId='';localStorage.removeItem('apd_ttyd_session_id');if(frame){frame.src='about:blank';frame.style.display='none';}const killedTtyd=(data.killed_ttyd_pids||[]).length;const killedTmux=(data.killed_tmux_sessions||[]).length;const stopped=(data.stopped_sessions||[]).length;if(statusEl)statusEl.textContent=`旧终端已清理：停止会话 ${stopped} 个，ttyd 进程 ${killedTtyd} 个，tmux 会话 ${killedTmux} 个。`;status('旧工程终端已清理');}catch(e){if(statusEl)statusEl.textContent='清理旧终端失败：'+(e.message||e);status('清理旧终端失败：'+(e.message||e));}}
function renderRuntimeCard(item){return `<div class="card"><h4>${esc(item.project_name||item.delegated_id)} <span class="chip ${item.status==='running'?'ok':'warn'}">${esc(item.status||'-')}</span></h4><div class="small">ID：${esc(item.delegated_id||'')}<br/>端口：${esc(String(item.port||'-'))} · PID：${esc(String(item.pid||'-'))}<br/>内部地址：${esc(item.base_url||'')}</div><div class="quick-row" style="margin-top:8px"><button onclick="selectRuntime('${esc(item.delegated_id||'')}')">选择</button><button onclick="stopRuntime('${esc(item.delegated_id||'')}')">停止</button></div></div>`}
async function fetchRuntimes(){const res=await fetch('/api/delegated-playground/list');const data=await res.json();if(!res.ok)throw new Error(data.error||'运行实例列表加载失败');return data.items||[];}
function clearCurrentRuntime(messageText='没有运行实例'){currentDelegatedId='';localStorage.removeItem('apd_delegated_id');document.getElementById('sessionInfo').textContent=messageText;}
async function ensureCurrentRuntime({autoSelect=true}={}){const items=await fetchRuntimes();const exists=items.find(x=>x.delegated_id===currentDelegatedId);if(exists)return exists;if(currentDelegatedId){clearCurrentRuntime('旧运行实例已失效');}if(autoSelect&&items[0]){selectRuntime(items[0].delegated_id,false);return items[0];}return null;}
async function refreshRuntimes(){try{const items=await fetchRuntimes();if(currentDelegatedId&&!items.some(x=>x.delegated_id===currentDelegatedId)){clearCurrentRuntime('旧运行实例已失效');}if(!currentDelegatedId&&items[0])selectRuntime(items[0].delegated_id,false);runtimeList.innerHTML=items.length?items.map(renderRuntimeCard).join(''):'<div class="small">暂无运行实例。点击“生成并启动”。</div>';}catch(e){runtimeList.innerHTML='<div class="small">加载失败：'+esc(e.message||e)+'</div>';}}
function selectRuntime(id,notify=true){currentDelegatedId=id;localStorage.setItem('apd_delegated_id',id);if(notify)status('已选择运行实例：'+id);document.getElementById('sessionInfo').textContent='运行实例：'+id;loadRuntimeConfig();}
async function startRuntime(){if(!sessionId){status('没有 session_id，请从 APD 主页面导出产物区打开本页');return;}localStorage.setItem('apd_delegated_project_name',projectName.value.trim());localStorage.setItem('apd_delegated_agent_goal',agentGoal.value.trim());localStorage.setItem('apd_delegated_default_task',defaultTask.value.trim());localStorage.setItem('apd_delegated_open_claude',openClaudeSource.value.trim());localStorage.setItem('apd_delegated_fake_runner',fakeRunner.value);status('正在生成并启动 Delegated Agent...');diagBody.innerHTML='<div class="card"><h3>正在启动</h3><div class="small">正在生成临时工程、复制 open_claude、启动 FastAPI。首次启动可能需要十几秒。</div></div>';try{const res=await fetch('/api/delegated-playground/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session_id:sessionId,project_name:projectName.value.trim(),agent_goal:agentGoal.value.trim(),default_task:defaultTask.value.trim(),open_claude_source:openClaudeSource.value.trim()||'/home/data/rag/open_claude/Openclaude-openclaude',fake_runner:fakeRunner.value!=='0'})});const data=await res.json();if(!res.ok)throw new Error(data.error||'启动失败');selectRuntime(data.delegated_id,false);diagBody.innerHTML=section('启动成功','现在可以在中间对话框发送任务。',data);await refreshRuntimes();status('Delegated Agent 已启动');}catch(e){diagBody.innerHTML='<div class="card"><h3>启动失败</h3><pre>'+esc(e.message||e)+'</pre><div class="small">优先检查 open_claude 路径和 dist/cli.js。</div></div>';status('启动失败：'+(e.message||e));}}
async function stopRuntime(id){id=id||currentDelegatedId;if(!id){status('没有可停止实例');return;}try{await ensureCurrentRuntime({autoSelect:false});if(!currentDelegatedId&&id){status('运行实例已不存在');await refreshRuntimes();return;}const res=await fetch(`/api/delegated-playground/${encodeURIComponent(id)}/stop`,{method:'POST'});const data=await res.json();if(!res.ok)throw new Error(data.error||'停止失败');if(currentDelegatedId===id){currentDelegatedId='';localStorage.removeItem('apd_delegated_id');}diagBody.innerHTML=section('已停止','临时服务进程已停止。',data);await refreshRuntimes();status('已停止');}catch(e){status('停止失败：'+(e.message||e));}}
async function restartRuntime(){if(pollTimer){clearInterval(pollTimer);pollTimer=null;}status('正在检查当前 Agent 实例...');try{const item=await ensureCurrentRuntime({autoSelect:true});if(!item){diagBody.innerHTML='<div class="card"><h3>没有可重启实例</h3><div class="small">当前页面保存的是旧实例 ID，但后端运行实例已经不存在。请点击“启动配置”里的“生成并启动”。</div></div>';status('没有可重启实例，请重新生成并启动');return;}status('正在重启当前 Agent 服务...');diagBody.innerHTML='<div class="card"><h3>正在重启当前 Agent</h3><div class="small">这会重启当前工作区的 FastAPI 进程，让工程开发终端刚修改的代码生效；不会重新生成工程。</div></div>';const res=await fetch(`/api/delegated-playground/${encodeURIComponent(currentDelegatedId)}/restart`,{method:'POST'});const data=await res.json();if(!res.ok){if(res.status===404){clearCurrentRuntime('旧运行实例已失效');await refreshRuntimes();throw new Error('当前运行实例已失效，请重新生成并启动');}throw new Error(data.error||'重启失败');}currentJobId='';currentJobSnapshot=null;currentLlmDiagnosisText='';turns=[];renderChat();diagBody.innerHTML=section('重启完成','当前 Agent 服务已重新加载代码。请重新发送测试话术验证改动是否生效。',data);await refreshRuntimes();document.getElementById('sessionInfo').textContent='运行实例：'+currentDelegatedId+' · 已重启';status('当前 Agent 已重启，代码改动已重新加载');}catch(e){diagBody.innerHTML='<div class="card"><h3>重启失败</h3><pre>'+esc(e.message||e)+'</pre><div class="small">如果提示实例失效，不是代码重启失败，而是 APD 页面保存了旧运行实例。重新“生成并启动”即可恢复。</div></div>';status('重启失败：'+(e.message||e));}}
async function loadRuntimeConfig(){if(!currentDelegatedId)return;try{const res=await fetch(`/api/delegated-playground/${encodeURIComponent(currentDelegatedId)}/config`);const data=await res.json();if(!res.ok)throw new Error(data.error||'配置读取失败');diagBody.innerHTML=renderConfig(data);status('运行前检查完成');}catch(e){diagBody.innerHTML='<div class="card"><h3>运行前检查失败</h3><pre>'+esc(e.message||e)+'</pre></div>';}}
function renderConfig(data){const r=data.runtime||{};return `<div class="card"><h3>运行前检查</h3><div class="chips"><span class="chip ${r.fake_runner?'ok':'warn'}">${r.fake_runner?'fake runner':'真实 runner'}</span><span class="chip ${r.node_available?'ok':'bad'}">Node ${r.node_available?'可用':'不可用'}</span><span class="chip ${r.open_claude_cli_exists?'ok':'bad'}">CLI ${r.open_claude_cli_exists?'存在':'不存在'}</span><span class="chip ${r.model_configured?'ok':'warn'}">模型 ${r.model_configured?'已配置':'未配置'}</span></div><div class="small">Agent：${esc(data.agent_name||'')}<br/>目标：${esc(data.agent_goal||'')}</div></div>${section('完整配置','这里来自生成工程的 /api/config。',data)}`;}
function fillHello(){message.value='请在 artifacts/report.md 写一段“hello delegated agent”，并生成 artifacts/result.json。';}
function clearDelegatedSession(){if(pollTimer){clearInterval(pollTimer);pollTimer=null;}turns=[];currentJobId='';currentJobSnapshot=null;currentLlmDiagnosisText='';renderChat();diagBody.innerHTML='<div class="empty">会话已清空。Delegated Agent 实例仍在运行，可以继续发送新任务。</div>';status('已清空当前页面会话和当前 Job 状态');}
async function stopDelegatedSession(){if(!currentDelegatedId){clearDelegatedSession();status('没有运行中的 Delegated Agent，会话已清空');return;}if(!confirm('确定停止当前 Delegated Agent 实例？停止后需要重新“生成并启动”才能继续对话。'))return;if(pollTimer){clearInterval(pollTimer);pollTimer=null;}const stoppedId=currentDelegatedId;await stopRuntime(stoppedId);turns=[];currentJobId='';currentJobSnapshot=null;currentLlmDiagnosisText='';renderChat();document.getElementById('sessionInfo').textContent='已停止';}
async function sendMessage(){try{await ensureCurrentRuntime({autoSelect:true});}catch(e){status('运行实例检查失败：'+(e.message||e));return;}if(!currentDelegatedId){status('请先生成并启动');return;}const text=message.value.trim();if(!text){message.focus();return;}message.value='';turns.push({role:'user',text});turns.push({role:'agent',text:'Agent 思考中... 正在创建 Job 并执行 Runner',pending:true});renderChat();diagBody.innerHTML='<div class="card"><h3>任务已发送</h3><div class="thinking">等待 Job 创建...</div></div>';try{const res=await fetch(`/api/delegated-playground/${encodeURIComponent(currentDelegatedId)}/chat`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:text})});const data=await res.json();if(!res.ok)throw new Error(data.error||'发送失败');currentJobId=data.job_id;turns[turns.length-1]={role:'agent',text:`任务已创建：${currentJobId}\\n状态：${data.status||'-'}`,pending:true};renderChat();startPolling();status('Job 已创建：'+currentJobId);}catch(e){turns[turns.length-1]={role:'agent',text:'发送失败：'+(e.message||e),pending:false,error:true};renderChat();diagBody.innerHTML='<div class="card"><h3>发送失败</h3><pre>'+esc(e.message||e)+'</pre></div>';status('发送失败');}}
function diagnosisSummary(d){d=d||{};const levels=d.levels||[];const p=d.primary_level||levels[0]||{};const status=p.key==='ok'?'基本正常':(Number(p.confidence||0)>=0.82?'明显需要修':'需要关注');const cls=p.key==='ok'?'ok':(Number(p.confidence||0)>=0.82?'bad':'warn');return {status,cls,layer:p.label||p.key||'未定位',file:p.file||((d.suggested_files||[])[0])||'暂未定位文件',reason:p.reason||d.summary||'需要结合右侧白盒继续判断。',action:p.action||'查看右侧白盒详情，再用同一句话复测。'};}
function renderDiagCard(t){if(t.pending)return `<div class="card"><h4>本轮状态</h4><div class="thinking">Agent 正在执行：创建 Job → 启动 runner → 读取 stdout/stderr → 收集产物。</div></div>`;const d=t.diagnosis||{};const s=diagnosisSummary(d);return `<div class="card"><h4>本轮诊断摘要</h4><div class="summary-grid"><div class="summary-row"><b>是否正常</b><span class="summary-status ${s.cls}">${esc(s.status)}</span></div><div class="summary-row"><b>最可能问题</b><span>${esc(s.layer)} · 建议看 ${esc(s.file)}</span></div><div class="summary-row"><b>原因</b><span>${esc(s.reason)}</span></div><div class="summary-row"><b>下一步</b><span>${esc(s.action)}</span></div></div><div class="chips">${(d.levels||[]).map(x=>`<span class="chip ${x.key==='ok'?'ok':(Number(x.confidence||0)>0.8?'bad':'warn')}">${esc(x.label||x.key)} · ${esc(String(Math.round(Number(x.confidence||0)*100)))}%</span>`).join('')}</div><div class="quick-row"><button class="primary" onclick="sendDelegatedFixToAi()">让 AI 修这个问题</button><button onclick="showCurrentWhitebox()">查看白盒详情</button><button onclick="runDelegatedLlmDiagnosis()">LLM 诊断本轮</button><button onclick="copyRepairTask()">复制修复任务</button></div></div>`;}
function renderChat(){chatList.innerHTML=turns.length?turns.map(t=>`<div class="msg ${t.role==='user'?'user':'agent'}"><div class="meta">${t.role==='user'?'你':'Agent'}${t.pending?' · 运行中':''}</div><div class="bubble ${t.pending?'thinking':'md-body'}">${t.role==='agent'&&!t.pending?md(t.text):esc(t.text)}</div>${t.role==='agent'?renderDiagCard(t):''}</div>`).join(''):'<div class="empty">启动后直接输入真实任务。</div>';chatList.scrollTop=chatList.scrollHeight;}
function startPolling(){if(pollTimer)clearInterval(pollTimer);pollTimer=setInterval(pollJob,1300);pollJob();}
function latestRunnerThinking(data){const thinking=data.thinking||{};if((thinking.text||'').trim())return String(thinking.text).trim();const events=data.events||[];const outputs=events.filter(e=>e.type==='runner_screen'||e.type==='runner_output');const lastOutput=outputs.slice(-1)[0];if(lastOutput){return ((lastOutput.data||{}).text||lastOutput.message||'').trim();}const logs=(data.logs||{}).stdout||'';if(logs.trim()){return logs.trim().split('\n').slice(-18).join('\n');}const lastEvent=events.slice(-1)[0]||{};return lastEvent.message||lastEvent.type||'等待 open_claude 输出';}
function currentAgentReply(data){const reply=data.agent_reply||{};return String(reply.text||'').trim();}
function compactRunnerStatus(data){const job=data.job||{},thinking=data.thinking||{};const events=data.events||[];const lastEvent=events.slice(-1)[0]||{};const statusText=job.summary||lastEvent.message||thinking.title||'正在执行任务';const meta=[];if(job.status)meta.push('状态：'+job.status);if(thinking.pid)meta.push('PID：'+thinking.pid);if(thinking.silence_seconds!==undefined&&thinking.silence_seconds!==null)meta.push('静默 '+thinking.silence_seconds+' 秒');return `正在处理：${statusText}${meta.length?'\n'+meta.join(' · '):''}\n详细 CLI 屏幕、stdout/stderr 和事件请看右侧“过程与诊断”。`;}
async function pollJob(){if(!currentDelegatedId||!currentJobId)return;try{const res=await fetch(`/api/delegated-playground/${encodeURIComponent(currentDelegatedId)}/job/${encodeURIComponent(currentJobId)}`);const data=await res.json();if(!res.ok)throw new Error(data.error||'读取 Job 失败');currentJobSnapshot=data;diagBody.innerHTML=renderJob(data);const job=data.job||{};const st=(job.status||'').toLowerCase();if(turns.length&&turns[turns.length-1].role==='agent'&&!['completed','failed','timeout'].includes(st)){const reply=currentAgentReply(data);const replyBlock=reply?`【Agent 正在回复】\n${reply}\n\n`:'';turns[turns.length-1]={role:'agent',text:`${replyBlock}Job：${job.job_id||currentJobId}\n状态：${job.status||'-'}\n摘要：${job.summary||''}\n\n${compactRunnerStatus(data)}`,pending:true,diagnosis:data.diagnosis||{},snapshot:data};renderChat();status('Job '+(job.status||'-')+'：'+(job.summary||''));}if(['completed','failed','timeout'].includes(st)){clearInterval(pollTimer);pollTimer=null;const report=currentAgentReply(data)||data.report||((job.result||{}).summary)||latestRunnerThinking(data)||st;turns[turns.length-1]={role:'agent',text:report,pending:false,error:st!=='completed',diagnosis:data.diagnosis||{},repair_task:data.repair_task||'',snapshot:data};renderChat();status('Job 结束：'+st);}}catch(e){diagBody.innerHTML='<div class="card"><h3>读取 Job 失败</h3><pre>'+esc(e.message||e)+'</pre></div>';}}
function eventTimeline(events){if(!events.length)return'<div class="small">暂无事件。</div>';return `<div class="summary-grid">${events.map(e=>{const type=e.type||'';const c=type.includes('failed')||type.includes('timeout')?'bad':(type.includes('completed')||type.includes('exit')?'ok':'warn');return `<div class="card"><div class="chips"><span class="chip ${c}">${esc(type)}</span><span class="chip">${esc((e.time||'').replace('T',' ').slice(0,19))}</span></div><div>${esc(e.message||'')}</div>${e.data?`<pre>${esc(safeJson(e.data))}</pre>`:''}</div>`}).join('')}</div>`;}
function renderOpenClaudeProcess(data){const events=data.events||[],logs=data.logs||{},thinking=data.thinking||{};const runnerEvents=events.filter(e=>String(e.type||'').startsWith('runner_'));const start=runnerEvents.find(e=>e.type==='runner_start')||{};const proc=runnerEvents.find(e=>e.type==='runner_process_started')||{};const outputs=runnerEvents.filter(e=>e.type==='runner_output'||e.type==='runner_screen').slice(-12);const heartbeats=runnerEvents.filter(e=>e.type==='runner_heartbeat').slice(-5);return `<div class="card"><h3>open_claude 执行过程</h3><div class="summary-grid"><div class="summary-row"><b>命令</b><span>${esc(((start.data||{}).command)||'未启动')}</span></div><div class="summary-row"><b>PID</b><span>${esc(String(thinking.pid||(proc.data||{}).pid||'-'))}</span></div><div class="summary-row"><b>工作目录</b><span>${esc(((start.data||{}).cwd)||'-')}</span></div><div class="summary-row"><b>超时</b><span>${esc(String(((start.data||{}).timeout_seconds)||'-'))} 秒</span></div></div><div class="card" style="margin-top:10px"><h4>当前深度思考 / CLI 屏幕</h4><div class="small">${esc(thinking.explain||'展示 open_claude 当前可见输出。')}</div><pre>${esc(latestRunnerThinking(data)||'等待 open_claude 输出...')}</pre></div>${heartbeats.length?`<details open><summary>运行心跳</summary>${eventTimeline(heartbeats)}</details>`:''}${outputs.length?`<details open><summary>最近输出事件</summary>${eventTimeline(outputs)}</details>`:'<div class="help">还没有捕获到 open_claude 输出。如果状态一直 running，可能是在模型请求、首次确认、或 CLI 无输出等待。</div>'}</div><details open><summary>stdout / stderr 原始日志</summary><h4>stdout.log</h4><pre>${esc(logs.stdout||'暂无 stdout 输出')}</pre><h4>stderr.log</h4><pre>${esc(logs.stderr||'暂无 stderr 输出')}</pre></details>`;}
function whiteboxLayer(title,desc,payload,open=false){return `<details ${open?'open':''}><summary>${esc(title)}</summary><div class="small" style="margin:8px 0">${esc(desc||'')}</div><pre>${esc(safeJson(payload))}</pre></details>`;}
function renderWhitebox(data){const wb=data.whitebox||{};return `<div class="card"><h3>白盒过程</h3><div class="small">这一块对应真实 Delegated Agent 链路：Task Pack → Runner/open_claude → stdout/stderr → Artifacts → Agent 回复 → Events。不是只看日志，而是把每层输入输出拆开看。</div></div>${whiteboxLayer('1. Task Pack / 本轮任务输入','APD 交给委托 Agent 的结构化任务包，决定 open_claude 到底要做什么。',wb.task_pack,true)}${whiteboxLayer('2. Runner / open_claude 启动','是否真的启动 open_claude、命令是什么、工作目录在哪、PID 和超时是多少。',wb.runner,true)}${whiteboxLayer('3. LLM 与 CLI 输出','open_claude 的 stdout/stderr、流式输出和当前屏幕，是判断卡住/无回复/模型失败的证据。',wb.llm_and_cli_output,true)}${whiteboxLayer('4. Agent 回复解析','最终给用户看的内容来自哪里：report.md、result.summary，还是 stdout 解析。',wb.agent_reply,true)}${whiteboxLayer('5. Artifacts / 产物协议','检查 report.md/result.json 是否按约定生成。',wb.artifacts,false)}${whiteboxLayer('6. Events / Trace','Job 状态流转和 runner 事件，用来复盘全过程。',wb.events,false)}`;}
function renderDiagnosisPanel(data){const d=data.diagnosis||{};const s=diagnosisSummary(d);return `<div class="card"><h3>诊断与修复</h3><div class="summary-grid"><div class="summary-row"><b>是否正常</b><span class="summary-status ${s.cls}">${esc(s.status)}</span></div><div class="summary-row"><b>优先看</b><span>${esc(s.layer)} · ${esc(s.file)}</span></div><div class="summary-row"><b>原因</b><span>${esc(s.reason)}</span></div><div class="summary-row"><b>建议</b><span>${esc(s.action)}</span></div></div><div class="chips">${(d.levels||[]).map(x=>`<span class="chip ${x.key==='ok'?'ok':(Number(x.confidence||0)>0.8?'bad':'warn')}">${esc(x.label||x.key)} · ${esc(String(Math.round(Number(x.confidence||0)*100)))}%</span>`).join('')}</div><div class="quick-row"><button class="primary" onclick="sendDelegatedFixToAi()">让 AI 修这个问题</button><button onclick="runDelegatedLlmDiagnosis()">LLM 诊断本轮</button><button onclick="copyRepairTask()">复制修复任务</button><button onclick="copyWhitebox()">复制白盒 JSON</button></div><div id="llmDiagnosisBox" class="small" style="margin-top:8px;white-space:pre-wrap">点击“让 AI 修这个问题”会发送到右侧工程开发终端；如果终端未启动，会复制修复任务并提示先启动 open_claude。</div></div>`;}
function renderJob(data){const job=data.job||{},events=data.events||[],arts=data.artifacts||[],result=job.result||{},report=data.report||'',reply=currentAgentReply(data);const cls=job.status==='completed'?'ok':(['failed','timeout'].includes(job.status)?'bad':'warn');const isRunning=job.status==='running';return `<div class="card"><h3>本轮结论</h3><div class="summary-grid"><div class="summary-row"><b>状态</b><span class="summary-status ${cls}">${esc(job.status||'-')}</span></div><div class="summary-row"><b>摘要</b><span>${esc(job.summary||result.summary||'')}</span></div><div class="summary-row"><b>Job</b><span>${esc(job.job_id||'')}</span></div><div class="summary-row"><b>Task Pack</b><span>${esc(job.task_pack_path||'-')}</span></div></div>${isRunning?'<div class="help">当前不是 queued，已经进入 running。若长时间不结束，通常是真实 open_claude 在执行、等待首次确认、模型网关卡住或没有输出。下面会显示 open_claude 命令、PID、心跳和输出。</div>':''}</div>${renderDiagnosisPanel(data)}<div class="card"><h3>Agent 回复</h3><div class="md-body">${md(reply||report||result.summary||'暂无回复文本，任务还没完成或正在工具调用。')}</div></div>${renderWhitebox(data)}${renderOpenClaudeProcess(data)}<div class="card"><h3>产物</h3><div class="chips">${arts.length?arts.map(a=>`<span class="file">${esc(a.name||'')} · ${esc(String(a.size||0))} bytes</span>`).join(''):'<span class="small">暂无产物</span>'}</div></div><details><summary>完整 Job / Result JSON</summary>${section('Job','生成工程 /api/jobs/{job_id} 返回。',job)}${section('Result','artifacts/result.json 解析结果。',result)}</details>`;}
function showCurrentWhitebox(){if(!currentJobSnapshot){status('还没有 Job 白盒数据');return;}diagBody.innerHTML=renderJob(currentJobSnapshot);status('已显示当前 Job 白盒详情');}
function copyText(text){navigator.clipboard?.writeText(String(text||''));}
function copyWhitebox(){if(!currentJobSnapshot){status('没有白盒数据可复制');return;}copyText(safeJson(currentJobSnapshot.whitebox||{}));status('已复制白盒 JSON');}
function copyRepairTask(){if(!currentJobSnapshot){status('没有修复任务可复制');return;}copyText(currentJobSnapshot.repair_task||'');status('已复制修复任务，可发给右侧工程开发终端里的 open_claude');}
async function sendDelegatedFixToAi(){if(!currentJobSnapshot){status('没有可修复的 Job，请先发送一轮任务');return;}const task=currentJobSnapshot.repair_task||'';if(!task){status('当前没有生成修复任务');return;}localStorage.setItem('apd_cli_collab_intent',task);localStorage.setItem('apd_collab_intent',task);localStorage.setItem('apd_developer_request',task);localStorage.setItem('apd_pm_guide_stage','debugged');const ttydSessionId=engineeringTtydSessionId||localStorage.getItem('apd_ttyd_session_id')||'';if(!ttydSessionId){copyText(task);status('未发现右侧工程开发终端：已复制修复任务，请先点“启动 open_claude”');return;}status('正在把修复任务发送给右侧工程开发终端...');try{const res=await fetch(`/api/dev-studio/ttyd/${encodeURIComponent(ttydSessionId)}/send`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:task,raw:false,columns:120,rows:32})});const data=await res.json();if(!res.ok||!data.sent)throw new Error(data.error||'发送失败');status('已发送给右侧工程开发终端，请查看 open_claude 执行结果');}catch(e){copyText(task);status('发送失败，已复制修复任务：'+(e.message||e));window.open('/?open_agent_ide=1','_blank');}}
async function runDelegatedLlmDiagnosis(){if(!currentDelegatedId||!currentJobId){status('没有当前 Job，无法诊断');return;}const box=document.getElementById('llmDiagnosisBox');if(box)box.textContent='LLM 正在读取当前 Job 白盒数据并诊断...';try{const res=await fetch(`/api/delegated-playground/${encodeURIComponent(currentDelegatedId)}/job/${encodeURIComponent(currentJobId)}/llm-diagnose`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({job_id:currentJobId,settings:llmSettings()})});const data=await res.json();currentLlmDiagnosisText=String(data.diagnosis||'无诊断内容')+(data.error?'\n\n失败原因：'+data.error:'');if(box)box.innerHTML=`<div class="md-body">${md(currentLlmDiagnosisText)}</div><div class="quick-row" style="margin-top:8px"><button onclick="copyText(currentLlmDiagnosisText)">复制诊断原文</button><button class="primary" onclick="copyRepairTask()">复制修复任务</button></div>`;status(data.ok?'LLM 诊断完成':'LLM 诊断未成功，已显示离线说明');}catch(e){if(box)box.textContent='LLM 诊断请求失败：'+(e.message||e);status('LLM 诊断请求失败');}}
function section(title,desc,data){return `<div class="card"><h3>${esc(title)}</h3><div class="small">${esc(desc||'')}</div><pre>${esc(safeJson(data))}</pre></div>`;}
(async function init(){projectName.value=localStorage.getItem('apd_delegated_project_name')||'delegated-agent-demo';fakeRunner.value=localStorage.getItem('apd_delegated_fake_runner')||'0';agentGoal.value=localStorage.getItem('apd_delegated_agent_goal')||'';defaultTask.value=localStorage.getItem('apd_delegated_default_task')||defaultTask.value;openClaudeSource.value=localStorage.getItem('apd_delegated_open_claude')||openClaudeSource.value;loadDiagSettings();loadEngineeringSettings();message.value='请在 artifacts/report.md 写一段“hello delegated agent”，并生成 artifacts/result.json。';if(!sessionId)status('没有 session_id：请从 APD 主页面“导出产物”打开本页');else status('已绑定 APD 会话：'+sessionId);await refreshRuntimes();if(currentDelegatedId)loadRuntimeConfig();})();
</script>
</body>
</html>
"""

LOGIN_HTML = r"""
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>登录 - Agent Protocol Designer</title>
  <style>
    body { margin:0; height:100vh; display:flex; align-items:center; justify-content:center; background:#0f172a; color:#e5e7eb; font-family:system-ui,-apple-system,"Segoe UI",sans-serif; }
    .box { width:min(380px, 92vw); background:#111827; border:1px solid #263244; border-radius:16px; padding:26px; box-shadow:0 24px 80px rgba(0,0,0,.35); }
    h1 { font-size:20px; margin:0 0 8px; }
    p { color:#94a3b8; margin:0 0 18px; font-size:13px; }
    input { width:100%; box-sizing:border-box; margin:8px 0; padding:11px; border-radius:10px; border:1px solid #263244; background:#0b1220; color:#e5e7eb; }
    button { width:100%; margin-top:12px; padding:11px; border-radius:10px; border:1px solid #0ea5e9; background:#0369a1; color:white; cursor:pointer; }
    .err { color:#fca5a5; min-height:20px; margin-top:10px; font-size:13px; }
  </style>
</head>
<body>
  <div class="box">
    <h1>Agent Protocol Designer</h1>
    <p>请输入用户名和密码后访问设计器。</p>
    <input id="username" placeholder="用户名" value="admin" />
    <input id="password" placeholder="密码" type="password" />
    <button onclick="login()">登录</button>
    <div class="err" id="err"></div>
  </div>
<script>
async function login(){
  err.textContent = '';
  const res = await fetch('/api/login', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({username: username.value.trim(), password: password.value})});
  if (res.ok) location.href = '/';
  else err.textContent = (await res.json()).error || '登录失败';
}
password.addEventListener('keydown', e => { if(e.key === 'Enter') login(); });
</script>
</body>
</html>
"""

HTML = r"""
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Agent Protocol Designer</title>
  <link rel="stylesheet" href="/static/vendor/xterm4/xterm.css?v=20260610c" />
  <style>
    :root { color-scheme: dark; --bg:#0f172a; --panel:#111827; --muted:#94a3b8; --text:#e5e7eb; --accent:#38bdf8; --ok:#22c55e; --warn:#f59e0b; --border:#263244; }
    * { box-sizing: border-box; }
    html, body { height:100%; overflow:hidden; }
    body { margin:0; font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background:var(--bg); color:var(--text); display:flex; flex-direction:column; }
    header { flex:0 0 auto; padding:14px 20px; border-bottom:1px solid var(--border); display:flex; align-items:center; justify-content:space-between; gap:16px; min-height:68px; }
    h1 { margin:0; font-size:20px; }
    .subtitle { color:var(--muted); font-size:13px; margin-top:4px; }
    main { flex:1 1 auto; min-height:0; display:grid; grid-template-columns: minmax(420px, 0.95fr) minmax(420px, 1.05fr); overflow:hidden; }
    section { min-width:0; min-height:0; border-right:1px solid var(--border); display:flex; flex-direction:column; overflow:hidden; }
    section:last-child { border-right:0; }
    .toolbar, .settings { flex:0 0 auto; padding:8px 12px; border-bottom:1px solid var(--border); display:flex; gap:8px; flex-wrap:wrap; align-items:center; }
    .top-actions { display:flex; align-items:center; gap:8px; flex-wrap:wrap; justify-content:flex-end; }
    .top-actions .step-btn { display:inline-flex; align-items:center; gap:6px; }
    .top-actions .step-index { display:inline-flex; align-items:center; justify-content:center; width:20px; height:20px; border-radius:999px; background:#0f172a; border:1px solid #38bdf8; color:#bae6fd; font-size:12px; font-weight:800; }
    .top-actions .secondary-link { background:#172033; border-color:#334155; color:#cbd5e1; }
    .settings-panel { border-bottom:1px solid var(--border); background:#0b1220; }
    .settings-panel summary { cursor:pointer; padding:9px 12px; color:#cbd5e1; font-size:13px; }
    .settings-panel .settings { border-bottom:0; padding-top:0; }
    .tool-menu { position:relative; }
    .tool-menu summary { cursor:pointer; list-style:none; background:#1e293b; border:1px solid var(--border); border-radius:8px; padding:8px 12px; }
    .tool-menu summary::-webkit-details-marker { display:none; }
    .tool-menu .menu-panel { position:absolute; right:0; top:40px; min-width:220px; z-index:20; border:1px solid var(--border); border-radius:12px; padding:10px; background:#020617; box-shadow:0 16px 50px rgba(0,0,0,.35); display:flex; flex-direction:column; gap:8px; }
    .quick-panel { padding:0 12px 8px; border-bottom:1px solid var(--border); background:#08111f; }
    .quick-panel summary { cursor:pointer; color:#cbd5e1; font-size:13px; padding:8px 0; }
    .quick-panel .quick-actions { display:flex; gap:8px; flex-wrap:wrap; padding-bottom:4px; }
    .export-actions { position:relative; display:inline-flex; }
    .export-actions .export-toggle { background:#1e293b; border:1px solid var(--border); border-radius:8px; padding:8px 12px; }
    .export-actions .menu-panel { display:none; position:absolute; right:0; top:38px; min-width:260px; max-width:420px; z-index:30; border:1px solid var(--border); border-radius:12px; padding:10px; background:#020617; box-shadow:0 16px 50px rgba(0,0,0,.45); gap:8px; flex-wrap:wrap; }
    .export-actions.open .menu-panel { display:flex; }
    .settings input { background:#0b1220; border:1px solid var(--border); color:var(--text); border-radius:8px; padding:7px 9px; min-width:150px; max-width:230px; }
    button { background:#1e293b; color:var(--text); border:1px solid var(--border); border-radius:8px; padding:8px 12px; cursor:pointer; }
    button.primary { background:#0369a1; border-color:#0ea5e9; }
    button:hover { filter:brightness(1.15); }
    .chat { flex:1 1 auto; min-height:0; overflow:auto; padding:14px; display:flex; flex-direction:column; gap:12px; }
    .msg { padding:12px 14px; border:1px solid var(--border); border-radius:12px; line-height:1.55; white-space:pre-wrap; }
    .user { background:#172554; align-self:flex-end; max-width:86%; }
    .assistant { background:#111827; align-self:flex-start; max-width:92%; }
    .meta { font-size:12px; color:var(--muted); margin-bottom:4px; }
    .inputbar { flex:0 0 auto; padding:10px 12px; border-top:1px solid var(--border); display:flex; gap:10px; }
    textarea { flex:1; min-height:46px; max-height:120px; resize:vertical; border-radius:10px; border:1px solid var(--border); background:#0b1220; color:var(--text); padding:10px; font-size:14px; }
    .right { min-height:0; overflow:hidden; display:flex; flex-direction:column; background:#08111f; }
    .right-nav { flex:0 0 auto; display:flex; gap:8px; align-items:center; padding:10px 12px; border-bottom:1px solid var(--border); background:#0b1220; }
    .right-tab { background:#111827; color:#cbd5e1; border-color:#263244; }
    .right-tab.active { background:#0369a1; border-color:#38bdf8; color:#fff; }
    .right-nav-note { margin-left:auto; color:#94a3b8; font-size:12px; }
    .right-panels { flex:1 1 auto; min-height:0; overflow:hidden; }
    .right-tab-panel { min-height:0; height:100%; overflow:hidden; display:none; }
    .right-tab-panel.active { display:flex; flex-direction:column; }
    .pane { min-height:0; overflow:hidden; display:flex; flex-direction:column; height:100%; }
    .pane h2 { flex:0 0 auto; font-size:14px; margin:0; padding:10px 14px; border-bottom:1px solid var(--border); display:flex; justify-content:space-between; align-items:center; gap:10px; }
    .export-header-actions { display:flex; align-items:center; justify-content:flex-end; gap:8px; flex-wrap:wrap; }
    .dev-entry-grid { padding:14px; overflow:auto; display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; }
    .dev-entry-card { border:1px solid var(--border); background:#0f172a; border-radius:14px; padding:14px; display:flex; flex-direction:column; gap:8px; }
    .dev-entry-card h3 { margin:0; font-size:15px; color:#fef3c7; }
    .dev-entry-card p { margin:0; color:#cbd5e1; font-size:13px; line-height:1.55; }
    .dev-entry-card button { align-self:flex-start; margin-top:auto; }
    .dev-entry-card.full { grid-column:1 / -1; }
    .advice-preview { border:1px solid var(--border); border-radius:12px; background:#020617; padding:12px; max-height:320px; overflow:auto; color:#dbeafe; }
    .advice-preview h1 { font-size:18px; margin:0 0 10px; color:#e0f2fe; }
    .advice-preview h2 { font-size:15px; margin:14px 0 8px; color:#fef3c7; }
    .advice-preview ul { margin:6px 0 10px 20px; padding:0; }
    .advice-preview li { margin:4px 0; }
    .landing-guide { flex:0 0 auto; margin:12px 14px 0; border:1px solid #1d4ed8; border-radius:14px; background:linear-gradient(135deg,#071224,#0f172a); padding:12px; }
    .landing-guide-head { display:flex; gap:10px; align-items:center; justify-content:space-between; margin-bottom:10px; }
    .landing-guide-title { font-weight:800; color:#e0f2fe; }
    .landing-guide-stage { border:1px solid #38bdf8; color:#bae6fd; background:#082f49; border-radius:999px; padding:4px 8px; font-size:12px; }
    .landing-guide-body { display:grid; grid-template-columns:1.1fr 1fr; gap:10px; }
    .landing-guide-box { border:1px solid #263244; border-radius:12px; background:#020617; padding:10px; color:#cbd5e1; font-size:13px; line-height:1.55; }
    .landing-guide-box strong { color:#fef3c7; }
    .landing-guide-actions { display:flex; gap:8px; flex-wrap:wrap; margin-top:8px; }
    .landing-guide-prompt { white-space:pre-wrap; color:#dbeafe; }
    @media (max-width: 1000px) { .landing-guide-body { grid-template-columns:1fr; } }
    pre { flex:1; margin:0; overflow:auto; padding:14px; background:#020617; color:#d1d5db; font-size:12px; line-height:1.45; }
    .pill { font-size:12px; color:var(--muted); }
    .questions { flex:0 0 auto; max-height:92px; overflow:auto; padding:10px 14px; border-top:1px solid var(--border); color:#cbd5e1; font-size:13px; }
    .question { margin:4px 0; color:#fef3c7; }
    .status { font-size:12px; color:var(--muted); }
    .ok { color:var(--ok); } .warn { color:var(--warn); }
    .drawer-mask { position:fixed; inset:0; background:rgba(2,6,23,.62); display:none; z-index:50; }
    .drawer-mask.open { display:block; }
    .drawer { position:absolute; top:0; right:0; width:min(760px, 92vw); height:100%; background:#0b1220; border-left:1px solid var(--border); box-shadow:-20px 0 60px rgba(0,0,0,.35); display:flex; flex-direction:column; }
    .wide-drawer { width:min(1080px, 94vw); }
    .debug-drawer { width:min(1320px, 96vw); }
    .debug-body { overflow:auto; padding:18px 22px 48px; line-height:1.65; color:#dbeafe; }
    .debug-result { max-width:1180px; margin:0 auto; }
    .debug-chat-panel { max-width:1180px; margin:0 auto 14px; border:1px solid #1f3b5d; border-radius:16px; background:#07111f; padding:12px; }
    .debug-chat-history { display:flex; flex-direction:column; gap:8px; max-height:220px; overflow:auto; margin-bottom:10px; }
    .debug-chat-empty { color:#94a3b8; font-size:13px; }
    .debug-msg { border:1px solid #1f334d; border-radius:13px; padding:9px 10px; background:#0f172a; }
    .debug-msg.user { border-color:#1d4ed8; background:rgba(30,64,175,.28); }
    .debug-msg.agent { border-color:#166534; background:rgba(20,83,45,.24); }
    .debug-msg strong { display:block; color:#e0f2fe; margin-bottom:4px; font-size:12px; }
    .debug-msg p { margin:0; color:#dbeafe; font-size:13px; white-space:pre-wrap; }
    .debug-diagnosis { margin-top:8px; border:1px solid #334155; border-radius:12px; padding:9px; background:rgba(2,6,23,.42); }
    .debug-diagnosis h4 { margin:0 0 6px; color:#fde68a; font-size:13px; }
    .debug-level-row { display:flex; flex-wrap:wrap; gap:6px; margin:6px 0; }
    .debug-level { border-radius:999px; padding:4px 8px; font-size:12px; font-weight:800; border:1px solid #334155; background:#0f172a; color:#cbd5e1; }
    .debug-level.intent { border-color:#f97316; background:rgba(154,52,18,.28); color:#fed7aa; }
    .debug-level.executor { border-color:#38bdf8; background:rgba(14,116,144,.24); color:#bae6fd; }
    .debug-level.tool { border-color:#a855f7; background:rgba(88,28,135,.26); color:#e9d5ff; }
    .debug-level.workflow { border-color:#eab308; background:rgba(113,63,18,.28); color:#fef3c7; }
    .debug-level.validator { border-color:#ef4444; background:rgba(127,29,29,.28); color:#fecaca; }
    .debug-fix-actions { display:flex; gap:8px; flex-wrap:wrap; margin-top:9px; }
    .debug-fix-actions button { margin:0; }
    .debug-diagnosis ul { margin:6px 0 0 18px; padding:0; color:#cbd5e1; font-size:12px; }
    .debug-diagnosis li { margin:3px 0; }
    .debug-file-chips { display:flex; flex-wrap:wrap; gap:6px; margin-top:7px; }
    .debug-file-chip { border:1px solid #1d4ed8; background:rgba(30,64,175,.25); color:#bfdbfe; border-radius:999px; padding:3px 7px; font-size:12px; }

    .debug-chat-input { display:grid; grid-template-columns:1fr auto auto; gap:8px; align-items:end; }
    .debug-chat-input textarea { min-height:58px; resize:vertical; border-radius:12px; border:1px solid #1f3b5d; background:#020617; color:#e5e7eb; padding:9px 10px; }
    @media (max-width: 900px) { .debug-chat-input { grid-template-columns:1fr; } }

    .cli-drawer { width:min(1480px, 98vw); }
    .cli-drawer.fullscreen { width:100vw; left:0; right:0; border-left:0; }
    .cli-body { flex:1 1 auto; min-height:0; display:grid; grid-template-columns:minmax(360px, 430px) minmax(0, 1fr); gap:14px; overflow:hidden; padding:14px 18px 18px; }
    .cli-scroll-config { min-height:0; overflow:hidden; max-height:none; padding-right:0; display:grid; grid-template-rows:minmax(0,2fr) minmax(0,1fr); gap:12px; } .cli-left-top,.cli-left-bottom{min-height:0;overflow:auto;} .cli-left-top{display:flex;flex-direction:column;border:1px solid #1e3a5f;border-radius:18px;min-height:0;height:100%;overflow-y:auto;overflow-x:hidden;background:rgba(8,47,73,.18);padding:0;scrollbar-gutter:stable;} .cli-left-bottom{border:1px solid #475569;border-radius:16px;background:rgba(2,6,23,.45);padding:10px;box-shadow:inset 0 1px 0 rgba(148,163,184,.12);overflow-y:auto;overflow-x:hidden;scrollbar-gutter:stable;} .cli-left-bottom .preview-dev-details{margin-top:8px;} .cli-left-bottom .preview-tip{margin:8px 0;} .compact-flow{gap:4px;margin:6px 0}.compact-flow .flow-step{font-size:11px;padding:3px 6px}.compact-flow .flow-arrow{font-size:11px}.cli-left-bottom ol{margin:6px 0 6px 18px;padding:0}.cli-left-bottom li{margin:3px 0}.cli-left-bottom p{margin:6px 0} .cli-pane-label{font-size:12px;font-weight:800;color:#bae6fd;margin:0 0 8px;letter-spacing:.02em}.cli-left-top>.cli-pane-label{position:sticky;top:0;z-index:2;padding:8px 10px;margin:0;border-bottom:1px solid #1e3a5f;background:#06101f;border-radius:18px 18px 0 0}.cli-left-top>.cli-command-deck{border-radius:0 0 18px 18px;border:0;box-shadow:none;}
    .cli-terminal-wrap { min-width:0; min-height:0; height:100%; display:flex; flex-direction:column; margin-top:0; }
    .cli-command-deck { min-height:0; flex:1 0 auto; display:flex; flex-direction:column; border:1px solid #1e3a5f; border-radius:18px; background:linear-gradient(135deg, rgba(8,47,73,.96), rgba(15,23,42,.98)); padding:12px; box-shadow:0 16px 40px rgba(0,0,0,.22); overflow:visible; } .cli-command-result{min-height:180px;max-height:360px;overflow:auto;}
    .cli-command-head { flex:0 0 auto; display:flex; justify-content:space-between; gap:12px; align-items:center; margin-bottom:8px; }
    .cli-command-title { display:flex; flex-direction:column; gap:2px; }
    .cli-command-title strong { color:#f8fafc; font-size:16px; }
    .cli-command-title span { color:#bfdbfe; font-size:12px; }
    .cli-command-pill { flex:0 0 auto; border:1px solid #38bdf8; color:#e0f2fe; background:rgba(14,165,233,.14); border-radius:999px; padding:5px 10px; font-size:12px; }
    .apd-flow-details { flex:0 0 auto; margin:6px 0 8px; border:1px solid #1f3b5d; border-radius:12px; background:rgba(2,6,23,.28); overflow:hidden; }
    .apd-flow-details summary { cursor:pointer; list-style:none; display:flex; align-items:center; justify-content:space-between; gap:10px; padding:7px 9px; color:#bae6fd; font-size:12px; font-weight:800; user-select:none; }
    .apd-flow-details summary::-webkit-details-marker { display:none; }
    .apd-flow-details summary::before { content:'▸'; color:#38bdf8; margin-right:2px; transition:transform .15s ease; }
    .apd-flow-details[open] summary::before { transform:rotate(90deg); }
    .apd-flow-details summary span:first-child { display:inline-flex; align-items:center; gap:5px; }
    .apd-flow-summary { color:#e0f2fe; font-weight:700; text-align:right; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
    .apd-flow-strip { display:grid; grid-template-columns:repeat(5,minmax(0,1fr)); gap:6px; margin:0 8px 8px; padding-top:7px; border-top:1px solid rgba(31,59,93,.72); }
    .apd-flow-step { border:1px solid #1f3b5d; border-radius:10px; padding:7px 6px; background:rgba(2,6,23,.34); color:#94a3b8; font-size:11px; line-height:1.35; }
    .apd-flow-step strong { display:block; color:#dbeafe; font-size:11px; margin-bottom:2px; }
    .apd-flow-step.active { border-color:#38bdf8; background:rgba(14,165,233,.16); color:#e0f2fe; box-shadow:0 0 0 1px rgba(56,189,248,.12) inset; }
    .apd-flow-step.done { border-color:#16a34a; background:rgba(22,163,74,.13); color:#bbf7d0; }
    .apd-next-hint { flex:0 0 auto; border:1px solid #164e63; border-radius:14px; padding:9px 10px; margin-bottom:9px; background:rgba(8,47,73,.72); color:#dbeafe; font-size:13px; }
    .apd-next-hint strong { color:#67e8f9; }
    .cli-command-grid { flex:0 0 auto; display:grid; grid-template-columns:minmax(0,1fr) 168px; gap:10px; align-items:stretch; }
    .cli-command-input { display:flex; flex-direction:column; gap:6px; }
    .cli-command-input label { color:#dbeafe; font-size:13px; font-weight:700; }
    .cli-command-input textarea { min-height:112px; resize:vertical; border-radius:14px; border:1px solid #1f3b5d; background:#020617; color:#e5e7eb; padding:11px 12px; line-height:1.55; }
    .cli-command-actions { display:grid; grid-template-columns:1fr; gap:8px; align-content:start; }
    .cli-command-actions button { width:100%; margin:0; }
    .cli-mode-select { display:flex; flex-direction:column; gap:5px; color:#bfdbfe; font-size:12px; font-weight:700; }
    .cli-mode-select select { width:100%; border-radius:10px; border:1px solid #1f3b5d; background:#020617; color:#e5e7eb; padding:7px 9px; }
    .cli-command-meta { display:flex; gap:8px; flex-wrap:wrap; align-items:center; color:#93c5fd; font-size:12px; }
    .cli-command-meta span { border:1px solid #1f3b5d; border-radius:999px; padding:4px 8px; background:rgba(2,6,23,.35); }
    .cli-command-result { flex:0 0 auto; min-height:180px; max-height:360px; margin-top:10px; overflow:auto; border-top:1px solid #1f3b5d; padding-top:10px; scrollbar-gutter:stable; }
    .cli-result-card { border:1px solid #1f3b5d; border-radius:14px; background:rgba(2,6,23,.42); padding:10px; }
    .cli-result-card h3 { margin:0 0 8px; color:#e0f2fe; font-size:14px; }
    .cli-result-card p { margin:5px 0; color:#cbd5e1; font-size:13px; }
    .cli-result-card pre { max-height:260px; margin:8px 0 0; white-space:pre-wrap; }
    .cli-help-details { flex:0 0 auto; margin-top:8px; }
    .cli-help-details summary { cursor:pointer; color:#93c5fd; font-size:12px; }
    .cli-help-details button { width:100%; margin:6px 0 0; }
    .agent-debug-shell { display:flex; flex-direction:column; gap:12px; }
    .agent-debug-hero { border:1px solid #1e40af; border-radius:16px; padding:12px; background:radial-gradient(circle at top left, rgba(59,130,246,.28), rgba(15,23,42,.98) 55%); }
    .agent-debug-hero h3 { margin:0 0 8px; color:#f8fafc; font-size:16px; }
    .agent-debug-hero p { margin:5px 0; color:#cbd5e1; font-size:13px; }
    .agent-debug-kpis { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:8px; margin-top:10px; }
    .agent-debug-kpi { border:1px solid rgba(148,163,184,.25); border-radius:12px; padding:8px; background:rgba(2,6,23,.42); }
    .agent-debug-kpi strong { display:block; color:#93c5fd; font-size:12px; }
    .agent-debug-kpi span { display:block; margin-top:3px; color:#f8fafc; font-weight:800; }
    .agent-flow { position:relative; display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:10px; margin:0; }
    .agent-flow-node { position:relative; min-height:86px; border:1px solid #334155; border-radius:15px; background:#0f172a; padding:10px 10px 10px 12px; box-shadow:0 10px 26px rgba(0,0,0,.22); }
    .agent-flow-node::before { content:attr(data-step); display:inline-flex; align-items:center; justify-content:center; width:24px; height:24px; border-radius:999px; margin-bottom:7px; font-size:12px; font-weight:900; background:#1e293b; color:#e2e8f0; }
    .agent-flow-node strong { display:block; color:#f8fafc; font-size:13px; }
    .agent-flow-node span { display:block; color:#cbd5e1; font-size:12px; margin-top:4px; line-height:1.45; }
    .agent-flow-node.ok { border-color:#16a34a; background:linear-gradient(135deg, rgba(20,83,45,.58), rgba(15,23,42,.98)); }
    .agent-flow-node.ok::before { background:#16a34a; color:#052e16; }
    .agent-flow-node.warn { border-color:#eab308; background:linear-gradient(135deg, rgba(113,63,18,.58), rgba(15,23,42,.98)); }
    .agent-flow-node.warn::before { background:#eab308; color:#422006; }
    .agent-flow-node.fail { border-color:#ef4444; background:linear-gradient(135deg, rgba(127,29,29,.62), rgba(15,23,42,.98)); }
    .agent-flow-node.fail::before { background:#ef4444; color:#450a0a; }
    .agent-debug-section-title { display:flex; align-items:center; justify-content:space-between; gap:8px; color:#bae6fd; font-weight:800; font-size:13px; margin-top:2px; }
    .agent-node-list { display:grid; grid-template-columns:1fr; gap:8px; margin-top:8px; }
    .agent-node-card { border:1px solid #1f3b5d; border-radius:13px; background:rgba(2,6,23,.44); padding:9px; }
    .agent-node-card strong { color:#fde68a; font-size:13px; }
    .agent-node-card small { display:block; color:#93c5fd; margin-top:3px; }
    .agent-node-card pre { max-height:150px; margin:7px 0 0; white-space:pre-wrap; }
    @media (max-width: 720px) { .agent-flow, .agent-debug-kpis { grid-template-columns:1fr; } }

    .cli-compact-row { display:flex; align-items:center; gap:8px; flex-wrap:wrap; margin:8px 0 0; }
    .cli-compact-row .preview-tip { flex:1 1 auto; margin:0; }
    @media (max-width: 900px) { .cli-command-grid { grid-template-columns:1fr; } .cli-command-actions { grid-template-columns:repeat(2,minmax(0,1fr)); } }
    @media (max-width: 1100px) { .cli-body { grid-template-columns:1fr; overflow:auto; } .cli-scroll-config { overflow:visible; min-height:720px; grid-template-rows:auto auto; } .cli-left-top,.cli-left-bottom{overflow:visible;} .cli-terminal-wrap { min-height:620px; } }
    .cli-drawer.fullscreen .cli-scroll-config { display:grid; grid-template-rows:minmax(0,2fr) minmax(0,1fr); }
    .cli-drawer.fullscreen .cli-terminal-wrap { margin-top:0; }
    .drawer-head { padding:14px 18px; border-bottom:1px solid var(--border); display:flex; justify-content:space-between; align-items:center; }
    .drawer-head h2 { margin:0; font-size:17px; }
    .guide-body { overflow:auto; padding:22px 28px 60px; line-height:1.72; color:#dbeafe; }
    .guide-body h1 { font-size:24px; margin:0 0 16px; }
    .guide-body h2 { font-size:19px; margin:28px 0 10px; color:#f8fafc; border-bottom:1px solid var(--border); padding-bottom:6px; }
    .guide-body h3 { font-size:16px; margin:22px 0 8px; color:#bae6fd; }
    .guide-body p { margin:9px 0; }
    .guide-body ul { margin:8px 0 12px 22px; padding:0; }
    .guide-body li { margin:4px 0; }
    .guide-body code { background:#172033; padding:2px 5px; border-radius:5px; color:#fef3c7; }
    .guide-body pre { display:block; margin:12px 0; padding:12px; border:1px solid var(--border); border-radius:10px; background:#020617; color:#e5e7eb; overflow:auto; white-space:pre; }
    .guide-body blockquote { margin:12px 0; padding:8px 12px; border-left:3px solid var(--accent); background:#0f1b2e; color:#cbd5e1; }
    .guide-body hr { border:0; border-top:1px solid var(--border); margin:20px 0; }
    .case-body table { width:100%; border-collapse:collapse; margin:12px 0 18px; font-size:13px; }
    .case-body th, .case-body td { border:1px solid var(--border); padding:8px 10px; vertical-align:top; }
    .case-body th { background:#111827; color:#f8fafc; }
    .case-hero { border:1px solid #164e63; background:linear-gradient(135deg, #082f49, #0f172a); border-radius:16px; padding:18px; margin-bottom:16px; }
    .case-hero h1 { margin:0 0 8px; font-size:24px; }
    .case-hero p { margin:6px 0; color:#bfdbfe; }
    .case-tag { display:inline-flex; align-items:center; margin-top:8px; padding:5px 9px; border-radius:999px; border:1px solid #38bdf8; color:#e0f2fe; font-size:12px; background:rgba(14,165,233,.12); }
    .case-grid { display:grid; grid-template-columns:repeat(2, minmax(0, 1fr)); gap:12px; margin:14px 0; }
    .case-card { border:1px solid var(--border); border-radius:14px; padding:14px; background:#0f172a; }
    .case-card h3 { margin:0 0 8px; color:#bae6fd; font-size:15px; }
    .case-card p { margin:6px 0; color:#cbd5e1; font-size:13px; }
    .case-card ul { margin:8px 0 0 18px; }
    .flow-wrap { display:flex; flex-wrap:wrap; gap:8px; align-items:center; margin:12px 0 18px; }
    .flow-node { border:1px solid #334155; background:#111827; border-radius:12px; padding:9px 10px; min-width:128px; max-width:190px; }
    .flow-node strong { display:block; color:#f8fafc; font-size:13px; }
    .flow-node span { display:block; color:#94a3b8; font-size:12px; margin-top:3px; }
    .flow-arrow { color:#38bdf8; font-weight:700; }
    .gap-list { display:grid; grid-template-columns:repeat(2, minmax(0, 1fr)); gap:10px; margin:12px 0; }
    .gap-card { border:1px solid #3f2d13; background:#1c1917; border-radius:12px; padding:12px; }
    .gap-card strong { color:#fef3c7; }
    .gap-card p { font-size:13px; margin:7px 0 0; color:#d6d3d1; }
    .roadmap { display:flex; flex-direction:column; gap:8px; margin:12px 0 20px; }
    .roadmap-item { display:grid; grid-template-columns:54px 150px 1fr; gap:10px; align-items:start; border:1px solid var(--border); border-radius:12px; padding:10px; background:#0f172a; }
    .roadmap-phase { color:#22c55e; font-weight:800; }
    .roadmap-title { color:#f8fafc; font-weight:700; }
    .raw-toggle { margin-top:20px; border-top:1px solid var(--border); padding-top:14px; }
    .raw-toggle summary { cursor:pointer; color:#93c5fd; font-weight:700; }
    @media (max-width: 900px) { .case-grid, .gap-list { grid-template-columns:1fr; } .roadmap-item { grid-template-columns:1fr; } }
    .history-list { overflow:auto; padding:14px; display:flex; flex-direction:column; gap:10px; }
    .history-card { border:1px solid var(--border); border-radius:12px; padding:12px; background:#111827; }
    .history-card:hover { border-color:#38bdf8; }
    .history-title { font-weight:700; margin-bottom:6px; color:#f8fafc; }
    .history-meta { font-size:12px; color:var(--muted); margin-bottom:10px; }
    .workflow-hero { border:1px solid #1d4ed8; background:linear-gradient(135deg,#0f172a,#172554); border-radius:16px; padding:18px; margin-bottom:16px; }
    .workflow-hero h1 { margin:0 0 8px; }
    .workflow-hero p { color:#bfdbfe; margin:0; }
    .workflow-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; margin:14px 0; }
    .workflow-card { border:1px solid var(--border); border-radius:14px; background:#0f172a; padding:14px; }
    .workflow-card h3 { margin:0 0 8px; color:#bae6fd; }
    .workflow-card p { color:#cbd5e1; font-size:13px; }
    .workflow-table { width:100%; border-collapse:collapse; margin:12px 0 18px; overflow:hidden; border-radius:12px; }
    .workflow-table th, .workflow-table td { border:1px solid #334155; padding:10px; vertical-align:top; }
    .workflow-table th { background:#172033; color:#f8fafc; }
    .workflow-table td { background:#0b1220; color:#dbeafe; }
    .workflow-pill { display:inline-block; padding:3px 8px; border-radius:999px; background:#1e3a8a; color:#bfdbfe; font-size:12px; margin:2px 4px 2px 0; }
    .sandbox-form { display:grid; grid-template-columns:1fr 1fr; gap:12px; margin-bottom:14px; }
    .sandbox-form label { display:flex; flex-direction:column; gap:6px; color:#cbd5e1; font-size:13px; }
    .sandbox-form input, .sandbox-form select, .sandbox-form textarea { width:100%; border:1px solid var(--border); border-radius:9px; background:#020617; color:#e5e7eb; padding:9px; font-size:13px; }
    .sandbox-form textarea { min-height:92px; max-height:240px; font-family:ui-monospace,SFMono-Regular,Menlo,monospace; }
    .sandbox-full { grid-column:1 / -1; }
    .sandbox-actions { display:flex; gap:8px; flex-wrap:wrap; margin:8px 0 14px; }
    .preview-help { border:1px solid var(--border); border-radius:14px; background:#0f172a; padding:14px; margin:12px 0 16px; }
    .preview-help h3 { margin:0 0 8px; color:#bae6fd; }
    .preview-help-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px; }
    .preview-help-card { border:1px solid #1f334d; border-radius:10px; padding:10px; background:#08111f; }
    .preview-help-card strong { color:#fef3c7; }
    .preview-help-card p { margin:6px 0 0; color:#cbd5e1; font-size:13px; }
    .preview-result { display:flex; flex-direction:column; gap:12px; }
    .preview-section { border:1px solid var(--border); border-radius:14px; overflow:hidden; background:#08111f; }
    .preview-section h3 { margin:0; padding:10px 12px; background:#111827; color:#bae6fd; font-size:15px; border-bottom:1px solid var(--border); }
    .preview-section .desc { padding:10px 12px; color:#cbd5e1; font-size:13px; border-bottom:1px solid var(--border); }
    .preview-section pre { max-height:320px; min-height:initial; flex:none; white-space:pre-wrap; }
    .preview-summary { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:8px; margin-bottom:12px; }
    .preview-summary div { border:1px solid var(--border); border-radius:10px; padding:10px; background:#0f172a; }
    .preview-summary strong { display:block; color:#fef3c7; margin-bottom:4px; }
    .beginner-box { border:1px solid #164e63; background:#082f49; border-radius:14px; padding:14px; margin-bottom:12px; }
    .beginner-box h3 { margin:0 0 8px; color:#e0f2fe; }
    .beginner-box p { margin:7px 0; color:#dbeafe; }
    .beginner-box ul { margin:8px 0 0 20px; color:#cbd5e1; }
    .example-list { display:flex; flex-direction:column; gap:8px; margin:10px 0 14px; }
    .example-card { border:1px solid var(--border); border-radius:12px; padding:10px; background:#0f172a; }
    .example-card strong { color:#fef3c7; }
    .example-card p { margin:5px 0; color:#cbd5e1; font-size:13px; }
    .example-card button { margin-top:6px; }
    .compact-card { padding:9px 10px; }
    .compact-row { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
    .compact-row strong { margin-right:auto; }
    .compact-row button { margin-top:0; padding:5px 9px; }
    .compact-details { margin-top:8px; padding:8px; }
    .workspace-more { margin-top:10px; border-top:1px solid var(--border); padding-top:10px; }
    .ttyd-frame { flex:1 1 auto; width:100%; height:100%; min-height:0; border:1px solid var(--border); border-radius:14px; background:#020617; display:none; }
    .cli-drawer.fullscreen .ttyd-frame { height:100%; min-height:0; }
    .ttyd-status { flex:0 0 auto; color:#93c5fd; font-size:13px; margin:0 0 8px; padding:8px 10px; border:1px solid #1f334d; border-radius:12px; background:#06101f; }
    .cli-terminal { background:#020617; border:1px solid var(--border); border-radius:12px; padding:8px; height:520px; min-height:340px; overflow:hidden; }
    .cli-drawer.fullscreen .cli-terminal { height:calc(100vh - 126px); min-height:520px; }
    .cli-terminal .xterm { height:100%; }
    .cli-terminal .xterm-viewport { border-radius:10px; }
    .cli-help { flex:0 0 auto; margin-top:10px; color:#94a3b8; font-size:13px; line-height:1.7; }
    .cli-send-bar { flex:0 0 auto; display:grid; grid-template-columns:auto 1fr auto auto; gap:8px; align-items:end; margin-top:8px; padding:8px; border:1px solid #1f334d; border-radius:12px; background:#06101f; }
    .cli-send-prefix { color:#22c55e; font-family:ui-monospace,SFMono-Regular,Menlo,monospace; padding:8px 0; }
    .cli-send-bar textarea { min-height:38px; max-height:128px; resize:none; overflow:auto; background:#020617; color:#e5e7eb; border:1px solid var(--border); border-radius:9px; padding:8px 10px; font-size:13px; line-height:1.45; font-family:ui-monospace,SFMono-Regular,Menlo,monospace; }
    .cli-send-tip { color:#64748b; font-size:12px; padding:8px 0; white-space:nowrap; }
    .cli-drawer.fullscreen .cli-help { display:none; }
    .cli-workspace-panel { border:1px solid #1e3a8a; background:#071224; border-radius:14px; padding:12px; margin:10px 0 14px; }
    .cli-workspace-row { display:grid; grid-template-columns:1fr auto auto; gap:8px; align-items:end; }
    .cli-workspace-list { display:flex; flex-wrap:wrap; gap:8px; margin-top:10px; }
    .cli-workspace-pill { border:1px solid var(--border); background:#0f172a; color:#dbeafe; border-radius:999px; padding:6px 10px; cursor:pointer; }
    .cli-workspace-pill.active { border-color:#38bdf8; color:#e0f2fe; background:#083344; }
    .preview-mode { display:flex; gap:8px; margin-bottom:10px; flex-wrap:wrap; }
    .preview-dev { display:none; flex-direction:column; gap:12px; }
    .preview-result.show-dev .preview-dev { display:flex; }
    .preview-result.show-dev .preview-beginner-only { display:none; }
    .preview-tip { border:1px solid #365314; background:#13240d; color:#dcfce7; border-radius:12px; padding:12px; }
    .timeline { display:flex; flex-direction:column; gap:10px; }
    .timeline-card { border:1px solid var(--border); border-radius:12px; padding:12px; background:#0f172a; display:grid; grid-template-columns:auto 1fr; gap:10px; }
    .timeline-step { width:30px; height:30px; border-radius:999px; display:flex; align-items:center; justify-content:center; background:#1e293b; color:#e5e7eb; font-weight:700; }
    .timeline-card.done .timeline-step { background:#166534; }
    .timeline-card.blocked .timeline-step, .timeline-card.awaiting_confirmation .timeline-step { background:#92400e; }
    .timeline-card.partial .timeline-step { background:#1d4ed8; }
    .timeline-card.skipped .timeline-step { background:#475569; }
    .timeline-title { color:#fef3c7; font-weight:700; margin-bottom:4px; }
    .timeline-plain { color:#dbeafe; margin-bottom:4px; }
    .timeline-detail { color:#94a3b8; font-size:13px; }
    .diagnostics { border:1px solid #7c2d12; background:#1c1008; border-radius:12px; padding:12px; }
    .diagnostics h3 { margin:0 0 8px; color:#fed7aa; }
    .diagnostics ul { margin:6px 0 0 20px; }
    .preview-dev-details { border:1px solid var(--border); border-radius:14px; background:#06101f; padding:10px; }
    .preview-dev-details summary { cursor:pointer; color:#fef3c7; font-weight:600; padding:4px 2px 10px; }
    .preview-dev-details[open] { padding-bottom:12px; }
  </style>
</head>
<body>
<header>
  <div>
    <h1>Agent Protocol Designer</h1>
    <div class="subtitle">面向小白的 Agent 场景开发向导：先设计，再预览，再生成。</div>
  </div>
  <div class="top-actions">
    <button onclick="focusDesignerInput()" class="primary step-btn"><span class="step-index">1</span>设计 Agent</button>
    <button onclick="openCliCollabAssistant()" class="primary step-btn"><span class="step-index">2</span>工程开发台</button>
    <button onclick="openDelegatedInspector()" class="primary step-btn"><span class="step-index">3</span>真实调试台</button>
    <button onclick="openHistory()" class="secondary-link">历史</button>
    <details class="tool-menu">
      <summary>更多</summary>
      <div class="menu-panel">
        <button onclick="openPreview()">预览运行</button>
        <button onclick="openRuntime()">Runtime 运行</button>
        <button onclick="openDemoPlayground()">Demo Playground</button>
        <button onclick="openGuide()">Agent 架构笔记</button>
        <button onclick="openDynamicWorkflow()">Dynamic Workflow 架构</button>
        <button onclick="openWritingCase()">写作 Agent 案例</button>
        <button onclick="openBidValidation()">招投标 Agent 验证</button>
        <button onclick="openAgentOsEvaluation()">AgentOS 评估</button>
        <button onclick="openCapabilityBoundary()">APD 能力边界</button>
        <button onclick="openAgentRegistry()">Agent Registry</button>
        <button onclick="openObservability()">Runtime 观测</button>
        <button onclick="openGovernance()">权限审计</button>
        <button onclick="openMultiAgent()">Multi-Agent 协作</button>
        <button onclick="openSandbox()">Docker 沙箱（实验）</button>
        <button onclick="logout()">退出登录</button>
      </div>
    </details>
    <div class="status" id="status">checking...</div>
  </div>
</header>
<main>
  <section>
    <details class="settings-panel">
      <summary>LLM 配置（一般不用展开）</summary>
      <div class="settings">
        <input id="apiBase" placeholder="API Base，如 http://host/v1" />
        <input id="apiKey" placeholder="API Key，可留空用环境变量" type="password" />
        <input id="model" placeholder="Model，如 gpt-5.5" />
        <input id="timeout" placeholder="LLM超时秒，如120" type="number" min="10" step="10" />
        <input id="serverTimeout" placeholder="服务端等待秒，如150" type="number" min="20" step="10" />
        <input id="maxTokens" placeholder="最大输出tokens，如2000" type="number" min="200" step="100" />
        <label style="font-size:12px;color:var(--muted);display:flex;align-items:center;gap:5px;"><input id="responseFormat" type="checkbox" /> 强制JSON</label>
        <button onclick="saveSettings()">保存配置</button>
      </div>
    </details>
    <div class="toolbar">
      <button onclick="resetSession()">新会话</button>
      <button onclick="openHistory()">历史会话</button>
      <button onclick="openPreview()" class="primary">预览运行 Agent</button>
      <button onclick="quick('请评审当前协议，先指出最危险的 5 个问题，再告诉我下一步该怎么进入开发。')">评审协议</button>
      <button onclick="quick('请基于当前协议生成开发落地计划，告诉我应该先实现哪些状态、规划器、校验器和执行器。')">开发引导</button>
    </div>
    <details class="quick-panel">
      <summary>快捷示例 / 高级提示</summary>
      <div class="quick-actions">
        <button onclick="quick('我想设计一个客服退款智能体，能查询订单、申请退款、修改地址、投诉物流。')">客服示例</button>
        <button onclick="quick('请先帮我判断这个 Agent 场景应该用直接问答、单步操作调用、协议驱动多轮操作调用、状态机、agent_loop 还是多 agent 协作。不要直接进入操作设计，先说明为什么。')">场景判断</button>
        <button onclick="openDynamicWorkflow()">Dynamic Workflow</button>
        <button onclick="openSandbox()">沙箱运行（实验）</button>
      </div>
    </details>
    <div class="chat" id="chat"></div>
    <div class="inputbar">
      <textarea id="message" placeholder="描述你想做的 Agent 场景。设计器会一次只问一个问题，按 Ctrl+Enter 发送。"></textarea>
      <button class="primary" onclick="sendMessage()">发送</button>
    </div>
  </section>
  <section class="right">
    <div class="right-nav" role="tablist" aria-label="右侧工作区">
      <button class="right-tab active" data-right-tab="protocol" onclick="switchRightTab('protocol')">协议草案</button>
      <button class="right-tab" data-right-tab="exports" onclick="switchRightTab('exports')">导出产物</button>
      <button class="right-tab" data-right-tab="develop" onclick="switchRightTab('develop')">开发入口</button>
      <span class="right-nav-note">右侧只展示当前步骤需要看的内容</span>
    </div>
    <div class="right-panels">
      <div class="right-tab-panel active" data-right-panel="protocol">
        <div class="pane">
          <h2>协议草案 <span class="pill" id="stage">stage: discover</span></h2>
          <div id="landingGuide" class="landing-guide">正在判断 Agent 落地阶段...</div>
          <pre id="protocol">{}</pre>
          <div class="questions" id="questions">下一步问题会显示在这里。</div>
        </div>
      </div>
      <div class="right-tab-panel" data-right-panel="exports">
        <div class="pane">
          <h2>
            导出产物
            <span class="export-header-actions">
              <button onclick="downloadScaffold()">生成可运行 Demo zip</button>
              <button onclick="downloadDelegatedAgent()" class="primary">生成 Delegated Agent zip</button>
              <button onclick="openDelegatedInspector()" class="primary">打开真实调试台</button>
              <button onclick="openDemoPlayground()">在线运行 Demo</button>
              <button onclick="openCliCollabAssistant()">打开工程开发台</button>
              <span class="export-actions" id="exportActions">
                <button type="button" class="export-toggle" onclick="toggleExportMenu(event)">更多导出 ▾</button>
                <span class="menu-panel" onclick="event.stopPropagation()">
                  <button onclick="downloadExport('protocol.json')">protocol.json</button>
                  <button onclick="downloadExport('workflow.json')">workflow.json</button>
                  <button onclick="downloadExport('workflow_plan.md')">workflow计划</button>
                  <button onclick="downloadExport('architecture_check.md')">架构检查</button>
                  <button onclick="downloadExport('architecture_check.json')">架构检查JSON</button>
                  <button onclick="downloadExport('memory_policy.md')">记忆策略</button>
                  <button onclick="downloadExport('memory_policy.json')">记忆策略JSON</button>
                  <button onclick="downloadExport('state_model.md')">状态模型</button>
                  <button onclick="downloadExport('state_model.json')">状态模型JSON</button>
                  <button onclick="downloadExport('artifact_model.md')">产物模型</button>
                  <button onclick="downloadExport('artifact_model.json')">产物模型JSON</button>
                  <button onclick="downloadExport('tool_registry.md')">工具注册</button>
                  <button onclick="downloadExport('tool_registry.json')">工具注册JSON</button>
                  <button onclick="downloadExport('permission_policy.md')">权限策略</button>
                  <button onclick="downloadExport('permission_policy.json')">权限策略JSON</button>
                  <button onclick="downloadExport('error_recovery.md')">失败恢复</button>
                  <button onclick="downloadExport('error_recovery.json')">失败恢复JSON</button>
                  <button onclick="downloadExport('eval_policy.md')">评测策略</button>
                  <button onclick="downloadExport('eval_policy.json')">评测策略JSON</button>
                  <button onclick="downloadExport('scene_classification.md')">场景分类</button>
                  <button onclick="downloadExport('context_pack_schema.md')">上下文包</button>
                  <button onclick="downloadExport('intent_planner_prompt.md')">意图提示词</button>
                  <button onclick="downloadExport('intent_binding_spec.md')">意图绑定</button>
                  <button onclick="downloadExport('intent_eval_cases.json')">意图评测</button>
                  <button onclick="downloadExport('binding_eval_cases.json')">绑定评测</button>
                  <button onclick="downloadExport('planner_prompt.md')">planner prompt</button>
                  <button onclick="downloadExport('executor_skeleton.py')">executor.py</button>
                  <button onclick="downloadExport('development_plan.md')">开发计划</button>
                  <button onclick="downloadExport('development_advice.md')">下一步建议</button>
                  <button onclick="downloadExport('development_advice.json')">建议JSON</button>
                </span>
              </span>
            </span>
          </h2>
          <details class="preview-dev-details" open>
            <summary>Demo zip 使用说明：下载后怎么启动和验证</summary>
            <div class="desc">这个说明专门对应“生成可运行 Demo zip”。下载解压后，进入生成项目的 `backend` 目录执行下面命令。</div>
            <pre><code>cd test-agent-demo/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000</code></pre>
            <div class="desc">启动后优先验证这 4 个接口：</div>
            <pre><code># 单轮 Agent 运行：看 Context Pack、OpCall、Validator、Tool、State、Artifact、Trace
curl -X POST http://127.0.0.1:8000/agent/run \
  -H 'Content-Type: application/json' \
  -d '{"message":"测试一句用户话术","context":{"current_state":"drafting"}}'

# Workflow 节点级运行
curl -X POST http://127.0.0.1:8000/workflow/run \
  -H 'Content-Type: application/json' \
  -d '{"message":"按当前 workflow 跑一次","context":{"current_state":"drafting"},"max_steps":3}'

# 查看 Demo 内存：状态、记忆、产物版本、Trace、Job
curl http://127.0.0.1:8000/store/snapshot

# 查看 Tool Adapter dry-run 模板
curl http://127.0.0.1:8000/tools</code></pre>
            <div class="preview-tip"><strong>重点看：</strong> `/agent/run` 返回里是否有 `tool_results`、`state_snapshot`、`artifact_versions`、`trace`；`/workflow/run` 是否有 `node_states`。</div>
          </details>
          <details class="preview-dev-details">
            <summary>Delegated Agent zip 使用说明：独立部署 + fake runner + open_claude</summary>
            <div class="desc">这个说明对应“生成 Delegated Agent zip”。它不是普通 Demo，而是一个可独立部署的任务型 Agent 服务，默认内置 open_claude，并先用 fake runner 验证闭环。</div>
            <pre><code>unzip your-agent-delegated-agent.zip
cd your-agent/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp ../.env.example ../.env
uvicorn app.main:app --host 0.0.0.0 --port 8000</code></pre>
            <div class="desc">启动后打开 Web 控制台：</div>
            <pre><code>http://127.0.0.1:8000</code></pre>
            <div class="desc">第一条验收路径默认是 fake runner，不需要 LLM，不需要 Node，提交任务后应生成：</div>
            <pre><code>data/jobs/&lt;job_id&gt;/trace/task_pack.md
data/jobs/&lt;job_id&gt;/trace/stdout.log
data/jobs/&lt;job_id&gt;/artifacts/report.md
data/jobs/&lt;job_id&gt;/artifacts/result.json</code></pre>
            <div class="desc">要接真实 open_claude，把项目根目录 `.env` 改成：</div>
            <pre><code>OPEN_CLAUDE_FAKE=0
OPENAI_BASE_URL=http://your-gateway/v1
OPENAI_API_KEY=sk-...
OPENAI_MODEL=your-model</code></pre>
            <div class="preview-tip"><strong>边界：</strong>V1 是单进程轻量版，只适合本地/可信内网。真实 open_claude 具备文件和命令执行能力，不要公网裸露。</div>
          </details>
          <pre id="exports">等待生成...</pre>
        </div>
      </div>
      <div class="right-tab-panel" data-right-panel="develop">
        <div class="pane">
          <h2>开发入口 <span class="pill">预览、学习、生成、沙箱</span></h2>
          <div class="dev-entry-grid">
            <div class="dev-entry-card full">
              <h3>现在先开发什么</h3>
              <p>这里会根据当前协议状态，自动判断你应该先补协议、先跑预览、先生成 Demo，还是先实现校验器/执行器。</p>
              <div id="developmentAdvice" class="advice-preview">等待生成建议...</div>
              <button onclick="downloadExport('development_advice.md')">下载下一步建议</button>
            </div>
            <div class="dev-entry-card">
              <h3>1. 预览运行</h3>
              <p>先用当前协议跑一次模拟链路，看上下文、意图、操作、校验、权限、恢复和评测沉淀是否符合预期。</p>
              <button onclick="openPreview()" class="primary">打开预览运行</button>
            </div>
            <div class="dev-entry-card">
              <h3>2. Runtime 运行</h3>
              <p>按 workflow 节点级模拟运行，看到每个节点状态、人工确认暂停、产物和 Trace。</p>
              <button onclick="openRuntime()" class="primary">打开 Runtime</button>
            </div>
            <div class="dev-entry-card">
              <h3>3. 架构笔记</h3>
              <p>忘记 ReAct、能力协议、记忆、状态、工具、权限边界时，从这里快速复习 APD 的设计思路。</p>
              <button onclick="openGuide()">查看架构笔记</button>
            </div>
            <div class="dev-entry-card">
              <h3>4. 生成 Demo</h3>
              <p>把当前协议导出成可运行 Agent Harness Demo，方便交给 Codex/Claude 或直接继续开发。</p>
              <button onclick="downloadScaffold()">下载 Demo zip</button>
            </div>
            <div class="dev-entry-card">
              <h3>5. Demo Playground</h3>
              <p>不下载 zip，直接在 APD 内生成、启动并测试当前 Agent Demo。</p>
              <button onclick="openDemoPlayground()" class="primary">在线运行 Demo</button>
            </div>
            <div class="dev-entry-card">
              <h3>6. 沙箱验证</h3>
              <p>把生成 Demo 或目标项目放进 Docker 临时环境，运行冒烟测试或工程 Agent 检查。</p>
              <button onclick="openSandbox()">打开 Docker 沙箱</button>
            </div>
            <div class="dev-entry-card">
              <h3>Dynamic Workflow</h3>
              <p>如果场景是多阶段、多分支、并行、人工确认或强校验，进入动态工作流说明。</p>
              <button onclick="openDynamicWorkflow()">查看工作流架构</button>
            </div>
            <div class="dev-entry-card">
              <h3>招投标验证</h3>
              <p>查看 C 方案如何验证 APD 的 workflow、artifact、knowledge、人工确认和导出能力。</p>
              <button onclick="openBidValidation()">查看招投标验证</button>
            </div>
            <div class="dev-entry-card">
              <h3>AgentOS 评估</h3>
              <p>判断 APD 是否应该继续平台化，以及下一阶段应该先补哪些 Runtime 能力。</p>
              <button onclick="openAgentOsEvaluation()">查看 AgentOS 评估</button>
            </div>
            <div class="dev-entry-card">
              <h3>APD 能力边界</h3>
              <p>集中查看 APD 当前能做什么、不能做什么、下一阶段应该补什么，防止方向跑偏。</p>
              <button onclick="openCapabilityBoundary()">查看能力边界</button>
            </div>
            <div class="dev-entry-card">
              <h3>Agent Registry</h3>
              <p>把当前会话设计出的 Agent / Workflow 注册成可复用、可复制、可版本管理的条目。</p>
              <button onclick="openAgentRegistry()">打开 Agent Registry</button>
            </div>
            <div class="dev-entry-card">
              <h3>Runtime 观测</h3>
              <p>汇总 Runtime Job、Tool Run、Eval Replay、失败率、等待确认和 Trace 指标。</p>
              <button onclick="openObservability()">打开观测面板</button>
            </div>
            <div class="dev-entry-card">
              <h3>权限审计</h3>
              <p>查看 Runtime、Tool、Eval、Artifact、Registry 的审计事件、敏感字段提示和保留策略。</p>
              <button onclick="openGovernance()">打开权限审计</button>
            </div>
            <div class="dev-entry-card">
              <h3>Multi-Agent 协作</h3>
              <p>查看多个 Agent 如何分工、传消息、交接任务、处理冲突并形成协同 Trace。</p>
              <button onclick="openMultiAgent()">打开协作分析</button>
            </div>
            <div class="dev-entry-card">
              <h3>写作案例</h3>
              <p>查看已落地写作 Agent 的当前架构和未来演进路线，方便对照真实项目。</p>
              <button onclick="openWritingCase()">查看写作案例</button>
            </div>
          </div>
        </div>
      </div>
    </div>
  </section>
</main>
<div class="drawer-mask" id="guideMask" onclick="closeGuide(event)">
  <aside class="drawer" onclick="event.stopPropagation()">
    <div class="drawer-head">
      <h2>Agent 架构笔记</h2>
      <div>
        <button onclick="loadGuide()">刷新</button>
        <button onclick="closeGuide()">关闭</button>
      </div>
    </div>
    <div class="guide-body" id="guide">加载中...</div>
  </aside>
</div>
<div class="drawer-mask" id="workflowMask" onclick="closeDynamicWorkflow(event)">
  <aside class="drawer wide-drawer" onclick="event.stopPropagation()">
    <div class="drawer-head">
      <h2>Dynamic Workflow 架构</h2>
      <div>
        <button onclick="renderDynamicWorkflow()">刷新</button>
        <button onclick="closeDynamicWorkflow()">关闭</button>
      </div>
    </div>
    <div class="guide-body case-body" id="dynamicWorkflow">加载中...</div>
  </aside>
</div>
<div class="drawer-mask" id="caseMask" onclick="closeWritingCase(event)">
  <aside class="drawer wide-drawer" onclick="event.stopPropagation()">
    <div class="drawer-head">
      <h2>写作 Agent 落地案例</h2>
      <div>
        <button onclick="loadWritingCase()">刷新</button>
        <button onclick="closeWritingCase()">关闭</button>
      </div>
    </div>
    <div class="guide-body case-body" id="writingCase">加载中...</div>
  </aside>
</div>
<div class="drawer-mask" id="bidValidationMask" onclick="closeBidValidation(event)">
  <aside class="drawer wide-drawer" onclick="event.stopPropagation()">
    <div class="drawer-head">
      <h2>招投标 Agent 验证</h2>
      <div>
        <button onclick="loadBidValidation()">刷新</button>
        <button onclick="closeBidValidation()">关闭</button>
      </div>
    </div>
    <div class="guide-body case-body" id="bidValidation">加载中...</div>
  </aside>
</div>
<div class="drawer-mask" id="agentOsMask" onclick="closeAgentOsEvaluation(event)">
  <aside class="drawer wide-drawer" onclick="event.stopPropagation()">
    <div class="drawer-head">
      <h2>AgentOS 评估</h2>
      <div>
        <button onclick="loadAgentOsEvaluation()">刷新</button>
        <button onclick="closeAgentOsEvaluation()">关闭</button>
      </div>
    </div>
    <div class="guide-body case-body" id="agentOsEvaluation">加载中...</div>
  </aside>
</div>
<div class="drawer-mask" id="agentRegistryMask" onclick="closeAgentRegistry(event)">
  <aside class="drawer wide-drawer" onclick="event.stopPropagation()">
    <div class="drawer-head">
      <h2>Agent Registry</h2>
      <div>
        <button onclick="registerCurrentAgent()">注册当前 Agent</button>
        <button onclick="loadAgentRegistry()">刷新</button>
        <button onclick="closeAgentRegistry()">关闭</button>
      </div>
    </div>
    <div class="guide-body">
      <p>Agent Registry 用来保存已经设计好的 Agent / Workflow。后续可以从这里复制成新会话，或者给同一个 Agent 增加新版本。</p>
      <div id="agentRegistryList" class="example-list">加载中...</div>
      <div id="agentRegistryDetail" class="preview-result">选择一个 Agent 查看详情。</div>
    </div>
  </aside>
</div>
<div class="drawer-mask" id="observabilityMask" onclick="closeObservability(event)">
  <aside class="drawer wide-drawer" onclick="event.stopPropagation()">
    <div class="drawer-head">
      <h2>Runtime 观测面板</h2>
      <div>
        <button onclick="loadObservability()">刷新</button>
        <button onclick="closeObservability()">关闭</button>
      </div>
    </div>
    <div class="guide-body">
      <div id="observabilityPanel" class="preview-result">加载中...</div>
    </div>
  </aside>
</div>
<div class="drawer-mask" id="capabilityBoundaryMask" onclick="closeCapabilityBoundary(event)">
  <aside class="drawer wide-drawer" onclick="event.stopPropagation()">
    <div class="drawer-head">
      <h2>APD 能力边界</h2>
      <div>
        <button onclick="loadCapabilityBoundary()">刷新</button>
        <button onclick="closeCapabilityBoundary()">关闭</button>
      </div>
    </div>
    <div class="guide-body case-body" id="capabilityBoundary">加载中...</div>
  </aside>
</div>
<div class="drawer-mask" id="governanceMask" onclick="closeGovernance(event)">
  <aside class="drawer wide-drawer" onclick="event.stopPropagation()">
    <div class="drawer-head">
      <h2>权限审计与数据治理</h2>
      <div>
        <button onclick="loadGovernance()">刷新</button>
        <button onclick="closeGovernance()">关闭</button>
      </div>
    </div>
    <div class="guide-body">
      <div id="governancePanel" class="preview-result">加载中...</div>
    </div>
  </aside>
</div>
<div class="drawer-mask" id="historyMask" onclick="closeHistory(event)">
  <aside class="drawer" onclick="event.stopPropagation()">
    <div class="drawer-head">
      <h2>历史会话</h2>
      <div>
        <button onclick="refreshSessions()">刷新</button>
        <button onclick="closeHistory()">关闭</button>
      </div>
    </div>
    <div class="history-list" id="historyList">加载中...</div>
  </aside>
</div>
<div class="drawer-mask" id="previewMask" onclick="closePreview(event)">
  <aside class="drawer wide-drawer" onclick="event.stopPropagation()">
    <div class="drawer-head">
      <h2>预览运行 Agent</h2>
      <div>
        <button onclick="runPreview()">运行</button>
        <button onclick="closePreview()">关闭</button>
      </div>
    </div>
    <div class="guide-body">
      <p>预览运行不会修改真实状态，只会基于当前协议模拟：上下文包 → 意图框架 → 操作调用 → 校验器 → 模拟执行器。</p>
      <div class="beginner-box">
        <h3>先用大白话理解</h3>
        <p>你可以把这次预览想成：用户说了一句话，Agent 先听懂，再决定要不要做事，最后只是演示一下会怎么做。</p>
        <ul>
          <li>它不是完整业务系统，也不会真的改文件、写数据库、导出文档。</li>
          <li>它是在帮你看：当前设计出来的 Agent 会不会理解对、选对操作、该拦的时候有没有拦。</li>
          <li>如果预览都不对，先改协议和操作设计；如果预览对了，再考虑生成代码和真实执行器。</li>
        </ul>
      </div>
      <div class="preview-help">
        <h3>怎么看预览结果</h3>
        <div class="preview-help-grid">
          <div class="preview-help-card"><strong>记忆读取（Memory Retrieval）</strong><p>先按记忆策略找出本轮相关的历史偏好、任务进度或项目规则，再决定是否放进上下文。</p></div>
          <div class="preview-help-card"><strong>上下文包（Context Pack）</strong><p>本轮交给规划器看的信息。它决定 LLM 观察到了什么，也能检查有没有给多、给少、给乱。</p></div>
          <div class="preview-help-card"><strong>状态上下文（State Context）</strong><p>说明任务现在走到哪一步，以及当前阶段允许做什么，避免流程顺序错乱。</p></div>
          <div class="preview-help-card"><strong>产物上下文（Artifact Context）</strong><p>说明本轮会读取、创建、修改或导出哪些产物，以及是否需要快照和审查。</p></div>
          <div class="preview-help-card"><strong>工具计划（Tool Plan）</strong><p>说明这个业务操作底层会用哪些工具，工具是否有副作用和高风险。</p></div>
          <div class="preview-help-card"><strong>权限判断（Permission Check）</strong><p>判断本轮能否自动执行、是否要人工确认、是否触发禁止规则或角色要求。</p></div>
          <div class="preview-help-card"><strong>失败恢复（Error Recovery）</strong><p>如果本轮不能继续，说明应该追问、阻断、重试、回滚、等待确认还是转人工。</p></div>
          <div class="preview-help-card"><strong>意图框架（Intent Frame）</strong><p>LLM 对用户话术的语义理解，例如用户想创建、修改、删除，目标是什么，风险多高。</p></div>
          <div class="preview-help-card"><strong>操作调用（OpCall）</strong><p>把自由意图绑定到有限操作，是真正准备交给程序执行的 operation + params。</p></div>
          <div class="preview-help-card"><strong>校验结果（Validator）</strong><p>程序侧的确定性检查，例如操作是否存在、是否缺信息、是否需要确认。</p></div>
          <div class="preview-help-card"><strong>模拟执行器（Mock Executor）</strong><p>只说明如果真实执行会做什么，不会写库、不会导出、不会修改文档。</p></div>
          <div class="preview-help-card"><strong>过程日志（Trace）</strong><p>用于复盘：每一步为什么这样判断，后续调试误路由、漏校验、上下文错误都看这里。</p></div>
        </div>
      </div>
      <div class="sandbox-form">
        <label class="sandbox-full">测试话术
          <textarea id="previewMessage" placeholder="例如：帮我生成一篇政务通知初稿"></textarea>
        </label>
        <label class="sandbox-full">补充上下文 JSON，可留空
          <textarea id="previewContext" placeholder='例如：{"current_state":"draft_generated","existing_artifacts":[{"id":"draft_document","version":"v1"}],"selected_text":"...","active_section_id":"chapter_2","memory":[{"type":"user_preference_memory","content":"用户偏好正式中文风格"}]}'></textarea>
        </label>
      </div>
      <div class="sandbox-actions">
        <button onclick="generatePreviewExamples()">AI 生成当前场景测试示例</button>
        <button onclick="setPreviewMode(true)">展开开发者细节</button>
        <button onclick="setPreviewMode(false)">收起开发者细节</button>
        <button onclick="runPreview()" class="primary">运行预览</button>
      </div>
      <div id="previewExamples" class="example-list"></div>
      <div id="previewResult" class="preview-result">等待运行...</div>
    </div>
  </aside>
</div>
<div class="drawer-mask" id="runtimeMask" onclick="closeRuntime(event)">
  <aside class="drawer wide-drawer" onclick="event.stopPropagation()">
    <div class="drawer-head">
      <h2>Agent Runtime 雏形</h2>
      <div>
        <button onclick="loadRuntimePlan()">查看计划</button>
        <button onclick="runRuntime()">运行</button>
        <button onclick="closeRuntime()">关闭</button>
      </div>
    </div>
    <div class="guide-body">
      <div class="preview-help">
        <h3>这个入口是干什么的？</h3>
        <p>预览运行看的是“一轮 Agent 怎么理解用户”。Runtime 运行看的是“整个 workflow 怎么按节点一步步执行”。它会展示节点状态、人工确认暂停、模拟产物和过程日志。</p>
      </div>
      <div class="sandbox-form">
        <label class="sandbox-full">本次运行目标 / 用户话术
          <textarea id="runtimeMessage" placeholder="例如：上传招标文件后，生成投标文件目录和需求矩阵"></textarea>
        </label>
        <label class="sandbox-full">运行上下文 JSON，可留空
          <textarea id="runtimeContext" placeholder='例如：{"current_state":"uploaded","existing_artifacts":[{"id":"tender_file","version":"v1"}]}'></textarea>
        </label>
        <label class="sandbox-full">已确认节点 JSON，可留空
          <textarea id="runtimeApprovals" placeholder='例如：{"confirm_requirement_matrix":true}'></textarea>
        </label>
      </div>
      <div class="sandbox-actions">
        <button onclick="loadRuntimePlan()">只看 Runtime 计划</button>
        <button onclick="loadRuntimeJobs()">刷新历史运行</button>
        <button onclick="continueRuntimeJob()">确认并继续当前暂停任务</button>
        <button onclick="runEvalReplay()">运行 Eval 回放</button>
        <button onclick="runToolTests()">运行 Tool 测试</button>
        <button onclick="runRuntime()" class="primary">新建并运行 Runtime</button>
      </div>
      <div id="runtimeJobs" class="example-list"></div>
      <div id="runtimeResult" class="preview-result">等待运行...</div>
    </div>
  </aside>
</div>
<div class="drawer-mask" id="multiAgentMask" onclick="closeMultiAgent(event)">
  <aside class="drawer wide-drawer" onclick="event.stopPropagation()">
    <div class="drawer-head">
      <h2>Multi-Agent 协作雏形</h2>
      <div>
        <button onclick="analyzeMultiAgent()">分析当前会话</button>
        <button onclick="closeMultiAgent()">关闭</button>
      </div>
    </div>
    <div class="guide-body">
      <div class="preview-help">
        <h3>这个入口是干什么的？</h3>
        <p>它不是让多个 LLM 随便聊天，而是把复杂任务拆成有边界的角色：谁规划、谁检索、谁生成、谁执行、谁审校、谁批准，并规定消息格式、交接要求、冲突处理和协同 Trace。</p>
      </div>
      <div class="sandbox-form">
        <label class="sandbox-full">协作目标 / 测试话术
          <textarea id="multiAgentMessage" placeholder="例如：根据招标文件生成投标目录、检索企业素材、并行生成章节，最后人工确认后导出 DOCX"></textarea>
        </label>
      </div>
      <div class="sandbox-actions">
        <button onclick="analyzeMultiAgent()" class="primary">分析多 Agent 协作</button>
        <button onclick="quick('请基于当前协议继续补 Multi-Agent 协作设计：角色分工、消息协议、交接规则、冲突处理和协同 Trace。')">让设计器继续追问</button>
      </div>
      <div id="multiAgentResult" class="preview-result">等待分析...</div>
    </div>
  </aside>
</div>
<div class="drawer-mask" id="demoPlaygroundMask" onclick="closeDemoPlayground(event)">
  <aside class="drawer wide-drawer" onclick="event.stopPropagation()">
    <div class="drawer-head">
      <h2>可运行 Demo Playground</h2>
      <div>
        <button onclick="startDemoPlayground()">生成并启动</button>
        <button onclick="stopDemoPlayground()">停止</button>
        <button onclick="closeDemoPlayground()">关闭</button>
      </div>
    </div>
    <div class="guide-body">
      <div class="beginner-box">
        <h3>小白版：点一次，看这个 Agent Demo 到底能不能跑</h3>
        <p>你不用理解接口、不用下载 zip、不用装依赖。点击下面的大按钮后，APD 会自动完成 4 件事：生成 Demo、启动 Demo、跑一句测试话术、把结果翻译成中文说明。</p>
        <p>你只需要看：有没有选中操作、有没有产物版本、有没有过程日志、Workflow 有没有节点结果。</p>
      </div>
      <div class="sandbox-form">
        <label>项目名
          <input id="demoProjectName" placeholder="例如 test-agent-demo；留空自动使用当前项目名" />
        </label>
        <label class="sandbox-full">测试话术
          <textarea id="demoMessage" placeholder="例如：请生成章节内容"></textarea>
        </label>
        <label class="sandbox-full">上下文 JSON，可留空
          <textarea id="demoContext" placeholder='例如：{"current_state":"drafting"}'></textarea>
        </label>
      </div>
      <div class="sandbox-actions">
        <button onclick="runDemoOneClick()" class="primary">一键体验：生成、启动并自动测试</button>
        <button onclick="stopDemoPlayground()">停止 Demo</button>
      </div>
      <details class="preview-dev-details">
        <summary>高级模式：我想分别测试接口</summary>
        <div class="sandbox-actions">
          <button onclick="startDemoPlayground()">1. 只生成并启动</button>
          <button onclick="runDemoAgent()">2. 测试单轮 Agent</button>
          <button onclick="runDemoWorkflow()">3. 测试 Workflow</button>
          <button onclick="loadDemoStore()">查看 Store</button>
          <button onclick="loadDemoTools()">查看 Tools</button>
        </div>
      </details>
      <div id="demoPlaygroundStatus" class="example-list"></div>
      <div id="demoPlaygroundResult" class="preview-result">点击“一键体验”开始。</div>
    </div>
  </aside>
</div>
<div class="drawer-mask" id="delegatedPlaygroundMask" onclick="closeDelegatedPlayground(event)">
  <aside class="drawer wide-drawer" onclick="event.stopPropagation()">
    <div class="drawer-head">
      <h2>Delegated Agent 在线调试台</h2>
      <div>
        <button onclick="startDelegatedPlayground()" class="primary">生成并启动</button>
        <button onclick="stopDelegatedPlayground()">停止</button>
        <button onclick="closeDelegatedPlayground()">关闭</button>
      </div>
    </div>
    <div class="guide-body">
      <div class="beginner-box">
        <h3>作用：不用下载 zip，直接在 APD 里跑 Delegated Agent</h3>
        <p>这里会临时生成一个独立 Delegated Agent 工程，启动 FastAPI 服务，然后你可以像最终用户一样发消息。右侧会显示 Job 状态、Task Pack 过程、产物和 report.md。</p>
        <p><strong>默认 fake runner：</strong>先验证任务链路，不消耗 LLM；切到真实 runner 后才会启动内置 open_claude。</p>
      </div>
      <div class="sandbox-form">
        <label>项目名
          <input id="delegatedProjectName" placeholder="例如 delegated-agent-demo；留空自动使用当前项目名" />
        </label>
        <label>运行模式
          <select id="delegatedFakeRunner">
            <option value="1">fake runner：先验证链路</option>
            <option value="0">真实 open_claude：调用模型和执行器</option>
          </select>
        </label>
        <label class="sandbox-full">Agent 目标
          <textarea id="delegatedAgentGoal" placeholder="留空自动使用当前协议摘要"></textarea>
        </label>
        <label class="sandbox-full">默认任务说明
          <textarea id="delegatedDefaultTask" placeholder="请根据用户输入完成任务，并把最终结果写入 artifacts/report.md 和 artifacts/result.json。"></textarea>
        </label>
        <label class="sandbox-full">open_claude 路径
          <input id="delegatedOpenClaudeSource" placeholder="/home/data/rag/open_claude/Openclaude-openclaude" />
        </label>
      </div>
      <div class="sandbox-actions">
        <button onclick="startDelegatedPlayground()" class="primary">1. 生成并启动</button>
        <button onclick="refreshDelegatedPlaygroundList()">刷新运行列表</button>
        <button onclick="loadDelegatedConfig()">运行前检查</button>
      </div>
      <div id="delegatedPlaygroundStatus" class="example-list"></div>
      <div class="runtime-grid" style="grid-template-columns:minmax(320px,420px) 1fr;">
        <section class="preview-section">
          <h3>对话测试</h3>
          <div class="desc">像最终用户一样输入。每次发送都会创建一个 Job，并持续轮询状态。</div>
          <textarea id="delegatedChatInput" placeholder="例如：请在 artifacts/report.md 写一段 hello delegated agent，并生成 artifacts/result.json。"></textarea>
          <div class="sandbox-actions">
            <button onclick="sendDelegatedMessage()" class="primary">发送给 Agent</button>
            <button onclick="fillDelegatedHello()">填入 hello 验收任务</button>
          </div>
          <div id="delegatedChatLog" class="example-list"></div>
        </section>
        <section class="preview-section">
          <h3>运行过程 / 产物</h3>
          <div class="desc">这里显示 Job 状态、Events、Artifacts 和 report.md。它对应生成工程里的真实 API，而不是 APD 假数据。</div>
          <div id="delegatedPlaygroundResult" class="preview-result">先点击“生成并启动”。</div>
        </section>
      </div>
    </div>
  </aside>
</div>
<div class="drawer-mask" id="interactiveCliMask" onclick="closeInteractiveCli(event)">
  <aside class="drawer wide-drawer cli-drawer" onclick="event.stopPropagation()">
    <div class="drawer-head">
      <h2>工程开发台</h2>
      <div>
        <button type="button" onclick="startTtydCli()" class="primary">启动终端</button>
        <button onclick="stopTtydCli()">停止</button>
        <button onclick="cleanupTtydCli(false)">清理旧终端</button>
        <button id="cliFullscreenBtn" onclick="toggleCliFullscreen()">全屏</button>
        <button onclick="closeInteractiveCli()">关闭</button>
      </div>
    </div>
    <div class="guide-body cli-body">
      <div class="cli-scroll-config">
      <div class="cli-left-top">
      <div class="cli-pane-label">上栏 2/3：需求协作与任务生成</div>
      <section id="cliCollabAssistantPanel" class="cli-command-deck">
        <div class="cli-command-head">
          <div class="cli-command-title">
            <strong>工程需求区</strong>
          </div>
          <span class="cli-command-pill">需求 → AI 开发 → 重启 → 调试 → 修复</span>
        </div>
        <details id="apdFlowDetails" class="apd-flow-details">
          <summary><span>流程进度</span><span id="apdFlowSummary" class="apd-flow-summary">当前：1 需求 · 说清想改什么</span></summary>
          <div id="apdFlowStrip" class="apd-flow-strip"></div>
        </details>
        <div id="apdNextHint" class="apd-next-hint"><strong>下一步：</strong>输入一句需求，点击“帮我推进下一步”。</div>
        <div class="cli-command-grid">
          <div class="cli-command-input">
            <label for="cliCollabUserIntent">你想让这个 Agent 增加/改进什么？</label>
            <textarea id="cliCollabUserIntent" oninput="markPmRequirementChanged()" placeholder="例如：我希望这个 Agent 支持上传业务文件，抽取关键信息，并按规则生成可编辑结果。"></textarea>
          </div>
          <div class="cli-command-actions">
            <label class="cli-mode-select">需求复杂度
              <select id="cliTaskMode" onchange="saveCliTaskMode()">
                <option value="concise">日常补充，默认</option>
                <option value="standard">明确功能</option>
                <option value="deep">复杂改造</option>
              </select>
            </label>
            <button onclick="pmGuideNext()" class="primary">帮我推进下一步</button>
            <button onclick="openAgentIdeDelegatedInspector()" class="primary">打开真实调试台</button>
            <button onclick="openStandaloneRuntimeInspector()">工程 Demo 调试</button>
            <button onclick="resetPmGuideFlow()">重新开始流程</button>
            <details class="cli-help-details">
              <summary>高级手动操作</summary>
              <button onclick="analyzePmRequirement()">只分析需求</button>
              <button onclick="sendPmAction('inspect')">让 AI 看现有项目</button>
              <button onclick="sendPmAction('plan')">让 AI 出实现方案</button>
              <button onclick="sendPmAction('build')">让 AI 开始开发</button>
              <button onclick="sendPmAction('summary')">让 AI 总结结果</button>
            </details>
          </div>
        </div>
        <details class="cli-help-details">
          <summary>使用说明 / 模式区别</summary>
          <div class="cli-command-meta">
            <span>第一次终端安全确认：右侧回车一次</span>
            <span>真实调试台：像最终用户一样对话，并查看 open_claude 执行过程</span>
            <span>不满意结果：继续描述哪里不对</span>
            <span>日常补充/明确功能/复杂改造按需求复杂度选择</span>
          </div>
        </details>
        <div id="cliCollabTaskResult" class="preview-result cli-command-result"><div class="cli-result-card"><h3>从这里开始</h3><p>输入一句业务需求，然后点“帮我推进下一步”。APD 会自动决定是先分析、让 AI 看项目、出方案，还是开始开发。</p></div></div>
      </section>
      </div>
      <div class="cli-left-bottom">
      <div class="cli-pane-label">下栏 1/3：工程控制 / 流程说明 / 高级设置</div>

        <div id="collabWorkspaceConsole" class="collab-workspace-console">
          <div class="collab-console-head">
            <div>
              <strong>工程控制台</strong>
              <div id="collabWorkspaceSummary" class="small">正在读取当前工作区...</div>
            </div>
            <span class="cli-command-pill">工程工作区 / 终端 / 版本 / 下载</span>
          </div>
          <div class="collab-console-actions">
            <button onclick="ensureCollabWorkspace()" class="primary">创建/选择工作区</button>
            <button onclick="openInteractiveCli()">查看右侧执行区</button>
            <button onclick="restartAgentDebugSession()">重启当前 Agent</button>
            <button onclick="openAgentIdeDelegatedInspector()" class="primary">打开真实调试台</button>
            <button onclick="openStandaloneRuntimeInspector()">工程 Demo 调试</button>
            <button onclick="saveCurrentWorkspaceVersion()">保存版本</button>
            <button onclick="downloadCurrentWorkspace()">下载工程</button>
            <button onclick="openDevStudioAdvanced()">高级工具</button>
          </div>
          <div id="collabWorkspaceMiniList" class="collab-workspace-mini-list">暂无工作区信息。</div>
        </div>

      <details class="preview-dev-details">
        <summary>流程向导（1-5步，点击展开）</summary>
        <div class="flow-wrap compact-flow">
          <span class="flow-step">1. APD 对话拆需求</span><span class="flow-arrow">→</span>
          <span class="flow-step">2. 生成协议/脚手架</span><span class="flow-arrow">→</span>
          <span class="flow-step">3. 打开 ttyd 终端</span><span class="flow-arrow">→</span>
          <span class="flow-step">4. 复制任务包给 CLI</span><span class="flow-arrow">→</span>
          <span class="flow-step">5. 运行验收并保存版本</span>
        </div>
        <ol>
          <li><strong>先让 APD 设计清楚：</strong>确认意图、操作、校验、记忆、工具、交付物，不要一上来就写代码。</li>
          <li><strong>再生成工作区：</strong>工作区就是 Claude/Codex/open_claude 后续修改的真实 Agent 工程。</li>
          <li><strong>进入 ttyd 终端：</strong>这里默认使用最近工作区，适合让 CLI 阅读项目、修改代码、运行命令。</li>
          <li><strong>按任务包开发：</strong>从“Agent 开发台”复制工程任务包给终端里的 CLI，改完后运行验收。</li>
        </ol>
        <p><strong>推荐第一句话：</strong><code>请先阅读当前项目结构，告诉我这个 Agent 工程每个关键文件负责什么。先不要修改代码。</code></p>
      </details>
      <div id="cliWorkspaceHint" class="preview-tip">默认工作区：自动使用最近一次。一般只需要打开本页等待 ttyd 自动启动。</div>

      <details class="preview-dev-details">
        <summary>高级设置：Runner / 模型 / 工作区 / 备用终端</summary>
        <div class="cli-workspace-panel">
          <div class="cli-workspace-row">
            <label>工作区名称，可留空
              <input id="cliWorkspaceName" placeholder="例如 writing-agent-cli；留空自动使用当前会话标题" />
            </label>
            <button onclick="createCliWorkspace()" class="primary">创建并使用</button>
            <button onclick="refreshCliWorkspaces()">刷新工作区</button>
          </div>
          <div id="cliWorkspaceList" class="cli-workspace-list">正在加载工作区...</div>
        </div>

        <div class="sandbox-form">
          <label>Runner
            <select id="cliRunner">
              <option value="open_claude">open_claude</option>
              <option value="codex">codex</option>
              <option value="claude_code">claude_code</option>
              <option value="custom_shell">custom_shell</option>
            </select>
          </label>
          <label>模型，可留空
            <input id="cliModel" placeholder="例如 claude-sonnet-4-6 / 网关模型名" />
          </label>
          <label>Base URL
            <input id="cliBaseUrl" placeholder="例如 http://192.168.1.156:18888/v1，可留空" />
          </label>
          <label>Key / Token
            <input id="cliApiKey" type="password" placeholder="只在本次浏览器保存；后端日志会脱敏" />
          </label>
          <label>open_claude CLI 目录
            <input id="cliRoot" placeholder="/home/data/rag/open_claude/Openclaude-openclaude" />
          </label>
          <label>目标项目目录，可留空用当前工作区
            <input id="cliWorkDir" placeholder="CLI 的 cwd；留空使用当前工作区项目目录" />
          </label>
          <label class="sandbox-full">启动时提示词，可留空
            <textarea id="cliInitialPrompt" placeholder="例如：请先阅读当前项目结构，告诉我这个 Agent 工程的主要文件分别负责什么，不要修改代码。"></textarea>
          </label>
          <label class="sandbox-full">自定义命令模板，仅 custom_shell 需要
            <textarea id="cliCommandTemplate" placeholder="可用变量：{prompt} {project_root}"></textarea>
          </label>
        </div>
        <div class="sandbox-actions">
          <button onclick="fillInteractiveCliDefaults()">填入 open_claude 默认配置</button>
          <button onclick="saveInteractiveCliSettings()">保存配置</button>
          <button type="button" onclick="startTtydCli()" class="primary">重启 ttyd</button>
          <button type="button" onclick="cleanupTtydCli(true)">清理全部 APD 旧终端</button>
          <button type="button" data-cli-action="start" onclick="startInteractiveCli(event)">启动备用 xterm</button>
          <button onclick="stopInteractiveCli()">停止 xterm</button>
          <button onclick="toggleCliFullscreen()">切换全屏</button>
        </div>
      </details>
      </div>
      </div>
      <div class="cli-terminal-wrap">
      <div id="ttydStatus" class="ttyd-status">ttyd 终端未启动。</div>
      <iframe id="ttydFrame" class="ttyd-frame" title="ttyd Web Terminal"></iframe>
      <details class="preview-dev-details" style="display:none;"><summary>备用 xterm 终端（如果 ttyd 不可用再展开）</summary>
      <div id="cliTerminal" class="cli-terminal"></div>
      <div class="cli-help">
        <strong>备用输入规则：</strong>xterm 模式下方向键、回车、Ctrl+C 直接在黑色终端里操作；中文和长文本用底部命令栏发送。
      </div>
      <div class="cli-send-bar">
        <span class="cli-send-prefix">中文&gt;</span>
        <textarea id="cliTextInput" rows="1" placeholder="输入中文需求或长文本，Enter 发送，Shift+Enter 换行"></textarea>
        <span class="cli-send-tip">Enter 发送 · Shift+Enter 换行</span>
        <button onclick="sendInteractiveCliText()" class="primary">发送</button>
      </div>
      <div id="cliFallbackInput" class="cli-help" style="display:none;">
        <textarea id="cliFallbackText" style="width:100%; min-height:70px; background:#020617; color:#e5e7eb; border:1px solid var(--border); border-radius:9px; padding:8px;" placeholder="终端组件不可用时，在这里输入要发送给 CLI 的内容，然后点发送。"></textarea>
        <button onclick="sendInteractiveCliFallback()" class="primary">发送到 CLI</button>
      </div>
      </details>
      </div>
    </div>
  </aside>
</div>
<div class="drawer-mask" id="agentDebugMask" onclick="closeAgentDebug(event)">
  <aside class="drawer debug-drawer" onclick="event.stopPropagation()">
    <div class="drawer-head">
      <h2>Runtime Inspector</h2>
      <div>
        <button onclick="runCliAgentDebug()" class="primary">发送当前需求</button>
        <button onclick="sendLatestAgentDebugFixToCli()">修复上一轮问题</button>
        <button onclick="restartAgentDebugSession()">重启当前 Agent</button>
        <button onclick="stopAgentDebugSession()">停止会话</button>
        <button onclick="closeAgentDebug()">关闭</button>
      </div>
    </div>
    <div class="debug-body">
      <div class="debug-chat-panel">
        <div id="agentDebugConversation" class="debug-chat-history"><div class="debug-chat-empty">这里会显示连续调试对话。输入测试话术后，Agent 会在同一个调试会话里持续运行。</div></div>
        <div class="debug-chat-input">
          <textarea id="agentDebugMessage" placeholder="输入一轮测试话术，例如：帮我按模板生成一段报告"></textarea>
          <button onclick="sendAgentDebugTurn()" class="primary">发送测试</button>
          <button onclick="sendLatestAgentDebugFixToCli()">修复上一轮问题</button>
          <button onclick="stopAgentDebugSession()">停止会话</button>
        </div>
      </div>
      <div id="agentDebugResult" class="debug-result">
        <div class="agent-debug-shell"><div class="agent-debug-hero"><h3>等待运行</h3><p>建议使用左侧“打开调试页”在新标签页连续调试；这里保留为备用内嵌视图。</p></div></div>
      </div>
    </div>
  </aside>
</div>
<div class="drawer-mask" id="devStudioMask" onclick="closeDevStudio(event)">
  <aside class="drawer wide-drawer" onclick="event.stopPropagation()">
    <div class="drawer-head">
      <h2>高级工程工具箱</h2>
      <div>
        <button onclick="openInteractiveCli()" class="primary">回到工程开发台</button>
        <button onclick="createDevWorkspace()">创建工作区</button>
        <button onclick="buildDeveloperPlan()">生成任务包</button>
        <button onclick="runDeveloperRunner()">执行 Runner</button>
        <button onclick="closeDevStudio()">关闭</button>
      </div>
    </div>
    <div class="guide-body">
      <div id="devAdvancedTools" class="case-hero">
        <h1>高级工程工具箱</h1>
        <p>主流程已经合并到“工程开发台”。这里保留文件编辑、Runner 细节、工作区列表、快速运行等兜底能力。</p>
        <span class="case-tag">高级工具：文件编辑 / Runner 配置 / 工作区版本 / 手动验收</span>
      </div>

      <div class="flow-wrap">
        <span class="flow-step">高级 1. 工作区</span>
        <span class="flow-arrow">→</span>
        <span class="flow-step">高级 2. 任务包</span>
        <span class="flow-arrow">→</span>
        <span class="flow-step">高级 3. Runner</span>
        <span class="flow-arrow">→</span>
        <span class="flow-step">高级 4. 验收</span>
        <span class="flow-arrow">→</span>
        <span class="flow-step">高级 5. 版本</span>
      </div>


      <details id="collabAssistantPanel" class="preview-dev-details" open>
        <summary>工程开发助手：把你的想法翻译给 open_claude</summary>
        <div class="beginner-box">
          <h3>你不用先想“要改哪个文件、属于哪个架构层”</h3>
          <p>直接用中文描述：我想加什么能力、哪里效果不好、报了什么错。APD 会先判断这是新增功能、修 Bug、效果优化、工具接入还是验收问题，再整理成 open_claude 能执行的工程任务。</p>
        </div>
        <div class="sandbox-form">
          <label class="sandbox-full">你的想法 / 问题 / 现象
            <textarea id="collabUserIntent" placeholder="例如：我想让这个 Agent 支持用户上传 Word 模板，然后按模板生成文档。或者：运行后效果很泛，没有按招标文件评分点生成目录。"></textarea>
          </label>
        </div>
        <div class="sandbox-actions">
          <button onclick="buildCollabTask()" class="primary">生成 open_claude 任务</button>
          <button onclick="copyCollabTask()">复制任务</button>
          <button onclick="useCollabAsDeveloperRequest()">同步到主流程</button>
          <button onclick="openInteractiveCli()">打开终端</button>
          <button onclick="runDevWorkspace()">运行验收</button>
          <button onclick="saveDevVersion()">保存版本</button>
        </div>
        <div class="preview-tip"><strong>推荐用法：</strong>先生成任务 → 复制 → 打开终端 → 粘贴给 open_claude。open_claude 改完后，回到这里点“运行验收”；不满意就继续描述问题。</div>
        <div id="collabTaskResult" class="preview-result">描述你的想法，APD 会把它翻译成“工程任务包 + 下一步动作”。</div>
      </details>

      <details class="preview-dev-details" open>
        <summary>主流程：AI 工程开发</summary>
        <div class="sandbox-form">
          <label class="sandbox-full">Agent 改造需求
            <textarea id="developerRequest" placeholder="例如：帮我把这个 Agent 改成生成章节前先通过 tools.py 检索知识库素材，并在 workflow.py 里体现检索节点。"></textarea>
          </label>
          <label>Runner
            <select id="developerRunner">
              <option value="open_claude">open_claude</option>
              <option value="codex">codex</option>
              <option value="claude_code">claude_code</option>
              <option value="custom_shell">custom_shell</option>
            </select>
          </label>
          <label>模型，可留空
            <input id="developerModel" placeholder="例如 claude-sonnet-4-6 / 网关模型名" />
          </label>
        </div>
        <div class="sandbox-actions">
          <button onclick="buildDeveloperPlan()" class="primary">1. 生成架构方案和任务包</button>
          <button onclick="copyDeveloperTask()">复制任务包</button>
          <button onclick="runDeveloperRunner()" class="primary">2. 执行选中 Runner</button>
          <button onclick="runDevWorkspace()">3. 运行当前 Agent 验收</button>
          <button onclick="saveDevVersion()">4. 满意后保存版本</button>
        </div>
        <div class="preview-tip"><strong>重点：</strong>Runner 不直接决定 Agent 架构。APD 会先生成包含架构层、推荐文件、风险边界和验收标准的任务包。</div>
        <div id="developerPlanResult" class="preview-result">等待生成 Agent 架构改造方案...</div>
      </details>

      <details class="preview-dev-details">
        <summary>Runner 配置：URL / Key / 目录 / 命令模板</summary>
        <div class="sandbox-form">
          <label>Base URL
            <input id="developerBaseUrl" placeholder="例如 http://192.168.1.156:18888/v1，可留空" />
          </label>
          <label>Key / Token
            <input id="developerApiKey" type="password" placeholder="只在本次浏览器保存；后端日志会脱敏" />
          </label>
          <label>open_claude CLI 目录
            <input id="developerCliRoot" placeholder="/home/data/rag/open_claude/Openclaude-openclaude" />
          </label>
          <label>目标项目目录，可留空用当前工作区
            <input id="developerWorkDir" placeholder="要让 Runner 修改的 Agent 项目目录" />
          </label>
          <label>超时秒数
            <input id="developerTimeout" type="number" min="30" max="3600" step="30" value="900" />
          </label>
          <label class="sandbox-full">自定义命令模板，仅 custom_shell 或高级用法需要
            <textarea id="developerCommandTemplate" placeholder="可用变量：{task} {task_file} {project_root}"></textarea>
          </label>
        </div>
        <div class="sandbox-actions">
          <button onclick="fillDeveloperRunnerDefaults()">填入 open_claude 默认目录</button>
        </div>
        <div class="desc">open_claude / codex / claude_code / custom_shell 都走同一个任务包。Key 不会返回页面日志，但会保存在当前浏览器 localStorage。</div>
      </details>

      <details class="preview-dev-details" open>
        <summary>工作区与运行验收</summary>
        <div class="sandbox-form">
          <label>工作区 / 项目名
            <input id="devWorkspaceName" placeholder="例如 writing-agent-studio；留空自动使用当前会话标题" />
          </label>
          <label class="sandbox-full">测试话术
            <textarea id="devRunMessage" placeholder="例如：请根据招标文件生成投标目录"></textarea>
          </label>
          <label class="sandbox-full">上下文 JSON，可留空
            <textarea id="devRunContext" placeholder='例如：{"current_state":"drafting"}'></textarea>
          </label>
        </div>
        <div class="sandbox-actions">
          <button onclick="createDevWorkspace()" class="primary">创建工作区</button>
          <button onclick="refreshDevWorkspaces()">刷新工作区</button>
          <button onclick="runDevWorkspace()" class="primary">运行当前版本</button>
          <button onclick="downloadDevWorkspace('current')">下载当前版本</button>
        </div>
        <div id="devStudioWorkspaces" class="example-list"></div>
      </details>

      <details class="preview-dev-details">
        <summary>快速改回复：只用于验证边改边看</summary>
        <div class="sandbox-form">
          <label class="sandbox-full">把运行回复改成这句话
            <textarea id="devQuickReply" placeholder="例如：我已经按你的要求完成处理，这是页面内实时修改后的回复。"></textarea>
          </label>
        </div>
        <div class="sandbox-actions">
          <button onclick="quickReplyAndRunDev()" class="primary">保存回复并立即运行</button>
        </div>
        <div class="desc">这个功能只是为了快速验证工作区能被修改并马上运行，不是开发台主流程。</div>
      </details>

      <details class="preview-dev-details">
        <summary>页面内文件编辑器：开发者临时查看/微调代码</summary>
        <div class="sandbox-form">
          <label>选择要编辑的文件
            <select id="devFileSelect" onchange="loadDevFile()"></select>
          </label>
          <label class="sandbox-full">文件内容
            <textarea id="devFileEditor" style="min-height:360px;font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace;font-size:12px;"></textarea>
          </label>
        </div>
        <div class="sandbox-actions">
          <button onclick="loadDevFiles()">刷新文件列表</button>
          <button onclick="loadDevFile()">读取文件</button>
          <button onclick="saveDevFile()">保存文件</button>
          <button onclick="saveDevFileAndRun()" class="primary">保存并立即运行</button>
        </div>
        <div class="desc">主流程建议使用 Developer Runner；文件编辑器只用于临时查看或小改。</div>
      </details>

      <h2>执行 / 验收结果</h2>
      <div id="devStudioResult" class="preview-result">先创建或选择工作区，然后从“主流程：AI 工程开发”开始。</div>
    </div>
  </aside>
</div>
<div class="drawer-mask" id="sandboxMask" onclick="closeSandbox(event)">
  <aside class="drawer wide-drawer" onclick="event.stopPropagation()">
    <div class="drawer-head">
      <h2>Docker 沙箱运行</h2>
      <div>
        <button onclick="runSandbox()">运行</button>
        <button onclick="closeSandbox()">关闭</button>
      </div>
    </div>
    <div class="guide-body">
      <p>当前版本会复制目标项目到临时目录，只把临时副本挂载到 Docker 容器，运行后返回 stdout / stderr / diff / APD_RESULT.json。</p>
      <div class="sandbox-form">
        <label class="sandbox-full">目标项目目录
          <input id="sandboxProjectPath" placeholder="例如 /home/data/rag/ragyuyan/rag_agent；留空则自动生成当前 APD 脚手架" />
        </label>
        <label>执行器
          <select id="sandboxRunner" onchange="applySandboxRunnerPreset()">
            <option value="custom">custom</option>
            <option value="codex">codex</option>
            <option value="claude">claude</option>
          </select>
        </label>
        <label>Docker 镜像
          <input id="sandboxImage" placeholder="例如 python:3.12-slim / apd-codex-runner:latest" value="python:3.12-slim" />
        </label>
        <label>超时秒
          <input id="sandboxTimeout" type="number" min="10" max="3600" step="10" value="300" />
        </label>
        <label>网络
          <select id="sandboxNetwork">
            <option value="bridge">bridge，可访问网络</option>
            <option value="none">none，禁用网络</option>
            <option value="host">host，宿主网络</option>
          </select>
        </label>
        <label class="sandbox-full">任务说明
          <textarea id="sandboxTask" placeholder="描述要交给工程 Agent 的任务。"></textarea>
        </label>
        <label class="sandbox-full">容器内执行命令
          <textarea id="sandboxCommand"></textarea>
        </label>
      </div>
      <div class="sandbox-actions">
        <button onclick="fillSandboxSmokeCommand()">填入冒烟测试命令</button>
        <button onclick="saveSandboxSettings()">保存沙箱配置</button>
        <button onclick="runSandbox()" class="primary">运行 Docker 沙箱</button>
      </div>
      <pre id="sandboxResult">等待运行...</pre>
    </div>
  </aside>
</div>
<script src="/static/vendor/xterm4/xterm.js?v=20260610c"></script>
<script src="/static/vendor/xterm4-addon-fit/addon-fit.js?v=20260610c"></script>
<script>
let sessionId = localStorage.getItem('apd_session_id') || '';
let protocol = {};
let currentRuntimeJobId = localStorage.getItem('apd_runtime_job_id') || '';

function toggleExportMenu(event) {
  if (event) event.stopPropagation();
  const menu = document.getElementById('exportActions');
  if (menu) menu.classList.toggle('open');
}
function closeExportMenu() {
  const menu = document.getElementById('exportActions');
  if (menu) menu.classList.remove('open');
}
document.addEventListener('click', closeExportMenu);

function focusDesignerInput() {
  try {
    const input = document.getElementById('message');
    if (input) { input.focus(); input.scrollIntoView({behavior:'smooth', block:'center'}); }
    setStatus('从这里开始描述你要设计的 Agent 场景', 'ok');
  } catch (err) {}
}
function switchRightTab(tab) {
  const selected = tab || 'protocol';
  document.querySelectorAll('[data-right-tab]').forEach(btn => btn.classList.toggle('active', btn.dataset.rightTab === selected));
  document.querySelectorAll('[data-right-panel]').forEach(panel => panel.classList.toggle('active', panel.dataset.rightPanel === selected));
  localStorage.setItem('apd_right_tab', selected);
}
function protocolHasOperations() {
  const ops = (protocol || {}).operations || [];
  const intents = (protocol || {}).intent_recognition || {};
  return Array.isArray(ops) ? ops.length > 0 : !!ops || Object.keys(intents || {}).length > 0;
}
function buildLandingGuideState() {
  const hasSession = !!sessionId;
  const hasProtocol = !!protocol && Object.keys(protocol || {}).length > 0;
  const hasOps = protocolHasOperations();
  const hasWorkspace = !!currentDevWorkspaceId || !!localStorage.getItem('apd_dev_workspace_id');
  const hasTtyd = !!currentTtydSessionId || !!localStorage.getItem('apd_ttyd_session_id');
  const hasTask = !!document.getElementById('developerTaskText');
  if (!hasSession) return {stage:'0. 新建会话', next:'先新建或打开一个会话，再描述你要落地的 Agent。', prompt:'我想设计一个 Agent，它要解决的业务问题是：', actions:[['新会话','resetSession','primary']]};
  if (!hasProtocol || !hasOps) return {stage:'1. 需求澄清', next:'先把场景说清楚，让 APD 一次只问一个问题，收敛出核心操作和边界。', prompt:'请一步一步问我问题，帮我把这个 Agent 场景拆成意图、操作、状态、工具、校验和交付物。', actions:[['继续设计','focusDesignerInput','primary'], ['场景判断','quickSceneClassify','']]};
  if (!hasWorkspace) return {stage:'2. 生成工程', next:'协议已经有雏形了，下一步创建可持续开发工作区，作为 open_claude 修改的真实项目。', prompt:'请基于当前协议生成开发落地计划，告诉我应该先实现哪些状态、规划器、校验器和执行器。', actions:[['打开工程开发台','openCliCollabAssistant','primary'], ['创建工作区','createDevWorkspace','']]};
  if (!hasTtyd) return {stage:'3. 接入 open_claude', next:'工作区已准备好，下一步打开终端，让 open_claude 先阅读项目结构，不要急着改代码。', prompt:'请先阅读当前项目结构，告诉我这个 Agent 工程每个关键文件负责什么。先不要修改代码。', actions:[['打开工程开发台','openCliCollabAssistant','primary'], ['生成任务包','buildDeveloperPlan','']]};
  if (!hasTask) return {stage:'4. 生成任务包', next:'终端已接入。现在让 APD 生成工程任务包，再复制给 open_claude 执行，避免随口开发导致失控。', prompt:'请根据当前协议和工作区，生成一份给 open_claude 的工程任务包：包含目标、要改文件、禁止事项、验收标准和运行命令。', actions:[['打开工程开发台','openCliCollabAssistant','primary'], ['生成任务包','buildDeveloperPlan','']]};
  return {stage:'5. 协作开发/验收', next:'把任务包发给 open_claude，改完后运行 Demo 验收；符合预期就保存版本，不符合就生成下一轮修复任务包。', prompt:'请按这个任务包修改项目。改完后说明改了哪些文件、如何运行、如何验证。不要改无关文件。', actions:[['打开工程开发台','openCliCollabAssistant','primary'], ['运行验收','runDevWorkspace',''], ['保存版本','saveDevVersion','']]};
}
function renderLandingGuide() {
  const el = document.getElementById('landingGuide');
  if (!el) return;
  const state = buildLandingGuideState();
  const actions = (state.actions || []).map(([label, fn, cls]) => `<button class="${escapeHtml(cls || '')}" onclick="${escapeHtml(fn)}()">${escapeHtml(label)}</button>`).join('');
  el.innerHTML = `<div class="landing-guide-head"><div class="landing-guide-title">Agent 落地向导</div><div class="landing-guide-stage">${escapeHtml(state.stage)}</div></div>
    <div class="landing-guide-body">
      <div class="landing-guide-box"><strong>下一步：</strong>${escapeHtml(state.next)}<div class="landing-guide-actions">${actions}</div></div>
      <div class="landing-guide-box"><strong>推荐发给 open_claude：</strong><div class="landing-guide-prompt">${escapeHtml(state.prompt)}</div><div class="landing-guide-actions"><button onclick="copyLandingPrompt()">复制话术</button></div></div>
    </div>`;
}
function copyLandingPrompt() {
  const state = buildLandingGuideState();
  navigator.clipboard?.writeText(state.prompt || '');
  setStatus('已复制推荐话术', 'ok');
}
function quickSceneClassify() {
  message.value = '请先帮我判断这个 Agent 场景应该用直接问答、单步操作调用、协议驱动多轮操作调用、状态机、agent_loop 还是多 agent 协作。不要直接进入操作设计，先说明为什么。';
  sendMessage();
}

function loadSettings() {
  apiBase.value = localStorage.getItem('apd_api_base') || '';
  apiKey.value = '';
  model.value = localStorage.getItem('apd_model') || '';
  timeout.value = localStorage.getItem('apd_timeout') || '';
  serverTimeout.value = localStorage.getItem('apd_server_timeout') || '';
  maxTokens.value = localStorage.getItem('apd_max_tokens') || '';
  responseFormat.checked = localStorage.getItem('apd_response_format') === '1';
}
function saveSettings() {
  localStorage.setItem('apd_api_base', apiBase.value.trim());
  if (apiKey.value.trim()) localStorage.setItem('apd_api_key', apiKey.value.trim());
  else localStorage.removeItem('apd_api_key');
  localStorage.setItem('apd_model', model.value.trim());
  localStorage.setItem('apd_timeout', timeout.value.trim());
  localStorage.setItem('apd_server_timeout', serverTimeout.value.trim());
  localStorage.setItem('apd_max_tokens', maxTokens.value.trim());
  localStorage.setItem('apd_response_format', responseFormat.checked ? '1' : '0');
  setStatus('配置已保存', 'ok');
}
function settings() {
  return {
    api_base: apiBase.value.trim(),
    api_key: apiKey.value.trim(),
    model: model.value.trim(),
    timeout: timeout.value ? Number(timeout.value) : undefined,
    server_timeout: serverTimeout.value ? Number(serverTimeout.value) : undefined,
    max_tokens: maxTokens.value ? Number(maxTokens.value) : undefined,
    response_format: responseFormat.checked ? '1' : '0',
  };
}
function setStatus(text, cls='') {
  statusEl = document.getElementById('status');
  statusEl.textContent = text;
  statusEl.className = 'status ' + cls;
}
function addMsg(role, content) {
  const div = document.createElement('div');
  div.className = 'msg ' + (role === 'user' ? 'user' : 'assistant');
  div.innerHTML = `<div class="meta">${role === 'user' ? '你' : '设计器'}</div>${escapeHtml(content)}`;
  chat.appendChild(div);
  chat.scrollTop = chat.scrollHeight;
}
function escapeHtml(s) { return (s || '').replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])); }
function renderHistory(history) {
  chat.innerHTML = '';
  (history || []).forEach(item => addMsg(item.role === 'user' ? 'user' : 'assistant', item.content || ''));
}
function openHistory() {
  document.getElementById('historyMask').classList.add('open');
  refreshSessions();
}
function closeHistory(event) {
  if (event && event.target !== document.getElementById('historyMask')) return;
  document.getElementById('historyMask').classList.remove('open');
}
async function refreshSessions() {
  try {
    const res = await fetch('/api/sessions');
    const data = await res.json();
    const list = document.getElementById('historyList');
    const sessions = data.sessions || [];
    if (!sessions.length) { list.innerHTML = '<div class="history-card">暂无历史会话。</div>'; return; }
    list.innerHTML = sessions.map(item => `
      <div class="history-card">
        <div class="history-title">${escapeHtml(item.title || '未命名会话')}</div>
        <div class="history-meta">更新时间：${escapeHtml(item.updated_at || '-')}｜阶段：${escapeHtml(item.stage || '-')}｜消息数：${item.message_count || 0}</div>
        <button onclick="loadSession('${item.session_id}'); closeHistory();">继续这个会话</button>
        <button onclick="deleteSession('${item.session_id}')">删除</button>
      </div>
    `).join('');
  } catch (err) { setStatus('历史加载失败：' + err, 'warn'); }
}
async function loadSession(id) {
  if (!id) return;
  const res = await fetch(`/api/session/${id}`);
  const data = await res.json();
  sessionId = data.session_id;
  localStorage.setItem('apd_session_id', sessionId);
  protocol = data.protocol || {};
  renderHistory(data.history || []);
  const last = data.last_response || {protocol, stage:'discover', next_questions:[]};
  renderProtocol(last, data.exports_preview || {});
  setStatus('已加载历史会话：' + sessionId, 'ok');
}
async function deleteSession(id) {
  if (!id) return;
  if (!confirm('确认删除这个历史会话？删除后不可恢复。')) return;
  const res = await fetch(`/api/session/${id}`, {method:'DELETE'});
  if (!res.ok) { setStatus('删除失败', 'warn'); return; }
  if (id === sessionId) { sessionId = ''; localStorage.removeItem('apd_session_id'); await resetSession(); }
  await refreshSessions();
  setStatus('历史会话已删除', 'ok');
}
async function logout() {
  await fetch('/api/logout', {method:'POST'});
  location.href = '/login';
}
async function resetSession() {
  const res = await fetch('/api/reset', {method:'POST'});
  const data = await res.json();
  sessionId = data.session_id;
  localStorage.setItem('apd_session_id', sessionId);
  protocol = data.protocol;
  chat.innerHTML = '';
  addMsg('assistant', '新会话已创建。请先用一句话描述你想设计的 Agent 场景。我会一次只问一个问题，慢慢帮你拆能力协议。');
  renderProtocol({protocol, stage:'discover', next_questions:[]}, data.exports_preview || {});
  await refreshSessions();
}
async function sendMessage() {
  const text = message.value.trim();
  if (!text) return;
  message.value = '';
  addMsg('user', text);
  setStatus('设计器思考中...', 'warn');
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 180000);
  try {
    const res = await fetch('/api/chat', {method:'POST', headers:{'Content-Type':'application/json'}, signal: controller.signal, body: JSON.stringify({session_id: sessionId, message: text, settings: settings()})});
    const data = await res.json();
    if (!res.ok) { addMsg('assistant', data.error || '请求失败'); setStatus('请求失败', 'warn'); return; }
    sessionId = data.session_id;
    localStorage.setItem('apd_session_id', sessionId);
    protocol = data.protocol;
    addMsg('assistant', data.response.assistant_message || '已更新。');
    renderProtocol(data.response, data.exports_preview || {});
    const cfg = data.llm_config || {};
    setStatus((data.response.fallback ? '离线降级模式' : '已更新协议草案') + ` | ${cfg.api_base || 'no api'} / ${cfg.model || 'no model'} / key=${cfg.has_key ? 'yes' : 'no'} / timeout=${cfg.llm_timeout}s / max=${cfg.max_tokens}`, data.response.fallback ? 'warn' : 'ok');
  } catch (err) {
    addMsg('assistant', '请求超时或网络异常：' + (err && err.message ? err.message : err));
    setStatus('请求失败/超时', 'warn');
  } finally {
    clearTimeout(timer);
  }
}
function renderProtocol(response, exportsPreview) {
  document.getElementById('protocol').textContent = JSON.stringify(response.protocol || protocol || {}, null, 2);
  document.getElementById('stage').textContent = 'stage: ' + (response.stage || 'discover');
  const qs = response.next_questions || [];
  document.getElementById('questions').innerHTML = qs.length ? qs.map((q, i) => `<div class="question">${i+1}. ${escapeHtml(q)}</div>`).join('') : '暂无下一步问题。';
  document.getElementById('exports').textContent = JSON.stringify(exportsPreview || {}, null, 2);
  const advice = (exportsPreview || {})['development_advice.md'] || fallbackDevelopmentAdvice(response.protocol || protocol || {});
  const adviceEl = document.getElementById('developmentAdvice');
  if (adviceEl) adviceEl.innerHTML = renderMarkdown(advice);
  renderLandingGuide();
  refreshCollabWorkspaceConsole();
}
function fallbackDevelopmentAdvice(protocolValue) {
  const ops = (protocolValue || {}).operations || [];
  if (!ops.length) return `# 下一步开发建议

当前建议：先通过对话把业务场景和核心 operation 收敛出来。没有 operation 时，不建议进入执行器开发。`;
  return `# 下一步开发建议

当前建议：先打开“预览运行”，用正常、信息不足、高风险三类话术测试当前协议，然后再生成可运行 Demo。`;
}
function openGuide() {
  document.getElementById('guideMask').classList.add('open');
  loadGuide();
}
function closeGuide(event) {
  if (event && event.target !== document.getElementById('guideMask')) return;
  document.getElementById('guideMask').classList.remove('open');
}
function mdEscape(s) { return (s || '').replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])); }
function inlineMd(s) {
  return mdEscape(s)
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
}
function renderMarkdown(md) {
  const lines = (md || '').split('\n');
  let html = '';
  let inCode = false;
  let code = [];
  let inList = false;
  let inTable = false;
  function closeList(){ if(inList){ html += '</ul>'; inList=false; } }
  function closeTable(){ if(inTable){ html += '</tbody></table>'; inTable=false; } }
  function tableCells(line){ return line.trim().replace(/^\|/, '').replace(/\|$/, '').split('\|').map(x => inlineMd(x.trim())); }
  for (let idx=0; idx<lines.length; idx++) {
    const line = lines[idx];
    if (line.trim().startsWith('```')) {
      if (!inCode) { closeList(); inCode = true; code = []; }
      else { html += '<pre><code>' + mdEscape(code.join('\n')) + '</code></pre>'; inCode = false; }
      continue;
    }
    if (inCode) { code.push(line); continue; }
    if (!line.trim()) { closeList(); closeTable(); continue; }
    if (line.includes('|') && idx + 1 < lines.length && /^\s*\|?\s*:?-{3,}:?/.test(lines[idx+1])) {
      closeList(); closeTable();
      const heads = tableCells(line);
      html += '<table><thead><tr>' + heads.map(c => '<th>'+c+'</th>').join('') + '</tr></thead><tbody>';
      inTable = true; idx++; continue;
    }
    if (inTable && line.includes('|')) {
      const cells = tableCells(line);
      html += '<tr>' + cells.map(c => '<td>'+c+'</td>').join('') + '</tr>';
      continue;
    } else if (inTable) { closeTable(); }
    if (line.startsWith('---')) { closeList(); closeTable(); html += '<hr>'; continue; }
    if (line.startsWith('# ')) { closeList(); closeTable(); html += '<h1>' + inlineMd(line.slice(2)) + '</h1>'; continue; }
    if (line.startsWith('## ')) { closeList(); closeTable(); html += '<h2>' + inlineMd(line.slice(3)) + '</h2>'; continue; }
    if (line.startsWith('### ')) { closeList(); closeTable(); html += '<h3>' + inlineMd(line.slice(4)) + '</h3>'; continue; }
    if (line.startsWith('- ')) { if(!inList){ html += '<ul>'; inList=true; } html += '<li>' + inlineMd(line.slice(2)) + '</li>'; continue; }
    if (/^\d+\.\s+/.test(line)) { if(!inList){ html += '<ul>'; inList=true; } html += '<li>' + inlineMd(line.replace(/^\d+\.\s+/, '')) + '</li>'; continue; }
    if (line.startsWith('> ')) { closeList(); html += '<blockquote>' + inlineMd(line.slice(2)) + '</blockquote>'; continue; }
    closeList(); closeTable(); html += '<p>' + inlineMd(line) + '</p>';
  }
  closeList();
  closeTable();
  if (inCode) html += '<pre><code>' + mdEscape(code.join('\n')) + '</code></pre>';
  return html;
}
async function loadGuide() {
  try {
    const res = await fetch('/api/guide');
    document.getElementById('guide').innerHTML = renderMarkdown(await res.text());
  } catch (err) {
    document.getElementById('guide').textContent = '加载笔记失败：' + err;
  }
}
function openDynamicWorkflow() {
  document.getElementById('workflowMask').classList.add('open');
  renderDynamicWorkflow();
}
function closeDynamicWorkflow(event) {
  if (event && event.target !== document.getElementById('workflowMask')) return;
  document.getElementById('workflowMask').classList.remove('open');
}
function renderDynamicWorkflow() {
  const target = document.getElementById('dynamicWorkflow');
  target.innerHTML = `
    <div class="workflow-hero">
      <h1>Dynamic Workflow：把 Agent 从“一个会推理的角色”升级为“可编排的任务系统”</h1>
      <p>它适合多步骤、流程可变、需要并行、需要校验、需要沉淀经验的复杂 Agent 场景。核心不是让 LLM 一直自由循环，而是让 LLM 参与规划，让代码持有流程，让多个 Agent / Tool / Validator 分工执行。</p>
    </div>

    <h2>1. 为什么需要 Dynamic Workflow</h2>
    <div class="workflow-grid">
      <div class="workflow-card">
        <h3>单个 Agent 容易黑盒</h3>
        <p>如果让一个 Agent 同时负责理解、规划、执行、记忆、校验，流程长了以后很难知道它为什么这样做，也很难稳定复现。</p>
      </div>
      <div class="workflow-card">
        <h3>业务流程天然会变化</h3>
        <p>同样是“生成投标文件”，软件项目、工程项目、服务项目的目录、评分项、素材和校验规则都不同，不能只写死一个流程。</p>
      </div>
      <div class="workflow-card">
        <h3>复杂任务需要并行</h3>
        <p>资格要求、技术要求、商务要求、评分标准、废标项可以并行抽取；章节正文也可以按目录节点并行生成。</p>
      </div>
      <div class="workflow-card">
        <h3>质量必须靠校验链路</h3>
        <p>真正可用的 Agent 系统不能只看生成结果，还要检查覆盖率、证据来源、遗漏项、冲突项、幻觉和高风险操作。</p>
      </div>
    </div>

    <h2>2. 一眼看懂架构</h2>
    ${renderFlow([
      {layer:'Context Pack', impl:'上下文包', desc:'收集目标、输入、状态、历史、约束、用户偏好和外部资料'},
      {layer:'Workflow Planner', impl:'流程规划器', desc:'根据当前场景选择模板流程，或生成本次专用 Workflow Graph'},
      {layer:'Workflow Graph', impl:'工作流图', desc:'显式保存节点、边、并行组、分支、循环、人工确认和失败回退'},
      {layer:'Node Runtime', impl:'节点运行时', desc:'按图执行 Agent Node、Tool Node、Validator Node、Human Review Node'},
      {layer:'Observation Store', impl:'观察记录', desc:'保存每一步输入、输出、日志、错误、证据和中间产物'},
      {layer:'Final Artifact', impl:'最终交付物', desc:'协议、文档、代码、报告、投标文件或其他业务结果'}
    ], true)}

    <h2>3. ReAct、AgentLoop、Dynamic Workflow 怎么区分</h2>
    <table class="workflow-table">
      <thead><tr><th>模式</th><th>核心含义</th><th>适合场景</th><th>不适合点</th></tr></thead>
      <tbody>
        <tr><td>ReAct</td><td>思考、行动、观察的单次/多步推理模式</td><td>小范围工具调用、查询、局部操作</td><td>不等于完整业务 Agent 架构，缺少持久状态和系统级编排</td></tr>
        <tr><td>AgentLoop</td><td>Agent 持续观察上下文、规划下一步、调用工具、再观察</td><td>开放式探索、多轮交互、不确定任务推进</td><td>流程长了容易黑盒，校验和回退不够显式</td></tr>
        <tr><td>Dynamic Workflow</td><td>用显式工作流图组织多个 Agent、工具、校验器和人工确认</td><td>复杂业务、多分支、并行、可验证、可复用的任务系统</td><td>简单问答和单步工具调用没必要上这个复杂度</td></tr>
      </tbody>
    </table>

    <h2>4. 和传统 Agent 架构的比较</h2>
    <table class="workflow-table">
      <thead><tr><th>维度</th><th>Agent 架构</th><th>Dynamic Workflow</th></tr></thead>
      <tbody>
        <tr><td>核心关注</td><td>一个 Agent 有哪些能力、状态、工具、校验器</td><td>多个 Agent / 工具 / 校验器如何组成一次可靠任务流程</td></tr>
        <tr><td>设计问题</td><td>这个 Agent 能做什么、不能做什么、怎么把意图绑定到 OpCall</td><td>哪些节点串行、哪些并行、何时人工确认、失败后怎么回退</td></tr>
        <tr><td>流程控制</td><td>多由 Planner 或 AgentLoop 在上下文里推进</td><td>由显式 Workflow Graph 持有串行、并行、分支、循环和回退</td></tr>
        <tr><td>状态管理</td><td>关注 Agent 自己的状态和业务对象</td><td>还要关注节点状态、依赖状态、中间产物、运行日志和失败状态</td></tr>
        <tr><td>可观察性</td><td>看 Agent 日志和操作结果</td><td>看每个节点的输入、输出、依赖、状态、失败原因和证据</td></tr>
        <tr><td>APD 输出</td><td>objects / operations / validators / planner schema</td><td>再增加 nodes / edges / parallel groups / human review / artifacts</td></tr>
      </tbody>
    </table>

    <h2>5. 什么时候应该用 Dynamic Workflow</h2>
    <div class="workflow-grid">
      <div class="workflow-card">
        <h3>应该使用</h3>
        <ul>
          <li>任务超过 3 个以上阶段</li>
          <li>不同输入会走不同流程</li>
          <li>有多个子任务可以并行</li>
          <li>需要校验、回退、人工确认</li>
          <li>需要把流程沉淀成模板复用</li>
        </ul>
      </div>
      <div class="workflow-card">
        <h3>不必使用</h3>
        <ul>
          <li>普通问答</li>
          <li>一次性摘要</li>
          <li>单次工具调用</li>
          <li>固定且很短的流程</li>
          <li>没有可复用价值的临时任务</li>
        </ul>
      </div>
    </div>

    <h2>6. APD 应该怎么升级</h2>
    <div class="workflow-card">
      <p>APD 原来负责设计“能力协议”，升级后要继续设计“能力编排”。也就是：先知道有哪些原子能力，再知道这些能力如何组成一次可靠的任务系统。</p>
      <pre><code>APD v1：Agent Protocol Designer
  - objects
  - operations
  - validators
  - planner schema
  - executor skeleton

APD v2：Agent + Dynamic Workflow Designer
  - protocol
  - workflow graph
  - agent nodes
  - tool nodes
  - validator nodes
  - human review nodes
  - artifacts
  - failure strategy</code></pre>
    </div>

    <h2>7. Workflow Schema 草案</h2>
    <div class="workflow-card">
      <pre><code>{
  "workflow": {
    "name": "投标文件生成工作流",
    "goal": "根据招标文件生成可编辑、可导出的投标文件",
    "nodes": [
      {"id": "parse_tender", "type": "tool", "op": "parse_document"},
      {"id": "extract_requirements", "type": "agent", "agent": "TenderSemanticAgent"},
      {"id": "validate_semantics", "type": "validator"},
      {"id": "generate_outline", "type": "agent", "agent": "TenderOutlineAgent"}
    ],
    "edges": [
      ["parse_tender", "extract_requirements"],
      ["extract_requirements", "validate_semantics"],
      ["validate_semantics", "generate_outline"]
    ],
    "parallel_groups": ["semantic_extractors", "section_writers"],
    "human_review_points": ["confirm_rejection_items", "confirm_outline"],
    "failure_strategy": ["retry", "fallback", "ask_human", "rollback"],
    "artifacts": ["workflow.json", "agent_protocol.json", "implementation_plan.md"]
  }
}</code></pre>
    </div>

    <h2>8. 招投标系统示例</h2>
    ${renderFlow([
      {layer:'解析招标文件', impl:'Tool Node', desc:'PDF/DOCX → ParsedTenderDocument，纯代码优先，OCR 兜底'},
      {layer:'并行语义抽取', impl:'Agent Group', desc:'资格、技术、商务、评分、废标项并行抽取'},
      {layer:'语义校验', impl:'Validator Node', desc:'检查证据来源、重复、遗漏、冲突和置信度'},
      {layer:'生成响应矩阵', impl:'Agent + Tool', desc:'把招标要求、企业素材、响应章节建立绑定'},
      {layer:'生成投标目录', impl:'Agent Node', desc:'目录覆盖评分项、技术项、商务项和招标格式要求'},
      {layer:'匹配企业素材', impl:'RAG / Graph Node', desc:'从企业知识库检索案例、资质、图片、PDF、人员资料'},
      {layer:'并行生成章节', impl:'Agent Group', desc:'每个章节独立生成，绑定素材和来源证据'},
      {layer:'最终合规检查', impl:'Validator Node', desc:'覆盖率、废标项、格式、幻觉、缺失素材检查'},
      {layer:'编辑与导出', impl:'Tool Node', desc:'进入飞书式编辑器，支持在线修改并导出 DOCX'}
    ], true)}

    <h2>9. APD 对话引导应该怎么变</h2>
    <table class="workflow-table">
      <thead><tr><th>阶段</th><th>原来问什么</th><th>升级后还要问什么</th></tr></thead>
      <tbody>
        <tr><td>目标</td><td>你要设计什么 Agent</td><td>最终交付物是什么，成功标准是什么</td></tr>
        <tr><td>能力</td><td>有哪些对象、操作、校验器</td><td>哪些操作是原子能力，哪些是组合流程</td></tr>
        <tr><td>流程</td><td>较少显式设计</td><td>哪些步骤固定，哪些步骤动态，哪些可以并行</td></tr>
        <tr><td>风险</td><td>哪些操作需要确认</td><td>哪些节点失败要重试、回退、降级或人工确认</td></tr>
        <tr><td>落地</td><td>导出协议和开发计划</td><td>导出 workflow.json、节点实现计划、CLI 调用方式</td></tr>
      </tbody>
    </table>

    <h2>10. CLI / Claude Code 保证</h2>
    <div class="workflow-card">
      <p>升级后 Web UI 和 CLI 必须共用同一个 Core Engine、同一套会话文件、同一套 LLM 配置和同一套导出结果。Claude Code 通过 CLI 调用时，应能读取和续接 Web UI 里设计出的 workflow。</p>
      <pre><code>apd chat
apd resume &lt;session_id&gt;
apd sessions
apd export &lt;session_id&gt;
apd run --input requirement.md

# 导出物建议
workflow.json
agent_protocol.json
implementation_plan.md
planner_prompt.md
executor_skeleton.py</code></pre>
    </div>

    <h2>11. 第一阶段实现边界</h2>
    <ul>
      <li>先做 Workflow 设计与导出，不急着做真实执行器。</li>
      <li>先让协议里出现 workflow schema，再让 Web UI 和 CLI 都能展示、续接和导出。</li>
      <li>先支持手写/LLM 生成 Workflow Graph，不先做复杂可视化拖拽。</li>
      <li>后续再做可视化 DAG、并行子 Agent 执行、节点级日志和失败回退。</li>
    </ul>
  `;
}

function openWritingCase() {
  document.getElementById('caseMask').classList.add('open');
  loadWritingCase();
}
function closeWritingCase(event) {
  if (event && event.target !== document.getElementById('caseMask')) return;
  document.getElementById('caseMask').classList.remove('open');
}
function renderFlow(items, detailed=false) {
  return `<div class="flow-wrap">${(items || []).map((item, idx) => {
    const layer = typeof item === 'string' ? item : item.layer;
    const impl = typeof item === 'string' ? '' : item.impl;
    const desc = typeof item === 'string' ? '' : item.desc;
    return `${idx ? '<div class="flow-arrow">→</div>' : ''}<div class="flow-node"><strong>${escapeHtml(layer || '')}</strong>${impl ? `<span>${escapeHtml(impl)}</span>` : ''}${detailed && desc ? `<span>${escapeHtml(desc)}</span>` : ''}</div>`;
  }).join('')}</div>`;
}
function renderWritingCase(data) {
  const good = (data.good_points || []).map(x => `<li>${escapeHtml(x)}</li>`).join('');
  const gaps = (data.gaps || []).map(x => `
    <div class="gap-card">
      <strong>${escapeHtml(x.name)}</strong>
      <p>风险：${escapeHtml(x.risk)}</p>
      <p>演进：${escapeHtml(x.next)}</p>
    </div>`).join('');
  const roadmap = (data.evolution || []).map(x => `
    <div class="roadmap-item">
      <div class="roadmap-phase">${escapeHtml(x.phase)}</div>
      <div class="roadmap-title">${escapeHtml(x.title)}</div>
      <div>${escapeHtml(x.desc)}</div>
    </div>`).join('');
  const validation = (data.validation_points || []).map(x => `<li>${escapeHtml(x)}</li>`).join('');
  return `
    <div class="case-hero">
      <h1>${escapeHtml(data.title || '写作 Agent 落地案例')}</h1>
      <p>${escapeHtml(data.subtitle || '')}</p>
      <div class="case-tag">当前定位：${escapeHtml(data.current_name || '')}</div>
    </div>
    <div class="case-grid">
      <div class="case-card">
        <h3>实际情况</h3>
        <p>你的写作 Agent 现在是受控操作型，不是让 LLM 自由循环乱调用工具。</p>
        <ul>${good}</ul>
      </div>
      <div class="case-card">
        <h3>一句话结论</h3>
        <p>${escapeHtml(data.conclusion || '')}</p>
        <p>不要推倒重来，应该用失败案例驱动增量升级。</p>
      </div>
    </div>
    <h2>当前实际架构</h2>
    ${renderFlow(data.current_flow || [], true)}
    <h2>未来演进架构</h2>
    ${renderFlow(data.future_flow || [], false)}
    <h2>当前缺口</h2>
    <div class="gap-list">${gaps}</div>
    <h2>APD 验证结论</h2>
    <div class="case-card">
      <h3>${escapeHtml(data.validation_summary || '第 16 项验证结论')}</h3>
      <ul>${validation}</ul>
      <details class="raw-toggle">
        <summary>查看完整验证报告</summary>
        <div>${renderMarkdown(data.validation_markdown || '')}</div>
      </details>
    </div>
    <h2>建议演进路线</h2>
    <div class="roadmap">${roadmap}</div>
    <details class="raw-toggle">
      <summary>查看完整 Markdown 记录</summary>
      <div>${renderMarkdown(data.markdown || '')}</div>
    </details>
  `;
}
async function loadWritingCase() {
  const target = document.getElementById('writingCase');
  target.textContent = '加载中...';
  try {
    const res = await fetch('/api/writing-case?format=json');
    if (!res.ok) throw new Error('HTTP ' + res.status);
    target.innerHTML = renderWritingCase(await res.json());
  } catch (err) {
    try {
      const fallback = await fetch('/api/writing-case');
      target.innerHTML = renderMarkdown(await fallback.text());
    } catch (fallbackErr) {
      target.textContent = '加载案例失败：' + err + ' / ' + fallbackErr;
    }
  }
}
function openBidValidation() {
  document.getElementById('bidValidationMask').classList.add('open');
  loadBidValidation();
}
function closeBidValidation(event) {
  if (event && event.target !== document.getElementById('bidValidationMask')) return;
  document.getElementById('bidValidationMask').classList.remove('open');
}
async function loadBidValidation() {
  const target = document.getElementById('bidValidation');
  target.textContent = '加载中...';
  try {
    const res = await fetch('/api/bid-validation');
    if (!res.ok) throw new Error('HTTP ' + res.status);
    target.innerHTML = renderMarkdown(await res.text());
  } catch (err) {
    target.textContent = '加载招投标验证失败：' + err;
  }
}
function openAgentOsEvaluation() {
  document.getElementById('agentOsMask').classList.add('open');
  loadAgentOsEvaluation();
}
function closeAgentOsEvaluation(event) {
  if (event && event.target !== document.getElementById('agentOsMask')) return;
  document.getElementById('agentOsMask').classList.remove('open');
}
async function loadAgentOsEvaluation() {
  const target = document.getElementById('agentOsEvaluation');
  target.textContent = '加载中...';
  try {
    const res = await fetch('/api/agentos-evaluation');
    if (!res.ok) throw new Error('HTTP ' + res.status);
    target.innerHTML = renderMarkdown(await res.text());
  } catch (err) {
    target.textContent = '加载 AgentOS 评估失败：' + err;
  }
}
function openCapabilityBoundary() {
  document.getElementById('capabilityBoundaryMask').classList.add('open');
  loadCapabilityBoundary();
}
function closeCapabilityBoundary(event) {
  if (event && event.target !== document.getElementById('capabilityBoundaryMask')) return;
  document.getElementById('capabilityBoundaryMask').classList.remove('open');
}
async function loadCapabilityBoundary() {
  const target = document.getElementById('capabilityBoundary');
  target.textContent = '加载中...';
  try {
    const res = await fetch('/api/capability-boundary');
    if (!res.ok) throw new Error('HTTP ' + res.status);
    target.innerHTML = renderMarkdown(await res.text());
  } catch (err) {
    target.textContent = '加载 APD 能力边界失败：' + err;
  }
}
function openGovernance() {
  document.getElementById('governanceMask').classList.add('open');
  loadGovernance();
}
function closeGovernance(event) {
  if (event && event.target !== document.getElementById('governanceMask')) return;
  document.getElementById('governanceMask').classList.remove('open');
}
function renderGovernance(data) {
  const recs = (data.recommendations || []).map(x => `<li>${escapeHtml(x)}</li>`).join('');
  const events = (data.recent_events || []).map(event => `
    <div class="example-card">
      <strong>${escapeHtml(event.action || '')} <span class="pill">${escapeHtml(event.risk || '')}</span></strong>
      <p>范围：${escapeHtml(event.scope || '-')}；资源：${escapeHtml(event.resource_id || '-')}</p>
      <p>责任边界：${escapeHtml(event.responsibility_boundary || '-')}</p>
      <p>敏感提示：${escapeHtml(String((event.sensitive_findings || []).length))}</p>
    </div>`).join('');
  return `
    <section class="beginner-box">
      <h3>权限审计怎么看</h3>
      <p>这里记录关键动作：Runtime 运行、Tool 测试、Eval 回放、Registry 注册、Artifact 回滚等。它帮助你知道谁做了什么、风险多高、是否包含敏感数据、应该保留多久。</p>
    </section>
    <div class="preview-summary">
      <div><strong>审计事件</strong>${escapeHtml(String(data.total ?? 0))}</div>
      <div><strong>高风险</strong>${escapeHtml(String((data.by_risk || {}).high ?? 0))}</div>
      <div><strong>敏感提示</strong>${escapeHtml(String(data.sensitive_finding_count ?? 0))}</div>
      <div><strong>范围数</strong>${escapeHtml(String(Object.keys(data.by_scope || {}).length))}</div>
    </div>
    <section class="diagnostics"><h3>建议</h3><ul>${recs}</ul></section>
    <section class="preview-section"><h3>最近审计事件</h3><div class="desc">只展示最近 20 条，完整数据见 Raw。</div><div class="example-list">${events || '<div class="example-card">暂无审计事件。</div>'}</div></section>
    ${previewSection('Governance Raw', '完整治理与审计数据。', data)}`;
}
async function loadGovernance() {
  governancePanel.innerHTML = '加载审计数据...';
  try {
    const res = await fetch('/api/governance/summary');
    const data = await res.json();
    governancePanel.innerHTML = renderGovernance(data);
    setStatus('权限审计已加载', 'ok');
  } catch (err) {
    governancePanel.innerHTML = '加载权限审计失败：' + escapeHtml(err && err.message ? err.message : err);
    setStatus('权限审计加载失败', 'warn');
  }
}
function openObservability() {
  document.getElementById('observabilityMask').classList.add('open');
  loadObservability();
}
function closeObservability(event) {
  if (event && event.target !== document.getElementById('observabilityMask')) return;
  document.getElementById('observabilityMask').classList.remove('open');
}
function renderObservability(data) {
  const runtime = data.runtime_jobs || {};
  const tools = data.tool_runs || {};
  const evals = data.eval_replays || {};
  const registry = data.agent_registry || {};
  const recs = (data.recommendations || []).map(x => `<li>${escapeHtml(x)}</li>`).join('');
  return `
    <section class="beginner-box">
      <h3>观测面板怎么看</h3>
      <p>这里不是设计协议，而是看 APD 运行后的真实记录：跑过多少 Runtime、多少工具测试、多少 Eval 回放、哪里失败、哪里等人确认。</p>
    </section>
    <div class="preview-summary">
      <div><strong>Runtime Job</strong>${escapeHtml(String(runtime.total ?? 0))}</div>
      <div><strong>等待确认</strong>${escapeHtml(String(runtime.waiting_for_human ?? 0))}</div>
      <div><strong>Tool 失败率</strong>${escapeHtml(String(tools.failure_rate ?? 0))}</div>
      <div><strong>Eval 通过率</strong>${escapeHtml(String(evals.pass_rate ?? 0))}</div>
    </div>
    <section class="diagnostics"><h3>建议</h3><ul>${recs}</ul></section>
    <div class="case-grid">
      <div class="case-card"><h3>Runtime</h3><p>状态：${escapeHtml(JSON.stringify(runtime.statuses || {}))}</p><p>Trace 事件：${escapeHtml(String(runtime.trace_events ?? 0))}</p><p>产物：${escapeHtml(String(runtime.artifact_count ?? 0))}；回滚点：${escapeHtml(String(runtime.rollback_points ?? 0))}</p></div>
      <div class="case-card"><h3>Tool Run</h3><p>运行：${escapeHtml(String(tools.runs ?? 0))}；测试工具：${escapeHtml(String(tools.tool_tests ?? 0))}</p><p>通过：${escapeHtml(String(tools.passed ?? 0))}；失败：${escapeHtml(String(tools.failed ?? 0))}；需确认：${escapeHtml(String(tools.needs_confirmation ?? 0))}</p></div>
      <div class="case-card"><h3>Eval Replay</h3><p>回放：${escapeHtml(String(evals.runs ?? 0))}；用例：${escapeHtml(String(evals.cases ?? 0))}</p><p>通过：${escapeHtml(String(evals.passed ?? 0))}；失败：${escapeHtml(String(evals.failed ?? 0))}</p></div>
      <div class="case-card"><h3>Agent Registry</h3><p>Agent：${escapeHtml(String(registry.agents ?? 0))}</p><p>版本：${escapeHtml(String(registry.versions ?? 0))}</p></div>
    </div>
    ${previewSection('Observability Raw', '完整观测指标，方便开发排查。', data)}`;
}
async function loadObservability() {
  observabilityPanel.innerHTML = '加载观测指标...';
  try {
    const res = await fetch('/api/observability/summary');
    const data = await res.json();
    observabilityPanel.innerHTML = renderObservability(data);
    setStatus('观测指标已加载', 'ok');
  } catch (err) {
    observabilityPanel.innerHTML = '加载观测指标失败：' + escapeHtml(err && err.message ? err.message : err);
    setStatus('观测指标加载失败', 'warn');
  }
}
function openAgentRegistry() {
  document.getElementById('agentRegistryMask').classList.add('open');
  loadAgentRegistry();
}
function closeAgentRegistry(event) {
  if (event && event.target !== document.getElementById('agentRegistryMask')) return;
  document.getElementById('agentRegistryMask').classList.remove('open');
}
function renderAgentRegistryList(agents) {
  if (!agents || !agents.length) {
    agentRegistryList.innerHTML = '<div class="example-card">暂无注册 Agent。点击“注册当前 Agent”保存当前会话协议。</div>';
    return;
  }
  agentRegistryList.innerHTML = agents.map(agent => {
    const summary = agent.summary || {};
    return `<div class="example-card">
      <strong>${escapeHtml(agent.name || agent.agent_id || '')} <span class="pill">${escapeHtml(agent.current_version || '-')}</span></strong>
      <p>${escapeHtml(agent.description || '')}</p>
      <p>操作数：${escapeHtml(String(summary.operation_count ?? 0))}；工作流节点：${escapeHtml(String(summary.workflow_node_count ?? 0))}；版本数：${escapeHtml(String(agent.version_count ?? 0))}</p>
      <button onclick="loadAgentRegistryDetail('${escapeHtml(agent.agent_id || '')}')">查看</button>
      <button onclick="createAgentVersion('${escapeHtml(agent.agent_id || '')}')">保存新版本</button>
      <button onclick="cloneRegisteredAgent('${escapeHtml(agent.agent_id || '')}')">复制成新会话</button>
    </div>`;
  }).join('');
}
async function loadAgentRegistry() {
  agentRegistryList.innerHTML = '<div class="example-card">加载 Agent Registry...</div>';
  try {
    const res = await fetch('/api/registry/agents');
    const data = await res.json();
    renderAgentRegistryList(data.agents || []);
    setStatus('Agent Registry 已加载', 'ok');
  } catch (err) {
    agentRegistryList.innerHTML = '<div class="example-card">加载失败：' + escapeHtml(err && err.message ? err.message : err) + '</div>';
    setStatus('Agent Registry 加载失败', 'warn');
  }
}
async function registerCurrentAgent() {
  if (!sessionId) return;
  const fallback = (protocol.project_name || protocol.domain_summary || '未命名 Agent').toString().slice(0, 60);
  const name = prompt('请输入 Agent 名称', fallback);
  if (name === null) return;
  const description = prompt('请输入描述，可留空', (protocol.domain_summary || '').toString().slice(0, 120));
  try {
    const res = await fetch('/api/registry/register', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({session_id: sessionId, name, description})
    });
    const data = await res.json();
    agentRegistryDetail.innerHTML = previewSection('已注册 Agent', '当前会话协议已保存为 Agent Registry 条目。', data);
    loadAgentRegistry();
    setStatus('Agent 已注册', 'ok');
  } catch (err) {
    setStatus('Agent 注册失败', 'warn');
  }
}
async function loadAgentRegistryDetail(agentId) {
  if (!agentId) return;
  try {
    const res = await fetch(`/api/registry/agent/${encodeURIComponent(agentId)}`);
    const data = await res.json();
    agentRegistryDetail.innerHTML = previewSection('Agent Registry 详情', '包含当前版本、所有版本快照和协议摘要。', data);
    setStatus('Agent 详情已加载', 'ok');
  } catch (err) {
    agentRegistryDetail.innerHTML = '加载 Agent 详情失败：' + escapeHtml(err && err.message ? err.message : err);
  }
}
async function createAgentVersion(agentId) {
  if (!sessionId || !agentId) return;
  const notes = prompt('请输入版本说明', '基于当前会话保存新版本');
  if (notes === null) return;
  const res = await fetch(`/api/registry/agent/${encodeURIComponent(agentId)}/version`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({session_id: sessionId, notes})
  });
  const data = await res.json();
  agentRegistryDetail.innerHTML = previewSection('版本已保存', '已把当前会话协议追加为该 Agent 的新版本。', data);
  loadAgentRegistry();
}
async function cloneRegisteredAgent(agentId) {
  if (!agentId) return;
  const res = await fetch(`/api/registry/agent/${encodeURIComponent(agentId)}/clone`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({})
  });
  const data = await res.json();
  if (!res.ok) { alert(data.error || '复制失败'); return; }
  sessionId = data.session_id;
  localStorage.setItem('apd_session_id', sessionId);
  protocol = data.protocol || {};
  renderSession(data);
  closeAgentRegistry();
  setStatus('已复制为新会话', 'ok');
}
async function downloadExport(name) {
  closeExportMenu();
  if (!sessionId) {
    alert('还没有会话，请先新建或打开一个会话。');
    return;
  }
  const res = await fetch(`/api/export/${sessionId}/${encodeURIComponent(name)}`);
  if (!res.ok) {
    const text = await res.text();
    alert(text || '导出失败：' + name);
    return;
  }
  const text = await res.text();
  const blob = new Blob([text], {type:'text/plain;charset=utf-8'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(a.href);
  setStatus('已导出 ' + name, 'ok');
}
async function downloadScaffold() {
  if (!sessionId) return;
  const fallback = (protocol.project_name || 'generated-agent').toString().trim() || 'generated-agent';
  const name = prompt('请输入生成项目目录名', fallback);
  if (name === null) return;
  setStatus('正在生成可运行 Demo zip...', 'warn');
  const url = `/api/scaffold/${sessionId}.zip?name=${encodeURIComponent(name || fallback)}&template=fastapi-vue`;
  const res = await fetch(url);
  if (!res.ok) {
    const text = await res.text();
    setStatus('脚手架生成失败', 'warn');
    alert(text || '脚手架生成失败');
    return;
  }
  const blob = await res.blob();
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `${(name || fallback).trim() || 'generated-agent'}.zip`;
  a.click();
  URL.revokeObjectURL(a.href);
  setStatus('脚手架 zip 已生成', 'ok');
}
async function downloadDelegatedAgent() {
  if (!sessionId) return;
  const fallback = (protocol.project_name || 'delegated-agent').toString().trim() || 'delegated-agent';
  const projectName = prompt('请输入 Delegated Agent 项目目录名', fallback);
  if (projectName === null) return;
  const agentName = prompt('请输入 Agent 名称', (protocol.project_name || projectName || fallback).toString());
  if (agentName === null) return;
  const agentGoal = prompt('请输入 Agent 目标', (protocol.domain_summary || '接收用户任务，委托内置 open_claude 执行，并返回过程与产物。').toString());
  if (agentGoal === null) return;
  const defaultTask = prompt('请输入默认任务说明', '请根据用户输入完成任务，并把最终结果写入 artifacts/report.md 和 artifacts/result.json。');
  if (defaultTask === null) return;
  const openClaudeSource = prompt('open_claude 模板路径', '/home/data/rag/open_claude/Openclaude-openclaude');
  if (openClaudeSource === null) return;
  setStatus('正在生成独立 Delegated Agent zip...', 'warn');
  const res = await fetch(`/api/delegated-agent/${sessionId}.zip`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      project_name: projectName || fallback,
      agent_name: agentName || projectName || fallback,
      agent_goal: agentGoal || '',
      default_task: defaultTask || '',
      bundle_open_claude: true,
      open_claude_source: openClaudeSource || '/home/data/rag/open_claude/Openclaude-openclaude',
      deployment: 'local_and_docker'
    })
  });
  if (!res.ok) {
    const text = await res.text();
    setStatus('Delegated Agent 生成失败', 'warn');
    alert(text || 'Delegated Agent 生成失败');
    return;
  }
  const blob = await res.blob();
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `${(projectName || fallback).trim() || 'delegated-agent'}-delegated-agent.zip`;
  a.click();
  URL.revokeObjectURL(a.href);
  setStatus('Delegated Agent zip 已生成', 'ok');
}
function openDelegatedInspector() {
  const url = '/delegated-inspector' + (sessionId ? ('?session_id=' + encodeURIComponent(sessionId)) : '');
  window.open(url, '_blank');
}
function openAgentIdeDelegatedInspector() {
  const params = new URLSearchParams();
  if (sessionId) params.set('session_id', sessionId);
  const workspaceId = currentWorkspaceId();
  if (workspaceId) params.set('workspace_id', workspaceId);
  params.set('from', 'agent_ide');
  params.set('embedded', '1');
  window.open('/delegated-inspector?' + params.toString(), '_blank');
  setPmGuideStage('debugged');
  renderPmFlowGuide('debugged', '已打开真实调试台：先生成并启动 Delegated Agent，再像最终用户一样连续对话。');
  setStatus('已打开真实调试台：用于查看 open_claude 执行过程、回复和产物', 'ok');
}
function openPreview() {
  document.getElementById('previewMask').classList.add('open');
  if (!previewMessage.value.trim()) previewMessage.value = localStorage.getItem('apd_preview_message') || '帮我生成一篇政务通知初稿';
  previewContext.value = localStorage.getItem('apd_preview_context') || '';
}
function closePreview(event) {
  if (event && event.target !== document.getElementById('previewMask')) return;
  document.getElementById('previewMask').classList.remove('open');
}
function parsePreviewContext() {
  const raw = previewContext.value.trim();
  if (!raw) return {};
  try { return JSON.parse(raw); }
  catch (err) { throw new Error('补充上下文不是合法 JSON：' + err.message); }
}
async function openStandaloneRuntimeInspector() {
  try {
    currentDevWorkspaceId = await resolveSelectedCliWorkspaceId();
  } catch (err) {}
  const workspaceId = currentDevWorkspaceId || localStorage.getItem('apd_dev_workspace_id') || '';
  const url = '/runtime-inspector' + (workspaceId ? ('?workspace_id=' + encodeURIComponent(workspaceId)) : '');
  window.open(url, '_blank');
  setPmGuideStage('debugged');
  renderPmFlowGuide('debugged', '调试页已打开：先点“重启当前 Agent”加载 AI 刚改的代码，再发送测试话术。');
  setStatus('已打开独立调试页，可和开发台并排使用', 'ok');
}

function openRuntime() {
  document.getElementById('runtimeMask').classList.add('open');
  if (!runtimeMessage.value.trim()) runtimeMessage.value = localStorage.getItem('apd_runtime_message') || '按当前 workflow 跑一次节点级模拟运行';
  runtimeContext.value = localStorage.getItem('apd_runtime_context') || '';
  runtimeApprovals.value = localStorage.getItem('apd_runtime_approvals') || '';
  loadRuntimePlan();
  loadRuntimeJobs();
}
function closeRuntime(event) {
  if (event && event.target !== document.getElementById('runtimeMask')) return;
  document.getElementById('runtimeMask').classList.remove('open');
}
function parseRuntimeJson(el, label) {
  const raw = el.value.trim();
  if (!raw) return {};
  try { return JSON.parse(raw); }
  catch (err) { throw new Error(label + ' 不是合法 JSON：' + err.message); }
}
function renderRuntimeNodes(nodeStates) {
  const states = Object.values(nodeStates || {});
  if (!states.length) return '<div class="timeline-card"><div class="timeline-step">0</div><div>当前 workflow 还没有节点。请先在对话中让 APD 设计 Dynamic Workflow。</div></div>';
  return `<div class="timeline">${states.map((item, index) => {
    const status = String(item.status || 'pending').replace(/[^a-zA-Z0-9_-]/g, '_');
    const binding = item.implementation_binding || {};
    const bindingTarget = Array.isArray(binding.target) ? binding.target.join(', ') : (binding.target || '-');
    return `<div class="timeline-card ${escapeHtml(status)}">
      <div class="timeline-step">${index + 1}</div>
      <div>
        <div class="timeline-title">${escapeHtml(item.name || item.node_id || '')} <span class="pill">${escapeHtml(item.status || '')}</span></div>
        <div class="timeline-plain">类型：${escapeHtml(item.type || '-')}；依赖：${escapeHtml((item.depends_on || []).join(', ') || '无')}</div>
        <div class="timeline-plain">实现绑定：${escapeHtml(binding.binding_type || '待绑定')} → ${escapeHtml(bindingTarget)}；状态：${escapeHtml(binding.status || '-')}</div>
        <div class="timeline-detail">${escapeHtml(item.observation || (item.requires_confirmation ? '这个节点需要确认。' : '等待运行。'))}</div>
      </div>
    </div>`;
  }).join('')}</div>`;
}
function renderRuntimeJobs(jobs) {
  if (!jobs || !jobs.length) {
    runtimeJobs.innerHTML = '<div class="example-card">暂无 Runtime 历史运行。新建运行后会保存到这里，刷新页面也能继续查看。</div>';
    return;
  }
  runtimeJobs.innerHTML = jobs.map(job => {
    const waiting = job.waiting_for ? `；等待：${escapeHtml(job.waiting_for.name || job.waiting_for.node_id || '')}` : '';
    const summary = job.summary || {};
    return `<div class="example-card">
      <strong>${escapeHtml(job.title || job.job_id || '')}</strong>
      <p>状态：${escapeHtml(job.status || '-')}；完成：${escapeHtml(String(summary.completed_nodes ?? 0))}/${escapeHtml(String(summary.total_nodes ?? 0))}${waiting}</p>
      <p>更新时间：${escapeHtml(job.updated_at || '-')}</p>
      <button onclick="loadRuntimeJob('${escapeHtml(job.job_id || '')}')">打开</button>
      <button onclick="continueRuntimeJob('${escapeHtml(job.job_id || '')}')">确认并继续</button>
    </div>`;
  }).join('');
}
async function loadRuntimeJobs() {
  if (!sessionId) return;
  try {
    const res = await fetch(`/api/runtime/jobs?session_id=${encodeURIComponent(sessionId)}`);
    const data = await res.json();
    renderRuntimeJobs(data.jobs || []);
  } catch (err) {
    runtimeJobs.innerHTML = '<div class="example-card">加载 Runtime 历史失败：' + escapeHtml(err && err.message ? err.message : err) + '</div>';
  }
}
async function loadRuntimeJob(jobId) {
  if (!jobId) return;
  try {
    const res = await fetch(`/api/runtime/job/${encodeURIComponent(jobId)}`);
    const job = await res.json();
    if (!res.ok) throw new Error(job.error || '加载失败');
    currentRuntimeJobId = job.job_id || '';
    localStorage.setItem('apd_runtime_job_id', currentRuntimeJobId);
    runtimeMessage.value = job.user_message || runtimeMessage.value;
    runtimeContext.value = JSON.stringify(job.context || {}, null, 2);
    runtimeApprovals.value = JSON.stringify(job.approvals || {}, null, 2);
    runtimeResult.innerHTML = renderRuntimeResult(job.result || {});
    setStatus('Runtime Job 已打开', 'ok');
  } catch (err) {
    runtimeResult.innerHTML = '加载 Runtime Job 失败：' + escapeHtml(err && err.message ? err.message : err);
    setStatus('加载 Runtime Job 失败', 'warn');
  }
}
async function continueRuntimeJob(jobId) {
  jobId = jobId || currentRuntimeJobId;
  if (!jobId) {
    setStatus('没有可继续的 Runtime Job，请先运行或打开历史任务', 'warn');
    return;
  }
  runtimeResult.innerHTML = '正在确认并继续 Runtime Job...';
  try {
    const extraApprovals = parseRuntimeJson(runtimeApprovals, '已确认节点');
    const res = await fetch(`/api/runtime/job/${encodeURIComponent(jobId)}/continue`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({approve_waiting: true, approvals: extraApprovals, max_steps: 30})
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || '继续失败');
    currentRuntimeJobId = (data.job || {}).job_id || jobId;
    localStorage.setItem('apd_runtime_job_id', currentRuntimeJobId);
    runtimeResult.innerHTML = renderRuntimeResult(data);
    loadRuntimeJobs();
    setStatus(data.status === 'completed' ? 'Runtime Job 已完成' : 'Runtime Job 已继续运行', data.status === 'completed' ? 'ok' : 'warn');
  } catch (err) {
    runtimeResult.innerHTML = '继续 Runtime Job 失败：' + escapeHtml(err && err.message ? err.message : err);
    setStatus('继续 Runtime Job 失败', 'warn');
  }
}
function renderRuntimeResult(data) {
  const summary = data.summary || {};
  const beginner = data.beginner_explanation || {};
  const waiting = data.waiting_for || null;
  const waitingHtml = waiting ? `<section class="preview-tip"><strong>当前暂停：</strong>${escapeHtml(waiting.name || waiting.node_id || '')}。${escapeHtml(waiting.reason || '')}<br/>继续方式：${escapeHtml(waiting.how_to_continue || '')}</section>` : '';
  return `
    <section class="beginner-box">
      <h3>Runtime 用大白话怎么理解</h3>
      <p>${escapeHtml(beginner['一句话'] || '这是一次 workflow 节点级模拟运行。')}</p>
      <p>${escapeHtml(beginner['节点是什么'] || '')}</p>
      <p>${escapeHtml(beginner['为什么会暂停'] || '')}</p>
      <p>${escapeHtml(beginner['产物是什么'] || '')}</p>
    </section>
    <div class="preview-summary">
      <div><strong>运行状态</strong>${escapeHtml(data.status || '-')}</div>
      <div><strong>完成节点</strong>${escapeHtml(String(summary.completed_nodes ?? 0))} / ${escapeHtml(String(summary.total_nodes ?? 0))}</div>
      <div><strong>等待确认</strong>${escapeHtml(String(summary.awaiting_human_nodes ?? 0))}</div>
      <div><strong>产物数量</strong>${escapeHtml(String(summary.artifact_count ?? 0))}</div>
    </div>
    ${waitingHtml}
    <section class="preview-section"><h3>节点运行时间线</h3><div class="desc">这里看整个 workflow 每个节点的状态：pending 是未运行，completed 是完成，awaiting_human 是停下来等人确认。</div>${renderRuntimeNodes(data.node_states || {})}</section>
    <section class="preview-tip"><strong>下一步：</strong>${escapeHtml(data.next_action || '')}</section>
    <details class="preview-dev-details" open><summary>展开 Runtime 开发者细节：计划 / 上下文 / 节点状态 / 产物 / Trace</summary>
      ${previewSection('Runtime 计划', '把当前 workflow 归一化后的运行计划。', data.runtime_plan)}
      ${previewSection('上下文包（Context Pack）', 'Runtime 每次运行也需要读取上下文、状态、产物和权限策略。', data.context_pack)}
      ${previewSection('节点状态（Node States）', '每个 workflow 节点当前运行到哪里。', data.node_states)}
      ${previewSection('节点实现绑定（Implementation Bindings）', '每个节点绑定到哪类真实实现：Tool、Validator、LLM Prompt、RAG 或人工确认。', data.implementation_bindings)}
      ${previewSection('产物追踪（Artifacts）', '每个节点模拟产生的文档、报告、矩阵或导出包。', data.artifacts)}
      ${previewSection('产物版本（Artifact Versions）', '展示每个产物的版本号、快照、活动版本、确认状态、回滚点和来源 Trace。', data.artifact_versions)}
      ${previewSection('过程日志（Trace）', '节点级执行日志，用于排查流程卡在哪里。', data.trace)}
    </details>`;
}
async function loadRuntimePlan() {
  if (!sessionId) return;
  runtimeResult.innerHTML = '正在读取 Runtime 计划...';
  try {
    const message = runtimeMessage.value.trim();
    const res = await fetch('/api/runtime/plan', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({session_id: sessionId, message})
    });
    const data = await res.json();
    runtimeResult.innerHTML = previewSection('Runtime 计划', '先看当前 workflow 会被拆成哪些节点。点击“运行 Runtime 雏形”后，会按节点模拟执行。', data);
    setStatus('Runtime 计划已加载', 'ok');
  } catch (err) {
    runtimeResult.innerHTML = 'Runtime 计划加载失败：' + escapeHtml(err && err.message ? err.message : err);
    setStatus('Runtime 计划加载失败', 'warn');
  }
}
function renderToolTestResult(data) {
  const summary = data.summary || {};
  const rows = (data.results || []).map(item => `
    <div class="example-card">
      <strong>${escapeHtml(item.tool || '')} <span class="pill">${escapeHtml(item.status || '')}</span></strong>
      <p>dry-run：${escapeHtml(String(!!item.dry_run))}；风险：${escapeHtml(item.risk || '-')}；需要确认：${escapeHtml(String(!!item.requires_confirmation))}</p>
      <p>失败策略：${escapeHtml(item.failure_policy || '-')}</p>
      <p>错误：${escapeHtml(item.error || '无')}</p>
    </div>`).join('');
  return `
    <section class="beginner-box">
      <h3>Tool 测试结果</h3>
      <p>这里不会真实调用外部服务，只检查 Tool Registry 是否完整、输入输出形状是否清楚、风险和失败策略是否可见。</p>
    </section>
    <div class="preview-summary">
      <div><strong>总数</strong>${escapeHtml(String(summary.total ?? 0))}</div>
      <div><strong>通过</strong>${escapeHtml(String(summary.passed ?? 0))}</div>
      <div><strong>失败</strong>${escapeHtml(String(summary.failed ?? 0))}</div>
      <div><strong>需确认</strong>${escapeHtml(String(summary.needs_confirmation ?? 0))}</div>
    </div>
    <section class="preview-tip"><strong>下一步：</strong>${escapeHtml(data.next_action || '')}</section>
    <section class="preview-section"><h3>Tool Dry-run 明细</h3><div class="desc">高风险或有副作用工具目前只允许 dry-run，真实执行必须走权限确认和沙箱。</div><div class="example-list">${rows || '<div class="example-card">暂无工具。</div>'}</div></section>
    ${previewSection('Tool Test Raw', '完整工具测试记录，包含输入、输出、Trace 和失败策略。', data)}`;
}
async function runToolTests() {
  if (!sessionId) return;
  runtimeResult.innerHTML = 'Tool 测试运行中...';
  setStatus('Tool 测试运行中...', 'warn');
  try {
    const res = await fetch('/api/tools/test', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({session_id: sessionId})
    });
    const data = await res.json();
    runtimeResult.innerHTML = renderToolTestResult(data);
    setStatus('Tool 测试完成', (data.summary || {}).failed ? 'warn' : 'ok');
  } catch (err) {
    runtimeResult.innerHTML = 'Tool 测试失败：' + escapeHtml(err && err.message ? err.message : err);
    setStatus('Tool 测试失败', 'warn');
  }
}
function renderEvalReplayResult(data) {
  const summary = data.summary || {};
  const rows = (data.results || []).map(item => `
    <div class="example-card">
      <strong>${escapeHtml(item.case_id || '')} <span class="pill">${escapeHtml(item.passed ? 'passed' : 'failed')}</span></strong>
      <p>类型：${escapeHtml(item.case_type || '-')}；得分：${escapeHtml(String(item.score ?? '-'))}；Runtime：${escapeHtml(item.runtime_status || '-')}</p>
      <p>话术：${escapeHtml(item.user_message || '')}</p>
      <p>问题：${escapeHtml((item.problems || []).join('；') || '无')}</p>
    </div>`).join('');
  return `
    <section class="beginner-box">
      <h3>Eval 回放结果</h3>
      <p>把协议里的评测用例放进 Runtime 重新跑，看 operation、节点绑定、产物、Trace 是否符合预期。</p>
    </section>
    <div class="preview-summary">
      <div><strong>总数</strong>${escapeHtml(String(summary.total ?? 0))}</div>
      <div><strong>通过</strong>${escapeHtml(String(summary.passed ?? 0))}</div>
      <div><strong>失败</strong>${escapeHtml(String(summary.failed ?? 0))}</div>
      <div><strong>通过率</strong>${escapeHtml(String(summary.pass_rate ?? 0))}</div>
    </div>
    <section class="preview-tip"><strong>下一步：</strong>${escapeHtml(data.next_action || '')}</section>
    <section class="preview-section"><h3>回放 Case</h3><div class="desc">失败 case 应回到协议、workflow、validator、tool binding 或 prompt 中修正。</div><div class="example-list">${rows || '<div class="example-card">暂无 case。</div>'}</div></section>
    ${previewSection('Eval Replay Raw', '完整回放数据，方便复制给 Codex/Claude 分析。', data)}`;
}
async function runEvalReplay() {
  if (!sessionId) return;
  runtimeResult.innerHTML = 'Eval 回放运行中...';
  setStatus('Eval 回放运行中...', 'warn');
  try {
    const res = await fetch('/api/runtime/eval-replay', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({session_id: sessionId, limit: 12})
    });
    const data = await res.json();
    runtimeResult.innerHTML = renderEvalReplayResult(data);
    setStatus('Eval 回放完成', (data.summary || {}).failed ? 'warn' : 'ok');
  } catch (err) {
    runtimeResult.innerHTML = 'Eval 回放失败：' + escapeHtml(err && err.message ? err.message : err);
    setStatus('Eval 回放失败', 'warn');
  }
}
async function runRuntime() {
  if (!sessionId) return;
  const message = runtimeMessage.value.trim();
  localStorage.setItem('apd_runtime_message', message);
  localStorage.setItem('apd_runtime_context', runtimeContext.value.trim());
  localStorage.setItem('apd_runtime_approvals', runtimeApprovals.value.trim());
  runtimeResult.innerHTML = 'Runtime 运行中...';
  setStatus('Runtime 运行中...', 'warn');
  try {
    const res = await fetch('/api/runtime/run', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        session_id: sessionId,
        message,
        context: parseRuntimeJson(runtimeContext, '运行上下文'),
        approvals: parseRuntimeJson(runtimeApprovals, '已确认节点'),
        max_steps: 30
      })
    });
    const data = await res.json();
    currentRuntimeJobId = (data.job || {}).job_id || '';
    if (currentRuntimeJobId) localStorage.setItem('apd_runtime_job_id', currentRuntimeJobId);
    runtimeResult.innerHTML = renderRuntimeResult(data);
    loadRuntimeJobs();
    setStatus(data.status === 'completed' ? 'Runtime 运行完成并已保存' : 'Runtime 已暂停/阻断并已保存', data.status === 'completed' ? 'ok' : 'warn');
  } catch (err) {
    runtimeResult.innerHTML = 'Runtime 运行失败：' + escapeHtml(err && err.message ? err.message : err);
    setStatus('Runtime 运行失败', 'warn');
  }
}
function renderExampleList(examples) {
  if (!examples || !examples.length) {
    previewExamples.innerHTML = '<div class="example-card">暂无示例。</div>';
    return;
  }
  previewExamples.innerHTML = examples.map((item, index) => `
    <div class="example-card">
      <strong>${escapeHtml(item.title || ('示例 ' + (index + 1)))}</strong>
      <p>${escapeHtml(item.message || '')}</p>
      <p>为什么测：${escapeHtml(item.why || '用于测试当前 Agent 设计。')}</p>
      <p>可能关注：${escapeHtml((item.expected_focus || []).join(' / ') || '-')}</p>
      <button onclick="usePreviewExample(${index})">填入并运行</button>
    </div>
  `).join('');
  window.__previewExamples = examples;
}
function usePreviewExample(index) {
  const item = (window.__previewExamples || [])[index];
  if (!item) return;
  previewMessage.value = item.message || '';
  runPreview();
}
async function generatePreviewExamples() {
  if (!sessionId) return;
  previewExamples.innerHTML = '<div class="example-card">正在根据当前会话生成测试示例...</div>';
  setStatus('正在生成预览示例...', 'warn');
  try {
    const res = await fetch('/api/preview/examples', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({session_id: sessionId, count: 5, settings: settings()})
    });
    const data = await res.json();
    renderExampleList(data.examples || []);
    setStatus(data.fallback ? '示例生成完成：离线降级' : '示例生成完成', data.fallback ? 'warn' : 'ok');
  } catch (err) {
    previewExamples.innerHTML = '<div class="example-card">示例生成失败：' + escapeHtml(err && err.message ? err.message : err) + '</div>';
    setStatus('示例生成失败', 'warn');
  }
}
function previewJsonBlock(value) {
  return `<pre>${escapeHtml(JSON.stringify(value || {}, null, 2))}</pre>`;
}
function previewSection(title, desc, value) {
  return `<section class="preview-section"><h3>${escapeHtml(title)}</h3><div class="desc">${escapeHtml(desc)}</div>${previewJsonBlock(value)}</section>`;
}
function renderTimeline(items) {
  const cards = (items || []).map(item => {
    const status = String(item.status || 'done').replace(/[^a-zA-Z0-9_-]/g, '_');
    return `<div class="timeline-card ${escapeHtml(status)}">
      <div class="timeline-step">${escapeHtml(String(item.step || ''))}</div>
      <div>
        <div class="timeline-title">${escapeHtml(item.title || '')} <span class="pill">${escapeHtml(item.status || '')}</span></div>
        <div class="timeline-plain">${escapeHtml(item.plain || '')}</div>
        <div class="timeline-detail">${escapeHtml(item.detail || '')}</div>
      </div>
    </div>`;
  }).join('');
  return `<section class="preview-section"><h3>小白时间线：这次 Agent 是怎么跑的</h3><div class="desc">先看这里，不用读 JSON。每一步都对应开发者细节里的一个结构。</div><div class="timeline">${cards || '<div class="timeline-card"><div class="timeline-step">?</div><div>暂无时间线。</div></div>'}</div></section>`;
}
function renderDiagnostics(diagnostics) {
  diagnostics = diagnostics || {};
  const problems = (diagnostics.problems || []).map(x => `<li>${escapeHtml(x)}</li>`).join('');
  const suggestions = (diagnostics.suggestions || []).map(x => `<li>${escapeHtml(x)}</li>`).join('');
  return `<section class="diagnostics"><h3>问题定位和下一步建议</h3><div>${escapeHtml(diagnostics.focus || '优先看第一个异常步骤。')}</div><strong>可能问题</strong><ul>${problems}</ul><strong>建议处理</strong><ul>${suggestions}</ul></section>`;
}
function renderPreviewResult(data) {
  const op = data.op_call || {};
  const llm = data.llm || {};
  const beginner = data.beginner_explanation || {};
  const beginnerHtml = `<section class="beginner-box">
    <h3>这次结果用大白话怎么理解</h3>
    <p>${escapeHtml(beginner['一句话'] || '这是一次 Agent 决策链路预览。')}</p>
    <p>${escapeHtml(beginner['它是不是 Agent 的完整结构'] || '')}</p>
    <p>最简单的看法：用户说了一句话 → Agent 猜他想干什么 → 绑定一个内部操作 → 程序检查能不能做 → 这里只模拟，不真执行。</p>
  </section>`;
  const summary = `
    <div class="preview-summary">
      <div><strong>最终动作</strong>${escapeHtml(data.next_action || '-')}</div>
      <div><strong>绑定操作</strong>${escapeHtml(op.operation || '-')}</div>
      <div><strong>置信度</strong>${escapeHtml(String(op.confidence ?? '-'))}</div>
      <div><strong>LLM 状态</strong>${escapeHtml(llm.ok === false ? '失败/降级' : '成功')}</div>
    </div>`;
  const message = `<section class="preview-section"><h3>用户可见回复</h3><div class="desc">这是 Agent 本轮会回复给用户的话。小白先看这里是否符合预期。</div><pre>${escapeHtml(data.assistant_message || '')}</pre></section>`;
  const timeline = renderTimeline(data.preview_timeline || []);
  const diagnostics = renderDiagnostics(data.diagnostics || {});
  const evalCase = data.eval_case_suggestion || {};
  const evalTip = evalCase.should_record
    ? `<section class="preview-tip"><strong>评测沉淀：</strong>这轮建议保存成评测用例。原因：${escapeHtml((evalCase.reasons || []).join('；'))}</section>`
    : `<section class="preview-tip"><strong>评测沉淀：</strong>这轮暂时不用强制保存，可以继续用更极端的话术测试边界。</section>`;
  const simple = `<section class="preview-tip"><strong>下一步怎么看：</strong>如果“绑定操作”不对，说明 Agent 理解或协议设计要改；如果操作对但最终动作是追问/阻断，说明缺信息或校验规则在生效。</section>` + evalTip;
  const devSections =
    previewSection('记忆读取（Memory Retrieval）', '按记忆策略找出本轮相关记忆。预览模式不会访问真实记忆库，只展示策略和传入记忆如何影响上下文。', data.memory_retrieval) +
    previewSection('上下文包（Context Pack）', '本轮交给 LLM 观察的结构化上下文。若 Agent 理解错，先看这里是否缺少关键信息或给了干扰信息。', data.context_pack) +
    previewSection('状态上下文（State Context）', '任务当前阶段和可用状态流转。真实执行前，程序应校验当前状态是否允许这个操作。', (data.context_pack || {}).state_context) +
    previewSection('产物上下文（Artifact Context）', '当前协议定义的产物、已有产物、版本策略、导出格式和审查规则。', (data.context_pack || {}).artifact_context) +
    previewSection('产物影响（Artifact Effect）', '本轮操作预计会读取、创建、修改或导出哪些产物，以及是否需要快照或审查。', data.artifact_effect) +
    previewSection('工具上下文（Tool Context）', '当前协议注册了哪些底层工具，以及 operation 如何绑定到这些工具。', (data.context_pack || {}).tool_context) +
    previewSection('工具计划（Tool Plan）', '本轮操作预计会调用哪些工具，是否存在副作用或高风险工具。', data.tool_plan) +
    previewSection('权限上下文（Permission Context）', '当前协议定义了哪些自动允许、必须确认、禁止和角色要求。', (data.context_pack || {}).permission_context) +
    previewSection('权限判断（Permission Check）', '本轮操作和工具计划经过权限策略后的结果：允许、需要确认或禁止。', data.permission_check) +
    previewSection('失败恢复上下文（Recovery Context）', '当前协议定义的重试、降级、回滚、转人工和失败 case 记录策略。', (data.context_pack || {}).recovery_context) +
    previewSection('失败恢复计划（Recovery Plan）', '本轮如果不能继续，应该采取的恢复策略和下一步。', data.recovery_plan) +
    previewSection('评测沉淀建议（Eval Case Suggestion）', '判断本轮预览是否值得保存为以后反复回归的测试样本。简单说：哪里错过一次，以后就固定测一次。', data.eval_case_suggestion) +
    previewSection('记忆写入建议（Memory Write Proposal）', 'LLM 可以建议写入记忆，但真实系统必须由程序校验，长期记忆通常需要用户确认。', data.memory_write_proposal) +
    previewSection('意图框架（Intent Frame）', 'LLM 对用户话术的语义理解结果。它还不是执行命令，只是“用户到底想干什么”的解释。', data.intent_frame) +
    previewSection('操作调用（OpCall）', '把意图绑定到协议里的有限操作。真正工程实现时，程序只应该执行这里允许的 operation。', data.op_call) +
    previewSection('校验结果（Validator Results）', '程序侧校验层。它负责判断操作是否存在、信息是否足够、是否需要确认，不能完全交给 LLM。', data.validator_results) +
    previewSection('模拟执行器（Mock Executor）', '预览模式下不会真实执行，只展示如果接入真实 executor 后大概会做什么。', data.executor_preview) +
    previewSection('过程日志（Trace）', '完整决策链路，用来排查误解、误绑定、校验缺失和执行风险。', data.trace) +
    previewSection('原始返回（Raw）', '完整原始数据，方便开发调试和复制给 Codex/Claude 继续分析。', data);
  const dev = `<details class="preview-dev-details"><summary>展开开发者细节：记忆 / 状态 / 产物 / 工具 / 权限 / 失败恢复 / 评测沉淀 / 上下文包 / 意图框架 / 操作调用 / 校验 / Trace</summary>${devSections}</details>`;
  return beginnerHtml + summary + timeline + diagnostics + message + simple + dev;
}
function setPreviewMode(showDev) {
  const detail = previewResult.querySelector('.preview-dev-details');
  if (detail) {
    detail.open = !!showDev;
    detail.scrollIntoView({behavior:'smooth', block:'nearest'});
  } else if (showDev) {
    setStatus('请先运行一次预览，开发者细节会显示在结果底部', 'warn');
  }
}
async function runPreview() {
  if (!sessionId) return;
  const text = previewMessage.value.trim();
  if (!text) { previewResult.innerHTML = '请先输入测试话术。'; return; }
  localStorage.setItem('apd_preview_message', text);
  localStorage.setItem('apd_preview_context', previewContext.value.trim());
  previewResult.innerHTML = '预览运行中...';
  setStatus('预览运行中...', 'warn');
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 180000);
  try {
    const res = await fetch('/api/preview/run', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      signal: controller.signal,
      body: JSON.stringify({
        session_id: sessionId,
        message: text,
        context: parsePreviewContext(),
        settings: settings(),
      })
    });
    const data = await res.json();
    previewResult.innerHTML = renderPreviewResult(data);
    setPreviewMode(false);
    setStatus(data.fallback ? '预览完成：离线降级' : '预览运行完成', data.fallback ? 'warn' : 'ok');
  } catch (err) {
    previewResult.innerHTML = '预览运行失败：' + escapeHtml(err && err.message ? err.message : err);
    setStatus('预览运行失败', 'warn');
  } finally {
    clearTimeout(timer);
  }
}
function openMultiAgent() {
  document.getElementById('multiAgentMask').classList.add('open');
  if (!multiAgentMessage.value.trim()) multiAgentMessage.value = localStorage.getItem('apd_multi_agent_message') || '按当前协议分析是否需要多 Agent 协作';
  analyzeMultiAgent();
}
function closeMultiAgent(event) {
  if (event && event.target !== document.getElementById('multiAgentMask')) return;
  document.getElementById('multiAgentMask').classList.remove('open');
}
function renderMultiAgentRoles(roles) {
  if (!roles || !roles.length) return '<div class="example-card">暂无角色。</div>';
  return `<div class="case-grid">${roles.map(role => `<div class="case-card">
    <h3>${escapeHtml(role.name || role.id || '')}</h3>
    <p>${escapeHtml(role.responsibility || '')}</p>
    <p><strong>输入：</strong>${escapeHtml((role.inputs || []).join('、') || '-')}</p>
    <p><strong>输出：</strong>${escapeHtml((role.outputs || []).join('、') || '-')}</p>
    <p><span class="pill">风险：${escapeHtml(role.risk_level || '-')}</span></p>
  </div>`).join('')}</div>`;
}
function renderMultiAgentHandoffs(handoffs) {
  if (!handoffs || !handoffs.length) return '<div class="timeline-card"><div class="timeline-step">0</div><div>暂无交接。请先补 workflow 节点。</div></div>';
  return `<div class="timeline">${handoffs.map((item, index) => `<div class="timeline-card done">
    <div class="timeline-step">${index + 1}</div>
    <div>
      <div class="timeline-title">${escapeHtml(item.from_agent || '')} → ${escapeHtml(item.to_agent || '')}</div>
      <div class="timeline-plain">${escapeHtml(item.reason || '')}</div>
      <div class="timeline-detail">交接必须带：${escapeHtml((item.required_payload || []).join('、'))}</div>
    </div>
  </div>`).join('')}</div>`;
}
function renderMultiAgentTrace(trace) {
  return `<div class="timeline">${(trace || []).map(item => `<div class="timeline-card ${escapeHtml(item.status || 'done')}">
    <div class="timeline-step">${escapeHtml(String(item.step || ''))}</div>
    <div>
      <div class="timeline-title">${escapeHtml(item.event || '')}</div>
      <div class="timeline-plain">${escapeHtml(item.detail || '')}</div>
      <div class="timeline-detail">${escapeHtml((item.from_agent || item.agent || ''))}${item.to_agent ? ' → ' + escapeHtml(item.to_agent) : ''}</div>
    </div>
  </div>`).join('')}</div>`;
}
function renderMultiAgentResult(data) {
  const summary = data.summary || {};
  const beginner = data.beginner_explanation || {};
  const gaps = (data.gaps || []).map(x => `<li>${escapeHtml(x)}</li>`).join('');
  const conflictItems = ((data.conflict_policy || {}).conflict_types || []).map(item => `<li><strong>${escapeHtml(item.type || '')}</strong>：${escapeHtml(item.meaning || '')} 处理：${escapeHtml(item.resolution || '')}</li>`).join('');
  return `
    <section class="beginner-box">
      <h3>Multi-Agent 用大白话怎么理解</h3>
      <p>${escapeHtml(beginner['一句话'] || '')}</p>
      <p>${escapeHtml(beginner['为什么需要角色'] || '')}</p>
      <p>${escapeHtml(beginner['为什么需要交接'] || '')}</p>
      <p>${escapeHtml(beginner['为什么需要冲突策略'] || '')}</p>
    </section>
    <div class="preview-summary">
      <div><strong>角色数量</strong>${escapeHtml(String(summary.role_count ?? 0))}</div>
      <div><strong>交接数量</strong>${escapeHtml(String(summary.handoff_count ?? 0))}</div>
      <div><strong>就绪度</strong>${escapeHtml(String(summary.readiness_score ?? 0))}%</div>
      <div><strong>建议模式</strong>${escapeHtml(summary.recommended_mode || '-')}</div>
    </div>
    <section class="preview-section"><h3>角色分工</h3><div class="desc">每个 Agent 只负责一类事，避免一个 LLM 同时规划、执行、审校和批准。</div>${renderMultiAgentRoles(data.roles || [])}</section>
    <section class="preview-section"><h3>任务交接</h3><div class="desc">交接不是一句“你继续”，而是必须带目标、完成情况、剩余事项、产物和 Trace 引用。</div>${renderMultiAgentHandoffs(((data.handoff_contract || {}).handoffs || []))}</section>
    <section class="diagnostics"><h3>冲突处理和当前缺口</h3><ul>${conflictItems}</ul><strong>当前缺口</strong><ul>${gaps}</ul></section>
    <section class="preview-section"><h3>协同 Trace</h3><div class="desc">这里模拟多个 Agent 的协作过程，后续真实 Runtime 应把每条消息和交接都记录下来。</div>${renderMultiAgentTrace(data.collaboration_trace || [])}</section>
    <section class="preview-tip"><strong>下一步：</strong>${escapeHtml(data.next_action || '')}</section>
    <details class="preview-dev-details"><summary>展开开发者细节：消息协议 / 交接契约 / 冲突策略 / 原始数据</summary>
      ${previewSection('消息协议（Message Protocol）', '规定多个 Agent 之间消息必须长什么样，防止只靠自然语言传话。', data.message_protocol)}
      ${previewSection('交接契约（Handoff Contract）', '规定任务从一个 Agent 交给另一个 Agent 时必须带哪些结构化信息。', data.handoff_contract)}
      ${previewSection('冲突策略（Conflict Policy）', '规定多个 Agent 结论不一致时怎么仲裁，哪些情况必须人工确认。', data.conflict_policy)}
      ${previewSection('原始返回（Raw）', '完整多 Agent 协作分析结果。', data)}
    </details>`;
}
async function analyzeMultiAgent() {
  if (!sessionId) return;
  const text = multiAgentMessage.value.trim();
  localStorage.setItem('apd_multi_agent_message', text);
  multiAgentResult.innerHTML = '正在分析 Multi-Agent 协作...';
  setStatus('正在分析 Multi-Agent 协作...', 'warn');
  try {
    const res = await fetch('/api/multi-agent/analyze', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({session_id: sessionId, message: text})
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || '分析失败');
    multiAgentResult.innerHTML = renderMultiAgentResult(data);
    setStatus('Multi-Agent 协作分析完成', 'ok');
  } catch (err) {
    multiAgentResult.innerHTML = 'Multi-Agent 协作分析失败：' + escapeHtml(err && err.message ? err.message : err);
    setStatus('Multi-Agent 协作分析失败', 'warn');
  }
}
let currentDemoId = '';
let currentDelegatedId = localStorage.getItem('apd_delegated_id') || '';
let currentDelegatedJobId = '';
let delegatedPollTimer = null;
function openDelegatedPlayground() {
  document.getElementById('delegatedPlaygroundMask').classList.add('open');
  if (!delegatedProjectName.value.trim()) delegatedProjectName.value = localStorage.getItem('apd_delegated_project_name') || (protocol.project_name || 'delegated-agent-demo');
  if (!delegatedAgentGoal.value.trim()) delegatedAgentGoal.value = localStorage.getItem('apd_delegated_agent_goal') || (protocol.domain_summary || '');
  if (!delegatedDefaultTask.value.trim()) delegatedDefaultTask.value = localStorage.getItem('apd_delegated_default_task') || '请根据用户输入完成任务，并把最终结果写入 artifacts/report.md 和 artifacts/result.json。';
  if (!delegatedOpenClaudeSource.value.trim()) delegatedOpenClaudeSource.value = localStorage.getItem('apd_delegated_open_claude') || '/home/data/rag/open_claude/Openclaude-openclaude';
  if (!delegatedChatInput.value.trim()) delegatedChatInput.value = localStorage.getItem('apd_delegated_chat_input') || '请在 artifacts/report.md 写一段 hello delegated agent，并生成 artifacts/result.json。';
  refreshDelegatedPlaygroundList();
}
function closeDelegatedPlayground(event) {
  if (event && event.target !== document.getElementById('delegatedPlaygroundMask')) return;
  document.getElementById('delegatedPlaygroundMask').classList.remove('open');
}
function renderDelegatedSummary(item) {
  if (!item || !item.delegated_id) return '<div class="example-card">暂无运行中的 Delegated Agent。</div>';
  return `<div class="example-card">
    <strong>${escapeHtml(item.project_name || item.delegated_id)} <span class="pill">${escapeHtml(item.status || '-')}</span></strong>
    <p>Delegated ID：${escapeHtml(item.delegated_id || '')}；端口：${escapeHtml(String(item.port || '-'))}；PID：${escapeHtml(String(item.pid || '-'))}</p>
    <p>内部地址：${escapeHtml(item.base_url || '')}</p>
    <button onclick="selectDelegatedPlayground('${escapeHtml(item.delegated_id || '')}')">选择</button>
    <button onclick="stopDelegatedPlayground('${escapeHtml(item.delegated_id || '')}')">停止</button>
  </div>`;
}
function selectDelegatedPlayground(delegatedId) {
  currentDelegatedId = delegatedId;
  localStorage.setItem('apd_delegated_id', delegatedId);
  setStatus('已选择 Delegated Agent：' + delegatedId, 'ok');
  loadDelegatedConfig();
}
async function refreshDelegatedPlaygroundList() {
  try {
    const res = await fetch('/api/delegated-playground/list');
    const data = await res.json();
    const items = data.items || [];
    if (!currentDelegatedId) currentDelegatedId = localStorage.getItem('apd_delegated_id') || ((items[0] || {}).delegated_id || '');
    delegatedPlaygroundStatus.innerHTML = items.length ? items.map(renderDelegatedSummary).join('') : '<div class="example-card">暂无运行中的 Delegated Agent，点击“生成并启动”。</div>';
  } catch (err) {
    delegatedPlaygroundStatus.innerHTML = '<div class="example-card">加载 Delegated Agent 列表失败：' + escapeHtml(err && err.message ? err.message : err) + '</div>';
  }
}
async function startDelegatedPlayground() {
  if (!sessionId) return;
  const projectName = delegatedProjectName.value.trim();
  const agentGoal = delegatedAgentGoal.value.trim();
  const defaultTask = delegatedDefaultTask.value.trim();
  const openClaudeSource = delegatedOpenClaudeSource.value.trim() || '/home/data/rag/open_claude/Openclaude-openclaude';
  localStorage.setItem('apd_delegated_project_name', projectName);
  localStorage.setItem('apd_delegated_agent_goal', agentGoal);
  localStorage.setItem('apd_delegated_default_task', defaultTask);
  localStorage.setItem('apd_delegated_open_claude', openClaudeSource);
  delegatedPlaygroundResult.innerHTML = '<section class="beginner-box"><h3>正在生成并启动 Delegated Agent...</h3><p>APD 会生成临时工程、复制 open_claude、启动 FastAPI。首次打包可能需要十几秒。</p></section>';
  setStatus('正在启动 Delegated Agent 调试台...', 'warn');
  try {
    const res = await fetch('/api/delegated-playground/start', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        session_id: sessionId,
        project_name: projectName,
        agent_name: protocol.project_name || projectName || 'Delegated Agent',
        agent_goal: agentGoal,
        default_task: defaultTask,
        open_claude_source: openClaudeSource,
        fake_runner: delegatedFakeRunner.value !== '0'
      })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || '启动失败');
    currentDelegatedId = data.delegated_id;
    localStorage.setItem('apd_delegated_id', currentDelegatedId);
    delegatedPlaygroundResult.innerHTML = previewSection('Delegated Agent 已启动', '现在可以直接在左侧对话框发送任务。默认 fake runner 会很快返回；真实 open_claude 可能需要等待。', data);
    await refreshDelegatedPlaygroundList();
    await loadDelegatedConfig();
    setStatus('Delegated Agent 调试台已启动', 'ok');
  } catch (err) {
    delegatedPlaygroundResult.innerHTML = `<section class="diagnostics"><h3>启动失败</h3><p>${escapeHtml(err && err.message ? err.message : err)}</p><p>先检查 open_claude 路径是否存在，以及 dist/cli.js 是否存在。</p></section>`;
    setStatus('Delegated Agent 调试台启动失败', 'warn');
  }
}
async function stopDelegatedPlayground(delegatedId) {
  delegatedId = delegatedId || currentDelegatedId;
  if (!delegatedId) { setStatus('没有可停止的 Delegated Agent', 'warn'); return; }
  try {
    const res = await fetch(`/api/delegated-playground/${encodeURIComponent(delegatedId)}/stop`, {method:'POST'});
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || '停止失败');
    if (currentDelegatedId === delegatedId) {
      currentDelegatedId = '';
      localStorage.removeItem('apd_delegated_id');
    }
    delegatedPlaygroundResult.innerHTML = previewSection('Delegated Agent 已停止', '临时 FastAPI 进程已停止。', data);
    await refreshDelegatedPlaygroundList();
    setStatus('Delegated Agent 已停止', 'ok');
  } catch (err) {
    delegatedPlaygroundResult.innerHTML = '停止失败：' + escapeHtml(err && err.message ? err.message : err);
    setStatus('停止 Delegated Agent 失败', 'warn');
  }
}
async function loadDelegatedConfig() {
  if (!currentDelegatedId) { setStatus('请先启动或选择 Delegated Agent', 'warn'); return; }
  try {
    const res = await fetch(`/api/delegated-playground/${encodeURIComponent(currentDelegatedId)}/config`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || '读取配置失败');
    delegatedPlaygroundResult.innerHTML = previewSection('运行前检查', 'fake runner、Node、CLI、模型配置都在这里。', data);
  } catch (err) {
    delegatedPlaygroundResult.innerHTML = '运行前检查失败：' + escapeHtml(err && err.message ? err.message : err);
  }
}
function fillDelegatedHello() {
  delegatedChatInput.value = '请在 artifacts/report.md 写一段“hello delegated agent”，并生成 artifacts/result.json。';
}
async function sendDelegatedMessage() {
  if (!currentDelegatedId) { setStatus('请先生成并启动 Delegated Agent', 'warn'); return; }
  const messageText = delegatedChatInput.value.trim();
  if (!messageText) { setStatus('请输入消息', 'warn'); return; }
  localStorage.setItem('apd_delegated_chat_input', messageText);
  delegatedChatLog.innerHTML += `<div class="example-card"><strong>你</strong><p>${escapeHtml(messageText)}</p></div>`;
  delegatedPlaygroundResult.innerHTML = '<section class="beginner-box"><h3>Agent 已收到任务</h3><p>正在创建 Job，随后会轮询状态、过程和产物。</p></section>';
  try {
    const res = await fetch(`/api/delegated-playground/${encodeURIComponent(currentDelegatedId)}/chat`, {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({message: messageText})
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || '发送失败');
    currentDelegatedJobId = data.job_id;
    delegatedChatLog.innerHTML += `<div class="example-card"><strong>Agent</strong><p>任务已创建：${escapeHtml(currentDelegatedJobId)}，状态：${escapeHtml(data.status || '-')}</p></div>`;
    startDelegatedPolling();
    setStatus('Delegated Agent 任务已提交', 'ok');
  } catch (err) {
    delegatedPlaygroundResult.innerHTML = `<section class="diagnostics"><h3>发送失败</h3><p>${escapeHtml(err && err.message ? err.message : err)}</p></section>`;
    setStatus('Delegated Agent 发送失败', 'warn');
  }
}
function startDelegatedPolling() {
  if (delegatedPollTimer) clearInterval(delegatedPollTimer);
  delegatedPollTimer = setInterval(pollDelegatedJob, 1300);
  pollDelegatedJob();
}
async function pollDelegatedJob() {
  if (!currentDelegatedId || !currentDelegatedJobId) return;
  try {
    const res = await fetch(`/api/delegated-playground/${encodeURIComponent(currentDelegatedId)}/job/${encodeURIComponent(currentDelegatedJobId)}`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || '读取 Job 失败');
    delegatedPlaygroundResult.innerHTML = renderDelegatedJobResult(data);
    const status = ((data.job || {}).status || '').toLowerCase();
    if (['completed','failed','timeout'].includes(status)) {
      clearInterval(delegatedPollTimer);
      delegatedPollTimer = null;
      const report = data.report || ((data.job || {}).result || {}).summary || status;
      delegatedChatLog.innerHTML += `<div class="example-card"><strong>Agent 回复</strong><p>${escapeHtml(report).slice(0, 3000)}</p></div>`;
      setStatus('Delegated Agent 任务结束：' + status, status === 'completed' ? 'ok' : 'warn');
    }
  } catch (err) {
    delegatedPlaygroundResult.innerHTML = '读取 Job 失败：' + escapeHtml(err && err.message ? err.message : err);
  }
}
function renderDelegatedJobResult(data) {
  const job = data.job || {};
  const events = data.events || [];
  const artifacts = data.artifacts || [];
  const report = data.report || '';
  const result = job.result || {};
  return `
    <section class="beginner-box">
      <h3>Job 状态：${escapeHtml(job.status || '-')}</h3>
      <p><strong>摘要：</strong>${escapeHtml(job.summary || result.summary || '')}</p>
      <p><strong>Job ID：</strong>${escapeHtml(job.job_id || '')}</p>
    </section>
    <div class="preview-summary">
      <div><strong>状态</strong>${escapeHtml(job.status || '-')}</div>
      <div><strong>事件</strong>${escapeHtml(String(events.length))} 条</div>
      <div><strong>产物</strong>${escapeHtml(String(artifacts.length))} 个</div>
      <div><strong>Task Pack</strong>${escapeHtml(job.task_pack_path || '-')}</div>
    </div>
    <section class="preview-section">
      <h3>Agent 回复 / report.md</h3>
      <div class="md-body">${renderMarkdownForModal(report || result.summary || '暂无 report.md')}</div>
    </section>
    <section class="preview-section">
      <h3>产物</h3>
      <div>${artifacts.length ? artifacts.map(a => `<span class="pill">${escapeHtml(a.name || '')} · ${escapeHtml(String(a.size || 0))} bytes</span>`).join(' ') : '<span class="desc">暂无产物</span>'}</div>
    </section>
    <details class="preview-dev-details" open>
      <summary>过程 Events</summary>
      ${previewSection('Events', 'Job 的状态流转、runner 启动、完成或失败记录。', events)}
    </details>
    <details class="preview-dev-details">
      <summary>完整 Job JSON</summary>
      ${previewSection('Job', '生成工程 /api/jobs/{job_id} 返回。', job)}
      ${previewSection('Result', 'artifacts/result.json 解析结果。', result)}
    </details>`;
}
function openDemoPlayground() {
  document.getElementById('demoPlaygroundMask').classList.add('open');
  if (!demoProjectName.value.trim()) demoProjectName.value = localStorage.getItem('apd_demo_project_name') || (protocol.project_name || 'test-agent-demo');
  if (!demoMessage.value.trim()) demoMessage.value = localStorage.getItem('apd_demo_message') || '测试一句用户话术';
  demoContext.value = localStorage.getItem('apd_demo_context') || '{"current_state":"drafting"}';
  refreshDemoPlaygroundList();
}
function closeDemoPlayground(event) {
  if (event && event.target !== document.getElementById('demoPlaygroundMask')) return;
  document.getElementById('demoPlaygroundMask').classList.remove('open');
}
function parseDemoContext() {
  const raw = demoContext.value.trim();
  if (!raw) return {};
  try { return JSON.parse(raw); }
  catch (err) { throw new Error('上下文 JSON 不合法：' + err.message); }
}
function renderDemoSummary(demo) {
  if (!demo || !demo.demo_id) return '<div class="example-card">暂无运行中的 Demo。</div>';
  return `<div class="example-card">
    <strong>${escapeHtml(demo.project_name || demo.demo_id)} <span class="pill">${escapeHtml(demo.status || '-')}</span></strong>
    <p>Demo ID：${escapeHtml(demo.demo_id || '')}；端口：${escapeHtml(String(demo.port || '-'))}；PID：${escapeHtml(String(demo.pid || '-'))}</p>
    <p>内部地址：${escapeHtml(demo.base_url || '')}</p>
    <button onclick="selectDemo('${escapeHtml(demo.demo_id || '')}')">选择</button>
    <button onclick="stopDemoPlayground('${escapeHtml(demo.demo_id || '')}')">停止</button>
  </div>`;
}
function selectDemo(demoId) {
  currentDemoId = demoId;
  localStorage.setItem('apd_demo_id', demoId);
  setStatus('已选择 Demo：' + demoId, 'ok');
}
async function refreshDemoPlaygroundList() {
  try {
    const res = await fetch('/api/demo-playground/list');
    const data = await res.json();
    const demos = data.demos || [];
    if (!currentDemoId) currentDemoId = localStorage.getItem('apd_demo_id') || ((demos[0] || {}).demo_id || '');
    demoPlaygroundStatus.innerHTML = demos.length ? demos.map(renderDemoSummary).join('') : '<div class="example-card">暂无运行中的 Demo，点击“生成并启动 Demo”。</div>';
  } catch (err) {
    demoPlaygroundStatus.innerHTML = '<div class="example-card">加载 Demo 列表失败：' + escapeHtml(err && err.message ? err.message : err) + '</div>';
  }
}
function renderBeginnerList(items) {
  return `<ul>${(items || []).map(item => `<li>${escapeHtml(item)}</li>`).join('')}</ul>`;
}
function renderMaturityChecks(items, done) {
  const icon = done ? '✅' : '⚠️';
  const empty = done ? '暂无已完成项。' : '暂无明显缺口。';
  if (!items || !items.length) return `<div class="example-card">${empty}</div>`;
  return `<div class="case-grid">${items.map(item => `<div class="case-card">
    <h3>${icon} ${escapeHtml(item.name || '')}</h3>
    <p>${escapeHtml(item.why || '')}</p>
  </div>`).join('')}</div>`;
}
function renderNextTasks(tasks) {
  if (!tasks || !tasks.length) return '<div class="example-card">暂无下一步任务。</div>';
  return `<div class="timeline">${tasks.map((task, index) => `<div class="timeline-card ${index === 0 ? 'partial' : 'done'}">
    <div class="timeline-step">${index + 1}</div>
    <div>
      <div class="timeline-title">${escapeHtml(task.priority || '')}：${escapeHtml(task.title || '')}</div>
      <div class="timeline-plain">目标：${escapeHtml(task.goal || '')}</div>
      <div class="timeline-detail">建议文件：${escapeHtml((task.files || []).join('、') || '-')}</div>
    </div>
  </div>`).join('')}</div>`;
}
function renderDemoOneClickResult(data) {
  const exp = data.beginner_explanation || {};
  const maturity = data.maturity || {};
  const agent = data.agent_run || {};
  const workflow = data.workflow_run || {};
  const store = data.store_snapshot || {};
  const tools = data.tools || {};
  const op = (agent.op_call || {}).operation || '-';
  const permission = (agent.permission_check || {}).status || '-';
  const toolCount = (agent.tool_results || []).length;
  const artifactCount = (agent.artifact_versions || []).length;
  const traceCount = (agent.trace || []).length;
  const nodeCount = Object.keys(workflow.node_states || {}).length;
  const score = Number(maturity.score || 0);
  const scoreColor = score < 45 ? '#f97316' : (score < 70 ? '#eab308' : '#22c55e');
  return `
    <section class="beginner-box">
      <h3>先看结论：当前 Demo 成熟度 ${escapeHtml(String(score))}%</h3>
      <p><strong>阶段：</strong>${escapeHtml(maturity.stage || '未知')}</p>
      <p>${escapeHtml(maturity.plain_summary || exp['一句话结论'] || '')}</p>
      <div style="height:14px;background:#1e293b;border-radius:999px;overflow:hidden;margin-top:10px;">
        <div style="height:100%;width:${Math.max(0, Math.min(100, score))}%;background:${scoreColor};"></div>
      </div>
      <p style="margin-top:10px;"><strong>重点：</strong>如果你没感受到业务效果，通常不是没跑起来，而是还停在“可运行骨架”，真实 LLM、真实 Executor、真实工具或真实文档产物还没接。</p>
    </section>
    <section class="preview-section">
      <h3>现在已经做到什么</h3>
      <div class="desc">这些说明 APD 生成出来的工程骨架已经具备哪些能力。</div>
      ${renderMaturityChecks(maturity.completed || [], true)}
    </section>
    <section class="diagnostics">
      <h3>为什么你现在还没明显业务感受</h3>
      <div class="desc">这些是当前 Demo 还缺的真实业务能力。</div>
      ${renderMaturityChecks(maturity.missing || [], false)}
    </section>
    <section class="preview-section">
      <h3>下一步应该开发什么</h3>
      <div class="desc">这比看接口返回更重要。照这个顺序让 Codex / Claude Code / 人类开发即可。</div>
      ${renderNextTasks(maturity.next_tasks || [])}
    </section>
    <div class="preview-summary">
      <div><strong>选中的操作</strong>${escapeHtml(op)}</div>
      <div><strong>权限结果</strong>${escapeHtml(permission)}</div>
      <div><strong>工具结果</strong>${escapeHtml(String(toolCount))} 条</div>
      <div><strong>产物版本</strong>${escapeHtml(String(artifactCount))} 个</div>
      <div><strong>过程日志</strong>${escapeHtml(String(traceCount))} 条</div>
      <div><strong>Workflow 节点</strong>${escapeHtml(String(nodeCount))} 个</div>
    </div>
    <details class="preview-dev-details">
      <summary>展开：刚才 APD 自动做了什么</summary>
      ${renderBeginnerList(exp['你刚才点按钮发生了什么'])}
      ${previewSection('怎么看这些结果', '把英文技术字段翻译成人话。', exp['怎么看结果'] || [])}
    </details>
    <details class="preview-dev-details">
      <summary>展开开发者细节：完整接口返回 JSON</summary>
      ${previewSection('Maturity', '成熟度评估原始结构。', maturity)}
      ${previewSection('Agent Run', '生成 Demo 的单轮 Agent 接口返回。', agent)}
      ${previewSection('Workflow Run', '生成 Demo 的节点级 Workflow 接口返回。', workflow)}
      ${previewSection('Store Snapshot', '生成 Demo 的内存状态、记忆、产物和 Trace。', store)}
      ${previewSection('Tools', '生成 Demo 的工具适配器模板。', tools)}
      ${previewSection('Raw', '一键体验完整返回。', data)}
    </details>`;
}
async function runDemoOneClick() {
  if (!sessionId) return;
  const projectName = demoProjectName.value.trim();
  const messageText = demoMessage.value.trim() || '测试一句用户话术';
  localStorage.setItem('apd_demo_project_name', projectName);
  localStorage.setItem('apd_demo_message', messageText);
  localStorage.setItem('apd_demo_context', demoContext.value.trim());
  demoPlaygroundResult.innerHTML = '<section class="beginner-box"><h3>正在一键体验...</h3><p>APD 正在生成临时 Demo、启动服务、运行测试话术、读取状态和工具。一般需要几秒钟。</p></section>';
  setStatus('Demo 一键体验中...', 'warn');
  try {
    const res = await fetch('/api/demo-playground/one-click', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({session_id: sessionId, project_name: projectName, message: messageText, context: parseDemoContext()})
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || '一键体验失败');
    currentDemoId = (data.demo || {}).demo_id || currentDemoId;
    if (currentDemoId) localStorage.setItem('apd_demo_id', currentDemoId);
    demoPlaygroundResult.innerHTML = renderDemoOneClickResult(data);
    await refreshDemoPlaygroundList();
    setStatus('Demo 一键体验完成', 'ok');
  } catch (err) {
    demoPlaygroundResult.innerHTML = `<section class="diagnostics"><h3>一键体验失败</h3><p>${escapeHtml(err && err.message ? err.message : err)}</p><p>建议先确认当前会话已经有 operation；如果还没有，请回到左侧对话继续设计 Agent。</p></section>`;
    setStatus('Demo 一键体验失败', 'warn');
  }
}
async function startDemoPlayground() {
  if (!sessionId) return;
  const projectName = demoProjectName.value.trim();
  localStorage.setItem('apd_demo_project_name', projectName);
  demoPlaygroundResult.innerHTML = '正在生成并启动 Demo...';
  setStatus('正在启动 Demo Playground...', 'warn');
  try {
    const res = await fetch('/api/demo-playground/start', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({session_id: sessionId, project_name: projectName})
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || '启动失败');
    currentDemoId = data.demo_id;
    localStorage.setItem('apd_demo_id', currentDemoId);
    demoPlaygroundResult.innerHTML = previewSection('Demo 已启动', '现在可以测试 /agent/run、/workflow/run、/store/snapshot 和 /tools。', data);
    await refreshDemoPlaygroundList();
    setStatus('Demo Playground 已启动', 'ok');
  } catch (err) {
    demoPlaygroundResult.innerHTML = 'Demo 启动失败：' + escapeHtml(err && err.message ? err.message : err);
    setStatus('Demo Playground 启动失败', 'warn');
  }
}
async function stopDemoPlayground(demoId) {
  demoId = demoId || currentDemoId;
  if (!demoId) { setStatus('没有可停止的 Demo', 'warn'); return; }
  try {
    const res = await fetch(`/api/demo-playground/${encodeURIComponent(demoId)}/stop`, {method:'POST'});
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || '停止失败');
    if (currentDemoId === demoId) currentDemoId = '';
    demoPlaygroundResult.innerHTML = previewSection('Demo 已停止', '该临时 FastAPI 进程已停止。', data);
    await refreshDemoPlaygroundList();
    setStatus('Demo 已停止', 'ok');
  } catch (err) {
    demoPlaygroundResult.innerHTML = '停止 Demo 失败：' + escapeHtml(err && err.message ? err.message : err);
    setStatus('停止 Demo 失败', 'warn');
  }
}
async function runDemoEndpoint(kind) {
  if (!currentDemoId) { setStatus('请先生成并启动 Demo', 'warn'); return; }
  const messageText = demoMessage.value.trim() || '测试一句用户话术';
  localStorage.setItem('apd_demo_message', messageText);
  localStorage.setItem('apd_demo_context', demoContext.value.trim());
  const context = parseDemoContext();
  const path = kind === 'workflow' ? 'workflow-run' : 'agent-run';
  demoPlaygroundResult.innerHTML = `正在测试 /${kind === 'workflow' ? 'workflow/run' : 'agent/run'}...`;
  try {
    const res = await fetch(`/api/demo-playground/${encodeURIComponent(currentDemoId)}/${path}`, {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({message: messageText, context, max_steps: 3})
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || '运行失败');
    demoPlaygroundResult.innerHTML = renderDemoRunResult(kind, data);
    setStatus('Demo 测试完成', 'ok');
  } catch (err) {
    demoPlaygroundResult.innerHTML = 'Demo 测试失败：' + escapeHtml(err && err.message ? err.message : err);
    setStatus('Demo 测试失败', 'warn');
  }
}
function runDemoAgent() { return runDemoEndpoint('agent'); }
function runDemoWorkflow() { return runDemoEndpoint('workflow'); }
function renderDemoRunResult(kind, data) {
  const title = kind === 'workflow' ? 'Workflow Demo 运行结果' : 'Agent Demo 运行结果';
  const summary = kind === 'workflow'
    ? `<div class="preview-summary"><div><strong>状态</strong>${escapeHtml(data.status || '-')}</div><div><strong>节点数</strong>${escapeHtml(String(((data.summary || {}).total_nodes) ?? 0))}</div><div><strong>完成节点</strong>${escapeHtml(String(((data.summary || {}).completed_nodes) ?? 0))}</div><div><strong>产物</strong>${escapeHtml(String(((data.summary || {}).artifact_count) ?? 0))}</div></div>`
    : `<div class="preview-summary"><div><strong>下一步</strong>${escapeHtml(data.next_action || '-')}</div><div><strong>权限</strong>${escapeHtml((data.permission_check || {}).status || '-')}</div><div><strong>工具结果</strong>${escapeHtml(String((data.tool_results || []).length))}</div><div><strong>产物版本</strong>${escapeHtml(String((data.artifact_versions || []).length))}</div></div>`;
  return `<section class="beginner-box"><h3>${title}</h3><p>这是生成出来的 Demo 项目真实 FastAPI 接口返回，不是 APD Preview 的模拟结果。</p></section>${summary}` +
    previewSection('关键结果', '小白优先看这里：是否有工具结果、状态快照、产物版本、节点状态和 Trace。', kind === 'workflow' ? {node_states:data.node_states, summary:data.summary, artifacts:data.artifacts, trace:data.trace} : {op_call:data.op_call, tool_results:data.tool_results, state_snapshot:data.state_snapshot, artifact_versions:data.artifact_versions, trace:data.trace}) +
    previewSection('完整返回 Raw', '完整 JSON，方便复制给 Codex/Claude 分析。', data);
}
async function loadDemoStore() {
  if (!currentDemoId) { setStatus('请先生成并启动 Demo', 'warn'); return; }
  const res = await fetch(`/api/demo-playground/${encodeURIComponent(currentDemoId)}/store`);
  const data = await res.json();
  demoPlaygroundResult.innerHTML = previewSection('Demo Store Snapshot', '状态、记忆、产物版本、Trace 和 Job 都在这里。', data);
}
async function loadDemoTools() {
  if (!currentDemoId) { setStatus('请先生成并启动 Demo', 'warn'); return; }
  const res = await fetch(`/api/demo-playground/${encodeURIComponent(currentDemoId)}/tools`);
  const data = await res.json();
  demoPlaygroundResult.innerHTML = previewSection('Demo Tools', '生成项目里的 Tool Adapter dry-run 模板。', data);
}
let currentDevWorkspaceId = '';
function currentWorkspaceId(){return currentDevWorkspaceId || localStorage.getItem('apd_dev_workspace_id') || '';}
function openDevStudioAdvanced(){openDevStudio();setTimeout(()=>{const advanced=document.getElementById('devAdvancedTools');if(advanced)advanced.scrollIntoView({behavior:'smooth',block:'start'});},120);}
async function ensureCollabWorkspace(){if(currentWorkspaceId()){await refreshCollabWorkspaceConsole();setStatus('已使用当前工作区：'+currentWorkspaceId(),'ok');return currentWorkspaceId();} if(typeof createDevWorkspace==='function'){await createDevWorkspace();await refreshCollabWorkspaceConsole();return currentWorkspaceId();} setStatus('请先创建工作区','warn');return '';}
async function saveCurrentWorkspaceVersion(){const id=currentWorkspaceId();if(!id){setStatus('请先创建/选择工作区','warn');return;} await saveDevVersion(id); await refreshCollabWorkspaceConsole();}
function downloadCurrentWorkspace(){const id=currentWorkspaceId();if(!id){setStatus('请先创建/选择工作区','warn');return;} downloadDevWorkspace('current',id);}
function renderCollabWorkspaceMiniCard(workspace){const selected=workspace.workspace_id===currentWorkspaceId();const versions=workspace.versions||[];const name=workspace.name||workspace.project_name||workspace.workspace_id;const fileCount=String(((workspace.summary||{}).file_count)||0);return `<div class="collab-workspace-mini-card ${selected?'active':''}"><div class="row"><div><strong>${selected?'✅ ':''}${escapeHtml(name)}</strong><div class="small"><code>${escapeHtml(workspace.workspace_id||'')}</code> · ${escapeHtml(fileCount)} files · ${escapeHtml(workspace.current_version||'-')}</div></div><div class="quick-row"><button onclick="selectDevWorkspace('${workspace.workspace_id}');refreshCollabWorkspaceConsole()">选择</button><button onclick="selectDevWorkspace('${workspace.workspace_id}');openAgentIdeDelegatedInspector()">真实调试</button><button onclick="downloadDevWorkspace('current','${workspace.workspace_id}')">下载</button></div></div>${versions.length?`<div class="small">最近版本：${escapeHtml((versions[versions.length-1]||{}).version_id||'')}</div>`:'<div class="small">暂无版本快照，建议稳定后保存版本。</div>'}</div>`;}
async function refreshCollabWorkspaceConsole(){const summary=document.getElementById('collabWorkspaceSummary');const list=document.getElementById('collabWorkspaceMiniList');if(!summary&&!list)return;if(summary)summary.textContent='正在读取工作区...';try{const url=sessionId?`/api/dev-studio/workspaces?session_id=${encodeURIComponent(sessionId)}`:'/api/dev-studio/workspaces';const res=await fetch(url);const data=await res.json();if(!res.ok)throw new Error(data.error||'读取失败');const workspaces=data.workspaces||[];if(workspaces.length&&!currentWorkspaceId()){currentDevWorkspaceId=workspaces[0].workspace_id||'';localStorage.setItem('apd_dev_workspace_id',currentDevWorkspaceId);}const selected=workspaces.find(w=>w.workspace_id===currentWorkspaceId())||workspaces[0];if(summary)summary.textContent=selected?`当前：${selected.name||selected.project_name||selected.workspace_id} · ${selected.workspace_id}`:'还没有工作区，点击“创建/选择工作区”。';if(list)list.innerHTML=workspaces.length?workspaces.slice(0,3).map(renderCollabWorkspaceMiniCard).join(''):'<div class="collab-workspace-mini-card"><strong>还没有工作区</strong><div class="small">点击“创建/选择工作区”，APD 会基于当前协议生成可持续开发的 Agent 工程。</div></div>';updateCliWorkspaceHint();}catch(err){if(summary)summary.textContent='工作区读取失败';if(list)list.innerHTML='<div class="collab-workspace-mini-card">读取失败：'+escapeHtml(err&&err.message?err.message:err)+'</div>';}}

function openDevStudio() {
  document.getElementById('devStudioMask').classList.add('open');
  devWorkspaceName.value = localStorage.getItem('apd_dev_workspace_name') || '';
  devRunMessage.value = localStorage.getItem('apd_dev_run_message') || '测试一句用户话术';
  devRunContext.value = localStorage.getItem('apd_dev_run_context') || '{}';
  devVersionMessage.value = localStorage.getItem('apd_dev_version_message') || '';
  devQuickReply.value = localStorage.getItem('apd_dev_quick_reply') || '';
  loadOpenClaudeSettings();
  loadCollabDraft();
  currentDevWorkspaceId = localStorage.getItem('apd_dev_workspace_id') || '';
  refreshDevWorkspaces().then(() => loadDevFiles());
}
function openCollabAssistant() {
  openDevStudio();
  setTimeout(() => {
    const panel = document.getElementById('collabAssistantPanel');
    if (panel) {
      panel.open = true;
      panel.scrollIntoView({behavior:'smooth', block:'start'});
    }
    if (typeof collabUserIntent !== 'undefined') collabUserIntent.focus();
    refreshCollabWorkspaceConsole();setStatus('已打开工程开发台：直接描述你的想法或问题', 'ok');
  }, 120);
}
function closeDevStudio(event) {
  if (event && event.target !== document.getElementById('devStudioMask')) return;
  document.getElementById('devStudioMask').classList.remove('open');
}
function parseDevContext() {
  const raw = devRunContext.value.trim();
  if (!raw) return {};
  try { return JSON.parse(raw); }
  catch (err) { throw new Error('上下文 JSON 不合法：' + err.message); }
}
function selectDevWorkspace(workspaceId) {
  currentDevWorkspaceId = workspaceId;
  localStorage.setItem('apd_dev_workspace_id', workspaceId);
  refreshDevWorkspaces().then(() => loadDevFiles());
  updateCliWorkspaceHint();
  setStatus('已选择开发工作区：' + workspaceId, 'ok');
  renderLandingGuide();
}
function renderDevWorkspaceCard(workspace) {
  const selected = workspace.workspace_id === currentDevWorkspaceId;
  const workspaceDomId = 'workspaceMore_' + String(workspace.workspace_id || '').replace(/[^a-zA-Z0-9_-]/g, '_');
  const versions = workspace.versions || [];
  const focus = (((workspace.summary || {}).editable_focus) || []).map(item => `<li><code>${escapeHtml(item.path || '')}</code>：${escapeHtml(item.purpose || '')}</li>`).join('');
  const versionHtml = versions.length ? versions.map(version => `<div class="example-card compact-card">
    <strong>${escapeHtml(version.version_id || '')}</strong>：${escapeHtml(version.message || '')}
    <span class="pill">${escapeHtml(version.created_at || '-')}</span>
    <button onclick="rollbackDevWorkspace('${workspace.workspace_id}', '${version.version_id}')">回滚</button>
    <button onclick="downloadDevWorkspace('${version.version_id}', '${workspace.workspace_id}')">下载</button>
  </div>`).join('') : '<div class="example-card compact-card">暂无版本。</div>';
  return `<div class="example-card compact-card" style="border-color:${selected ? '#38bdf8' : 'var(--border)'}">
    <div class="compact-row">
      <strong>${selected ? '✅ ' : ''}${escapeHtml(workspace.name || workspace.project_name || workspace.workspace_id)}</strong>
      <span class="pill">${escapeHtml(workspace.current_version || '-')} · ${escapeHtml(String(((workspace.summary || {}).file_count) || 0))} files</span>
      <button onclick="selectDevWorkspace('${workspace.workspace_id}')">选择</button>
      <button onclick="runDevWorkspace('${workspace.workspace_id}')">运行</button>
      <button onclick="downloadDevWorkspace('current', '${workspace.workspace_id}')">下载</button>
      <button class="primary" onclick="toggleWorkspaceMore('${workspaceDomId}')">更多/版本</button>
    </div>
    <div id="${workspaceDomId}" class="workspace-more" style="display:none;">
      <p>项目目录：${escapeHtml(workspace.project_root || '')}</p>
      <p>工作区 ID：${escapeHtml(workspace.workspace_id || '')}</p>
      <div class="sandbox-form">
        <label class="sandbox-full">版本说明
          <input id="devVersionMessage" placeholder="例如：接入第一版真实 Planner 前的稳定版本" />
        </label>
      </div>
      <div class="sandbox-actions">
        <button onclick="saveDevVersion('${workspace.workspace_id}')">保存新版本</button>
      </div>
      <h3>建议改动文件</h3>
      <ul>${focus || '<li>暂无建议文件。</li>'}</ul>
      <h3>版本历史</h3>
      ${versionHtml}
    </div>
  </div>`;
}
function toggleWorkspaceMore(id) {
  const el = document.getElementById(id);
  if (!el) return;
  el.style.display = el.style.display === 'none' ? 'block' : 'none';
}
async function refreshDevWorkspaces() {
  if (!sessionId) return;
  try {
    const res = await fetch(`/api/dev-studio/workspaces?session_id=${encodeURIComponent(sessionId)}`);
    const data = await res.json();
    const workspaces = data.workspaces || [];
    const workspaceIds = workspaces.map(item => item.workspace_id).filter(Boolean);
    if (workspaces.length && (!currentDevWorkspaceId || !workspaceIds.includes(currentDevWorkspaceId))) {
      currentDevWorkspaceId = workspaceIds.includes(localStorage.getItem('apd_dev_workspace_id')) ? localStorage.getItem('apd_dev_workspace_id') : (workspaces[0].workspace_id || '');
      localStorage.setItem('apd_dev_workspace_id', currentDevWorkspaceId);
    }
    devStudioWorkspaces.innerHTML = workspaces.length
      ? workspaces.map(renderDevWorkspaceCard).join('')
      : '<div class="example-card">当前会话还没有开发工作区。先点击“创建可持续开发工作区”。</div>';
  } catch (err) {
    devStudioWorkspaces.innerHTML = '<div class="example-card">加载工作区失败：' + escapeHtml(err && err.message ? err.message : err) + '</div>';
  }
}
async function createDevWorkspace() {
  if (!sessionId) return;
  const name = devWorkspaceName.value.trim();
  localStorage.setItem('apd_dev_workspace_name', name);
  devStudioResult.innerHTML = '正在创建工作区：生成项目、保存 v1 快照...';
  setStatus('Agent 开发台正在创建工作区...', 'warn');
  try {
    const res = await fetch('/api/dev-studio/workspace', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({session_id: sessionId, name})
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || '创建失败');
    currentDevWorkspaceId = data.workspace_id;
    localStorage.setItem('apd_dev_workspace_id', currentDevWorkspaceId);
    devStudioResult.innerHTML = renderDevWorkspaceCreated(data);
    await refreshDevWorkspaces();
    await loadDevFiles();
    updateCliWorkspaceHint();
    setStatus('开发工作区已创建', 'ok');
    renderLandingGuide();
    await refreshCollabWorkspaceConsole();
  } catch (err) {
    devStudioResult.innerHTML = '<section class="diagnostics"><h3>创建失败</h3><p>' + escapeHtml(err && err.message ? err.message : err) + '</p></section>';
    setStatus('开发工作区创建失败', 'warn');
  }
}
function renderDevWorkspaceCreated(data) {
  return `<section class="beginner-box">
    <h3>工作区创建完成：现在你有一份可以持续开发的 Agent 项目了</h3>
    <p><strong>做到哪一步：</strong>APD 已根据当前协议生成工程骨架，并保存为第一个版本 v1。</p>
    <p><strong>下一步干什么：</strong>点击“运行当前版本”，看这份项目现在能跑到什么程度。</p>
    <p><strong>项目目录：</strong>${escapeHtml(data.project_root || '')}</p>
  </section>` + previewSection('工作区详情', '开发者可看完整结构。', data);
}
async function runDevWorkspace(workspaceId) {
  workspaceId = workspaceId || currentDevWorkspaceId;
  if (!workspaceId) { setStatus('请先创建或选择开发工作区', 'warn'); return; }
  const messageText = devRunMessage.value.trim() || '测试一句用户话术';
  localStorage.setItem('apd_dev_run_message', messageText);
  localStorage.setItem('apd_dev_run_context', devRunContext.value.trim());
  devStudioResult.innerHTML = '正在真实启动当前工作区并运行接口...';
  setStatus('开发台运行中...', 'warn');
  try {
    const context = parseDevContext();
    const res = await fetch(`/api/dev-studio/workspace/${encodeURIComponent(workspaceId)}/run`, {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({message: messageText, context, max_steps: 3})
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || '运行失败');
    devStudioResult.innerHTML = renderDevRunResult(data);
    await refreshDevWorkspaces();
    setStatus('开发台运行完成', 'ok');
  } catch (err) {
    devStudioResult.innerHTML = '<section class="diagnostics"><h3>运行失败</h3><p>' + escapeHtml(err && err.message ? err.message : err) + '</p><p>如果是依赖问题，请确认生成项目 backend 的 requirements 是否已安装在 APD 环境。</p></section>';
    setStatus('开发台运行失败', 'warn');
  }
}
function renderDevRunResult(data) {
  const agent = data.agent_run || {};
  const workflow = data.workflow_run || {};
  const store = data.store_snapshot || {};
  const explanation = data.explanation || {};
  const op = (agent.op_call || {}).operation || '-';
  const permission = (agent.permission_check || {}).status || '-';
  const nodeCount = Object.keys(workflow.node_states || {}).length;
  const traceCount = (store.trace_events || []).length;
  const nextFiles = (explanation['下一步改哪里'] || []).map(path => `<li><code>${escapeHtml(path)}</code></li>`).join('');
  return `<section class="beginner-box">
    <h3>运行完成：当前版本能真实启动并跑通接口</h3>
    <p><strong>做到哪一步：</strong>${escapeHtml(explanation['做到哪一步'] || '已启动当前项目并调用 Agent / Workflow 接口。')}</p>
    <p><strong>为什么还可能没有业务感受：</strong>${escapeHtml(explanation['为什么有业务感受还不强'] || '当前还是生成骨架，真实业务逻辑需要继续开发。')}</p>
    <p><strong>下一步：</strong>如果你认可这次结果，点“保存新版本”；如果要继续变强，就改下面这些文件。</p>
    <ul>${nextFiles || '<li>暂无建议文件。</li>'}</ul>
  </section>
  <div class="preview-summary">
    <div><strong>绑定操作</strong>${escapeHtml(op)}</div>
    <div><strong>权限结果</strong>${escapeHtml(permission)}</div>
    <div><strong>Workflow 节点</strong>${escapeHtml(String(nodeCount))}</div>
    <div><strong>Trace 数量</strong>${escapeHtml(String(traceCount))}</div>
  </div>
  <details class="preview-dev-details">
    <summary>展开开发者细节：接口返回和 Store</summary>
    ${previewSection('Agent Run', '单轮 Agent 接口返回。', agent)}
    ${previewSection('Workflow Run', '节点级 Workflow 接口返回。', workflow)}
    ${previewSection('Store Snapshot', '状态、记忆、产物、Trace。', store)}
    ${previewSection('Tools', '工具适配器。', data.tools)}
    ${previewSection('Raw', '完整返回。', data)}
  </details>`;
}
async function loadDevFiles() {
  if (!currentDevWorkspaceId) return;
  try {
    const res = await fetch(`/api/dev-studio/workspace/${encodeURIComponent(currentDevWorkspaceId)}/files`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || '加载文件失败');
    const files = data.files || [];
    devFileSelect.innerHTML = files.map(file => `<option value="${escapeHtml(file.path || '')}">${escapeHtml(file.path || '')} - ${escapeHtml(file.purpose || '')}</option>`).join('');
    if (files.some(file => file.path === 'backend/app/executor.py')) devFileSelect.value = 'backend/app/executor.py';
    await loadDevFile();
  } catch (err) {
    devStudioResult.innerHTML = '<section class="diagnostics"><h3>文件列表加载失败</h3><p>' + escapeHtml(err && err.message ? err.message : err) + '</p></section>';
  }
}
async function loadDevFile() {
  if (!currentDevWorkspaceId || !devFileSelect.value) return;
  try {
    const res = await fetch(`/api/dev-studio/workspace/${encodeURIComponent(currentDevWorkspaceId)}/file?path=${encodeURIComponent(devFileSelect.value)}`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || '读取文件失败');
    devFileEditor.value = data.content || '';
    setStatus('已读取文件：' + data.path, 'ok');
  } catch (err) {
    setStatus('读取文件失败', 'warn');
    devStudioResult.innerHTML = '<section class="diagnostics"><h3>读取文件失败</h3><p>' + escapeHtml(err && err.message ? err.message : err) + '</p></section>';
  }
}
async function saveDevFile() {
  if (!currentDevWorkspaceId || !devFileSelect.value) { setStatus('请先选择工作区和文件', 'warn'); return null; }
  const res = await fetch(`/api/dev-studio/workspace/${encodeURIComponent(currentDevWorkspaceId)}/file`, {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({path: devFileSelect.value, content: devFileEditor.value})
  });
  const data = await res.json();
  if (!res.ok) {
    devStudioResult.innerHTML = '<section class="diagnostics"><h3>保存文件失败</h3><p>' + escapeHtml(data.error || '保存失败') + '</p></section>';
    setStatus('保存文件失败', 'warn');
    return null;
  }
  devStudioResult.innerHTML = `<section class="beginner-box"><h3>文件已保存：${escapeHtml(data.path || '')}</h3><p>现在点“运行当前版本”，就能看到这次改动是否影响 Agent 行为。</p></section>` + previewSection('保存结果', '文件已写入当前工作区，但还没有保存成版本快照。', data);
  await refreshDevWorkspaces();
  setStatus('文件已保存', 'ok');
  return data;
}
async function saveDevFileAndRun() {
  const saved = await saveDevFile();
  if (saved) await runDevWorkspace();
}
async function quickReplyAndRunDev() {
  if (!currentDevWorkspaceId) { setStatus('请先创建或选择开发工作区', 'warn'); return; }
  const reply = devQuickReply.value.trim();
  if (!reply) { setStatus('请先填写快速回复文案', 'warn'); return; }
  localStorage.setItem('apd_dev_quick_reply', reply);
  devStudioResult.innerHTML = '正在修改 executor.py 里的回复文案，并立即运行...';
  setStatus('正在边改边运行...', 'warn');
  try {
    const res = await fetch(`/api/dev-studio/workspace/${encodeURIComponent(currentDevWorkspaceId)}/quick-reply`, {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({reply})
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || '快速修改失败');
    await loadDevFiles();
    await runDevWorkspace();
  } catch (err) {
    devStudioResult.innerHTML = '<section class="diagnostics"><h3>快速修改失败</h3><p>' + escapeHtml(err && err.message ? err.message : err) + '</p></section>';
    setStatus('快速修改失败', 'warn');
  }
}
let currentDeveloperPlan = null;
function loadOpenClaudeSettings() { loadDeveloperRunnerSettings(); }
function loadDeveloperRunnerSettings() {
  if (typeof developerRequest === 'undefined') return;
  developerRequest.value = localStorage.getItem('apd_developer_request') || '';
  developerRunner.value = localStorage.getItem('apd_developer_runner') || 'open_claude';
  developerBaseUrl.value = localStorage.getItem('apd_developer_base_url') || '';
  developerApiKey.value = localStorage.getItem('apd_developer_api_key') || '';
  developerModel.value = localStorage.getItem('apd_developer_model') || '';
  developerCliRoot.value = localStorage.getItem('apd_developer_cli_root') || '/home/data/rag/open_claude/Openclaude-openclaude';
  developerWorkDir.value = localStorage.getItem('apd_developer_work_dir') || '';
  developerTimeout.value = localStorage.getItem('apd_developer_timeout') || '900';
  developerCommandTemplate.value = localStorage.getItem('apd_developer_command_template') || '';
}
function saveDeveloperRunnerSettings() {
  localStorage.setItem('apd_developer_request', developerRequest.value.trim());
  localStorage.setItem('apd_developer_runner', developerRunner.value);
  localStorage.setItem('apd_developer_base_url', developerBaseUrl.value.trim());
  localStorage.setItem('apd_developer_api_key', developerApiKey.value.trim());
  localStorage.setItem('apd_developer_model', developerModel.value.trim());
  localStorage.setItem('apd_developer_cli_root', developerCliRoot.value.trim());
  localStorage.setItem('apd_developer_work_dir', developerWorkDir.value.trim());
  localStorage.setItem('apd_developer_timeout', developerTimeout.value.trim() || '900');
  localStorage.setItem('apd_developer_command_template', developerCommandTemplate.value.trim());
}
function fillDeveloperRunnerDefaults() {
  developerRunner.value = 'open_claude';
  developerCliRoot.value = '/home/data/rag/open_claude/Openclaude-openclaude';
  if (!developerTimeout.value) developerTimeout.value = '900';
  saveDeveloperRunnerSettings();
  setStatus('已填入 open_claude 默认 Runner 配置', 'ok');
}

let currentCollabTask = '';
function classifyCollabIntent(text) {
  if (/报错|错误|失败|异常|修复|bug|traceback|error|exception/i.test(text)) return '调试修复';
  if (/效果|不好|不对|泛|质量|不满意|优化|更准确|更稳定|不符合/i.test(text)) return '效果优化';
  if (/接入|知识库|工具|api|检索|数据库|上传|导出|docx|pdf|模板|文件/i.test(text)) return '工具/能力接入';
  if (/测试|验收|运行|验证|怎么用|启动|部署/i.test(text)) return '运行验收';
  if (/解释|看不懂|结构|文件|负责什么|架构|原理/i.test(text)) return '项目理解';
  if (/保存|版本|回滚|下载|发布|打包/i.test(text)) return '版本管理';
  return '新增/调整功能';
}
function summarizeCurrentProtocolForCollab() {
  const source = protocol && typeof protocol === 'object' ? protocol : {};
  const names = [];
  const collect = (items) => {
    if (!Array.isArray(items)) return;
    items.slice(0, 12).forEach(item => {
      if (typeof item === 'string') names.push(item);
      else if (item && typeof item === 'object') names.push(item.name || item.id || item.title || '未命名');
    });
  };
  collect(source.operations);
  collect(source.capabilities);
  collect(source.tools);
  const projectName = source.project_name || source.name || source.title || ((typeof currentSessionTitle !== 'undefined' && currentSessionTitle) ? currentSessionTitle : '当前 Agent');
  return {
    projectName,
    opsText: names.length ? names.join('、') : '当前协议还没有明确操作列表，请先从现有代码和 README 反推能力边界',
    hasProtocol: Boolean(Object.keys(source).length)
  };
}
function buildCollabRunnerTask(kind, text) {
  const summary = summarizeCurrentProtocolForCollab();
  const workspaceText = currentDevWorkspaceId ? currentDevWorkspaceId : '未选择；请先让用户在 APD 创建/选择工作区后再实际改代码';
  const typeGuide = {
    '调试修复': '先复现问题或阅读用户提供的报错；定位根因后做最小修复；不要绕过校验或删除关键架构层。',
    '效果优化': '先判断问题属于上下文不足、意图识别、OpCall 归一化、Validator、Executor、Observation 还是 Prompt 表达；优先补可观测链路和验收样例。',
    '工具/能力接入': '先定义工具能力边界、输入输出、失败情况和校验规则；再接入 planner/executor/workflow；避免把业务规则只写进提示词。',
    '运行验收': '先阅读启动命令和测试入口；运行最小验收；如果失败，输出失败原因和下一步修复建议。',
    '项目理解': '只阅读项目结构并解释关键文件职责；除非用户明确要求，不要修改代码。',
    '版本管理': '先说明当前工作区状态、可保存/回滚/下载的范围；不要擅自删除历史版本。',
    '新增/调整功能': '先拆成 Agent 架构层：Context Pack、Intent Planner、OpCall、Validator、Executor、Observation、Memory、Artifact；再做最小可运行改造。'
  };
  return `你现在扮演工程落地助手，在 APD 生成的 Agent 工作区里开发。\n\n【用户原始意图】\n${text}\n\n【APD 判断的任务类型】\n${kind}\n\n【当前工作区】\n${workspaceText}\n\n【当前协议摘要】\n- Agent/项目：${summary.projectName}\n- 已识别能力/操作：${summary.opsText}\n\n【执行策略】\n${typeGuide[kind] || typeGuide['新增/调整功能']}\n\n【请按这个顺序做】\n1. 先阅读项目结构，说明你判断的入口文件、协议文件、planner、executor、workflow、tools、前端体验文件分别在哪里。\n2. 用 5 行以内复述你对用户意图的理解，并指出它落在哪些 Agent 架构层。\n3. 列出计划修改的文件和原因，确认不会改无关文件。\n4. 开始做最小可运行改造。\n5. 修改完成后运行项目已有的最小验收命令；如果无法运行，说明缺少什么条件。\n6. 最后输出：改了哪些文件、怎么验证、还有哪些风险、下一步建议。\n\n【禁止事项】\n- 不要删除 APD 生成的协议、状态、校验、执行链路。\n- 不要把所有逻辑都塞进一个 prompt，能用结构化状态/操作/校验表达的必须落到代码。\n- 不要伪造测试通过；不能运行就明确说明原因。\n- 不要泄露或打印用户 Key。\n\n【验收标准】\n- 用户这句话对应的功能/问题有明确处理结果。\n- 代码中能看到 Agent 架构链路：上下文 → 意图/计划 → 操作 → 校验/执行 → 观察/结果。\n- 页面或接口能用一个具体样例跑通。\n- 输出说明足够让 APD 用户知道下一步点“运行验收”还是继续描述问题。`;
}
function renderCollabTaskView(kind, text, task) {
  const nextStep = currentDevWorkspaceId
    ? '下一步：点“复制任务”，再点“打开终端”，把任务粘贴给 open_claude。它改完后回来点“运行验收”。'
    : '下一步：你还没有工作区。先点“创建工作区”，再复制任务给 open_claude。';
  return `<section class="beginner-box">
    <h3>我先把你的话翻译成工程任务</h3>
    <p><strong>我理解这是：</strong>${escapeHtml(kind)}</p>
    <p><strong>用户原话：</strong>${escapeHtml(text)}</p>
    <p><strong>下一步：</strong>${escapeHtml(nextStep)}</p>
  </section>
  <section class="preview-section"><h3>为什么要这样转达？</h3>
    <ul>
      <li>open_claude 擅长改代码，但需要清楚知道目标、边界、验收标准。</li>
      <li>APD 在上层负责把自然语言需求翻译成 Agent 架构任务，避免随口乱改。</li>
      <li>如果结果不满意，不用重做；继续在这里描述“哪里不对”，APD 会生成下一轮任务。</li>
    </ul>
  </section>
  <details class="preview-dev-details" open><summary>可复制给 open_claude 的任务包</summary><pre id="collabTaskText">${escapeHtml(task)}</pre></details>`;
}
function buildCollabTask() {
  const text = (collabUserIntent.value || '').trim();
  if (!text) { setStatus('请先描述你的想法或问题', 'warn'); return ''; }
  const kind = classifyCollabIntent(text);
  currentCollabTask = buildCollabRunnerTask(kind, text);
  collabTaskResult.innerHTML = renderCollabTaskView(kind, text, currentCollabTask);
  developerRequest.value = text;
  localStorage.setItem('apd_collab_intent', text);
  localStorage.setItem('apd_developer_request', text);
  setStatus('已生成 open_claude 工程任务', 'ok');
  return currentCollabTask;
}
async function copyCollabTask() {
  const text = currentCollabTask || buildCollabTask();
  if (!text) return;
  await navigator.clipboard.writeText(text);
  setStatus('工程开发任务已复制，打开终端后粘贴给 open_claude', 'ok');
}
async function useCollabAsDeveloperRequest() {
  const text = (collabUserIntent.value || '').trim();
  if (!text) { setStatus('请先填写你的想法', 'warn'); return; }
  developerRequest.value = text;
  localStorage.setItem('apd_developer_request', text);
  await buildDeveloperPlan();
}
function loadCollabDraft() {
  if (typeof collabUserIntent === 'undefined') return;
  collabUserIntent.value = localStorage.getItem('apd_collab_intent') || '';
}

function renderDeveloperPlan(plan) {
  const layers = plan.architecture_layers || [];
  const files = plan.recommended_files || [];
  const checks = plan.acceptance_checks || [];
  const risks = plan.risks || [];
  return `<section class="beginner-box">
    <h3>APD 架构判断：${escapeHtml((plan.agent_types || []).join(' + ') || 'Agent 改造')}</h3>
    <p><strong>需求：</strong>${escapeHtml(plan.request || '')}</p>
    <p><strong>重点：</strong>Runner 只负责写代码，Agent 架构边界由 APD 的任务包约束。</p>
  </section>
  <section class="preview-section"><h3>涉及架构层</h3><div class="case-grid">${layers.map(item => `<div class="case-card"><h3>${escapeHtml(item.name || '')}</h3><p>${escapeHtml(item.why || '')}</p></div>`).join('')}</div></section>
  <section class="preview-section"><h3>推荐改动文件</h3><div class="timeline">${files.map((item, index) => `<div class="timeline-card partial"><div class="timeline-step">${index + 1}</div><div><div class="timeline-title">${escapeHtml(item.path || '')}</div><div class="timeline-detail">${escapeHtml(item.reason || '')}</div></div></div>`).join('')}</div></section>
  <section class="diagnostics"><h3>风险和边界</h3><ul>${risks.map(item => `<li>${escapeHtml(item)}</li>`).join('')}</ul></section>
  <section class="preview-section"><h3>验收标准</h3><ul>${checks.map(item => `<li><strong>${escapeHtml(item.name || '')}</strong>：${escapeHtml(item.expect || '')}</li>`).join('')}</ul></section>
  <details class="preview-dev-details" open><summary>工程任务包：可复制给 Codex / Claude Code / open_claude</summary><pre id="developerTaskText">${escapeHtml(plan.runner_task || '')}</pre></details>`;
}
async function buildDeveloperPlan() {
  if (!currentDevWorkspaceId) { setStatus('请先创建或选择开发工作区', 'warn'); return null; }
  const requestText = developerRequest.value.trim();
  if (!requestText) { setStatus('请先填写 Agent 改造需求', 'warn'); return null; }
  saveDeveloperRunnerSettings();
  developerPlanResult.innerHTML = 'APD 正在生成 Agent 架构改造方案和工程任务包...';
  setStatus('正在生成工程任务包...', 'warn');
  try {
    const res = await fetch(`/api/dev-studio/workspace/${encodeURIComponent(currentDevWorkspaceId)}/engineering-plan`, {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({session_id: sessionId, request: requestText})
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || '生成任务包失败');
    currentDeveloperPlan = data;
    developerPlanResult.innerHTML = renderDeveloperPlan(data);
    setStatus('工程任务包已生成', 'ok');
    return data;
  } catch (err) {
    developerPlanResult.innerHTML = '<section class="diagnostics"><h3>生成任务包失败</h3><p>' + escapeHtml(err && err.message ? err.message : err) + '</p></section>';
    setStatus('生成任务包失败', 'warn');
    return null;
  }
}
async function copyDeveloperTask() {
  if (!currentDeveloperPlan) await buildDeveloperPlan();
  const text = (currentDeveloperPlan || {}).runner_task || '';
  if (!text) { setStatus('暂无可复制任务包', 'warn'); return; }
  await navigator.clipboard.writeText(text);
  setStatus('工程任务包已复制', 'ok');
}
function renderDeveloperRunResult(data) {
  const plan = data.plan || {};
  const result = data.runner_result || {};
  const changed = result.changed_files || [];
  const changedHtml = changed.length ? `<div class="timeline">${changed.map((item, index) => `<div class="timeline-card partial"><div class="timeline-step">${index + 1}</div><div><div class="timeline-title">${escapeHtml(item.change || '')}</div><div class="timeline-detail">${escapeHtml(item.path || '')}</div></div></div>`).join('')}</div>` : '<div class="example-card">没有检测到文件改动。</div>';
  return `<section class="beginner-box">
    <h3>${escapeHtml(result.runner_label || result.runner || 'Runner')} 执行完成：${escapeHtml(result.status || '-')}</h3>
    <p><strong>Agent 类型：</strong>${escapeHtml((plan.agent_types || []).join(' + ') || '-')}</p>
    <p><strong>返回码：</strong>${escapeHtml(String(result.return_code ?? '-'))}</p>
    <p><strong>下一步：</strong>${escapeHtml(result.next_step || '运行当前 Agent 验收。')}</p>
  </section>
  <section class="preview-section"><h3>改动文件</h3><div class="desc">APD 根据执行前后文件快照检测。</div>${changedHtml}</section>
  <details class="preview-dev-details" open><summary>展开 Runner 输出日志</summary><pre>${escapeHtml(result.stdout || '')}</pre></details>
  <details class="preview-dev-details"><summary>展开 APD 架构任务包</summary>${renderDeveloperPlan(plan)}</details>
  ${previewSection('完整返回', 'Runner 原始返回，Key 已脱敏。', data)}`;
}
async function runDeveloperRunner() {
  if (!currentDevWorkspaceId) { setStatus('请先创建或选择开发工作区', 'warn'); return; }
  const requestText = developerRequest.value.trim();
  if (!requestText) { setStatus('请先填写 Agent 改造需求', 'warn'); return; }
  saveDeveloperRunnerSettings();
  if (!currentDeveloperPlan || currentDeveloperPlan.request !== requestText) await buildDeveloperPlan();
  devStudioResult.innerHTML = `${escapeHtml(developerRunner.value)} 正在按 APD 任务包执行工程开发，可能需要几分钟...`;
  setStatus('Developer Runner 运行中...', 'warn');
  const controller = new AbortController();
  const timeoutMs = Math.max(30, Number(developerTimeout.value || 900)) * 1000 + 30000;
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(`/api/dev-studio/workspace/${encodeURIComponent(currentDevWorkspaceId)}/developer-run`, {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      signal: controller.signal,
      body: JSON.stringify({
        session_id: sessionId,
        request: requestText,
        runner: developerRunner.value,
        base_url: developerBaseUrl.value.trim(),
        api_key: developerApiKey.value.trim(),
        model: developerModel.value.trim(),
        cli_root: developerCliRoot.value.trim(),
        work_dir: developerWorkDir.value.trim(),
        command_template: developerCommandTemplate.value.trim(),
        timeout_seconds: Number(developerTimeout.value || 900),
      })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Developer Runner 执行失败');
    currentDeveloperPlan = data.plan || currentDeveloperPlan;
    devStudioResult.innerHTML = renderDeveloperRunResult(data);
    await refreshDevWorkspaces();
    await loadDevFiles();
    const status = ((data.runner_result || {}).status || '');
    setStatus(status === 'completed' ? 'Developer Runner 执行完成' : 'Developer Runner 执行异常', status === 'completed' ? 'ok' : 'warn');
  } catch (err) {
    devStudioResult.innerHTML = '<section class="diagnostics"><h3>Developer Runner 执行失败</h3><p>' + escapeHtml(err && err.message ? err.message : err) + '</p></section>';
    setStatus('Developer Runner 执行失败', 'warn');
  } finally {
    clearTimeout(timer);
  }
}
function runOpenClaudeRunner() { developerRunner.value = 'open_claude'; return runDeveloperRunner(); }
let currentCliSessionId = localStorage.getItem('apd_cli_session_id') || '';

let currentCliCollabTask = '';
function getCliTaskMode() {
  const select = document.getElementById('cliTaskMode');
  return select ? (select.value || 'concise') : 'concise';
}
function cliTaskModeLabel(mode) {
  if (mode === 'deep') return '复杂改造';
  if (mode === 'standard') return '明确功能';
  return '日常补充';
}
function saveCliTaskMode() {
  const mode = getCliTaskMode();
  localStorage.setItem('apd_cli_task_mode', mode);
  setStatus('发送模式已切换为：' + cliTaskModeLabel(mode), 'ok');
}
function buildConciseCliTask(kind, text) {
  return `继续在当前项目里处理这个需求：
${text}

APD 判断：${kind}。
请先基于当前代码和终端上下文理解，不要重新大改架构。只做与本轮需求相关的最小修改；如果需要改代码，完成后说明改了哪些文件、如何验证、还有什么风险。`;
}
function buildStandardCliTask(kind, text) {
  const summary = summarizeCurrentProtocolForCollab();
  return `请在当前 Agent 工作区完成下面任务。

【本轮需求】
${text}

【APD 判断】
- 类型：${kind}
- Agent/项目：${summary.projectName}
- 已识别能力/操作：${summary.opsText}

【执行要求】
1. 先用 3 行以内复述你对需求的理解。
2. 指出涉及的 Agent 层：上下文、意图、操作、校验、执行、观察、记忆或产物。
3. 只修改相关文件，不要破坏已有 APD 协议和运行入口。
4. 完成后运行能运行的最小验证；不能运行就说明原因。
5. 最后输出：改动文件、验证结果、下一步建议。`;
}
function buildCliTaskByMode(mode, kind, text) {
  if (mode === 'deep') return buildCollabRunnerTask(kind, text);
  if (mode === 'standard') return buildStandardCliTask(kind, text);
  return buildConciseCliTask(kind, text);
}
function pmRequirementSummary(kind, mode) {
  const newAgentHint = /全新|另一个|独立|新系统|从零|重新做/.test((document.getElementById('cliCollabUserIntent') || {}).value || '') ? '可能需要评估是否新建 Agent' : '优先按现有 Agent 增量改造';
  return {type: kind, complexity: cliTaskModeLabel(mode), recommendation: newAgentHint};
}
function buildPmActionTask(action, text, kind, mode) {
  const summary = pmRequirementSummary(kind, mode);
  const base = text || '请基于当前项目状态继续处理。';
  if (action === 'inspect') return `请先阅读当前项目，不要修改代码。\n\n业务需求：${base}\n\n请用项目经理能看懂的话回答：\n1. 当前项目主要文件分别负责什么；\n2. 现有能力里哪些已经支持这个需求；\n3. 哪些能力还缺失；\n4. 这是增量改造还是建议新建 Agent；\n5. 下一步建议先做什么。`;
  if (action === 'plan') return `请基于当前项目和下面业务需求，输出实现方案，先不要修改代码。\n\n业务需求：${base}\n\nAPD 初步判断：${summary.type} / ${summary.complexity} / ${summary.recommendation}\n\n请输出：\n1. 需求理解；\n2. 增量改造范围；\n3. 需要改哪些文件和原因；\n4. 风险点；\n5. 最小可运行验收方式。`;
  if (action === 'build') return buildCliTaskByMode(mode, kind, base) + `\n\n本轮动作：请开始实现。优先做最小可运行版本，不要扩散到无关功能。`;
  if (action === 'summary') return `请总结当前这轮开发/分析结果，用项目经理能看懂的话回答：\n1. 已经完成了什么；\n2. 改了哪些文件；\n3. 怎么验证；\n4. 还有什么没完成或风险；\n5. 下一步建议。`;
  return buildCliTaskByMode(mode, kind, base);
}
function renderPmAnalysis(kind, text, mode) {
  const summary = pmRequirementSummary(kind, mode);
  return `<div class="cli-result-card">
    <h3>需求判断：${escapeHtml(summary.type)} / ${escapeHtml(summary.complexity)}</h3>
    <p><strong>你的需求：</strong>${escapeHtml(text || '未填写')}</p>
    <p><strong>APD 建议：</strong>${escapeHtml(summary.recommendation)}</p>
    <p><strong>推荐下一步：</strong>先点“让 AI 看现有项目”，再点“让 AI 出实现方案”，确认后再“让 AI 开始开发”。</p>
    <details class="preview-dev-details"><summary>为什么这样做？</summary><p>这样能避免 AI 没看现有代码就直接乱改，也能判断这是增量需求还是应该新建 Agent。</p></details>
  </div>`;
}
function openAgentDebug() {
  const mask = document.getElementById('agentDebugMask');
  if (mask) mask.classList.add('open');
}
function closeAgentDebug(event) {
  if (event && event.target !== document.getElementById('agentDebugMask')) return;
  const mask = document.getElementById('agentDebugMask');
  if (mask) mask.classList.remove('open');
}
function renderAgentDebugFlow(data) {
  const startup = data.startup || {};
  const agent = data.agent_run || {};
  const workflow = data.workflow_run || {};
  const store = data.store_snapshot || {};
  const tools = data.tools || {};
  const nodeStates = workflow.node_states || {};
  const traceItems = store.trace_events || store.trace || agent.trace || [];
  const toolResults = agent.tool_results || [];
  const op = (agent.op_call || {}).operation || '-';
  const permission = (agent.permission_check || {}).status || '-';
  const nodeCount = Object.keys(nodeStates).length;
  const traceCount = traceItems.length;
  const toolCount = Array.isArray(toolResults) ? toolResults.length : 0;
  const health = startup.ok === false ? '启动失败' : (nodeCount || traceCount || op !== '-' ? '已跑通' : '骨架可运行');
  const verdict = startup.ok === false
    ? '项目启动失败，需要先看日志。'
    : (nodeCount || traceCount ? 'Agent 已经产生状态流，可以继续看节点详情定位问题。' : 'Agent 能启动，但状态流还比较弱，通常说明 workflow / trace / tool 还没补全。');
  const nodes = [
    {name:'启动项目', status: startup.ok === false ? 'fail' : 'ok', detail: startup.ok === false ? '失败，看日志' : 'FastAPI 已启动'},
    {name:'意图/规划', status: op === '-' ? 'warn' : 'ok', detail: op === '-' ? '未看到明确 OpCall' : 'OpCall：' + op},
    {name:'校验', status: permission && permission !== '-' ? 'ok' : 'warn', detail: permission && permission !== '-' ? permission : '未看到校验结果'},
    {name:'工具', status: toolCount ? 'ok' : 'warn', detail: toolCount ? toolCount + ' 个工具结果' : '暂无工具调用'},
    {name:'Workflow', status: nodeCount ? 'ok' : 'warn', detail: nodeCount ? nodeCount + ' 个节点' : '暂无节点状态'},
    {name:'Trace/状态', status: traceCount ? 'ok' : 'warn', detail: traceCount ? traceCount + ' 条记录' : '暂无 Trace'},
  ];
  const flowHtml = nodes.map((item, index) => `<div class="agent-flow-node ${item.status}" data-step="${index + 1}"><strong>${escapeHtml(item.name)}</strong><span>${escapeHtml(item.detail)}</span></div>`).join('');
  const nodeHtml = nodeCount ? Object.entries(nodeStates).map(([name, state]) => {
    const status = (state && (state.status || state.state || state.phase)) || 'unknown';
    return `<div class="agent-node-card"><strong>${escapeHtml(name)}</strong><small>状态：${escapeHtml(String(status))}</small><pre>${escapeHtml(JSON.stringify(state, null, 2))}</pre></div>`;
  }).join('') : '<div class="agent-node-card"><strong>暂无 Workflow 节点</strong><small>需要继续补 workflow.py / trace 事件</small><pre>当前项目可能还只是单轮接口，或者还没有把节点状态写入 workflow_run.node_states。</pre></div>';
  return `<div class="agent-debug-shell">
    <div class="agent-debug-hero">
      <h3>运行结论：${escapeHtml(health)}</h3>
      <p>${escapeHtml(verdict)}</p>
      <div class="agent-debug-kpis">
        <div class="agent-debug-kpi"><strong>绑定操作</strong><span>${escapeHtml(op)}</span></div>
        <div class="agent-debug-kpi"><strong>Workflow 节点</strong><span>${escapeHtml(String(nodeCount))}</span></div>
        <div class="agent-debug-kpi"><strong>Trace 记录</strong><span>${escapeHtml(String(traceCount))}</span></div>
      </div>
    </div>
    <div>
      <div class="agent-debug-section-title"><span>状态流</span><span>${escapeHtml(nodes.filter(n => n.status === 'ok').length + '/' + nodes.length)} 正常</span></div>
      <div class="agent-flow">${flowHtml}</div>
    </div>
    <details class="preview-dev-details" open><summary>节点状态详情</summary><div class="agent-node-list">${nodeHtml}</div></details>
    <details class="preview-dev-details"><summary>原始返回 / 日志</summary>
      ${previewSection('Agent Run', '单轮 Agent 调用结果。', agent)}
      ${previewSection('Workflow Run', 'Workflow 节点执行结果。', workflow)}
      ${previewSection('Store Snapshot', '状态、记忆、产物、Trace。', store)}
      ${previewSection('Tools', '工具列表或工具适配器。', tools)}
      ${previewSection('Logs', '后端运行日志。', data.logs || '')}
    </details>
  </div>`;
}
let agentDebugTurns = [];
function extractAgentDebugReply(data) {
  const agent = data.agent_run || {};
  const candidates = [agent.assistant_message, agent.reply, agent.response, agent.final_response, agent.answer, agent.output, agent.message];
  for (const item of candidates) {
    if (typeof item === 'string' && item.trim()) return item.trim();
  }
  const nextAction = agent.next_action || agent.next || '';
  if (nextAction) return typeof nextAction === 'string' ? nextAction : JSON.stringify(nextAction, null, 2);
  const workflow = data.workflow_run || {};
  const nodeStates = workflow.node_states || {};
  const firstObservation = Object.values(nodeStates).map(item => item && item.observation).find(Boolean);
  if (firstObservation) return String(firstObservation);
  return '本轮已执行，但没有拿到面向用户的回复。请看下方 Agent Run 原始返回，可能需要在 executor.py 返回 assistant_message。';
}
function analyzeAgentDebugIssue(data) {
  const agent = (data || {}).agent_run || {};
  const workflow = (data || {}).workflow_run || {};
  const store = (data || {}).store_snapshot || {};
  const opCall = agent.op_call || {};
  const op = opCall.operation || '-';
  const confidence = opCall.confidence ?? '-';
  const permission = (agent.permission_check || {}).status || '-';
  const nodeStates = workflow.node_states || {};
  const traceItems = store.trace_events || store.trace || agent.trace || [];
  const toolResults = agent.tool_results || [];
  const files = new Set(['backend/app/executor.py']);
  const reasons = [];
  const levels = [];
  if (!op || op === '-' || op === 'ask_clarification') {
    levels.push({key:'intent', label:'意图识别问题'});
    files.add('backend/app/planner.py');
    reasons.push('意图/操作没有稳定命中，优先检查 planner.py 的意图识别和 OpCall 绑定。');
  } else {
    reasons.push('当前命中操作：' + op + '，置信度：' + confidence + '。');
  }
  if (permission === '-' || permission === 'failed' || permission === 'denied') {
    levels.push({key:'validator', label:'校验/权限问题'});
    files.add('backend/app/validators.py');
    reasons.push('校验结果不清晰或未通过，需要检查 validators.py 的规则和错误提示。');
  } else {
    reasons.push('校验/权限状态：' + permission + '。');
  }
  levels.push({key:'executor', label:'回复/执行问题'});
  reasons.push('如果“Agent 回复”业务语义不对，主要改 executor.py 的用户可见回复和执行结果。');
  if (!toolResults.length) {
    levels.push({key:'tool', label:'工具缺失/未调用'});
    files.add('backend/app/tools.py');
    reasons.push('没有看到工具结果；如果需求需要知识库、文件、模板、导出，检查 tools.py。');
  }
  if (!Object.keys(nodeStates).length) {
    levels.push({key:'workflow', label:'Workflow 状态缺失'});
    files.add('backend/app/workflow.py');
    reasons.push('没有看到 Workflow 节点状态；如果需要多步骤流程、DAG/循环状态，检查 workflow.py。');
  }
  if (!traceItems.length) {
    reasons.push('Trace 为空或很少；需要补充执行过程记录，方便定位问题。');
  }
  const uniqueLevels = [];
  const seen = new Set();
  levels.forEach(item => { if (!seen.has(item.key)) { seen.add(item.key); uniqueLevels.push(item); } });
  return {op, confidence, permission, files:Array.from(files), reasons, levels:uniqueLevels};
}
function buildAgentDebugFixTask(turnIndex) {
  const turn = agentDebugTurns[turnIndex];
  if (!turn) return '';
  const diagnosis = analyzeAgentDebugIssue(turn.data || {});
  return `请修复当前 Agent 调试中暴露的问题。\n\n【用户测试话术】\n${turn.message || ''}\n\n【Agent 当前回复】\n${turn.reply || ''}\n\n【APD 诊断】\n- 问题类型：${diagnosis.levels.map(item => item.label).join('、') || '回复质量问题'}\n- 命中操作：${diagnosis.op}\n- 校验状态：${diagnosis.permission}\n- 建议修改文件：${diagnosis.files.join('、')}\n\n【请你做】\n1. 先阅读上述建议文件，确认问题根因。\n2. 只做与本轮问题相关的最小修改。\n3. 如果是意图识别问题，优先改 planner.py；如果是回复不对，优先改 executor.py；如果缺工具，改 tools.py；如果缺节点状态，改 workflow.py。\n4. 修改后说明改了哪些文件，并告诉我应该用什么测试话术重新验证。`;
}
function appendAgentDebugSystemMessage(title, message) {
  const box = document.getElementById('agentDebugConversation');
  if (!box) return;
  const html = `<div class="debug-msg agent"><strong>${escapeHtml(title)}</strong><p>${escapeHtml(message)}</p></div>`;
  if (box.querySelector('.debug-chat-empty')) box.innerHTML = html;
  else box.insertAdjacentHTML('beforeend', html);
  box.scrollTop = box.scrollHeight;
}
async function sendAgentDebugFixToCli(turnIndex) {
  const task = buildAgentDebugFixTask(turnIndex);
  if (!task) { setStatus('没有可修复的调试轮次', 'warn'); return; }
  openInteractiveCli();
  appendAgentDebugSystemMessage('修复任务已生成', '正在把本轮诊断打包发送给右侧 open_claude/CLI。');
  if (!currentTtydSessionId) {
    await startTtydCli();
    appendAgentDebugSystemMessage('等待终端确认', '右侧终端已启动。如果出现安全确认，请先在右侧回车确认，然后再点一次“修复上一轮问题”。');
    setStatus('终端已启动，请确认后再发送修复任务', 'warn');
    return;
  }
  const sent = await sendPreparedCliTaskToTtyd(task);
  appendAgentDebugSystemMessage(sent ? '修复任务已发送' : '修复任务发送失败', sent ? '请看右侧 open_claude/CLI，它会根据诊断修改建议文件。' : '没有成功发送到右侧 CLI，请确认终端已启动并处于可输入状态。');
}
async function sendLatestAgentDebugFixToCli() {
  if (!agentDebugTurns.length) {
    setStatus('还没有调试轮次，请先发送一次测试话术', 'warn');
    openAgentDebug();
    return;
  }
  await sendAgentDebugFixToCli(agentDebugTurns.length - 1);
}
function buildAgentDebugAdvice(data, turnIndex) {
  const diagnosis = analyzeAgentDebugIssue(data || {});
  const fileHtml = diagnosis.files.map(file => `<span class="debug-file-chip">${escapeHtml(file)}</span>`).join('');
  const levelHtml = diagnosis.levels.length ? diagnosis.levels.map(item => `<span class="debug-level ${escapeHtml(item.key)}">${escapeHtml(item.label)}</span>`).join('') : '<span class="debug-level executor">回复质量问题</span>';
  return `<div class="debug-diagnosis">
    <h4>为什么会这样回复 / 应该改哪里</h4>
    <div class="debug-level-row">${levelHtml}</div>
    <ul>${diagnosis.reasons.map(item => `<li>${escapeHtml(item)}</li>`).join('')}</ul>
    <div class="debug-file-chips">${fileHtml}</div>
    <div class="debug-fix-actions"><button onclick="sendAgentDebugFixToCli(${Number(turnIndex)})" class="primary">让 AI 修这个问题</button></div>
  </div>`;
}
function renderAgentDebugConversation() {
  const box = document.getElementById('agentDebugConversation');
  if (!box) return;
  if (!agentDebugTurns.length) {
    box.innerHTML = '<div class="debug-chat-empty">这里会显示连续调试对话。输入测试话术后，Agent 会在同一个调试会话里持续运行。</div>';
    return;
  }
  box.innerHTML = agentDebugTurns.map((turn, index) => `<div class="debug-msg user"><strong>你 · 第 ${index + 1} 轮</strong><p>${escapeHtml(turn.message || '')}</p></div><div class="debug-msg agent"><strong>Agent 回复</strong><p>${escapeHtml(turn.reply || '')}</p>${buildAgentDebugAdvice(turn.data || {}, index)}</div>`).join('');
  box.scrollTop = box.scrollHeight;
}
function setAgentDebugLoading(message) {
  const result = document.getElementById('agentDebugResult');
  if (result) result.innerHTML = `<div class="agent-debug-shell"><div class="agent-debug-hero"><h3>正在运行调试...</h3><p>${escapeHtml(message || 'APD 正在把测试话术发送给当前 Agent，并收集状态流。')}</p></div><div class="agent-flow">
    <div class="agent-flow-node warn" data-step="1"><strong>会话</strong><span>保持运行</span></div>
    <div class="agent-flow-node warn" data-step="2"><strong>Agent Run</strong><span>进行中</span></div>
    <div class="agent-flow-node warn" data-step="3"><strong>Workflow</strong><span>等待</span></div>
    <div class="agent-flow-node warn" data-step="4"><strong>Trace</strong><span>等待</span></div>
  </div></div>`;
}
async function sendAgentDebugTurn(messageOverride) {
  openAgentDebug();
  const input = document.getElementById('agentDebugMessage');
  const message = (messageOverride || (input ? input.value : '') || getPmRequirementText() || '测试一句用户话术').trim();
  if (!message) { setStatus('请先输入测试话术', 'warn'); return; }
  if (input && !messageOverride) input.value = '';
  setAgentDebugLoading('正在发送：' + message);
  setStatus('正在连续调试 Agent...', 'warn');
  try {
    currentDevWorkspaceId = await resolveSelectedCliWorkspaceId();
    if (!currentDevWorkspaceId) throw new Error('没有可用工作区，请先打开或创建 Agent 工作区');
    const res = await fetch(`/api/dev-studio/workspace/${encodeURIComponent(currentDevWorkspaceId)}/debug/turn`, {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({message, context:{source:'agent_ide_debug', turn:agentDebugTurns.length + 1}, max_steps: 8})
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || '调试发送失败');
    agentDebugTurns.push({message, reply: extractAgentDebugReply(data), data});
    renderAgentDebugConversation();
    const result = document.getElementById('agentDebugResult');
    if (result) result.innerHTML = renderAgentDebugFlow(data);
    setStatus('Agent 连续调试完成一轮', 'ok');
  } catch (err) {
    const result = document.getElementById('agentDebugResult');
    if (result) result.innerHTML = `<div class="agent-debug-shell"><div class="agent-debug-hero"><h3>本轮调试失败</h3><p>${escapeHtml(err && err.message ? err.message : err)}</p></div></div>`;
    setStatus('Agent 连续调试失败', 'warn');
  }
}
async function runCliAgentDebug() {
  const input = document.getElementById('agentDebugMessage');
  if (input && !input.value.trim()) input.value = getPmRequirementText() || '测试一句用户话术';
  await sendAgentDebugTurn(input ? input.value : '测试一句用户话术');
}
async function restartAgentDebugSession() {
  try {
    currentDevWorkspaceId = await resolveSelectedCliWorkspaceId();
    if (!currentDevWorkspaceId) { setStatus('没有可重启的调试会话', 'warn'); return; }
    const result = document.getElementById('agentDebugResult');
    if (result) result.innerHTML = '<div class="agent-debug-shell"><div class="agent-debug-hero"><h3>正在重启当前 Agent...</h3><p>CLI 修改代码后，需要重启调试服务才能加载新代码。</p></div></div>';
    const res = await fetch(`/api/dev-studio/workspace/${encodeURIComponent(currentDevWorkspaceId)}/debug/restart`, {method:'POST'});
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || '重启失败');
    const port = ((data.debug_session || {}).port || '-');
    setStatus('当前 Agent 已重启，端口 ' + port, 'ok');
    if (result) result.innerHTML = `<div class="agent-debug-shell"><div class="agent-debug-hero"><h3>当前 Agent 已重启</h3><p>新代码已加载。现在可以继续发送测试话术验证。</p><div class="agent-debug-kpis"><div class="agent-debug-kpi"><strong>端口</strong><span>${escapeHtml(String(port))}</span></div><div class="agent-debug-kpi"><strong>状态</strong><span>running</span></div><div class="agent-debug-kpi"><strong>说明</strong><span>已刷新</span></div></div></div></div>`;
  } catch (err) {
    const result = document.getElementById('agentDebugResult');
    if (result) result.innerHTML = `<div class="agent-debug-shell"><div class="agent-debug-hero"><h3>重启失败</h3><p>${escapeHtml(err && err.message ? err.message : err)}</p></div></div>`;
    setStatus('重启当前 Agent 失败', 'warn');
  }
}

async function stopAgentDebugSession() {
  try {
    currentDevWorkspaceId = await resolveSelectedCliWorkspaceId();
    if (!currentDevWorkspaceId) { setStatus('没有可停止的调试会话', 'warn'); return; }
    await fetch(`/api/dev-studio/workspace/${encodeURIComponent(currentDevWorkspaceId)}/debug/stop`, {method:'POST'});
    setStatus('调试会话已停止', 'ok');
    const result = document.getElementById('agentDebugResult');
    if (result) result.innerHTML = '<div class="agent-debug-shell"><div class="agent-debug-hero"><h3>调试会话已停止</h3><p>下次发送测试话术时会重新启动 Agent 服务。</p></div></div>';
  } catch (err) {
    setStatus('停止调试会话失败', 'warn');
  }
}


async function sendPreparedCliTaskToTtyd(taskText) {
  const text = taskText || '';
  if (!text) return false;
  if (!currentTtydSessionId) {
    await startTtydCli();
    setStatus('终端已启动；如果右侧出现安全确认，请先回车确认，再点一次当前动作', 'warn');
    return false;
  }
  const statusEl = document.getElementById('ttydStatus');
  if (statusEl) statusEl.textContent = '正在把任务发送给右侧 AI 终端...';
  try {
    const res = await fetch(`/api/dev-studio/ttyd/${encodeURIComponent(currentTtydSessionId)}/send`, {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({text, raw:false, columns:120, rows:32})
    });
    const data = await res.json();
    if (!res.ok || !data.sent) throw new Error(data.error || '发送失败');
    if (statusEl) statusEl.innerHTML = `<strong>已发送给右侧 AI：</strong>${escapeHtml(String(data.sent_chars || text.length))} 字符`;
    setStatus('已发送给右侧 AI', 'ok');
    return true;
  } catch (err) {
    const msg = err && err.message ? err.message : String(err);
    if (statusEl) statusEl.textContent = '发送失败：' + msg;
    setStatus('发送失败，可复制备用', 'warn');
    return false;
  }
}
function getPmRequirementText() {
  const input = document.getElementById('cliCollabUserIntent');
  return input ? input.value.trim() : '';
}
function analyzePmRequirement() {
  const text = getPmRequirementText();
  if (!text) { setStatus('请先输入你的需求', 'warn'); return ''; }
  const kind = classifyCollabIntent(text);
  const mode = getCliTaskMode();
  const result = document.getElementById('cliCollabTaskResult');
  if (result) result.innerHTML = renderPmAnalysis(kind, text, mode);
  localStorage.setItem('apd_cli_collab_intent', text);
  localStorage.setItem('apd_cli_task_mode', mode);
  setStatus('需求已分析，建议先让 AI 看现有项目', 'ok');
  return kind;
}
async function sendPmAction(action) {
  const text = getPmRequirementText();
  if (!text && action !== 'summary') { setStatus('请先输入你的需求', 'warn'); return; }
  const kind = classifyCollabIntent(text || '总结验收');
  const mode = getCliTaskMode();
  currentCliCollabTask = buildPmActionTask(action, text, kind, mode);
  const result = document.getElementById('cliCollabTaskResult');
  if (result) result.innerHTML = renderCliCollabTaskView(kind, text || '总结当前结果', currentCliCollabTask, mode);
  localStorage.setItem('apd_cli_collab_intent', text);
  localStorage.setItem('apd_cli_task_mode', mode);
  return await sendPreparedCliTaskToTtyd(currentCliCollabTask);
}
function getPmGuideStage() {
  return localStorage.getItem('apd_pm_guide_stage') || 'new';
}
function setPmGuideStage(stage) {
  localStorage.setItem('apd_pm_guide_stage', stage || 'new');
}
function markPmRequirementChanged() {
  setPmGuideStage('new');
  renderPmFlowGuide('new');
}
function resetPmGuideFlow() {
  setPmGuideStage('new');
  renderPmFlowGuide('new');
  const result = document.getElementById('cliCollabTaskResult');
  if (result) result.innerHTML = '<div class="cli-result-card"><h3>已重新开始</h3><p>输入或确认当前需求后，点“帮我推进下一步”。</p></div>';
  setStatus('流程已重置，从分析需求开始', 'ok');
}
function pmFlowIndex(stage) {
  const order = ['new', 'inspected', 'planned', 'built', 'restarted', 'debugged', 'summarized'];
  const index = order.indexOf(stage || 'new');
  return index < 0 ? 0 : index;
}
function renderPmFlowGuide(stage, customHint) {
  const steps = [
    {key:'new', title:'1 需求', text:'说清想改什么'},
    {key:'inspected', title:'2 AI 看项目', text:'读结构找文件'},
    {key:'planned', title:'3 AI 开发', text:'出方案并改代码'},
    {key:'restarted', title:'4 重启', text:'加载新代码'},
    {key:'debugged', title:'5 调试修复', text:'测试并一键修'}
  ];
  const current = pmFlowIndex(stage);
  const strip = document.getElementById('apdFlowStrip');
  if (strip) strip.innerHTML = steps.map(item => {
    const idx = pmFlowIndex(item.key);
    const cls = idx < current ? 'done' : (idx === current || (stage === 'built' && item.key === 'restarted') ? 'active' : '');
    return `<div class="apd-flow-step ${cls}"><strong>${escapeHtml(item.title)}</strong>${escapeHtml(item.text)}</div>`;
  }).join('');
  const summary = document.getElementById('apdFlowSummary');
  if (summary) {
    const summaryKey = stage === 'built' ? 'restarted' : (stage || 'new');
    const currentStep = steps.find(item => item.key === summaryKey) || steps[0];
    summary.textContent = `当前：${currentStep.title} · ${currentStep.text}`;
  }
  const hintMap = {
    new:'输入一句业务需求，然后点“帮我推进下一步”。',
    inspected:'等右侧 AI 看完项目后，再点“帮我推进下一步”，让它出实现方案。',
    planned:'确认方案方向可以后，再点“帮我推进下一步”，让 AI 开始改代码。',
    built:'AI 改完文件后，先点“打开调试页”，再点调试页里的“重启当前 Agent”。',
    restarted:'新代码已加载，去调试页发送测试话术，看 Agent 真实回复。',
    debugged:'如果回复不对，在调试页点“让 AI 修这个问题”，修完后再次重启验证。',
    summarized:'本轮闭环完成。有新需求就改输入框继续增量开发。'
  };
  const hint = document.getElementById('apdNextHint');
  if (hint) hint.innerHTML = `<strong>下一步：</strong>${escapeHtml(customHint || hintMap[stage] || hintMap.new)}`;
}
function renderPmGuideProgress(stage, message) {
  const names = {new:'准备分析', inspected:'已让 AI 看项目', planned:'已让 AI 出方案', built:'已让 AI 开发', summarized:'已总结'};
  return `<div class="cli-result-card">
    <h3>下一步引导：${escapeHtml(names[stage] || '继续推进')}</h3>
    <p>${escapeHtml(message || '')}</p>
    <p><strong>使用方式：</strong>等右侧 AI 回答完，再点一次“帮我推进下一步”。</p>
  </div>`;
}
async function pmGuideNext() {
  const text = getPmRequirementText();
  const result = document.getElementById('cliCollabTaskResult');
  if (!text) {
    if (result) result.innerHTML = '<div class="cli-result-card"><h3>先写一句需求</h3><p>例如：我希望这个 Agent 支持上传业务文件，抽取关键信息，并按规则生成可编辑结果。</p></div>';
    const input = document.getElementById('cliCollabUserIntent');
    if (input) input.focus();
    setStatus('请先输入一句业务需求', 'warn');
    return;
  }
  const stage = getPmGuideStage();
  if (stage === 'new') {
    analyzePmRequirement();
    const sent = await sendPmAction('inspect');
    if (sent) {
      setPmGuideStage('inspected');
      renderPmFlowGuide('inspected');
      if (result) result.innerHTML += renderPmGuideProgress('inspected', '我已经让右侧 AI 先阅读现有项目。等它回答完，再点一次主按钮，我会让它出实现方案。');
    }
    return;
  }
  if (stage === 'inspected') {
    const sent = await sendPmAction('plan');
    if (sent) {
      setPmGuideStage('planned');
      renderPmFlowGuide('planned');
      if (result) result.innerHTML += renderPmGuideProgress('planned', '我已经让右侧 AI 输出实现方案。你看方案可以后，再点一次主按钮开始开发。');
    }
    return;
  }
  if (stage === 'planned') {
    const sent = await sendPmAction('build');
    if (sent) {
      setPmGuideStage('built');
      renderPmFlowGuide('built');
      if (result) result.innerHTML += renderPmGuideProgress('built', '我已经让右侧 AI 开始开发。等它改完和运行完，再点一次主按钮做总结验收。');
    }
    return;
  }
  if (stage === 'built') {
    const sent = await sendPmAction('summary');
    if (sent) {
      setPmGuideStage('summarized');
      renderPmFlowGuide('summarized');
      if (result) result.innerHTML += renderPmGuideProgress('summarized', '已进入总结验收阶段。如果你有新需求，直接改输入框，流程会自动重新开始。');
    }
    return;
  }
  analyzePmRequirement();
  setPmGuideStage('new');
}
function renderCliCollabTaskView(kind, text, task, mode) {
  const modeLabel = cliTaskModeLabel(mode);
  return `<div class="cli-result-card">
    <h3>翻译结果：${escapeHtml(modeLabel)} / ${escapeHtml(kind)}</h3>
    <p><strong>你的需求：</strong>${escapeHtml(text)}</p>
    <p><strong>发送提示：</strong>确认右侧终端已在输入位置，然后点“发送到 ttyd”。</p>
    <details class="preview-dev-details" open><summary>将发送给 open_claude 的内容</summary><pre id="cliCollabTaskText">${escapeHtml(task)}</pre></details>
  </div>`;
}
function loadCliCollabDraft() {
  renderPmFlowGuide(getPmGuideStage());
  const input = document.getElementById('cliCollabUserIntent');
  if (input) input.value = localStorage.getItem('apd_cli_collab_intent') || localStorage.getItem('apd_collab_intent') || '';
  const mode = document.getElementById('cliTaskMode');
  if (mode) mode.value = localStorage.getItem('apd_cli_task_mode') || 'concise';
}
function buildCliCollabTask() {
  const input = document.getElementById('cliCollabUserIntent');
  const result = document.getElementById('cliCollabTaskResult');
  const text = input ? input.value.trim() : '';
  if (!text) { setStatus('请先描述要让 open_claude 做什么', 'warn'); return ''; }
  const kind = classifyCollabIntent(text);
  const mode = getCliTaskMode();
  currentCliCollabTask = buildCliTaskByMode(mode, kind, text);
  if (result) result.innerHTML = renderCliCollabTaskView(kind, text, currentCliCollabTask, mode);
  if (typeof developerRequest !== 'undefined') developerRequest.value = text;
  if (typeof collabUserIntent !== 'undefined') collabUserIntent.value = text;
  localStorage.setItem('apd_cli_collab_intent', text);
  localStorage.setItem('apd_collab_intent', text);
  localStorage.setItem('apd_developer_request', text);
  localStorage.setItem('apd_cli_task_mode', mode);
  setStatus('已生成' + cliTaskModeLabel(mode) + '，可发送到 ttyd', 'ok');
  return currentCliCollabTask;
}

async function sendCliCollabTaskToTtyd() {
  const text = buildCliCollabTask();
  if (!text) return;
  await sendPreparedCliTaskToTtyd(text);
}

async function copyCliCollabTask() {
  const text = buildCliCollabTask();
  if (!text) return;
  await navigator.clipboard.writeText(text);
  setStatus('任务已复制：请在下方 ttyd 终端 Ctrl+V 粘贴', 'ok');
}
function fillCliInitialPromptFromCollab() {
  const text = buildCliCollabTask();
  if (!text) return;
  const target = document.getElementById('cliInitialPrompt');
  if (target) target.value = text;
  saveInteractiveCliSettings();
  setStatus('已填到启动提示词；重启 ttyd 后会作为初始任务', 'ok');
}
async function sendCliCollabTaskToXterm() {
  const text = buildCliCollabTask();
  if (!text) return;
  if (!currentCliSessionId) {
    setStatus('备用 xterm 未启动；ttyd 请用复制后 Ctrl+V', 'warn');
    return;
  }
  await writeCliOutput('\r\n> ' + text + '\r\n');
  await sendInteractiveCliData(text.endsWith('\n') ? text : text + '\n');
  setStatus('已发送到备用 xterm CLI', 'ok');
}
function openCliCollabAssistant() {
  localStorage.setItem('apd_cli_fullscreen', '1');
  openInteractiveCli();
  setTimeout(() => {
    applyCliFullscreenPreference();
    const panel = document.getElementById('cliCollabAssistantPanel');
    if (panel) panel.scrollIntoView({behavior:'smooth', block:'start'});
    loadCliCollabDraft();
    const input = document.getElementById('cliCollabUserIntent');
    if (input) input.focus();
    renderPmFlowGuide(getPmGuideStage());
    setStatus('已打开全屏工程开发台：左边写需求，右边看 open_claude 执行', 'ok');
  }, 160);
}

let currentTtydSessionId = localStorage.getItem('apd_ttyd_session_id') || '';
let cliPollTimer = null;
let cliTerm = null;
let cliFitAddon = null;
let cliInputBuffer = '';
let cliSending = false;
window.__apdXtermErrors = window.__apdXtermErrors || [];
window.addEventListener('error', event => {
  const src = (event.filename || '').toString();
  if (src.includes('/static/vendor/xterm')) {
    window.__apdXtermErrors.push({message:event.message || '', filename:src, lineno:event.lineno || 0, colno:event.colno || 0});
  }
});
function loadScriptOnce(src) {
  return new Promise((resolve, reject) => {
    const script = document.createElement('script');
    script.src = src;
    script.async = false;
    script.onload = () => { script.dataset.loaded = '1'; resolve(); };
    script.onerror = () => reject(new Error('脚本加载失败：' + src));
    document.head.appendChild(script);
  });
}
function bridgeXtermGlobals() {
  if (!window.Terminal && window.module && window.module.exports && window.module.exports.Terminal) window.Terminal = window.module.exports.Terminal;
  if (!window.Terminal && window.exports && window.exports.Terminal) window.Terminal = window.exports.Terminal;
  if (!window.FitAddon && window.module && window.module.exports && window.module.exports.FitAddon) window.FitAddon = window.module.exports;
  if (!window.FitAddon && window.exports && window.exports.FitAddon) window.FitAddon = window.exports;
}
async function ensureXtermLoaded() {
  bridgeXtermGlobals();
  if (window.Terminal || (window.Xterm && window.Xterm.Terminal)) return;
  const previousModule = window.module;
  const previousExports = window.exports;
  try {
    window.module = undefined;
    window.exports = undefined;
    await loadScriptOnce('/static/vendor/xterm4/xterm.js?v=20260610c&retry=' + Date.now());
  } finally {
    if (typeof previousModule === 'undefined') delete window.module; else window.module = previousModule;
    if (typeof previousExports === 'undefined') delete window.exports; else window.exports = previousExports;
  }
  bridgeXtermGlobals();
  if (!window.FitAddon) {
    try { await loadScriptOnce('/static/vendor/xterm4-addon-fit/addon-fit.js?v=20260610c&retry=' + Date.now()); } catch (err) {}
  }
  bridgeXtermGlobals();
}
function resolveTerminalClass() {
  if (window.Terminal && window.Terminal.Terminal) return window.Terminal.Terminal;
  if (typeof window.Terminal === 'function') return window.Terminal;
  if (window.Xterm && window.Xterm.Terminal) return window.Xterm.Terminal;
  return null;
}
function resolveFitAddonClass() {
  if (window.FitAddon && typeof window.FitAddon.FitAddon === 'function') return window.FitAddon.FitAddon;
  if (typeof window.FitAddon === 'function') return window.FitAddon;
  if (window.FitAddon && window.FitAddon.default && typeof window.FitAddon.default.FitAddon === 'function') return window.FitAddon.default.FitAddon;
  if (window.AttachAddon && typeof window.AttachAddon.FitAddon === 'function') return window.AttachAddon.FitAddon;
  return null;
}
async function writeCliOutput(value) {
  await ensureCliTerminal();
  const text = String(value || '');
  if (!cliTerm) {
    const el = document.getElementById('cliTerminal');
    if (el) el.textContent += text;
    return;
  }
  if (typeof cliTerm.write === 'function') cliTerm.write(text);
  else {
    const el = document.getElementById('cliTerminal');
    if (el) el.textContent += text;
  }
}
async function clearCliOutput(value) {
  await ensureCliTerminal();
  if (cliTerm && typeof cliTerm.clear === 'function') cliTerm.clear();
  else {
    const el = document.getElementById('cliTerminal');
    if (el) el.textContent = '';
  }
  if (value) await writeCliOutput(value);
}
function createFallbackTerminal(el, reason) {
  el.dataset.fallback = '1';
  el.style.overflow = 'auto';
  el.style.whiteSpace = 'pre-wrap';
  el.textContent = '浏览器终端组件暂不可用，已切换到普通文本兜底模式。\n' + reason + '\n\n';
  const fallback = document.getElementById('cliFallbackInput');
  if (fallback) fallback.style.display = 'block';
  cliTerm = {
    write(value) { el.textContent += String(value || ''); el.scrollTop = el.scrollHeight; },
    writeln(value) { el.textContent += String(value || '') + '\n'; el.scrollTop = el.scrollHeight; },
    clear() { el.textContent = ''; },
    focus() {}
  };
  return cliTerm;
}
async function ensureCliTerminal() {
  const el = document.getElementById('cliTerminal');
  if (!el) return null;
  if (cliTerm) return cliTerm;
  try {
    await ensureXtermLoaded();
  } catch (err) {
    return createFallbackTerminal(el, '加载 xterm 脚本失败：' + (err && err.message ? err.message : err));
  }
  const TerminalClass = resolveTerminalClass();
  if (!TerminalClass) {
    return createFallbackTerminal(el, '诊断：window.Terminal=' + typeof window.Terminal + '，module.exports.Terminal=' + !!(window.module && window.module.exports && window.module.exports.Terminal) + '，exports.Terminal=' + !!(window.exports && window.exports.Terminal) + '，errors=' + JSON.stringify(window.__apdXtermErrors || []));
  }
  try {
    cliTerm = new TerminalClass({
      cursorBlink: true,
      convertEol: true,
      fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
      fontSize: 13,
      lineHeight: 1.25,
      scrollback: 5000,
      theme: {background: '#020617', foreground: '#d1d5db', cursor: '#f8fafc', selectionBackground: '#334155'},
    });
    const FitAddonClass = resolveFitAddonClass();
    if (FitAddonClass) {
      try {
        cliFitAddon = new FitAddonClass();
        cliTerm.loadAddon(cliFitAddon);
      } catch (fitErr) {
        cliFitAddon = null;
        window.__apdXtermErrors.push({message:'FitAddon 初始化失败：' + (fitErr && fitErr.message ? fitErr.message : fitErr), filename:'xterm-fit'});
      }
    }
    cliTerm.open(el);
    setTimeout(fitCliTerminal, 0);
    setTimeout(fitCliTerminal, 120);
    cliTerm.writeln('CLI 终端已准备好。打开后会自动启动最近一次工作区。');
    cliTerm.writeln('提示：如果没有自动启动，可再点“启动 CLI”手动重试。');
    cliTerm.onData(data => {
      if (!currentCliSessionId) return;
      sendInteractiveCliData(data);
    });
    window.addEventListener('resize', fitCliTerminal);
    if (window.ResizeObserver) {
      const ro = new ResizeObserver(() => fitCliTerminal());
      ro.observe(el);
    }
    return cliTerm;
  } catch (err) {
    cliTerm = null;
    return createFallbackTerminal(el, '初始化 xterm 失败：' + (err && err.message ? err.message : err));
  }
}
function fitCliTerminal() {
  try { if (cliFitAddon && typeof cliFitAddon.fit === 'function') cliFitAddon.fit(); } catch (err) {}
}
function renderCliWorkspacePill(workspace) {
  const selected = workspace.workspace_id === currentDevWorkspaceId;
  const name = workspace.name || workspace.project_name || workspace.workspace_id;
  const files = String(((workspace.summary || {}).file_count) || 0);
  return `<button type="button" data-workspace-id="${escapeHtml(workspace.workspace_id || '')}" class="cli-workspace-pill ${selected ? 'active' : ''}" onclick="selectCliWorkspace('${workspace.workspace_id}')">${selected ? '✅ ' : ''}${escapeHtml(name)} · ${escapeHtml(files)} files</button>`;
}
async function refreshCliWorkspaces() {
  const list = document.getElementById('cliWorkspaceList');
  if (!list) return;
  list.innerHTML = '正在加载最近工作区...';
  try {
    const res = await fetch('/api/dev-studio/workspaces');
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || '加载失败');
    const workspaces = data.workspaces || [];
    const workspaceIds = workspaces.map(item => item.workspace_id).filter(Boolean);
    if (workspaces.length && (!currentDevWorkspaceId || !workspaceIds.includes(currentDevWorkspaceId))) {
      currentDevWorkspaceId = workspaces[0].workspace_id || '';
      localStorage.setItem('apd_dev_workspace_id', currentDevWorkspaceId);
    } else if (!workspaces.length) {
      currentDevWorkspaceId = '';
      localStorage.removeItem('apd_dev_workspace_id');
    }
    list.innerHTML = workspaces.length ? workspaces.map(renderCliWorkspacePill).join('') : '<span class="pill">暂无工作区。点“启动 CLI”会自动基于当前会话创建一个。</span>';
    updateCliWorkspaceHint();
  } catch (err) {
    list.innerHTML = '<span class="pill">加载失败：' + escapeHtml(err && err.message ? err.message : err) + '</span>';
  }
}
async function selectCliWorkspace(workspaceId) {
  currentDevWorkspaceId = workspaceId;
  localStorage.setItem('apd_dev_workspace_id', workspaceId);
  updateCliWorkspaceHint();
  await refreshCliWorkspaces();
  setStatus('已选择 CLI 工作区：' + workspaceId, 'ok');
}
async function createCliWorkspace() {
  if (!sessionId) { setStatus('请先选择或创建会话', 'warn'); await refreshCliWorkspaces(); return; }
  const input = document.getElementById('cliWorkspaceName');
  const name = input ? input.value.trim() : '';
  localStorage.setItem('apd_cli_workspace_name', name);
  setStatus('正在创建 CLI 工作区...', 'warn');
  const list = document.getElementById('cliWorkspaceList');
  if (list) list.innerHTML = '正在创建工作区：生成项目、保存 v1 快照...';
  try {
    const res = await fetch('/api/dev-studio/workspace', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({session_id: sessionId, name})
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || '创建失败');
    currentDevWorkspaceId = data.workspace_id;
    localStorage.setItem('apd_dev_workspace_id', currentDevWorkspaceId);
    updateCliWorkspaceHint();
    await refreshCliWorkspaces();
    setStatus('CLI 工作区已创建并选中', 'ok');
  } catch (err) {
    if (list) list.innerHTML = '<span class="pill">创建失败：' + escapeHtml(err && err.message ? err.message : err) + '</span>';
    setStatus('CLI 工作区创建失败', 'warn');
  }
}
function applyCliFullscreenPreference() {
  const drawer = document.querySelector('#interactiveCliMask .cli-drawer');
  const btn = document.getElementById('cliFullscreenBtn');
  const enabled = localStorage.getItem('apd_cli_fullscreen') === '1';
  if (drawer) drawer.classList.toggle('fullscreen', enabled);
  if (btn) btn.textContent = enabled ? '退出全屏' : '全屏';
  setTimeout(fitCliTerminal, 80);
}
function toggleCliFullscreen() {
  const enabled = localStorage.getItem('apd_cli_fullscreen') === '1';
  localStorage.setItem('apd_cli_fullscreen', enabled ? '0' : '1');
  applyCliFullscreenPreference();
}
function openInteractiveCli() {
  document.getElementById('interactiveCliMask').classList.add('open');
  bindInteractiveCliButtons();
  applyCliFullscreenPreference();
  currentDevWorkspaceId = '';
  loadInteractiveCliSettings();
  loadCliCollabDraft();
  bindInteractiveCliButtons();
  const cliNameInput = document.getElementById('cliWorkspaceName');
  if (cliNameInput) cliNameInput.value = localStorage.getItem('apd_cli_workspace_name') || localStorage.getItem('apd_dev_workspace_name') || '';
  updateCliWorkspaceHint();
  refreshCliWorkspaces();
  setTimeout(async () => {
    try {
      await startTtydCli();
    } catch (err) {
      const statusEl = document.getElementById('ttydStatus');
      if (statusEl) statusEl.textContent = 'ttyd 自动启动失败：' + (err && err.message ? err.message : err);
      setStatus('ttyd 自动启动失败，可尝试备用 xterm', 'warn');
    }
  }, 120);
  setStatus('交互式 CLI 已打开：正在自动启动 ttyd 终端...', 'warn');
}
function closeInteractiveCli(event) {
  if (event && event.target !== document.getElementById('interactiveCliMask')) return;
  document.getElementById('interactiveCliMask').classList.remove('open');
}
function updateCliWorkspaceHint() {
  const hint = document.getElementById('cliWorkspaceHint');
  if (!hint) return;
  if (currentDevWorkspaceId) {
    hint.innerHTML = `<strong>默认工作区：</strong>${escapeHtml(currentDevWorkspaceId)}。一般不用手动选择，直接点“启动 CLI”；如果想换项目，再点下面其他工作区。`;
  } else {
    hint.innerHTML = '<strong>默认工作区：</strong>正在自动寻找最近一次工作区。没有工作区时，点“启动 CLI”会自动基于当前会话创建一个。';
  }
}
function cliEl(id) {
  return document.getElementById(id);
}
function cliFormValue(id) {
  const el = cliEl(id);
  return el && typeof el.value === 'string' ? el.value : '';
}
function setCliFormValue(id, value) {
  const el = cliEl(id);
  if (el) el.value = value || '';
}
function loadInteractiveCliSettings() {
  setCliFormValue('cliRunner', localStorage.getItem('apd_cli_runner') || localStorage.getItem('apd_developer_runner') || 'open_claude');
  setCliFormValue('cliBaseUrl', localStorage.getItem('apd_cli_base_url') || localStorage.getItem('apd_developer_base_url') || '');
  setCliFormValue('cliApiKey', localStorage.getItem('apd_cli_api_key') || localStorage.getItem('apd_developer_api_key') || '');
  setCliFormValue('cliModel', localStorage.getItem('apd_cli_model') || localStorage.getItem('apd_developer_model') || '');
  setCliFormValue('cliRoot', localStorage.getItem('apd_cli_root') || localStorage.getItem('apd_developer_cli_root') || '/home/data/rag/open_claude/Openclaude-openclaude');
  setCliFormValue('cliWorkDir', localStorage.getItem('apd_cli_work_dir') || localStorage.getItem('apd_developer_work_dir') || '');
  setCliFormValue('cliInitialPrompt', localStorage.getItem('apd_cli_initial_prompt') || '');
  setCliFormValue('cliCommandTemplate', localStorage.getItem('apd_cli_command_template') || localStorage.getItem('apd_developer_command_template') || '');
  // 不自动恢复旧 CLI 会话，避免 TUI 状态错乱；需要时重新点击“启动 CLI”。
}
function saveInteractiveCliSettings() {
  localStorage.setItem('apd_cli_runner', cliFormValue('cliRunner') || 'open_claude');
  localStorage.setItem('apd_cli_base_url', cliFormValue('cliBaseUrl').trim());
  localStorage.setItem('apd_cli_api_key', cliFormValue('cliApiKey').trim());
  localStorage.setItem('apd_cli_model', cliFormValue('cliModel').trim());
  localStorage.setItem('apd_cli_root', cliFormValue('cliRoot').trim());
  localStorage.setItem('apd_cli_work_dir', cliFormValue('cliWorkDir').trim());
  localStorage.setItem('apd_cli_initial_prompt', cliFormValue('cliInitialPrompt').trim());
  localStorage.setItem('apd_cli_command_template', cliFormValue('cliCommandTemplate').trim());
  setStatus('交互式 CLI 配置已保存', 'ok');
}
function fillInteractiveCliDefaults() {
  setCliFormValue('cliRunner', 'open_claude');
  setCliFormValue('cliRoot', '/home/data/rag/open_claude/Openclaude-openclaude');
  if (!cliFormValue('cliInitialPrompt').trim()) {
    setCliFormValue('cliInitialPrompt', '请先阅读当前项目结构，告诉我这个 Agent 工程的主要文件分别负责什么。先不要修改代码。');
  }
  saveInteractiveCliSettings();
  setStatus('已填入 open_claude 交互式默认配置', 'ok');
}
function getSelectedCliWorkspaceId() {
  const active = document.querySelector('#cliWorkspaceList .cli-workspace-pill.active');
  if (active && active.dataset.workspaceId) return active.dataset.workspaceId;
  if (currentDevWorkspaceId) return currentDevWorkspaceId;
  const stored = localStorage.getItem('apd_dev_workspace_id') || '';
  if (stored) return stored;
  const first = document.querySelector('#cliWorkspaceList .cli-workspace-pill[data-workspace-id]');
  if (first && first.dataset.workspaceId) return first.dataset.workspaceId;
  return '';
}
async function resolveSelectedCliWorkspaceId() {
  let workspaceId = '';
  const active = document.querySelector('#cliWorkspaceList .cli-workspace-pill.active');
  if (active && active.dataset.workspaceId) workspaceId = active.dataset.workspaceId;
  try {
    const res = await fetch('/api/dev-studio/workspaces');
    const data = await res.json();
    if (res.ok) {
      const workspaces = data.workspaces || [];
      const workspaceIds = workspaces.map(item => item.workspace_id).filter(Boolean);
      if (!workspaceId || !workspaceIds.includes(workspaceId)) {
        workspaceId = workspaces.length ? (workspaces[0].workspace_id || '') : '';
      }
      if (workspaceId) {
        currentDevWorkspaceId = workspaceId;
        localStorage.setItem('apd_dev_workspace_id', workspaceId);
        await refreshCliWorkspaces();
        return workspaceId;
      }
    }
  } catch (err) {}
  if (!sessionId) return '';
  try {
    setStatus('没有找到已有工作区，正在自动创建...', 'warn');
    const createRes = await fetch('/api/dev-studio/workspace', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({session_id: sessionId, name: localStorage.getItem('apd_cli_workspace_name') || ''})
    });
    const created = await createRes.json();
    if (!createRes.ok) return '';
    workspaceId = created.workspace_id || '';
    if (workspaceId) {
      currentDevWorkspaceId = workspaceId;
      localStorage.setItem('apd_dev_workspace_id', workspaceId);
      await refreshCliWorkspaces();
    }
    return workspaceId;
  } catch (err) {
    return '';
  }
}
async function startTtydCli() {
  const statusEl = document.getElementById('ttydStatus');
  const frame = document.getElementById('ttydFrame');
  if (statusEl) statusEl.textContent = '正在启动 ttyd 终端，自动使用最近一次工作区...';
  setStatus('ttyd 终端启动中...', 'warn');
  currentDevWorkspaceId = await resolveSelectedCliWorkspaceId();
  if (!currentDevWorkspaceId) {
    if (statusEl) statusEl.textContent = '没有可用工作区，无法启动 ttyd。请先新建/打开 APD 会话。';
    setStatus('没有可用工作区，无法启动 ttyd', 'warn');
    return;
  }
  localStorage.setItem('apd_dev_workspace_id', currentDevWorkspaceId);
  saveInteractiveCliSettings();
  const res = await fetch(`/api/dev-studio/workspace/${encodeURIComponent(currentDevWorkspaceId)}/ttyd/start`, {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({
      runner: cliFormValue('cliRunner') || 'open_claude',
      base_url: cliFormValue('cliBaseUrl').trim(),
      api_key: cliFormValue('cliApiKey').trim(),
      model: cliFormValue('cliModel').trim(),
      cli_root: cliFormValue('cliRoot').trim(),
      work_dir: cliFormValue('cliWorkDir').trim(),
      command_template: cliFormValue('cliCommandTemplate').trim(),
      initial_prompt: cliFormValue('cliInitialPrompt').trim(),
    })
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || 'ttyd 启动失败');
  currentTtydSessionId = data.session_id || '';
  localStorage.setItem('apd_ttyd_session_id', currentTtydSessionId);
  if (frame) {
    frame.src = data.url;
    frame.style.display = 'block';
  }
  if (statusEl) statusEl.innerHTML = `<strong>ttyd 已启动：</strong><a href="${escapeHtml(data.url)}" target="_blank">${escapeHtml(data.url)}</a> · 工作区 ${escapeHtml(currentDevWorkspaceId)}`;
  setStatus('ttyd 终端已启动', 'ok');
  renderLandingGuide();
}
async function stopTtydCli() {
  const statusEl = document.getElementById('ttydStatus');
  const frame = document.getElementById('ttydFrame');
  if (!currentTtydSessionId) {
    if (frame) { frame.src = ''; frame.style.display = 'none'; }
    if (statusEl) statusEl.textContent = 'ttyd 终端未启动。';
    return;
  }
  try {
    await fetch(`/api/dev-studio/ttyd/${encodeURIComponent(currentTtydSessionId)}/stop`, {method:'POST'});
  } catch (err) {}
  currentTtydSessionId = '';
  localStorage.removeItem('apd_ttyd_session_id');
  if (frame) { frame.src = ''; frame.style.display = 'none'; }
  if (statusEl) statusEl.textContent = 'ttyd 终端已停止。';
  setStatus('ttyd 终端已停止', 'ok');
  renderLandingGuide();
}
async function cleanupTtydCli(allApd) {
  const statusEl = document.getElementById('ttydStatus');
  const frame = document.getElementById('ttydFrame');
  const workspaceId = allApd ? '' : (currentDevWorkspaceId || localStorage.getItem('apd_dev_workspace_id') || '');
  if (!allApd && !workspaceId) {
    setStatus('没有当前工作区，无法按工作区清理', 'warn');
    return;
  }
  if (statusEl) statusEl.textContent = allApd ? '正在清理全部 APD 旧终端...' : '正在清理当前工作区旧终端...';
  setStatus(allApd ? '正在清理全部 APD 旧终端...' : '正在清理当前工作区旧终端...', 'warn');
  try {
    const res = await fetch('/api/dev-studio/ttyd/cleanup', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({workspace_id: workspaceId, all_apd: !!allApd})
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || '清理失败');
    currentTtydSessionId = '';
    localStorage.removeItem('apd_ttyd_session_id');
    if (frame) { frame.src = ''; frame.style.display = 'none'; }
    const ttydCount = (data.killed_ttyd_pids || []).length;
    const tmuxCount = (data.killed_tmux_sessions || []).length;
    const msg = `清理完成：ttyd ${ttydCount} 个，tmux ${tmuxCount} 个`;
    if (statusEl) statusEl.textContent = msg + (data.errors && data.errors.length ? '；部分失败请看服务日志。' : '。');
    setStatus(msg, 'ok');
    renderLandingGuide();
  } catch (err) {
    const msg = '清理旧终端失败：' + (err && err.message ? err.message : err);
    if (statusEl) statusEl.textContent = msg;
    setStatus(msg, 'warn');
  }
}
let cliStarting = false;
async function startInteractiveCli(event) {
  if (event && event.preventDefault) event.preventDefault();
  if (event && event.stopPropagation) event.stopPropagation();
  if (cliStarting) {
    await ensureCliTerminal();
    await writeCliOutput('上一次启动还在处理中，请稍等或刷新页面后重试。\r\n');
    return;
  }
  cliStarting = true;
  if (cliPollTimer) { clearInterval(cliPollTimer); cliPollTimer = null; }
  currentCliSessionId = '';
  localStorage.removeItem('apd_cli_session_id');
  setStatus('已点击启动 CLI，正在准备...', 'warn');
  await ensureCliTerminal();
  await clearCliOutput('已点击启动 CLI，正在寻找默认工作区...\r\n');
  currentDevWorkspaceId = await resolveSelectedCliWorkspaceId();
  if (currentDevWorkspaceId) localStorage.setItem('apd_dev_workspace_id', currentDevWorkspaceId);
  if (!currentDevWorkspaceId) {
    updateCliWorkspaceHint();
    setStatus('没有可用工作区，且当前没有可用于自动创建的会话', 'warn');
    await ensureCliTerminal();
    await clearCliOutput('还没有可用工作区，无法启动 CLI。\r\n如果这是第一次使用，请先新建/打开一个 APD 会话；之后再点“启动 CLI”，系统会自动创建或选择最近工作区。\r\n');
    cliStarting = false;
    return;
  }
  updateCliWorkspaceHint();
  saveInteractiveCliSettings();
  await ensureCliTerminal();
  await clearCliOutput('正在启动交互式 CLI...\r\n默认工作区：' + currentDevWorkspaceId + '\r\n这一步会在服务器上启动真实命令行进程。\r\n\r\n');
  setStatus('交互式 CLI 启动中...', 'warn');
  try {
    const res = await fetch(`/api/dev-studio/workspace/${encodeURIComponent(currentDevWorkspaceId)}/cli/start`, {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({
        runner: cliFormValue('cliRunner') || 'open_claude',
        base_url: cliFormValue('cliBaseUrl').trim(),
        api_key: cliFormValue('cliApiKey').trim(),
        model: cliFormValue('cliModel').trim(),
        cli_root: cliFormValue('cliRoot').trim(),
        work_dir: cliFormValue('cliWorkDir').trim(),
        command_template: cliFormValue('cliCommandTemplate').trim(),
        initial_prompt: cliFormValue('cliInitialPrompt').trim(),
      })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'CLI 启动失败');
    currentCliSessionId = data.session_id;
    localStorage.setItem('apd_cli_session_id', currentCliSessionId);
    await clearCliOutput(`[${data.runner}] ${data.status} pid=${data.pid}\r\n${data.command_preview || ''}\r\n\r\n`);
    await writeCliOutput(data.output || '');
    startCliPolling();
    if (cliTerm) cliTerm.focus();
    setStatus('交互式 CLI 已启动', 'ok');
  } catch (err) {
    await clearCliOutput('CLI 启动失败：' + (err && err.message ? err.message : err));
    setStatus('交互式 CLI 启动失败', 'warn');
  } finally {
    cliStarting = false;
  }
}
function bindInteractiveCliButtons() {
  document.querySelectorAll('[data-cli-action="start"]').forEach(btn => {
    if (btn.dataset.boundStartCli === '1') return;
    btn.dataset.boundStartCli = '1';
    btn.addEventListener('click', event => {
      event.preventDefault();
      event.stopPropagation();
      startInteractiveCli(event);
    });
  });
}
document.addEventListener('click', event => {
  const btn = event.target && event.target.closest ? event.target.closest('[data-cli-action="start"]') : null;
  if (!btn) return;
  event.preventDefault();
  event.stopPropagation();
  startInteractiveCli(event);
}, true);
function startCliPolling() {
  if (cliPollTimer) clearInterval(cliPollTimer);
  cliPollTimer = setInterval(readInteractiveCli, 300);
}
async function readInteractiveCli() {
  if (!currentCliSessionId) return;
  try {
    const res = await fetch(`/api/dev-studio/cli/${encodeURIComponent(currentCliSessionId)}/read`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || '读取失败');
    await writeCliOutput(data.output || '');
    if (data.status !== 'running' && cliPollTimer) {
      clearInterval(cliPollTimer);
      cliPollTimer = null;
      await writeCliOutput(`\r\n[CLI ${data.status}]\r\n`);
    }
  } catch (err) {
    await writeCliOutput('\r\n读取 CLI 输出失败：' + (err && err.message ? err.message : err) + '\r\n');
    if (cliPollTimer) clearInterval(cliPollTimer);
    cliPollTimer = null;
  }
}
async function sendInteractiveCliData(text) {
  if (!currentCliSessionId || !text) return;
  cliInputBuffer += text;
  if (cliSending) return;
  cliSending = true;
  try {
    while (cliInputBuffer) {
      const payload = cliInputBuffer;
      cliInputBuffer = '';
      const res = await fetch(`/api/dev-studio/cli/${encodeURIComponent(currentCliSessionId)}/send`, {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({text: payload, raw: true})
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || '发送失败');
      await writeCliOutput(data.output || '');
    }
  } catch (err) {
    await writeCliOutput('\r\n发送失败：' + (err && err.message ? err.message : err) + '\r\n');
    setStatus('发送 CLI 输入失败', 'warn');
  } finally {
    cliSending = false;
  }
}
function autosizeCliTextInput() {
  const input = document.getElementById('cliTextInput');
  if (!input) return;
  input.style.height = 'auto';
  input.style.height = Math.min(128, Math.max(38, input.scrollHeight)) + 'px';
}
async function sendInteractiveCliText() {
  const input = document.getElementById('cliTextInput');
  const text = input ? input.value : '';
  if (!text.trim()) return;
  if (input) { input.value = ''; autosizeCliTextInput(); input.focus(); }
  await writeCliOutput('\r\n> ' + text + '\r\n');
  await sendInteractiveCliData(text.endsWith('\n') ? text : text + '\n');
}
async function sendInteractiveCliFallback() {
  const input = document.getElementById('cliFallbackText');
  const text = input ? input.value : '';
  if (!text.trim()) return;
  if (input) input.value = '';
  await sendInteractiveCliData(text.endsWith('\n') ? text : text + '\n');
}
async function stopInteractiveCli() {
  if (!currentCliSessionId) { setStatus('没有 CLI 会话', 'warn'); return; }
  try {
    const res = await fetch(`/api/dev-studio/cli/${encodeURIComponent(currentCliSessionId)}/stop`, {method:'POST'});
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || '停止失败');
    await writeCliOutput(`\r\n[CLI stopped pid=${data.pid || '-'}]\r\n`);
    if (cliPollTimer) clearInterval(cliPollTimer);
    cliPollTimer = null;
    currentCliSessionId = '';
    localStorage.removeItem('apd_cli_session_id');
    setStatus('CLI 已停止', 'ok');
  } catch (err) {
    await writeCliOutput('\r\n停止失败：' + (err && err.message ? err.message : err) + '\r\n');
    setStatus('停止 CLI 失败', 'warn');
  }
}
async function saveDevVersion(workspaceId) {
  workspaceId = workspaceId || currentDevWorkspaceId;
  if (!workspaceId) { setStatus('请先创建或选择开发工作区', 'warn'); return; }
  const messageText = devVersionMessage.value.trim() || '保存当前稳定版本';
  localStorage.setItem('apd_dev_version_message', messageText);
  try {
    const res = await fetch(`/api/dev-studio/workspace/${encodeURIComponent(workspaceId)}/version`, {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({message: messageText})
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || '保存失败');
    devStudioResult.innerHTML = `<section class="beginner-box"><h3>版本已保存：${escapeHtml(data.version_id || '')}</h3><p>以后如果改坏了，可以回滚到这个版本。</p></section>` + previewSection('版本详情', '这是一张项目快照。', data);
    await refreshDevWorkspaces();
    setStatus('开发台版本已保存', 'ok');
  } catch (err) {
    devStudioResult.innerHTML = '保存版本失败：' + escapeHtml(err && err.message ? err.message : err);
    setStatus('保存版本失败', 'warn');
  }
}
async function rollbackDevWorkspace(workspaceId, versionId) {
  workspaceId = workspaceId || currentDevWorkspaceId;
  if (!workspaceId || !versionId) { setStatus('请选择工作区和版本', 'warn'); return; }
  if (!confirm(`确定回滚到 ${versionId} 吗？当前未保存改动会丢失。`)) return;
  try {
    const res = await fetch(`/api/dev-studio/workspace/${encodeURIComponent(workspaceId)}/rollback`, {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({version_id: versionId})
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || '回滚失败');
    currentDevWorkspaceId = workspaceId;
    devStudioResult.innerHTML = `<section class="beginner-box"><h3>已回滚到 ${escapeHtml(versionId)}</h3><p>当前工作区已恢复成这个版本的代码。可以再次运行验证。</p></section>` + previewSection('工作区详情', '回滚后的工作区。', data);
    await refreshDevWorkspaces();
    setStatus('开发台已回滚', 'ok');
  } catch (err) {
    devStudioResult.innerHTML = '回滚失败：' + escapeHtml(err && err.message ? err.message : err);
    setStatus('回滚失败', 'warn');
  }
}
function downloadDevWorkspace(versionId, workspaceId) {
  workspaceId = workspaceId || currentDevWorkspaceId;
  versionId = versionId || 'current';
  if (!workspaceId) { setStatus('请先创建或选择开发工作区', 'warn'); return; }
  window.location.href = `/api/dev-studio/workspace/${encodeURIComponent(workspaceId)}/download?version=${encodeURIComponent(versionId)}`;
  setStatus('正在下载开发台项目 zip', 'ok');
}
function openSandbox() {
  document.getElementById('sandboxMask').classList.add('open');
  loadSandboxSettings();
}
function closeSandbox(event) {
  if (event && event.target !== document.getElementById('sandboxMask')) return;
  document.getElementById('sandboxMask').classList.remove('open');
}
function fillSandboxSmokeCommand() {
  sandboxCommand.value = `python - <<'PY'
from pathlib import Path
import json
print('APD DockerSandbox smoke test')
print(Path('/workspace/TASK.md').read_text(encoding='utf-8')[:2000])
Path('/workspace/APD_RESULT.json').write_text(json.dumps({
  'status': 'ok',
  'summary': 'sandbox smoke test completed',
  'changed_files': [],
  'tests': ['smoke'],
  'risks': []
}, ensure_ascii=False, indent=2), encoding='utf-8')
PY`;
}
function applySandboxRunnerPreset() {
  if (sandboxRunner.value === 'codex') {
    sandboxImage.value = 'apd-codex-runner:latest';
    sandboxCommand.value = `MODEL_ARG=""
if [ -n "\${OPENAI_MODEL:-}" ]; then MODEL_ARG="-m \${OPENAI_MODEL}"; fi
codex exec \
  --dangerously-bypass-approvals-and-sandbox \
  --skip-git-repo-check \
  --ephemeral \
  -C /workspace/project \
  $MODEL_ARG \
  "$(cat /workspace/TASK.md)"`;
  } else if (sandboxRunner.value === 'claude') {
    sandboxImage.value = 'apd-claude-runner:latest';
    sandboxCommand.value = `MODEL_ARG=""
if [ -n "\${LLM_MODEL:-}" ]; then MODEL_ARG="--model \${LLM_MODEL}"; fi
claude --print \
  --permission-mode bypassPermissions \
  --no-session-persistence \
  --output-format text \
  $MODEL_ARG \
  "$(cat /workspace/TASK.md)"`;
  } else {
    sandboxImage.value = sandboxImage.value || 'python:3.12-slim';
    fillSandboxSmokeCommand();
  }
}
function loadSandboxSettings() {
  sandboxProjectPath.value = localStorage.getItem('apd_sandbox_project_path') || '';
  sandboxRunner.value = localStorage.getItem('apd_sandbox_runner') || 'custom';
  sandboxImage.value = localStorage.getItem('apd_sandbox_image') || 'python:3.12-slim';
  sandboxTimeout.value = localStorage.getItem('apd_sandbox_timeout') || '300';
  sandboxNetwork.value = localStorage.getItem('apd_sandbox_network') || 'bridge';
  sandboxTask.value = localStorage.getItem('apd_sandbox_task') || '请阅读 /workspace/TASK.md 和 /workspace/apd_inputs 中的 APD 产物，完成一次工程检查或最小实现，并写入 /workspace/APD_RESULT.json。';
  sandboxCommand.value = localStorage.getItem('apd_sandbox_command') || '';
  if (sandboxCommand.value.trim() === 'codex --version' || sandboxCommand.value.trim() === 'claude --version') {
    applySandboxRunnerPreset();
  } else if (!sandboxCommand.value) {
    if (sandboxRunner.value === 'codex' || sandboxRunner.value === 'claude') applySandboxRunnerPreset();
    else fillSandboxSmokeCommand();
  }
}
function saveSandboxSettings() {
  localStorage.setItem('apd_sandbox_project_path', sandboxProjectPath.value.trim());
  localStorage.setItem('apd_sandbox_runner', sandboxRunner.value);
  localStorage.setItem('apd_sandbox_image', sandboxImage.value.trim());
  localStorage.setItem('apd_sandbox_timeout', sandboxTimeout.value.trim());
  localStorage.setItem('apd_sandbox_network', sandboxNetwork.value);
  localStorage.setItem('apd_sandbox_task', sandboxTask.value.trim());
  localStorage.setItem('apd_sandbox_command', sandboxCommand.value.trim());
  setStatus('沙箱配置已保存', 'ok');
}
async function runSandbox() {
  if (!sessionId) return;
  saveSandboxSettings();
  sandboxResult.textContent = 'Docker 沙箱运行中...';
  setStatus('Docker 沙箱运行中...', 'warn');
  const controller = new AbortController();
  const timeoutMs = Math.max(10, Number(sandboxTimeout.value || 300)) * 1000 + 30000;
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch('/api/sandbox/run', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      signal: controller.signal,
      body: JSON.stringify({
        session_id: sessionId,
        project_path: sandboxProjectPath.value.trim(),
        runner: sandboxRunner.value,
        image: sandboxImage.value.trim(),
        command: sandboxCommand.value,
        task: sandboxTask.value,
        timeout_seconds: Number(sandboxTimeout.value || 300),
        network: sandboxNetwork.value,
        settings: settings(),
        keep_job: true,
      })
    });
    const data = await res.json();
    sandboxResult.textContent = JSON.stringify(data, null, 2);
    setStatus(data.ok ? 'Docker 沙箱运行完成' : 'Docker 沙箱运行失败', data.ok ? 'ok' : 'warn');
  } catch (err) {
    sandboxResult.textContent = 'Docker 沙箱请求失败：' + (err && err.message ? err.message : err);
    setStatus('Docker 沙箱请求失败', 'warn');
  } finally {
    clearTimeout(timer);
  }
}
window.openInteractiveCli = openInteractiveCli;
window.closeInteractiveCli = closeInteractiveCli;
window.startInteractiveCli = startInteractiveCli;
window.startTtydCli = startTtydCli;
window.stopTtydCli = stopTtydCli;
window.cleanupTtydCli = cleanupTtydCli;
window.readInteractiveCli = readInteractiveCli;
window.stopInteractiveCli = stopInteractiveCli;
window.sendInteractiveCliText = sendInteractiveCliText;
window.fillInteractiveCliDefaults = fillInteractiveCliDefaults;
window.saveInteractiveCliSettings = saveInteractiveCliSettings;
window.createCliWorkspace = createCliWorkspace;
window.refreshCliWorkspaces = refreshCliWorkspaces;
window.selectCliWorkspace = selectCliWorkspace;
window.toggleCliFullscreen = toggleCliFullscreen;
function quick(text) { message.value = text; sendMessage(); }
const cliTextInputEl = document.getElementById('cliTextInput');
if (cliTextInputEl) {
  cliTextInputEl.addEventListener('input', autosizeCliTextInput);
  cliTextInputEl.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendInteractiveCliText();
    }
  });
}
message.addEventListener('keydown', (e) => { if (e.ctrlKey && e.key === 'Enter') sendMessage(); });
(async function init(){
  switchRightTab(localStorage.getItem('apd_right_tab') || 'protocol');
  loadSettings();
  loadInteractiveCliSettings();
  try {
    const res = await fetch('/api/status');
    const data = await res.json();
    if (!apiBase.value && data.api_base) apiBase.value = data.api_base;
    if (!model.value && data.model) model.value = data.model;
    if (!timeout.value && data.llm_timeout) timeout.value = data.llm_timeout;
    if (!serverTimeout.value && data.server_timeout) serverTimeout.value = data.server_timeout;
    if (!maxTokens.value && data.max_tokens) maxTokens.value = data.max_tokens;
    responseFormat.checked = responseFormat.checked || data.response_format === '1';
    if (!apiKey.value) localStorage.removeItem('apd_api_key');
    setStatus(`env: ${data.api_base || 'no api'} / ${data.model || 'no model'} / key=${data.has_key ? 'yes' : 'no'} / timeout=${data.llm_timeout}s / max=${data.max_tokens}`, data.has_key ? 'ok' : 'warn');
  } catch(e) { setStatus('status failed', 'warn'); }
  await refreshSessions();
  if (!sessionId) await resetSession();
  else await loadSession(sessionId);
  renderLandingGuide();
  const initParams = new URLSearchParams(location.search);
  if (initParams.get('open_agent_ide') === '1') {
    setTimeout(openCliCollabAssistant, 300);
  }
})();
</script>
</body>
</html>
"""
