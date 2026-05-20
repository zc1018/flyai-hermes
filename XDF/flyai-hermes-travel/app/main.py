from __future__ import annotations

from contextlib import asynccontextmanager
import asyncio
import json
import os
import shutil
from pathlib import Path
import threading
from typing import Any, AsyncIterator, Dict, List, Optional

from fastapi import Cookie, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from .auth import COOKIE_NAME, create_session_token, verify_session_token
from .config import ROOT_DIR, Settings, load_settings
from .conversation import build_conversation_turn, conversation_title
from .hermes import HermesClient, HermesResult, HermesStreamEvent, runtime_env
from .models import (
    ConversationCreateRequest,
    ConversationMessageRequest,
    ConversationResponse,
    ConversationSearchRequest,
    CreateUserRequest,
    HealthResponse,
    HistoryItem,
    LoginRequest,
    MeResponse,
    QueryRequest,
    QueryResponse,
    ResetPasswordRequest,
    UpdateUserRequest,
    UserResponse,
    XhsConfigRequest,
)
from .normalizer import error_blocks, normalize_output
from .storage import QueryStore
from .xhs import XhsClient, XhsSearchResult, normalize_xhs_cookie_input, xhs_cookie_has_required_session


settings = load_settings()
store = QueryStore(settings.database_path)
hermes_client = HermesClient(settings)
xhs_client = XhsClient(settings, store)
ENV_FILE = Path(os.getenv("APP_ENV_FILE", ROOT_DIR / ".env"))


@asynccontextmanager
async def lifespan(_: FastAPI):
    store.init(settings.owner_password)
    yield


app = FastAPI(title="Hermes FlyAI Travel", version="0.1.0", lifespan=lifespan)
app.mount("/assets", StaticFiles(directory=str(ROOT_DIR / "static")), name="assets")


class QueryRuntime:
    def __init__(self, global_concurrency: int = 2, queue_timeout_seconds: int = 60):
        self.global_concurrency = global_concurrency
        self.queue_timeout_seconds = queue_timeout_seconds
        self._global = threading.BoundedSemaphore(global_concurrency)
        self._lock = threading.Lock()
        self._active_by_user: Dict[int, int] = {}
        self._queued = 0

    def stats(self) -> Dict[str, int]:
        with self._lock:
            return {
                "global_concurrency": self.global_concurrency,
                "active": sum(self._active_by_user.values()),
                "queued": self._queued,
            }

    def acquire(self, user: Dict[str, Any]) -> Optional["QueryPermit"]:
        user_id = int(user["id"])
        max_concurrent = int(user.get("max_concurrent") or 1)
        with self._lock:
            if self._active_by_user.get(user_id, 0) >= max_concurrent:
                return None
            self._active_by_user[user_id] = self._active_by_user.get(user_id, 0) + 1
            self._queued += 1

        acquired = False
        try:
            acquired = self._global.acquire(timeout=self.queue_timeout_seconds)
            if not acquired:
                return None
            return QueryPermit(self, user_id)
        finally:
            with self._lock:
                self._queued = max(0, self._queued - 1)
                if not acquired:
                    current = self._active_by_user.get(user_id, 0) - 1
                    if current > 0:
                        self._active_by_user[user_id] = current
                    else:
                        self._active_by_user.pop(user_id, None)

    def release(self, user_id: int) -> None:
        self._global.release()
        with self._lock:
            current = self._active_by_user.get(user_id, 0) - 1
            if current > 0:
                self._active_by_user[user_id] = current
            else:
                self._active_by_user.pop(user_id, None)


class QueryPermit:
    def __init__(self, runtime: QueryRuntime, user_id: int):
        self.runtime = runtime
        self.user_id = user_id
        self.released = False

    def release(self) -> None:
        if not self.released:
            self.runtime.release(self.user_id)
            self.released = True


query_runtime = QueryRuntime()


def require_auth(flyai_travel_session: str = Cookie(default="", alias=COOKIE_NAME)) -> Dict[str, Any]:
    token = verify_session_token(flyai_travel_session, settings.session_secret)
    if not token:
        raise HTTPException(status_code=401, detail="请先输入访问口令。")
    user = store.get_user(int(token["user_id"]))
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return user


