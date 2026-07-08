"""Minimal signed-cookie authentication for the single-user Web app."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from typing import Any

from itsdangerous import BadSignature, URLSafeSerializer
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

SESSION_COOKIE = "schedule_session"


def configured_username() -> str:
    return os.getenv("WEB_USERNAME", "admin")


def configured_password_hash() -> str:
    return os.getenv("WEB_PASSWORD_HASH", "")


def configured_secret_key() -> str:
    return os.getenv("WEB_SECRET_KEY", "")


def serializer() -> URLSafeSerializer:
    secret = configured_secret_key()
    if not secret:
        raise RuntimeError("WEB_SECRET_KEY is required.")
    return URLSafeSerializer(secret_key=secret, salt="schedule-web-session")


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a password against sha256 or pbkdf2_sha256 hashes."""

    if not password_hash:
        return False
    if password_hash.startswith("pbkdf2_sha256$"):
        try:
            _, iterations_text, salt, expected = password_hash.split("$", 3)
            iterations = int(iterations_text)
        except ValueError:
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("utf-8"),
            iterations,
        ).hex()
        return secrets.compare_digest(digest, expected)
    digest = hashlib.sha256(password.encode("utf-8")).hexdigest()
    return secrets.compare_digest(digest, password_hash)


def make_password_hash(password: str) -> str:
    salt = secrets.token_hex(16)
    iterations = 260000
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        iterations,
    ).hex()
    return f"pbkdf2_sha256${iterations}${salt}${digest}"


def current_user(request: Request) -> str | None:
    value = request.cookies.get(SESSION_COOKIE)
    if not value:
        return None
    try:
        payload: Any = serializer().loads(value)
    except BadSignature:
        return None
    username = str(payload.get("username") or "")
    return username if username == configured_username() else None


def login_response(username: str, redirect_to: str = "/jobs") -> RedirectResponse:
    response = RedirectResponse(redirect_to, status_code=303)
    value = serializer().dumps({"username": username})
    response.set_cookie(
        SESSION_COOKIE,
        value,
        httponly=True,
        samesite="lax",
        secure=os.getenv("WEB_COOKIE_SECURE", "").lower() in {"1", "true", "yes"},
    )
    return response


def logout_response() -> RedirectResponse:
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response


def require_login(request: Request) -> str | Response:
    username = current_user(request)
    if username:
        return username
    return RedirectResponse("/login", status_code=303)
