"""
审计日志服务：记录配置变更与敏感操作。
所有写操作的路由应调用 record_audit() 而非直接操作 ORM。
"""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog, User


# 需要脱敏的字段名（写 before/after 快照时）
_SENSITIVE_KEYS = {"webhook_url", "token", "secret", "api_key", "password", "bot_token", "signing_key"}


def mask_sensitive(data: Any) -> Any:
    """递归将敏感字段值替换为 ******。"""
    if isinstance(data, dict):
        return {
            k: ("******" if k in _SENSITIVE_KEYS and v else mask_sensitive(v))
            for k, v in data.items()
        }
    if isinstance(data, list):
        return [mask_sensitive(v) for v in data]
    return data


async def record_audit(
    db: AsyncSession,
    *,
    actor: Optional[User],
    action: str,
    summary: str = "",
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
    before: Any = None,
    after: Any = None,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> AuditLog:
    """写入一条审计日志并 flush（调用方负责 commit）。"""
    entry = AuditLog(
        actor_id=actor.id if actor else None,
        actor_username=actor.username if actor else "anonymous",
        action=action,
        summary=summary[:255],
        target_type=target_type,
        target_id=target_id,
        before=mask_sensitive(before),
        after=mask_sensitive(after),
        ip=ip,
        user_agent=user_agent,
    )
    db.add(entry)
    await db.flush()
    return entry
