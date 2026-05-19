from __future__ import annotations

from contextlib import asynccontextmanager
import json
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
from .hermes import HermesClient, HermesResult, HermesStreamEvent, runtime_env
from .models import (
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
)
from .normalizer import error_blocks, normalize_output
from .storage import QueryStore


settings = load_settings()
store = QueryStore(settings.database_path)
hermes_client = HermesClient(settings)


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
        "runtime_mode": runtime_mode,
        "message": message,
    }


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
        "users": store.list_users(),
        "recent": store.list_recent(include_all=True, limit=20),
    }


@app.get("/api/admin/health")
def admin_health(_: Dict[str, Any] = Depends(require_owner)) -> Dict[str, Any]:
    return _health_details()


async def _stream_query_events(query_text: str, request: Request, user: Dict[str, Any]) -> AsyncIterator[str]:
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
        record = _store_result(query_text, result, status="error", user=user)
        yield _sse("result", record)
        return

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
                record = _store_result(query_text, result, status="error", user=user)
                yield _sse("result", record)
                return

            if event.type == "done":
                result = event.result or HermesResult(
                    stdout="",
                    stderr="Hermes stream ended without a result.",
                    return_code=1,
                    duration_ms=event.elapsed_ms,
                )
                record = _store_result(query_text, result, user=user)
                yield _sse("result", record)
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
        record = _store_result(query_text, result, status="error", user=user)
        yield _sse("result", record)
    finally:
        stream.close()
        permit.release()


def _next_stream_event(stream: Any) -> Optional[HermesStreamEvent]:
    try:
        return next(stream)
    except StopIteration:
        return None


def _store_result(
    query_text: str,
    result: HermesResult,
    status: str | None = None,
    user: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if status is None:
        status = "success" if result.return_code == 0 and not result.timed_out and result.stdout.strip() else "error"

    if status == "success":
        blocks = normalize_output(result.stdout, query_text)
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
    )


def _history_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    for item in items:
        raw_output = item.pop("raw_output", "")
        if item.get("status") == "success" and raw_output:
            item["blocks"] = normalize_output(raw_output, item.get("query", ""))
    return items


def _sse(event: str, data: Dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _failure_message(result: Any) -> str:
    detail = f"{result.stderr}\n{result.stdout}"
    if result.timed_out:
        return "Hermes 流式查询超过了服务端运行上限。请缩小日期范围、减少筛选条件后重试。"
    if "MCP HTTP 504" in detail or "Gateway Time-out" in detail:
        return "飞猪实时查询接口暂时超时。本应用没有切到 direct-flyai 降级路径；请稍后重试，或把城市、日期、直飞/价格条件拆开查询。"
    return "Hermes 查询失败或超时。"


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
