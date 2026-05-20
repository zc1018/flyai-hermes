from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]


@dataclass
class Settings:
    app_password: str
    owner_password: str
    session_secret: str
    hermes_bin: str
    hermes_home: str
    hermes_skill: str
    hermes_provider: str
    hermes_model: str
    hermes_timeout_seconds: int
    secure_cookies: bool
    database_path: Path
    xhs_enabled: bool = False
    mediacrawler_dir: Path = ROOT_DIR.parent / "MediaCrawler"
    xhs_login_type: str = "cookie"
    xhs_cookies: str = ""
    xhs_cache_ttl_hours: int = 12
    xhs_timeout_seconds: int = 45
    xhs_max_results: int = 6
    xhs_max_daily_per_user: int = 10


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def load_settings() -> Settings:
    database = Path(os.getenv("DATABASE_PATH", "data/travel.db"))
    if not database.is_absolute():
        database = ROOT_DIR / database

    return Settings(
        app_password=os.getenv("APP_PASSWORD", ""),
        owner_password=os.getenv("OWNER_PASSWORD", os.getenv("APP_PASSWORD", "")),
        session_secret=os.getenv("SESSION_SECRET", secrets.token_urlsafe(32)),
        hermes_bin=os.getenv("HERMES_BIN", "/Users/xdf/.local/bin/hermes"),
        hermes_home=os.getenv("HERMES_HOME", str(Path.home() / ".hermes")),
        hermes_skill=os.getenv("HERMES_SKILL", "flyai"),
        hermes_provider=os.getenv("HERMES_PROVIDER", "kimi-coding"),
        hermes_model=os.getenv("HERMES_MODEL", "kimi-k2.6"),
        hermes_timeout_seconds=_int_env("HERMES_TIMEOUT_SECONDS", 900),
        secure_cookies=_bool_env("SECURE_COOKIES", False),
        database_path=database,
        xhs_enabled=_bool_env("XHS_ENABLED", False),
        mediacrawler_dir=Path(os.getenv("MEDIACRAWLER_DIR", str(ROOT_DIR.parent / "MediaCrawler"))).expanduser(),
        xhs_login_type=os.getenv("XHS_LOGIN_TYPE", "cookie"),
        xhs_cookies=os.getenv("XHS_COOKIES", ""),
        xhs_cache_ttl_hours=_int_env("XHS_CACHE_TTL_HOURS", 12),
        xhs_timeout_seconds=_int_env("XHS_TIMEOUT_SECONDS", 45),
        xhs_max_results=_int_env("XHS_MAX_RESULTS", 6),
        xhs_max_daily_per_user=_int_env("XHS_MAX_DAILY_PER_USER", 10),
    )