def require_owner(user: Dict[str, Any] = Depends(require_auth)) -> Dict[str, Any]:
    if user.get("role") != "owner":
        raise HTTPException(status_code=403, detail="只有管理员可以访问后台。")
    return user


@app.get("/")
def index() -> FileResponse:
    return FileResponse(str(ROOT_DIR / "static" / "index.html"))


@app.get("/admin")
def admin_page() -> FileResponse:
    return FileResponse(str(ROOT_DIR / "static" / "admin.html"))


@app.post("/api/login")
def login(payload: LoginRequest, response: Response) -> Dict[str, bool]:
    if not settings.owner_password and not settings.app_password:
        raise HTTPException(status_code=500, detail="OWNER_PASSWORD is not configured")
    store.init(settings.owner_password)
    user = store.authenticate(payload.password)
    if not user:
        store.log_usage_event(None, "login_failed")
        raise HTTPException(status_code=401, detail="访问口令不正确，或该口令已停用。")

    response.set_cookie(
        COOKIE_NAME,
        create_session_token(settings.session_secret, int(user["id"]), str(user["role"])),
        httponly=True,
        samesite="lax",
        secure=settings.secure_cookies,
        max_age=60 * 60 * 24 * 7,
    )
    return {"ok": True}


@app.post("/api/logout")
def logout(response: Response) -> Dict[str, bool]:
    response.delete_cookie(COOKIE_NAME)
    return {"ok": True}


@app.get("/api/me", response_model=MeResponse)
def me(user: Dict[str, Any] = Depends(require_auth)) -> Dict[str, Any]:
    return {
        "authenticated": True,
        "id": user["id"],
        "label": user["label"],
        "role": user["role"],
        "quota": _quota_for_user(user),
    }


@app.get("/api/health", response_model=HealthResponse)
def health() -> Dict[str, Any]:
    detailed = _health_details()
    return {
        "ok": detailed["ok"],
        "runtime_mode": detailed["runtime_mode"],
        "message": None if detailed["ok"] else "服务暂时不可用，请稍后再试。",
    }


def _health_details() -> Dict[str, Any]:
    hermes_path = Path(settings.hermes_bin)
    hermes_ok = hermes_path.exists() and hermes_path.is_file()
    hermes_skill = _hermes_skill_status(settings.hermes_home, settings.hermes_skill)
    flyai_path = shutil.which("flyai", path=runtime_env().get("PATH"))
    db_status = store.check()
    hermes_runtime_ok = hermes_ok and hermes_skill["ok"]
    ok = bool(hermes_runtime_ok and flyai_path and db_status["ok"] and (settings.owner_password or settings.app_password))
    message = None if ok else "Deployment prerequisites are incomplete."
    runtime_mode = "hermes-stream" if hermes_runtime_ok else "unavailable"
    return {
        "ok": ok,
        "hermes_bin": {"ok": hermes_ok, "path": settings.hermes_bin},
        "hermes_skill": hermes_skill,
        "hermes_provider": {"provider": settings.hermes_provider, "model": settings.hermes_model},
        "flyai_cli": {"ok": bool(flyai_path), "path": flyai_path},
        "database": db_status,
        "app_password_configured": bool(settings.owner_password or settings.app_password),
        "xhs": xhs_client.health(),
        "runtime_mode": runtime_mode,
        "message": message,
    }


def _xhs_config_payload() -> Dict[str, Any]:
    health = xhs_client.health()
    return {
        "enabled": settings.xhs_enabled,
        "cookie_configured": bool(settings.xhs_cookies),
        "required_cookie_ok": bool(health.get("required_cookie_ok", True)),
        "required_cookie_name": health.get("required_cookie_name"),
        "timeout_seconds": settings.xhs_timeout_seconds,
        "max_results": settings.xhs_max_results,
        "max_daily_per_user": settings.xhs_max_daily_per_user,
        "cache_ttl_hours": settings.xhs_cache_ttl_hours,
        "mediacrawler_ready": bool(health.get("mediacrawler_dir_ok") and health.get("uv_ok") and health.get("required_cookie_ok", True)),
        "health": health,
    }


