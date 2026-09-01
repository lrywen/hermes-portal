"""
推送渠道配置路由：
- GET    /push/channels          列出全部渠道（脱敏）
- GET    /push/channels/{ch}     单个渠道详情
- PUT    /push/channels/{ch}     更新渠道配置（含启用开关）
- POST   /push/test              发送测试消息
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.permissions import client_info, get_current_user, require_permissions
from app.models import PushConfig, User
from app.services.audit import record_audit
from app.services.pusher import PushError, dispatch

router = APIRouter(prefix="/api/portal/push", tags=["push"])

ALLOWED_CHANNELS = {"feishu", "dingtalk", "wecom", "telegram", "custom_webhook"}


class PushChannelUpdate(BaseModel):
    enabled: Optional[bool] = None
    name: Optional[str] = Field(default=None, max_length=128)
    config: Optional[Dict[str, Any]] = None


class TestMessage(BaseModel):
    channel: str
    title: str = "Hermes Portal 测试"
    content: str = "✅ 推送渠道连通性测试成功。"


def _validate_config(channel: str, cfg: Dict[str, Any]) -> None:
    """按渠道类型校验必填字段。"""
    if channel in {"feishu", "dingtalk", "wecom", "custom_webhook"}:
        url = cfg.get("webhook_url", "")
        if not url or not url.startswith(("http://", "https://")):
            raise ValueError("webhook_url 必须是有效的 http(s) URL")
    if channel == "telegram":
        if not cfg.get("bot_token"):
            raise ValueError("telegram 渠道需要 bot_token")
        if not cfg.get("chat_id"):
            raise ValueError("telegram 渠道需要 chat_id")
    if channel == "custom_webhook":
        if cfg.get("headers") is not None and not isinstance(cfg["headers"], dict):
            raise ValueError("headers 必须是对象")


@router.get("/channels")
async def list_channels(
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(PushConfig).order_by(PushConfig.channel))
    return [c.to_dict() for c in result.scalars().all()]


@router.put("/channels/{channel}")
async def update_channel(
    channel: str,
    body: PushChannelUpdate,
    request: Request,
    user: User = Depends(require_permissions("push:manage")),
    db: AsyncSession = Depends(get_db),
):
    if channel not in ALLOWED_CHANNELS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"未知渠道：{channel}")
    result = await db.execute(select(PushConfig).where(PushConfig.channel == channel))
    cfg = result.scalar_one_or_none()
    if cfg is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "渠道记录不存在")

    before = cfg.to_dict(mask_secrets=False)
    if body.name is not None:
        cfg.name = body.name.strip() or cfg.name

    new_config = dict(cfg.config or {})
    if body.config is not None:
        # 前端把已脱敏字段传 "******" 表示保留原值
        for key, value in body.config.items():
            if value == "******" and key in new_config:
                continue
            new_config[key] = value
        try:
            _validate_config(channel, new_config)
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
        cfg.config = new_config

    if body.enabled is not None:
        # 启用前必须配置完毕
        if body.enabled:
            try:
                _validate_config(channel, new_config)
            except ValueError as exc:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, f"无法启用：{exc}")
        cfg.enabled = body.enabled

    await record_audit(
        db, actor=user, action="config.push.update", target_type="push_channel",
        target_id=channel, summary=f"更新推送渠道 {channel}",
        before=before, after=cfg.to_dict(mask_secrets=False), **client_info(request),
    )
    await db.commit()
    await db.refresh(cfg)
    return cfg.to_dict()


@router.post("/test")
async def test_channel(
    body: TestMessage,
    request: Request,
    user: User = Depends(require_permissions("push:manage")),
    db: AsyncSession = Depends(get_db),
):
    if body.channel not in ALLOWED_CHANNELS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"未知渠道：{body.channel}")
    result = await db.execute(select(PushConfig).where(PushConfig.channel == body.channel))
    cfg = result.scalar_one_or_none()
    if cfg is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "渠道未配置")
    try:
        await dispatch(
            cfg.channel_type,
            cfg.config or {},
            title=body.title,
            content=body.content,
            level="info",
        )
    except PushError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"推送失败：{exc}")

    await record_audit(
        db, actor=user, action="config.push.test", target_type="push_channel",
        target_id=body.channel, summary=f"测试推送渠道 {body.channel}",
        **client_info(request),
    )
    await db.commit()
    return {"ok": True, "channel": body.channel}
