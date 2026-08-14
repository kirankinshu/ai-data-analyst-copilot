"""
auth.py
-------
Minimal per-session token handling for the MVP.

This is intentionally simple: each browser session gets an opaque token
returned at upload time, which the frontend must send back on subsequent
requests. There is no user/password login in v1 (see PRD Section 14 - Open
Questions re: multi-user auth). Swap this module for JWT/OAuth without
touching the rest of the backend when that's needed.
"""

import hashlib
import hmac
import os
import time

SECRET = os.getenv("APP_SECRET", "dev-secret-change-me").encode()


def issue_token(session_id: str) -> str:
    ts = str(int(time.time()))
    sig = hmac.new(SECRET, f"{session_id}:{ts}".encode(), hashlib.sha256).hexdigest()
    return f"{session_id}:{ts}:{sig}"


def verify_token(token: str) -> str | None:
    try:
        session_id, ts, sig = token.split(":")
    except ValueError:
        return None
    expected = hmac.new(SECRET, f"{session_id}:{ts}".encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    return session_id
