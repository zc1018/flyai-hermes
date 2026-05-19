from __future__ import annotations

from contextlib import asynccontextmanager
import json
import shutil
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

from fastapi import Cookie, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from .auth import COOKIE_NAME, create_session_token, password_matches, verify_session_token
from .config import ROOT_DIR, Settings, load_settings
from .hermes import HermesClient, HermesResult, HermesStreamEvent, runtime_env
from .models import HealthResponse, HistoryItem, LoginRequest, QueryRequest, QueryResponse
from .normalizer import error_blocks, normalize_output
from .storage import QueryStore


settings = load_settings()
store = QueryStore(settings.database_path)
hermes_client = HermesClient(settings)


@asynccontextmanager
async def lifespan(_: FastAPI):
    store.init()
    yield


app = FastAPI(title="Hermes FlyAI Travel", version="0.1.0", lifespan=lifespan)
app.mount("/assets", StaticFiles(directory=str(ROOT_DIR / "static")), name="assets")


def require_auth(flyai_travel_session: str = Cookie(default="", alias=COOKIE_NAME)) -> None:
    if not verify_session_token(flyai_travel_session, settings.session_secret):
        raise HTTPException(status_code=401, detail="Unauthorized")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(str(ROOT_DIR / "static" / "index.html"))


@app.post("/api/login")
def login(payload: LoginRequest, response: Response) -> Dict[str, bool]:
    if not settings.app_password:
        raise HTTPException(status_code=500, detail="APP_PASSWORD is not configured")
    if not password_matches(payload.password, settings.app_password):
        raise HTTPException(status_code=401, detail="Invalid password")

    response.set_cookie(
        COOKIE_NAME,
        create_session_token(settings.session_secret),
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=60 * 60 * 24 * 7,
    )
    return {"ok": True}


@app.post("/api/logout")
def logout(response: Response) -> Dict[str, bool]:
    response.delete_cookie(COOKIE_NAME)
    return {"ok": True}


@app.get("/api/me")
def me(_: None = Depends(require_auth)) -> Dict[str, bool]:
    return {"authenticated": True}


@app.get("/api/health", response_model=HealthResponse)
def health() -> Dict[str, Any]:
    hermes_path = Path(settings.hermes_bin)
    hermes_ok = hermes_path.exists() and hermes_path.is_file()
    hermes_skill = _hermes_skill_status(settings.hermes_home, settings.hermes_skill)
    flyai_path = shutil.which("flyai", path=runtime_env().get("PATH"))
    db_status = store.check()
    hermes_runtime_ok = hermes_ok and hermes_skill["ok"]
    ok = bool(hermes_runtime_ok and flyai_path and db_status["ok"] and settings.app_password)
    message = None if ok else "Deployment prerequisites are incomplete."
    runtime_mode = "hermes-stream" if hermes_runtime_ok else "unavailable"
    return {
        "ok": ok,
        "hermes_bin": {"ok": hermes_ok, "path": settings.hermes_bin},
        "hermes_skill": hermes_skill,
        "hermes_provider": {"provider": settings.hermes_provider, "model": settings.hermes_model},
        "flyai_cli": {"ok": bool(flyai_path), "path": flyai_path},
        "database": db_status,
        "app_password_configured": bool(settings.app_password),
        "runtime_mode": runtime_mode,
        "message": message,
    }


@app.post("/api/query", response_model=QueryResponse)
def query(payload: QueryRequest, _: None = Depends(require_auth)) -> Dict[str, Any]:
    result = hermes_client.run(payload.query)
    if result.return_code == 0 and not result.timed_out and result.stdout.strip():
        blocks = normalize_output(result.stdout)
        status = "success"
    else:
        message = _failure_message(result)
        blocks = error_blocks(message, result.stderr or result.stdout)
        status = "error"

    return store.insert(
        query=payload.query,
        status=status,
        blocks=blocks,
        raw_output=result.stdout,
        stderr=result.stderr,
        duration_ms=result.duration_ms,
    )


@app.post("/api/query/stream")
def query_stream(payload: QueryRequest, request: Request, _: None = Depends(require_auth)) -> StreamingResponse:
    return StreamingResponse(
        _stream_query_events(payload.query, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.get("/api/history", response_model=List[HistoryItem])
def history(_: None = Depends(require_auth)) -> List[Dict[str, Any]]:
    return store.list_recent()


async def _stream_query_events(query_text: str, request: Request) -> AsyncIterator[str]:
    stream = hermes_client.run_stream(query_text)
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
                record = _store_result(query_text, result, status="error")
                yield _sse("result", record)
                return

            if event.type == "done":
                result = event.result or HermesResult(
                    stdout="",
                    stderr="Hermes stream ended without a result.",
                    return_code=1,
                    duration_ms=event.elapsed_ms,
                )
                record = _store_result(query_text, result)
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
        record = _store_result(query_text, result, status="error")
        yield _sse("result", record)
    finally:
        stream.close()


def _next_stream_event(stream: Any) -> Optional[HermesStreamEvent]:
    try:
        return next(stream)
    except StopIteration:
        return None


def _store_result(query_text: str, result: HermesResult, status: str | None = None) -> Dict[str, Any]:
    if status is None:
        status = "success" if result.return_code == 0 and not result.timed_out and result.stdout.strip() else "error"

    if status == "success":
        blocks = normalize_output(result.stdout)
    else:
        blocks = error_blocks(_failure_message(result), result.stderr or result.stdout)

    return store.insert(
        query=query_text,
        status=status,
        blocks=blocks,
        raw_output=result.stdout,
        stderr=result.stderr,
        duration_ms=result.duration_ms,
    )


def _sse(event: str, data: Dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _failure_message(result: Any) -> str:
    detail = f"{result.stderr}\n{result.stdout}"
    if result.timed_out:
        return "Hermes 流式查询超过了服务端运行上限。请缩小日期范围、减少筛选条件后重试。"
    if "MCP HTTP 504" in detail or "Gateway Time-out" in detail:
        return "飞猪实时查询接口暂时超时。本应用没有切到 direct-flyai 降级路径；请稍后重试，或把城市、日期、直飞/价格条件拆开查询。"
    return "Hermes 查询失败或超时。"


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
