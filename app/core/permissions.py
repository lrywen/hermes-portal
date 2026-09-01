"""
JWT 认证与 RBAC 权限校验。

使用方式（FastAPI 依赖注入）：
    from app.core.permissions import get_current_user, require_permissions

    @router.get("/secure")
    async def secure(user: User = Depends(get_current_user)):
        return {"user": user.username}

    @router.post("/trade")
    async def trade(user: User = Depends(require_permissions("trade:execute"))):
        ...
"""
from __future__ import annotations

from typing import Callable, List, Optional, Set

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import decode_token
from app.models import User

# 权限 → 允许角色的映射，也用于种子数据初始化
PERMISSION_ROLES: dict[str, list[str]] = {
    # 仪表盘只读
    "dashboard:read": ["viewer", "trader", "operator", "admin"],
    "trades:read": ["viewer", "trader", "operator", "admin"],
    "positions:read": ["viewer", "trader", "operator", "admin"],
    "analysis:read": ["viewer", "trader", "operator", "admin"],
    "channels:read": ["viewer", "trader", "operator", "admin"],
    "postmortems:read": ["viewer", "trader", "operator", "admin"],
    # 交易操作
    "trade:execute": ["trader", "operator", "admin"],
    "trade:close": ["trader", "operator", "admin"],
    # 智能体
    "agent:control": ["operator", "admin"],
    "agent:research": ["trader", "operator", "admin"],
    # 配置
    "config:read": ["viewer", "trader", "operator", "admin"],
    "config:write": ["operator", "admin"],
    # 推送与提醒设置
    "push:manage": ["operator", "admin"],
    "alert:manage": ["operator", "admin"],
    # 运维台
    "operator:mode": ["operator", "admin"],
    "operator:terminal": ["admin"],
    # 管理后台
    "admin:users": ["admin"],
    "admin:audit": ["admin", "operator"],
}


def _extract_token(authorization: Optional[str]) -> Optional[str]:
    """从 Authorization: Bearer <token> 头中提取 JWT。

    注意：EventSource（SSE）无法携带自定义 Header，其凭据改用
    GET /auth/sse-ticket 换取的短时票据，经 get_stream_user 的 ?ticket= 校验；
    access token 不再允许出现在 URL query 中（会落入链路访问日志）。
    """
    if authorization:
        parts = authorization.split(" ", 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            tok = parts[1].strip()
            if tok:
                return tok
    return None


async def get_current_user(
    authorization: Optional[str] = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    JWT 认证中间件（依赖）。
    仅接受 Header `Authorization: Bearer <access-jwt>`；
    校验 token 签名/过期时间，加载用户实体；失败抛 401。
    """
    token = _extract_token(authorization)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未提供认证令牌",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_token(token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="认证令牌无效或已过期",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="令牌类型错误",
        )

    user = await _load_active_user(db, payload.get("sub"))
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户不存在或已停用",
        )
    return user


async def get_optional_user(
    authorization: Optional[str] = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> Optional[User]:
    """与 get_current_user 相同但不抛异常，用于公开接口区分登录态。"""
    token = _extract_token(authorization)
    if not token:
        return None
    payload = decode_token(token)
    if not payload or payload.get("type") != "access":
        return None
    return await _load_active_user(db, payload.get("sub"))


async def _load_active_user(db: AsyncSession, user_id: Optional[str]) -> Optional[User]:
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    return user if (user is not None and user.is_active) else None


async def get_stream_user(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> User:
    """SSE/代理链路由认证依赖。

    支持两种凭据：
    - ``?ticket=<sse-ticket>``：EventSource 无法设置 Header，前端先用 access
      token 经 GET /auth/sse-ticket 换取 60s 短时票据再连接。票据生命周期极短，
      即使出现在 nginx/BFF/上游访问日志中也很快失效；
    - ``Authorization: Bearer <access>``：fetch/xhr 等普通请求走标准 Header。

    历史上的 ``?token=<access>``（15 分钟有效期，会在整条代理链日志中留存）已不再
    被接受；代理转发到 trader 前也会剥离 token/ticket 查询参数。
    """
    token: Optional[str] = None
    ticket = request.query_params.get("ticket")
    if ticket:
        token = ticket.strip() or None
        expected_type = "sse-ticket"
    else:
        if authorization:
            parts = authorization.split(" ", 1)
            if len(parts) == 2 and parts[0].lower() == "bearer":
                token = parts[1].strip() or None
        expected_type = "access"

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未提供认证凭据（SSE 请先获取 ?ticket= 短时票据）",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_token(token)
    if payload is None or payload.get("type") != expected_type:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="认证凭据无效、类型错误或已过期",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = await _load_active_user(db, payload.get("sub"))
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户不存在或已停用",
        )
    return user


def _user_permissions(user: User) -> Set[str]:
    """聚合用户所有角色下的权限码。"""
    return {perm.code for role in user.roles for perm in role.permissions}


def require_permissions(*required: str) -> Callable[..., object]:
    """
    RBAC 权限校验依赖工厂。
    用法：Depends(require_permissions("trade:execute", "config:write"))
    用户必须拥有列表中**全部**权限才放行（AND 语义）。
    若要任一权限即可，可自行改造 any()。
    """

    async def _checker(user: User = Depends(get_current_user)) -> User:
        owned = _user_permissions(user)
        missing = [perm for perm in required if perm not in owned]
        if missing:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"权限不足，缺少：{', '.join(missing)}",
            )
        return user

    return _checker


def require_roles(*roles: str) -> Callable[..., object]:
    """角色校验依赖工厂（用户必须属于给定角色之一）。"""

    async def _checker(user: User = Depends(get_current_user)) -> User:
        owned = {r.code for r in user.roles}
        if not owned.intersection(roles):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"角色不足，需要：{', '.join(roles)}",
            )
        return user

    return _checker


def client_info(request: Request) -> dict:
    """提取审计用客户端信息。"""
    forwarded = request.headers.get("x-forwarded-for")
    ip = forwarded.split(",")[0].strip() if forwarded else request.client.host if request.client else None
    return {
        "ip": ip,
        "user_agent": request.headers.get("user-agent", "")[:255],
    }