def _write_env_updates(updates: Dict[str, str]) -> None:
    ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    lines = ENV_FILE.read_text().splitlines() if ENV_FILE.exists() else []
    seen: set[str] = set()
    output: List[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            output.append(line)
            continue
        key, _ = line.split("=", 1)
        key = key.strip()
        if key in updates:
            output.append(f"{key}={_format_env_value(updates[key])}")
            seen.add(key)
        else:
            output.append(line)
    for key, value in updates.items():
        if key not in seen:
            output.append(f"{key}={_format_env_value(value)}")
    ENV_FILE.write_text("\n".join(output).rstrip() + "\n")


def _format_env_value(value: str) -> str:
    if value == "":
        return ""
    if any(char.isspace() for char in value) or any(char in value for char in ['"', "\\", "$", "`", "#", ";"]):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`")
        return f'"{escaped}"'
    return value


@app.post("/api/query", response_model=QueryResponse)
def query(payload: QueryRequest, user: Dict[str, Any] = Depends(require_auth)) -> Dict[str, Any]:
    _preflight_query(payload.query, user)
    permit = query_runtime.acquire(user)
    if permit is None:
        raise HTTPException(status_code=429, detail="当前查询较多，请稍后再试。")
    result = hermes_client.run(payload.query, timeout_seconds=_timeout_for_user(user))
    try:
        return _store_result(payload.query, result, user=user)
    finally:
        permit.release()


@app.post("/api/query/stream")
def query_stream(payload: QueryRequest, request: Request, user: Dict[str, Any] = Depends(require_auth)) -> StreamingResponse:
    _preflight_query(payload.query, user)
    return StreamingResponse(
        _stream_query_events(payload.query, request, user),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.get("/api/history", response_model=List[HistoryItem])
def history(user: Dict[str, Any] = Depends(require_auth)) -> List[Dict[str, Any]]:
    if user.get("role") == "owner":
        return _history_items(store.list_recent(include_all=True))
    if not user.get("can_view_history"):
        return []
    return _history_items(store.list_recent(user_id=int(user["id"])))


@app.post("/api/conversations", response_model=ConversationResponse)
def create_conversation(payload: ConversationCreateRequest, user: Dict[str, Any] = Depends(require_auth)) -> Dict[str, Any]:
    title = payload.title or "新的旅行计划"
    return store.create_conversation(int(user["id"]), title=title)


@app.get("/api/conversations", response_model=List[ConversationResponse])
def conversations(user: Dict[str, Any] = Depends(require_auth)) -> List[Dict[str, Any]]:
    if user.get("role") == "owner":
        return store.list_conversations(include_all=True)
    if not user.get("can_view_history"):
        return []
    return store.list_conversations(user_id=int(user["id"]))


@app.get("/api/conversations/{conversation_id}", response_model=ConversationResponse)
def conversation_detail(conversation_id: int, user: Dict[str, Any] = Depends(require_auth)) -> Dict[str, Any]:
    return _require_conversation(conversation_id, user)


@app.post("/api/conversations/{conversation_id}/messages/stream")
def conversation_message_stream(
    conversation_id: int,
    payload: ConversationMessageRequest,
    request: Request,
    user: Dict[str, Any] = Depends(require_auth),
) -> StreamingResponse:
    return StreamingResponse(
        _stream_conversation_message_events(conversation_id, payload.message, request, user),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.post("/api/conversations/{conversation_id}/search/stream")
def conversation_search_stream(
    conversation_id: int,
    payload: ConversationSearchRequest,
    request: Request,
    user: Dict[str, Any] = Depends(require_auth),
) -> StreamingResponse:
    conversation = _require_conversation(conversation_id, user)
    if conversation.get("status") == "running":
        raise HTTPException(status_code=409, detail="这个旅行会话正在查询中，请等待当前结果返回。")
    query_text = (payload.query or conversation.get("profile", {}).get("search_query") or "").strip()
    if not query_text:
        raise HTTPException(status_code=400, detail="还没有可执行的查询条件。请先补充旅行需求。")
    _preflight_query(query_text, user)
    store.update_conversation(conversation_id, {"status": "running"}, user_id=int(user["id"]), include_all=user.get("role") == "owner")
    return StreamingResponse(
        _stream_query_events(query_text, request, user, conversation_id=conversation_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.get("/api/admin/users", response_model=List[UserResponse])
def admin_users(_: Dict[str, Any] = Depends(require_owner)) -> List[Dict[str, Any]]:
    return store.list_users()


@app.post("/api/admin/users", response_model=UserResponse)
def admin_create_user(payload: CreateUserRequest, _: Dict[str, Any] = Depends(require_owner)) -> Dict[str, Any]:
    try:
        return store.create_user(
            label=payload.label,
            password=payload.password,
            daily_limit=payload.daily_limit,
            max_concurrent=payload.max_concurrent,
            timeout_seconds=payload.timeout_seconds,
            can_view_history=payload.can_view_history,
            enabled=payload.enabled,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.patch("/api/admin/users/{user_id}", response_model=UserResponse)
def admin_update_user(user_id: int, payload: UpdateUserRequest, _: Dict[str, Any] = Depends(require_owner)) -> Dict[str, Any]:
    updates = {key: value for key, value in payload.model_dump().items() if value is not None}
    user = store.update_user(user_id, updates)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在，或 owner 账号不可在这里修改。")
    return user


@app.post("/api/admin/users/{user_id}/reset-password", response_model=UserResponse)
def admin_reset_password(user_id: int, payload: ResetPasswordRequest, _: Dict[str, Any] = Depends(require_owner)) -> Dict[str, Any]:
    try:
        user = store.reset_password(user_id, payload.password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在，或 owner 账号不可在这里修改。")
    return user


@app.get("/api/admin/usage")
def admin_usage(_: Dict[str, Any] = Depends(require_owner)) -> Dict[str, Any]:
    return {
        "runtime": query_runtime.stats(),
        "xhs": xhs_client.stats(),
        "users": store.list_users(),
        "recent": store.list_recent(include_all=True, limit=20),
    }


@app.get("/api/admin/health")
def admin_health(_: Dict[str, Any] = Depends(require_owner)) -> Dict[str, Any]:
    return _health_details()


@app.get("/api/admin/xhs-config")
def admin_xhs_config(_: Dict[str, Any] = Depends(require_owner)) -> Dict[str, Any]:
    return _xhs_config_payload()


@app.post("/api/admin/xhs-config")
def admin_update_xhs_config(payload: XhsConfigRequest, _: Dict[str, Any] = Depends(require_owner)) -> Dict[str, Any]:
    cookies = settings.xhs_cookies
    if payload.clear_cookies:
        cookies = ""
    elif payload.cookies is not None and payload.cookies.strip():
        cookies = normalize_xhs_cookie_input(payload.cookies)

    if payload.enabled and not cookies:
        raise HTTPException(status_code=400, detail="请先粘贴小红书 cookie，再开启小红书补充。")
    if payload.enabled and settings.xhs_login_type == "cookie" and not xhs_cookie_has_required_session(cookies):
        raise HTTPException(status_code=400, detail="这份小红书 cookie 缺少 web_session，MediaCrawler 无法识别登录态。请从 www.xiaohongshu.com 的 Cookie 里复制 web_session 后再开启。")

    settings.xhs_enabled = payload.enabled
    settings.xhs_cookies = cookies
    settings.xhs_timeout_seconds = payload.timeout_seconds
    settings.xhs_max_results = payload.max_results
    settings.xhs_max_daily_per_user = payload.max_daily_per_user

    _write_env_updates(
        {
            "XHS_ENABLED": "true" if settings.xhs_enabled else "false",
            "MEDIACRAWLER_DIR": str(settings.mediacrawler_dir),
            "XHS_LOGIN_TYPE": settings.xhs_login_type,
            "XHS_COOKIES": settings.xhs_cookies,
            "XHS_TIMEOUT_SECONDS": str(settings.xhs_timeout_seconds),
            "XHS_MAX_RESULTS": str(settings.xhs_max_results),
            "XHS_MAX_DAILY_PER_USER": str(settings.xhs_max_daily_per_user),
            "XHS_CACHE_TTL_HOURS": str(settings.xhs_cache_ttl_hours),
        }
    )
    return _xhs_config_payload()


def _require_conversation(conversation_id: int, user: Dict[str, Any]) -> Dict[str, Any]:
    include_all = user.get("role") == "owner"
    conversation = store.get_conversation(conversation_id, user_id=int(user["id"]), include_all=include_all)
    if not conversation:
        raise HTTPException(status_code=404, detail="这个旅行会话不存在，或你没有访问权限。")
    return conversation


async def _stream_conversation_message_events(
    conversation_id: int,
    message: str,
    request: Request,
    user: Dict[str, Any],
) -> AsyncIterator[str]:
    conversation = _require_conversation(conversation_id, user)
    clean = message.strip()
    store.add_conversation_message(conversation_id, "user", "user_text", clean)
    if await request.is_disconnected():
        return

    turn = build_conversation_turn(conversation.get("profile") or {}, clean)
    profile = turn["profile"]
    title = conversation_title(profile, conversation.get("title") or "新的旅行计划")
    status = "ready" if turn["action"] in {"confirm", "search_requested"} else "draft"
    updated = store.update_conversation(
        conversation_id,
        {"profile": profile, "title": title, "status": status},
        user_id=int(user["id"]),
        include_all=user.get("role") == "owner",
    )
    message_type = "search_confirmation" if turn["action"] in {"confirm", "search_requested"} else "assistant_text"
    assistant = store.add_conversation_message(
        conversation_id,
        "assistant",
        message_type,
        turn["assistant_message"],
        {
            "action": turn["action"],
            "missing_fields": turn.get("missing_fields") or [],
            "confirmation": turn.get("confirmation"),
            "search_query": turn.get("search_query") or "",
            "profile": profile,
        },
    )
    yield _sse(
        "message",
        {
            "conversation": updated,
            "message": assistant,
            "action": turn["action"],
        },
    )


async def _stream_query_events(
    query_text: str,
    request: Request,
    user: Dict[str, Any],
    conversation_id: Optional[int] = None,
) -> AsyncIterator[str]:
    completed = False
    yield _sse(
        "progress",
        {
            "kind": "queued",
            "message": "请求已进入队列，正在等待 Hermes 执行资源。",
            "elapsed_ms": 0,
        },
    )
    permit = await run_in_threadpool(query_runtime.acquire, user)
    if permit is None:
        result = HermesResult(
            stdout="",
            stderr="当前查询较多或你的上一个查询仍在运行。",
            return_code=429,
            duration_ms=0,
        )
        record = _store_result(query_text, result, status="error", user=user, conversation_id=conversation_id)
        _record_conversation_result(conversation_id, record, "error")
        completed = True
        yield _sse("result", record)
        return

    xhs_task = _start_xhs_task(query_text, user)
    if xhs_task:
        yield _sse(
            "progress",
            {
                "kind": "xhs",
                "message": "同时寻找小红书高互动旅行笔记。",
                "elapsed_ms": 0,
            },
        )

    timeout_seconds = _timeout_for_user(user)
    stream = hermes_client.run_stream(query_text, timeout_seconds=timeout_seconds)
    try:
        while True:
            if await request.is_disconnected():
                return

            event = await run_in_threadpool(_next_stream_event, stream)
            if event is None:
                return
            if await request.is_disconnected():
                return

            if event.type in {"start", "chunk", "heartbeat"}:
                yield _sse(
                    "progress",
                    {
                        "kind": event.type,
                        "message": event.message,
                        "elapsed_ms": event.elapsed_ms,
                    },
                )
                continue

            if event.type == "error":
                result = HermesResult(
                    stdout="",
                    stderr=event.message,
                    return_code=127,
                    duration_ms=event.elapsed_ms,
                )
                record = _store_result(query_text, result, status="error", user=user, conversation_id=conversation_id)
                _record_conversation_result(conversation_id, record, "error")
                completed = True
                yield _sse("result", record)
                return

            if event.type == "done":
                result = event.result or HermesResult(
                    stdout="",
                    stderr="Hermes stream ended without a result.",
                    return_code=1,
                    duration_ms=event.elapsed_ms,
                )
                xhs_blocks = await _completed_xhs_blocks(xhs_task)
                record = _store_result(query_text, result, user=user, extra_blocks=xhs_blocks, conversation_id=conversation_id)
                _record_conversation_result(conversation_id, record, record["status"])
                completed = True
                yield _sse("result", record)
                permit.release()
                if xhs_task and not xhs_blocks and not xhs_task.done():
                    supplement_blocks = await _await_xhs_blocks(xhs_task)
                    if supplement_blocks and not await request.is_disconnected():
                        store.append_blocks(int(record["id"]), supplement_blocks)
                        _record_conversation_supplement(conversation_id, supplement_blocks)
                        yield _sse(
                            "supplement",
                            {
                                "query_id": record["id"],
                                "blocks": supplement_blocks,
                                "message": "已补充小红书高互动笔记。",
                            },
                        )
                return
    except Exception as exc:  # pragma: no cover - defensive streaming boundary
        if await request.is_disconnected():
            return
        result = HermesResult(
            stdout="",
            stderr=str(exc),
            return_code=1,
            duration_ms=0,
        )
        record = _store_result(query_text, result, status="error", user=user, conversation_id=conversation_id)
        _record_conversation_result(conversation_id, record, "error")
        completed = True
        yield _sse("result", record)
    finally:
        stream.close()
        permit.release()
        if conversation_id and not completed:
            store.update_conversation(int(conversation_id), {"status": "ready"}, include_all=True)


def _next_stream_event(stream: Any) -> Optional[HermesStreamEvent]:
    try:
        return next(stream)
    except StopIteration:
        return None


def _start_xhs_task(query_text: str, user: Dict[str, Any]) -> Optional[asyncio.Task[XhsSearchResult]]:
    try:
        if not xhs_client.should_attempt(user):
            return None
    except Exception:
        return None
    return asyncio.create_task(run_in_threadpool(xhs_client.search, query_text, user))


async def _completed_xhs_blocks(task: Optional[asyncio.Task[XhsSearchResult]]) -> List[Dict[str, Any]]:
    if not task or not task.done():
        return []
    try:
        result = await task
    except Exception:
        return []
    return _xhs_blocks_from_result(result)


async def _await_xhs_blocks(task: asyncio.Task[XhsSearchResult]) -> List[Dict[str, Any]]:
    try:
        result = await task
    except Exception:
        return []
    return _xhs_blocks_from_result(result)


def _xhs_blocks_from_result(result: XhsSearchResult) -> List[Dict[str, Any]]:
    if result.status != "ok":
        return []
    return [block for block in result.blocks or [] if block.get("type") == "xhs_post_card"]


def _store_result(
    query_text: str,
    result: HermesResult,
    status: str | None = None,
    user: Optional[Dict[str, Any]] = None,
    extra_blocks: Optional[List[Dict[str, Any]]] = None,
    conversation_id: Optional[int] = None,
) -> Dict[str, Any]:
    if status is None:
        status = (
            "success"
            if result.return_code == 0
            and not result.timed_out
            and result.stdout.strip()
            and not _looks_like_hermes_failure(f"{result.stderr}\n{result.stdout}")
            else "error"
        )

    if status == "success":
        blocks = normalize_output(result.stdout, query_text)
        if extra_blocks:
            blocks.extend(extra_blocks)
    else:
        blocks = error_blocks(_failure_message(result), result.stderr or result.stdout)

    return store.insert(
        query=query_text,
        status=status,
        blocks=blocks,
        raw_output=result.stdout,
        stderr=result.stderr,
        duration_ms=result.duration_ms,
        user_id=int(user["id"]) if user else None,
        conversation_id=conversation_id,
    )


def _record_conversation_result(conversation_id: Optional[int], record: Dict[str, Any], status: str) -> None:
    if not conversation_id:
        return
    conversation_status = "result" if status == "success" else "error"
    store.update_conversation(
        int(conversation_id),
        {"status": conversation_status, "last_query_id": record["id"]},
        include_all=True,
    )
    title = "查询结果" if status == "success" else "查询异常"
    content = f"{title}：{record.get('query', '')}"
    store.add_conversation_message(
        int(conversation_id),
        "assistant",
        "search_result",
        content,
        {
            "query_id": record.get("id"),
            "status": record.get("status"),
            "blocks": record.get("blocks") or [],
            "duration_ms": record.get("duration_ms"),
            "created_at": record.get("created_at"),
        },
    )


def _record_conversation_supplement(conversation_id: Optional[int], blocks: List[Dict[str, Any]]) -> None:
    if not conversation_id or not blocks:
        return
    store.add_conversation_message(
        int(conversation_id),
        "assistant",
        "xhs_posts",
        "已补充小红书高互动笔记。",
        {"blocks": blocks},
    )


def _history_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    for item in items:
        raw_output = item.pop("raw_output", "")
        stored_blocks = item.get("blocks") or []
        if _looks_like_hermes_failure(raw_output):
            item["status"] = "error"
            item["blocks"] = error_blocks(_failure_message_from_text(raw_output), raw_output)
            continue
        if item.get("status") == "success" and raw_output:
            item["blocks"] = normalize_output(raw_output, item.get("query", "")) + _stored_supplemental_blocks(stored_blocks)
    return items


def _stored_supplemental_blocks(blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    output = []
    seen = set()
    for block in blocks:
        if block.get("supplement") != "xhs" and block.get("type") != "xhs_post_card":
            continue
        key = (block.get("type"), block.get("title"), block.get("postUrl") or block.get("bookingUrl"))
        if key in seen:
            continue
        seen.add(key)
        output.append(block)
    return output


def _sse(event: str, data: Dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _failure_message(result: Any) -> str:
    detail = f"{result.stderr}\n{result.stdout}"
    if _looks_like_hermes_failure(detail):
        return _failure_message_from_text(detail)
    if result.timed_out:
        return "Hermes 流式查询超过了服务端运行上限。请缩小日期范围、减少筛选条件后重试。"
    if "MCP HTTP 504" in detail or "Gateway Time-out" in detail:
        return "飞猪实时查询接口暂时超时。本应用没有切到 direct-flyai 降级路径；请稍后重试，或把城市、日期、直飞/价格条件拆开查询。"
    return "Hermes 查询失败或超时。"


def _looks_like_hermes_failure(text: str) -> bool:
    if not text:
        return False
    markers = (
        "Failed to initialize agent",
        "AIAgent.__init__()",
        "unexpected keyword argument",
        "Traceback (most recent call last)",
    )
    return any(marker in text for marker in markers)


def _failure_message_from_text(text: str) -> str:
    if "Failed to initialize agent" in text or "unexpected keyword argument" in text:
        return "Hermes Agent 启动失败。请检查服务器上的 Hermes 版本和配置；当前查询没有成功调用到 fly.ai。"
    return "Hermes 执行过程中发生错误，本次没有拿到可用的实时旅行结果。"


def _quota_for_user(user: Dict[str, Any]) -> Dict[str, Any]:
    used_today = store.count_queries_today(int(user["id"]))
    daily_limit = int(user.get("daily_limit") or 0)
    unlimited = user.get("role") == "owner" or daily_limit < 0
    return {
        "daily_limit": daily_limit,
        "used_today": used_today,
        "remaining_today": None if unlimited else max(0, daily_limit - used_today),
        "unlimited": unlimited,
        "max_concurrent": user.get("max_concurrent"),
        "timeout_seconds": _timeout_for_user(user),
    }


def _preflight_query(query_text: str, user: Dict[str, Any]) -> None:
    clean = query_text.strip()
    if len(clean) < 2:
        raise HTTPException(status_code=400, detail="请输入更具体的旅行需求。")
    if user.get("role") != "owner" and len(clean) > 500:
        raise HTTPException(status_code=400, detail="这个问题有点长，请压缩到 500 字以内，分多次查询会更稳定。")

    quota = _quota_for_user(user)
    if not quota["unlimited"] and quota["remaining_today"] <= 0:
        raise HTTPException(status_code=429, detail="今天的查询额度已用完，明天会自动恢复。")


def _timeout_for_user(user: Dict[str, Any]) -> int:
    if user.get("role") == "owner":
        return min(int(user.get("timeout_seconds") or 900), settings.hermes_timeout_seconds)
    return min(int(user.get("timeout_seconds") or 300), 300)


def _hermes_skill_status(hermes_home: str, skill_name: str) -> Dict[str, Any]:
    skills_dir = Path(hermes_home) / "skills"
    if not skills_dir.exists():
        return {"ok": False, "name": skill_name, "path": str(skills_dir), "message": "skills directory missing"}

    for skill_file in skills_dir.rglob("SKILL.md"):
        try:
            head = skill_file.read_text(encoding="utf-8", errors="ignore")[:800]
        except OSError:
            continue
        if f"name: {skill_name}" in head or f"name: \"{skill_name}\"" in head or f"name: '{skill_name}'" in head:
            return {"ok": True, "name": skill_name, "path": str(skill_file)}
    return {"ok": False, "name": skill_name, "path": str(skills_dir), "message": "skill not found"}
