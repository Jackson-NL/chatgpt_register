"""管理员入口认证。

管理员密匙只用于登录，登录后使用短期 HttpOnly 签名 Cookie。签名票据不依赖
数据库或前端 localStorage，后端重启后仍可验证，但修改密匙会立即使旧票据失效。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from ..config import settings

router = APIRouter()

ADMIN_COOKIE_NAME = "accountops_admin_session"
_FAILED_ATTEMPTS: dict[str, list[float]] = {}


class AdminLoginBody(BaseModel):
    key: str = Field(min_length=1, max_length=512)


def _configured_key() -> str:
    return str(settings.admin_access_key or "").strip()


def _ensure_enabled() -> None:
    if not settings.admin_auth_enabled:
        raise HTTPException(status_code=404, detail="管理员功能已关闭")


def _client_identity(request: Request) -> str:
    # Do not trust X-Forwarded-For here: this is only a local brute-force guard,
    # and accepting a user-controlled header would make it trivial to bypass.
    return str(request.client.host if request.client else "unknown")


def _prune_attempts(now: float) -> None:
    window = max(1, int(settings.admin_login_window_seconds or 300))
    cutoff = now - window
    for client, attempts in list(_FAILED_ATTEMPTS.items()):
        recent = [stamp for stamp in attempts if stamp >= cutoff]
        if recent:
            _FAILED_ATTEMPTS[client] = recent
        else:
            _FAILED_ATTEMPTS.pop(client, None)


def _check_login_rate(request: Request, now: float) -> bool:
    _prune_attempts(now)
    client = _client_identity(request)
    return len(_FAILED_ATTEMPTS.get(client, [])) < max(1, int(settings.admin_login_max_attempts or 8))


def _record_failed_login(request: Request, now: float) -> None:
    client = _client_identity(request)
    _FAILED_ATTEMPTS.setdefault(client, []).append(now)


def _signature(payload: str, key: str) -> str:
    digest = hmac.new(key.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _issue_session(key: str, now: int | None = None) -> tuple[str, int]:
    issued_at = int(time.time() if now is None else now)
    payload = f"{issued_at}.{secrets.token_urlsafe(18)}"
    return f"{payload}.{_signature(payload, key)}", issued_at + max(60, int(settings.admin_session_ttl_seconds or 28800))


def _verify_session(token: str, key: str, now: int | None = None) -> int | None:
    parts = str(token or "").split(".")
    if len(parts) != 3 or not all(parts):
        return None
    try:
        issued_at = int(parts[0])
    except ValueError:
        return None
    current = int(time.time() if now is None else now)
    ttl = max(60, int(settings.admin_session_ttl_seconds or 28800))
    if issued_at > current + 60 or current - issued_at > ttl:
        return None
    expected = _signature(".".join(parts[:2]), key)
    if not hmac.compare_digest(parts[2], expected):
        return None
    return issued_at + ttl


def _cookie_kwargs() -> dict:
    return {
        "key": ADMIN_COOKIE_NAME,
        "httponly": True,
        "samesite": "lax",
        "secure": bool(settings.admin_cookie_secure),
        "path": "/",
    }


def require_admin(request: Request) -> dict:
    _ensure_enabled()
    key = _configured_key()
    if not key:
        raise HTTPException(status_code=503, detail="管理员密匙未配置")
    expires_at = _verify_session(request.cookies.get(ADMIN_COOKIE_NAME, ""), key)
    if expires_at is None:
        raise HTTPException(status_code=401, detail="需要管理员授权")
    return {"expires_at": expires_at}


@router.get("/status")
def admin_status():
    return {"enabled": bool(settings.admin_auth_enabled)}


@router.post("/login")
def admin_login(payload: AdminLoginBody, request: Request, response: Response):
    _ensure_enabled()
    key = _configured_key()
    if not key:
        raise HTTPException(status_code=503, detail="管理员密匙未配置")

    now = time.time()
    if not _check_login_rate(request, now):
        raise HTTPException(status_code=429, detail="尝试次数过多，请稍后再试", headers={"Retry-After": str(settings.admin_login_window_seconds)})
    if not secrets.compare_digest(payload.key.strip(), key):
        _record_failed_login(request, now)
        raise HTTPException(status_code=401, detail="管理员密匙错误")

    _FAILED_ATTEMPTS.pop(_client_identity(request), None)
    token, expires_at = _issue_session(key)
    response.set_cookie(value=token, max_age=max(60, int(settings.admin_session_ttl_seconds or 28800)), **_cookie_kwargs())
    return {
        "authenticated": True,
        "expires_at": datetime.fromtimestamp(expires_at, tz=timezone.utc).isoformat(),
    }


@router.get("/session")
def admin_session(identity: dict = Depends(require_admin)):
    return {
        "authenticated": True,
        "expires_at": datetime.fromtimestamp(identity["expires_at"], tz=timezone.utc).isoformat(),
    }


@router.get("/overview")
def admin_overview(identity: dict = Depends(require_admin)):
    return {
        "authenticated": True,
        "expires_at": datetime.fromtimestamp(identity["expires_at"], tz=timezone.utc).isoformat(),
        "capabilities": ["账号运维", "注册任务", "OAuth 授权", "Sub2API 管理"],
    }


@router.post("/logout")
def admin_logout(response: Response):
    response.delete_cookie(**_cookie_kwargs())
    return {"authenticated": False}
