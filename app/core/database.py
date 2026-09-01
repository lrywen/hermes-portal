"""
SQLAlchemy 异步数据库引擎与会话。
默认 SQLite；切换 PostgreSQL 只需改 DATABASE_URL。
"""
from __future__ import annotations

import os
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings

settings = get_settings()

# SQLite 需要 aiossqlite；postgres 需要 asyncpg
_url = settings.database_url
if _url.startswith("sqlite:///") and not _url.startswith("sqlite+aiosqlite:///"):
    _url = _url.replace("sqlite:///", "sqlite+aiosqlite:///", 1)

# 确保数据目录存在（SQLite 文件路径）
if _url.startswith("sqlite"):
    db_path = _url.split("///")[-1]
    if db_path and db_path != ":memory:":
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)

engine = create_async_engine(
    _url,
    echo=settings.debug,
    future=True,
    connect_args={"check_same_thread": False} if _url.startswith("sqlite") else {},
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
)


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 依赖：每请求一个会话，请求结束自动关闭。"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


async def init_db() -> None:
    """启动时建表并写入种子数据（默认管理员、内置角色权限）。"""
    from app.core import seed  # noqa: WPS433（延迟导入避免循环）
    from sqlalchemy import text  # noqa: WPS433

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # 轻量迁移：为已有 alert_configs 表补充 event_voices 列（新增字段，旧库缺失）
        if _url.startswith("sqlite"):
            cols = (await conn.execute(text("PRAGMA table_info(alert_configs)"))).fetchall()
            existing = {row[1] for row in cols}
            if "event_voices" not in existing:
                await conn.execute(text(
                    "ALTER TABLE alert_configs ADD COLUMN event_voices JSON NOT NULL DEFAULT '{}'"
                ))
    async with AsyncSessionLocal() as session:
        await seed.run_seed(session)
        await session.commit()
