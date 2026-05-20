from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any, Dict, Optional


COOKIE_NAME = os.getenv("COOKIE_NAME", "flyai_travel_session")
SESSION_TTL_SECONDS = 60 * 60 * 24 * 7
HASH_ITERATIONS = 260_000


def create_session_token(secret: str, user_id: int, role: str, now: Optional[int] = None) -> str:
    issued_at = int(now if now is not None else time.time())
    payload = json.dumps(
        {"iat": issued_at, "uid": int(user_id), "role": role},
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).digest()
    token = payload + b"." + base64.urlsafe_b64encode(signature)
    return base64.urlsafe_b64encode(token).decode("ascii")


def verify_session_token(token: str, secret: str, now: Optional[int] = None) -> Optional[Dict[str, Any]]:
    try:
        decoded = base64.urlsafe_b64decode(token.encode("ascii"))
        payload_raw, signature_raw = decoded.split(b".", 1)
        payload = json.loads(payload_raw.decode("utf-8"))
        issued_at = int(payload["iat"])
        user_id = int(payload["uid"])
        role = str(payload["role"])
        signature = base64.urlsafe_b64decode(signature_raw)
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None

    current = int(now if now is not None else time.time())
    if issued_at > current or current - issued_at > SESSION_TTL_SECONDS:
        return None

    expected = hmac.new(secret.encode("utf-8"), payload_raw, hashlib.sha256).digest()
    if not hmac.compare_digest(signature, expected):
        return None
    return {"user_id": user_id, "role": role, "issued_at": issued_at}


def password_matches(provided: str, expected: str) -> bool:
    if not expected:
        return False
    return hmac.compare_digest(provided, expected)


def hash_password(password: str) -> str:
    salt = secrets.token_urlsafe(18)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), HASH_ITERATIONS)
    return f"pbkdf2_sha256${HASH_ITERATIONS}${salt}${base64.urlsafe_b64encode(digest).decode('ascii')}"


def verify_password(password: str, password_hash: str) -> bool:
    parts = password_hash.split("$")
    if len(parts) != 4 or parts[0] != "pbkdf2_sha256":
        return password_matches(password, password_hash)
    _, iterations_raw, salt, digest_raw = parts
    try:
        iterations = int(iterations_raw)
        expected = base64.urlsafe_b64decode(digest_raw.encode("ascii"))
    except (ValueError, TypeError):
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations)
    return hmac.compare_digest(actual, expected)
