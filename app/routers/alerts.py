"""
提醒设置路由：
- GET  /alerts/config              获取全局提醒配置（弹窗位置、语音、事件勾选）
- PUT  /alerts/config              更新提醒配置
- GET  /alerts/events              获取支持的事件类型清单
- GET  /alerts/voices              自定义语音列表
- POST /alerts/voices              上传自定义语音文件（mp3/wav/ogg，≤2MB）
- DELETE /alerts/voices/{id}       删除自定义语音
- GET  /alerts/voices/{id}/audio   下载/播放语音文件
"""
from __future__ import annotations

import os
import uuid
from typing import Dict, List, Optional

import aiofiles
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.permissions import client_info, get_current_user, require_permissions
from app.models import AlertConfig, CustomVoice, User
from app.services.audit import record_audit

router = APIRouter(prefix="/api/portal/alerts", tags=["alerts"])
settings = get_settings()

# 支持的事件类型定义
EVENT_CATALOG = [
    {"code": "position_opened", "name": "新开仓", "category": "trade", "voice": True},
    {"code": "position_closed", "name": "平仓完成", "category": "trade", "voice": True},
    {"code": "order_filled", "name": "订单成交", "category": "trade", "voice": True},
    {"code": "stop_loss_triggered", "name": "止损触发", "category": "risk", "voice": True},
    {"code": "risk_alert", "name": "风险告警", "category": "risk", "voice": True},
    {"code": "circuit_breaker", "name": "熔断触发", "category": "risk", "voice": True},
    {"code": "agent_signal", "name": "智能体信号", "category": "agent", "voice": True},
    {"code": "mode_changed", "name": "运行模式变更", "category": "system", "voice": False},
    {"code": "system_error", "name": "系统错误", "category": "system", "voice": True},
    {"code": "feed_status", "name": "行情馈送降级/中断", "category": "risk", "voice": True},
]

ALLOWED_VOICE_TYPES = {
    "audio/mpeg": ".mp3",
    "audio/wav": ".wav",
    "audio/ogg": ".ogg",
    "audio/x-wav": ".wav",
}
MAX_VOICE_BYTES = 2 * 1024 * 1024  # 2 MB


class AlertConfigIn(BaseModel):
    popup_position: str = Field(default="bottom-right")
    voice_enabled: bool = False
    voice_id: Optional[str] = None
    voice_volume: float = Field(default=0.8, ge=0.0, le=1.0)
    event_types: List[str] = Field(default_factory=list)
    # 事件 code → 自定义语音 id；
    #   key 缺省 / 值为 None：跟随全局 voice_id；值为字符串：使用该语音
    event_voices: Dict[str, Optional[str]] = Field(default_factory=dict)


@router.get("/events")
async def list_events(_: User = Depends(get_current_user)):
    return EVENT_CATALOG


@router.get("/config")
async def get_alert_config(
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(AlertConfig).where(AlertConfig.id == 1))
    cfg = result.scalar_one_or_none()
    if cfg is None:
        cfg = AlertConfig(id=1)
        db.add(cfg)
        await db.commit()
        await db.refresh(cfg)
    return cfg.to_dict()


@router.put("/config")
async def update_alert_config(
    body: AlertConfigIn,
    request: Request,
    user: User = Depends(require_permissions("alert:manage")),
    db: AsyncSession = Depends(get_db),
):
    allowed_positions = {"bottom-right", "center", "top-right", "bottom-left"}
    if body.popup_position not in allowed_positions:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"popup_position 非法，允许：{allowed_positions}")

    valid_codes = {e["code"] for e in EVENT_CATALOG}
    invalid = set(body.event_types) - valid_codes
    if invalid:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"未知事件类型：{invalid}")

    # event_voices 的 key 必须是合法事件 code；非空 value 必须对应已存在的语音
    invalid_voice_keys = set(body.event_voices.keys()) - valid_codes
    if invalid_voice_keys:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"event_voices 包含未知事件：{invalid_voice_keys}")
    referenced_voice_ids = {vid for vid in body.event_voices.values() if vid}
    if referenced_voice_ids:
        existing = (
            await db.execute(select(CustomVoice.id).where(CustomVoice.id.in_(referenced_voice_ids)))
        ).scalars().all()
        missing = referenced_voice_ids - set(existing)
        if missing:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"event_voices 引用了不存在的语音：{missing}")

    result = await db.execute(select(AlertConfig).where(AlertConfig.id == 1))
    cfg = result.scalar_one_or_none()
    if cfg is None:
        cfg = AlertConfig(id=1)
        db.add(cfg)

    before = cfg.to_dict()
    cfg.popup_position = body.popup_position
    cfg.voice_enabled = body.voice_enabled
    cfg.voice_id = body.voice_id
    cfg.voice_volume = body.voice_volume
    cfg.event_types = body.event_types
    cfg.event_voices = body.event_voices

    # voice_id 若指定，必须存在
    if body.voice_id:
        voice = (await db.execute(select(CustomVoice).where(CustomVoice.id == body.voice_id))).scalar_one_or_none()
        if voice is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "指定的语音文件不存在")

    await record_audit(
        db, actor=user, action="config.alert.update", target_type="alert_config",
        target_id="1", summary="更新提醒设置", before=before, after=cfg.to_dict(),
        **client_info(request),
    )
    await db.commit()
    await db.refresh(cfg)
    return cfg.to_dict()


