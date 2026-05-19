from __future__ import annotations

import base64
import hashlib
import hmac
import time
from typing import Optional


COOKIE_NAME = "flyai_travel_session"
SESSION_TTL_SECONDS = 60 * 60 * 24 * 7


def create_session_token(secret: str, now: Optional[int] = None) -> str:
    issued_at = int(now if now is not None else time.time())
    payload = str(issued_at).encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).digest()
    token = payload + b"." + base64.urlsafe_b64encode(signature)
    return base64.urlsafe_b64encode(token).decode("ascii")


def verify_session_token(token: str, secret: str, now: Optional[int] = None) -> bool:
    try:
        decoded = base64.urlsafe_b64decode(token.encode("ascii"))
        issued_raw, signature_raw = decoded.split(b".", 1)
        issued_at = int(issued_raw.decode("utf-8"))
        signature = base64.urlsafe_b64decode(signature_raw)
    except (ValueError, TypeError):
        return False

    current = int(now if now is not None else time.time())
    if issued_at > current or current - issued_at > SESSION_TTL_SECONDS:
        return False

    expected = hmac.new(secret.encode("utf-8"), issued_raw, hashlib.sha256).digest()
    return hmac.compare_digest(signature, expected)


def password_matches(provided: str, expected: str) -> bool:
    if not expected:
        return False
    return hmac.compare_digest(provided, expected)

