from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    password: str = Field(min_length=1)


class QueryRequest(BaseModel):
    query: str = Field(min_length=2, max_length=2000)


class ConversationCreateRequest(BaseModel):
    title: Optional[str] = Field(default=None, max_length=120)


class ConversationMessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


class ConversationSearchRequest(BaseModel):
    query: Optional[str] = Field(default=None, min_length=2, max_length=2000)


class QueryResponse(BaseModel):
    id: int
    user_id: Optional[int] = None
    query: str
    status: str
    blocks: List[Dict[str, Any]]
    raw_output: str
    stderr: str = ""
    duration_ms: int
    created_at: str


class HistoryItem(BaseModel):
    id: int
    user_id: Optional[int] = None
    user_label: Optional[str] = None
    query: str
    status: str
    blocks: List[Dict[str, Any]]
    duration_ms: int
    created_at: str


class ConversationMessageResponse(BaseModel):
    id: int
    conversation_id: int
    role: str
    message_type: str
    content: str
    data: Dict[str, Any]
    created_at: str


class ConversationResponse(BaseModel):
    id: int
    user_id: int
    user_label: Optional[str] = None
    title: str
    status: str
    profile: Dict[str, Any]
    last_query_id: Optional[int] = None
    messages: List[ConversationMessageResponse] = Field(default_factory=list)
    created_at: str
    updated_at: str


class HealthResponse(BaseModel):
    ok: bool
    runtime_mode: str
    message: Optional[str] = None


class UserResponse(BaseModel):
    id: int
    label: str
    role: str
    enabled: bool
    daily_limit: int
    max_concurrent: int
    timeout_seconds: int
    can_view_history: bool
    used_today: int = 0
    created_at: str
    updated_at: str
    last_login_at: Optional[str] = None
    last_query_at: Optional[str] = None


class MeResponse(BaseModel):
    authenticated: bool
    id: int
    label: str
    role: str
    quota: Dict[str, Any]


class CreateUserRequest(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=6, max_length=200)
    daily_limit: int = Field(default=10, ge=0, le=1000)
    max_concurrent: int = Field(default=1, ge=1, le=10)
    timeout_seconds: int = Field(default=300, ge=30, le=900)
    can_view_history: bool = True
    enabled: bool = True


class UpdateUserRequest(BaseModel):
    label: Optional[str] = Field(default=None, min_length=1, max_length=80)
    enabled: Optional[bool] = None
    daily_limit: Optional[int] = Field(default=None, ge=0, le=1000)
    max_concurrent: Optional[int] = Field(default=None, ge=1, le=10)
    timeout_seconds: Optional[int] = Field(default=None, ge=30, le=900)
    can_view_history: Optional[bool] = None


class ResetPasswordRequest(BaseModel):
    password: str = Field(min_length=6, max_length=200)


class XhsConfigRequest(BaseModel):
    enabled: bool
    cookies: Optional[str] = Field(default=None, max_length=30000)
    clear_cookies: bool = False
    timeout_seconds: int = Field(default=45, ge=10, le=180)
    max_results: int = Field(default=6, ge=1, le=12)
    max_daily_per_user: int = Field(default=10, ge=0, le=100)