@router.get("/voices")
async def list_voices(
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(CustomVoice).order_by(CustomVoice.created_at.desc()))
    voices = result.scalars().all()
    # null id 代表系统默认 TTS 语音
    return [
        {"id": None, "name": "系统默认语音", "is_default": True},
        *[v.to_dict() for v in voices],
    ]


@router.post("/voices")
async def upload_voice(
    request: Request,
    file: UploadFile = File(...),
    name: str = Form(...),
    user: User = Depends(require_permissions("alert:manage")),
    db: AsyncSession = Depends(get_db),
):
    if file.content_type not in ALLOWED_VOICE_TYPES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"仅支持 {list(ALLOWED_VOICE_TYPES.keys())}")

    os.makedirs(settings.voice_upload_dir, exist_ok=True)
    ext = ALLOWED_VOICE_TYPES[file.content_type]
    stored_name = f"{uuid.uuid4().hex}{ext}"
    dest = os.path.join(settings.voice_upload_dir, stored_name)

    size = 0
    async with aiofiles.open(dest, "wb") as out:
        while chunk := await file.read(65536):
            size += len(chunk)
            if size > MAX_VOICE_BYTES:
                await out.close()
                os.remove(dest)
                raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "语音文件不能超过 2MB")
            await out.write(chunk)

    voice = CustomVoice(
        name=name[:128],
        filename=stored_name,
        content_type=file.content_type,
        size_bytes=size,
        uploaded_by=user.id,
    )
    db.add(voice)
    await db.flush()
    await record_audit(
        db, actor=user, action="config.alert.voice_upload", target_type="custom_voice",
        target_id=voice.id, summary=f"上传语音：{name}",
        after={"name": name, "size": size}, **client_info(request),
    )
    await db.commit()
    await db.refresh(voice)
    return voice.to_dict()


@router.delete("/voices/{voice_id}")
async def delete_voice(
    voice_id: str,
    request: Request,
    user: User = Depends(require_permissions("alert:manage")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(CustomVoice).where(CustomVoice.id == voice_id))
    voice = result.scalar_one_or_none()
    if voice is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "语音文件不存在")

    # 如果该语音正被引用，重置为系统默认（全局 + 每个事件映射）
    alert = (await db.execute(select(AlertConfig).where(AlertConfig.id == 1))).scalar_one_or_none()
    if alert:
        if alert.voice_id == voice_id:
            alert.voice_id = None
        if alert.event_voices:
            cleaned = {k: (None if v == voice_id else v) for k, v in alert.event_voices.items()}
            if cleaned != alert.event_voices:
                alert.event_voices = cleaned

    path = os.path.join(settings.voice_upload_dir, voice.filename)
    if os.path.exists(path):
        os.remove(path)

    await db.delete(voice)
    await record_audit(
        db, actor=user, action="config.alert.voice_delete", target_type="custom_voice",
        target_id=voice_id, summary=f"删除语音：{voice.name}", **client_info(request),
    )
    await db.commit()
    return {"ok": True}


@router.get("/voices/{voice_id}/audio")
async def download_voice(
    voice_id: str,
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(CustomVoice).where(CustomVoice.id == voice_id))
    voice = result.scalar_one_or_none()
    if voice is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "语音文件不存在")
    path = os.path.join(settings.voice_upload_dir, voice.filename)
    if not os.path.exists(path):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "语音文件已丢失")
    return FileResponse(path, media_type=voice.content_type, filename=voice.name)
