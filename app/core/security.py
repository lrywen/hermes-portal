"""
密码哈希与 JWT 签发/校验工具。
- 密码：bcrypt（passlib）
- Token：JWT（python-jose），含 access / refresh 两类
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from jose import JWTError, jwt

from app.core.config import get_settings

settings = get_settings()

# ---------- 密码 ----------
# 直接使用 bcrypt 库，避免 passlib 1.7.4 与 bcrypt 4.x 的探测 bug
import bcrypt


def hash_password(plain: str) -> str:
    # bcrypt 仅支持前 72 字节，显式截断
    pwd_bytes = plain.encode("utf-8")[:72]
    return bcrypt.hashpw(pwd_bytes, bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        pwd_bytes = plain.encode("utf-8")[:72]
        return bcrypt.checkpw(pwd_bytes, hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# ---------- JWT ----------
def _create_token(subject: str, token_type: str, ttl: timedelta, extra: Optional[Dict[str, Any]] = None) -> str:
    now = datetime.now(timezone.utc)
    payload: Dict[str, Any] = {
        "sub": subject,
        "type": token_type,
        "iat": int(now.timestamp()),
        "exp": int((now + ttl).timestamp()),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_access_token(user_id: str, roles: list[str], permissions: list[str]) -> str:
    return _create_token(
        subject=user_id,
        token_type="access",
        ttl=timedelta(minutes=settings.access_token_ttl_min),
        extra={"roles": roles, "permissions": permissions},
    )


def create_refresh_token(user_id: str) -> str:
    return _create_token(
        subject=user_id,
        token_type="refresh",
        ttl=timedelta(days=settings.refresh_token_ttl_days),
    )


def create_sse_ticket(user_id: str) -> str:
    """SSE 专用短时票据：浏览器原生 EventSource 不能设置 Header，
    只能以 ?ticket= 传递凭据；短 TTL 使其在 URL/日志泄漏后迅速失效。"""
    return _create_token(
        subject=user_id,
        token_type="sse-ticket",
        ttl=timedelta(seconds=settings.sse_ticket_ttl_sec),
    )


def decode_token(token: str) -> Optional[Dict[str, Any]]:
    """解码并校验 JWT；失败返回 None。"""
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError:
        return None
