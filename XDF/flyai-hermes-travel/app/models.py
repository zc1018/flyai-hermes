from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    password: str = Field(min_length=1)


class QueryRequest(BaseModel):
    query: str = Field(min_length=2, max_length=2000)


class QueryResponse(BaseModel):
    id: int
    query: str
    status: str
    blocks: List[Dict[str, Any]]
    raw_output: str
    stderr: str = ""
    duration_ms: int
    created_at: str


class HistoryItem(BaseModel):
    id: int
    query: str
    status: str
    blocks: List[Dict[str, Any]]
    duration_ms: int
    created_at: str


class HealthResponse(BaseModel):
    ok: bool
    hermes_bin: Dict[str, Any]
    hermes_skill: Dict[str, Any]
    hermes_provider: Dict[str, Any]
    flyai_cli: Dict[str, Any]
    database: Dict[str, Any]
    app_password_configured: bool
    runtime_mode: str
    message: Optional[str] = None
