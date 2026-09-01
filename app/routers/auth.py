"""
认证与用户管理路由：
- POST /auth/login           登录，返回 access + refresh token
- POST /auth/refresh         刷新 access token
- GET  /auth/me              当前用户信息
- GET  /users                用户列表（admin）
- POST /users                创建用户（admin）
- PATCH /users/{id}          启用/禁用、改角色、改密（admin）
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.permissions import client_info, get_current_user, require_permissions, require_roles
from app.core.config import get_settings
from app.core.security import (
    create_access_token,
    create_refresh_token,
    create_sse_ticket,
    decode_token,
    hash_password,
    verify_password,
)
from app.models import Role, User
from app.services.audit import record_audit

router = APIRouter(prefix="/api/portal", tags=["auth"])

# refresh token 的 httpOnly Cookie 名。access token 仅驻留浏览器内存（JS 变量），
# 刷新会话用的 refresh token 只经 Cookie 传递：httpOnly 使任何 XSS 脚本都无法
# 读取它，避免 JWT 长期明文存放在 localStorage 中被窃取。
REFRESH_COOKIE_NAME = "hermes_portal_refresh"
# Cookie 限定在认证路径下，减少随其他请求无谓发送的暴露面。
REFRESH_COOKIE_PATH = "/api/portal/auth"


def _set_refresh_cookie(resp: Response, refresh_token: str) -> None:
    """下发 httpOnly refresh Cookie（刷新时轮换，logout 时清除）。"""
    settings = get_settings()
    resp.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=refresh_token,
        max_age=settings.refresh_token_ttl_days * 86400,
        path=REFRESH_COOKIE_PATH,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
    )


def _clear_refresh_cookie(resp: Response) -> None:
    resp.delete_cookie(REFRESH_COOKIE_NAME, path=REFRESH_COOKIE_PATH)


# ---------- 请求/响应模型 ----------
class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)


class RefreshRequest(BaseModel):
    # 兼容旧调用方：refresh token 现以 httpOnly Cookie 传递，请求体不再需要；
    # 保留可选字段仅为向后兼容，前端不再发送。
    refresh_token: Optional[str] = None


class UserCreateRequest(BaseModel):
    username: str = Field(..., min_length=2, max_length=64)
    password: str = Field(..., min_length=6, max_length=128)
    display_name: str = ""
    roles: List[str] = Field(default_factory=lambda: ["viewer"])


class UserUpdateRequest(BaseModel):
    display_name: Optional[str] = None
    password: Optional[str] = Field(default=None, min_length=6, max_length=128)
    roles: Optional[List[str]] = None
    is_active: Optional[bool] = None


# ---------- 认证 ----------
@router.post("/auth/login")
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.username == body.username))
    user = result.scalar_one_or_none()
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户名或密码错误")
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "账号已停用")

    permissions = sorted({p.code for r in user.roles for p in r.permissions})
    roles = [r.code for r in user.roles]

    user.last_login_at = datetime.now(timezone.utc)
    await record_audit(
        db, actor=user, action="auth.login", summary=f"用户 {user.username} 登录",
        **client_info(request),
    )
    await db.commit()

    # refresh token 只经 httpOnly Cookie 下发，不进响应体/JS 可读存储；
    # access token 生命周期短（15 分钟），仅驻留浏览器内存。
    _set_refresh_cookie(response, create_refresh_token(user.id))
    return {
        "access_token": create_access_token(user.id, roles, permissions),
        "token_type": "bearer",
        "user": user.to_dict(),
    }


@router.post("/auth/refresh")
async def refresh_token(
    response: Response,
    request: Request,
    body: Optional[RefreshRequest] = None,
    db: AsyncSession = Depends(get_db),
):
    # 优先取 httpOnly Cookie 中的 refresh token；旧客户端经请求体发送的仍兼容。
    token = request.cookies.get(REFRESH_COOKIE_NAME)
    if not token and body is not None and body.refresh_token:
        token = body.refresh_token
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "缺少 refresh 凭据")

    payload = decode_token(token)
    if payload is None or payload.get("type") != "refresh":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "refresh token 无效或已过期")
    result = await db.execute(select(User).where(User.id == payload.get("sub")))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户不存在或已停用")
    permissions = sorted({p.code for r in user.roles for p in r.permissions})
    roles = [r.code for r in user.roles]
    # 刷新即轮换 refresh Cookie，缩短旧凭据被窃后的可用窗口。
    _set_refresh_cookie(response, create_refresh_token(user.id))
    return {
        "access_token": create_access_token(user.id, roles, permissions),
        "token_type": "bearer",
    }


@router.post("/auth/logout")
async def logout(response: Response):
    """登出：清除 httpOnly refresh Cookie。access token 驻留内存，由前端自行丢弃。"""
    _clear_refresh_cookie(response)
    return {"ok": True}


@router.get("/auth/me")
async def me(user: User = Depends(get_current_user)):
    return user.to_dict()


@router.get("/auth/sse-ticket")
async def sse_ticket(user: User = Depends(get_current_user)):
    """签发 SSE 短时票据（默认 60s）。前端先持 access token 调本端点取票，
    再以 EventSource('...?ticket=<ticket>') 建连，避免 15 分钟 access token
    出现在网关/上游访问日志中。"""
    return {
        "ticket": create_sse_ticket(user.id),
        "expires_in": get_settings().sse_ticket_ttl_sec,
    }


# ---------- 用户管理（admin） ----------
@router.get("/users")
async def list_users(
    _: User = Depends(require_permissions("admin:users")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).order_by(User.created_at.desc()))
    return [u.to_dict() for u in result.scalars().all()]


@router.post("/users")
async def create_user(
    body: UserCreateRequest,
    request: Request,
    admin: User = Depends(require_permissions("admin:users")),
    db: AsyncSession = Depends(get_db),
):
    exists = (await db.execute(select(User).where(User.username == body.username))).scalar_one_or_none()
    if exists:
        raise HTTPException(status.HTTP_409_CONFLICT, "用户名已存在")

    roles_result = await db.execute(select(Role).where(Role.code.in_(body.roles)))
    roles = roles_result.scalars().all()
    if len(roles) != len(set(body.roles)):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "包含未知角色")

    user = User(
        username=body.username,
        display_name=body.display_name or body.username,
        password_hash=hash_password(body.password),
        roles=list(roles),
    )
    db.add(user)
    await db.flush()
    await record_audit(
        db, actor=admin, action="user.create", target_type="user", target_id=user.id,
        summary=f"创建用户 {user.username}", after=user.to_dict(), **client_info(request),
    )
    await db.commit()
    return user.to_dict()


@router.patch("/users/{user_id}")
async def update_user(
    user_id: str,
    body: UserUpdateRequest,
    request: Request,
    admin: User = Depends(require_permissions("admin:users")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "用户不存在")

    before = user.to_dict()
    if body.display_name is not None:
        user.display_name = body.display_name
    if body.password is not None:
        user.password_hash = hash_password(body.password)
    if body.is_active is not None:
        user.is_active = body.is_active
    if body.roles is not None:
        roles = (await db.execute(select(Role).where(Role.code.in_(body.roles)))).scalars().all()
        if len(roles) != len(set(body.roles)):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "包含未知角色")
        user.roles = list(roles)

    await record_audit(
        db, actor=admin, action="user.update", target_type="user", target_id=user.id,
        summary=f"更新用户 {user.username}", before=before, after=user.to_dict(),
        **client_info(request),
    )
    await db.commit()
    return user.to_dict()
