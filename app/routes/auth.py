"""
Single-user session auth.

Design notes (this replaces an earlier itsdangerous-based attempt that failed
undiagnosed — this version is stdlib-only and covered by tests):
  - Auth is ENABLED only when AUTH_PASSWORD is set in .env.local. Empty
    password = the app is open (local dev default). No accounts, no usernames.
  - Token: "<expiry_unix>.<hmac_sha256(secret, expiry_unix)>" in an HttpOnly
    cookie. Verification is constant-time; expiry is enforced server-side.
  - GET /auth/status tells the SPA whether auth is enabled and whether the
    current cookie is valid, so the frontend never guesses.
"""

import hashlib
import hmac
import logging
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()

COOKIE_NAME = "docinfo_session"
SESSION_SECONDS = 7 * 24 * 3600  # 7 days


def auth_enabled() -> bool:
    return bool(settings.auth_password)


def _sign(expiry: int) -> str:
    return hmac.new(
        settings.session_secret.encode("utf-8"),
        str(expiry).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _make_token() -> str:
    expiry = int(time.time()) + SESSION_SECONDS
    return f"{expiry}.{_sign(expiry)}"


def is_authenticated(request: Request) -> bool:
    """True when auth is disabled, or the session cookie is valid and unexpired."""
    if not auth_enabled():
        return True
    token = request.cookies.get(COOKIE_NAME, "")
    expiry_str, _, signature = token.partition(".")
    if not expiry_str.isdigit() or not signature:
        return False
    if not hmac.compare_digest(signature, _sign(int(expiry_str))):
        return False
    return int(expiry_str) > time.time()


class LoginBody(BaseModel):
    password: str


@router.get("/auth/status")
def auth_status(request: Request):
    return {
        "enabled": auth_enabled(),
        "authenticated": is_authenticated(request),
    }


@router.post("/login")
def login(body: LoginBody):
    if not auth_enabled():
        return {"ok": True}  # nothing to log into
    if not hmac.compare_digest(body.password, settings.auth_password):
        logger.info("Login rejected: password mismatch")
        raise HTTPException(status_code=401, detail="Incorrect password")
    response = JSONResponse({"ok": True})
    response.set_cookie(
        COOKIE_NAME,
        _make_token(),
        httponly=True,
        samesite="lax",
        max_age=SESSION_SECONDS,
        secure=False,  # set True when served over HTTPS
    )
    return response


@router.post("/logout")
def logout():
    response = JSONResponse({"ok": True})
    response.delete_cookie(COOKIE_NAME)
    return response
