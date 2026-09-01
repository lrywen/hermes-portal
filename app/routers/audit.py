"""
审计日志查询路由：
- GET /audit/logs   分页查询，支持按 action / actor / 时间范围过滤
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.permissions import require_permissions
from app.models import AuditLog, User

router = APIRouter(prefix="/api/portal/audit", tags=["audit"])


@router.get("/logs")
async def list_logs(
    action: Optional[str] = Query(default=None, description="按 action 前缀过滤"),
    actor: Optional[str] = Query(default=None, description="按操作者用户名过滤"),
    target_type: Optional[str] = Query(default=None),
    days: int = Query(default=7, ge=1, le=90, description="查询最近 N 天"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    _: User = Depends(require_permissions("admin:audit")),
    db: AsyncSession = Depends(get_db),
):
    since = datetime.now(timezone.utc) - timedelta(days=days)
    stmt = select(AuditLog).where(AuditLog.created_at >= since)
    if action:
        stmt = stmt.where(AuditLog.action.like(f"{action}%"))
    if actor:
        stmt = stmt.where(AuditLog.actor_username == actor)
    if target_type:
        stmt = stmt.where(AuditLog.target_type == target_type)

    count_stmt = select(AuditLog).where(AuditLog.created_at >= since)
    # 简化：用 Python 侧分页，避免不同数据库的 count 方言差异
    result = await db.execute(stmt.order_by(desc(AuditLog.created_at)))
    all_rows = result.scalars().all()
    total = len(all_rows)
    start = (page - 1) * page_size
    rows = all_rows[start : start + page_size]
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [r.to_dict() for r in rows],
    }
