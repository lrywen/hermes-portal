"""
RBAC 与业务配置的 ORM 数据模型。

实体关系：
  User ──< UserRole >── Role ──< RolePermission >── Permission

业务模型：
  PushConfig      推送渠道配置（飞书/钉钉/企业微信/Telegram/自定义 Webhook）
  AlertConfig     全局提醒设置（弹窗位置、语音、事件类型勾选）
  AuditLog        配置变更与敏感操作审计日志
  CustomVoice     自定义上传语音文件元数据
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Table,
)
from sqlalchemy.orm import relationship

from app.core.database import Base


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------- RBAC 关联表 ----------
user_roles = Table(
    "user_roles",
    Base.metadata,
    Column("user_id", String(32), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("role_id", String(32), ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
)

role_permissions = Table(
    "role_permissions",
    Base.metadata,
    Column("role_id", String(32), ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
    Column("permission_id", String(32), ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True),
)


class User(Base):
    """平台用户。"""

    __tablename__ = "users"

    id = Column(String(32), primary_key=True, default=_uuid)
    username = Column(String(64), unique=True, nullable=False, index=True)
    display_name = Column(String(128), nullable=False, default="")
    password_hash = Column(String(255), nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), default=_now, nullable=False)
    last_login_at = Column(DateTime(timezone=True), nullable=True)

    roles = relationship("Role", secondary=user_roles, back_populates="users", lazy="selectin")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "username": self.username,
            "display_name": self.display_name,
            "is_active": self.is_active,
            "roles": [r.code for r in self.roles],
            "permissions": sorted({p.code for r in self.roles for p in r.permissions}),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_login_at": self.last_login_at.isoformat() if self.last_login_at else None,
        }


class Role(Base):
    """角色（viewer/trader/operator/admin）。"""

    __tablename__ = "roles"

    id = Column(String(32), primary_key=True, default=_uuid)
    code = Column(String(64), unique=True, nullable=False, index=True)
    name = Column(String(128), nullable=False)
    description = Column(String(255), default="")

    users = relationship("User", secondary=user_roles, back_populates="roles")
    permissions = relationship(
        "Permission", secondary=role_permissions, back_populates="roles", lazy="selectin"
    )


class Permission(Base):
    """原子权限，形如 trade:execute / config:write。"""

    __tablename__ = "permissions"

    id = Column(String(32), primary_key=True, default=_uuid)
    code = Column(String(64), unique=True, nullable=False, index=True)
    name = Column(String(128), nullable=False)
    description = Column(String(255), default="")
    category = Column(String(32), default="system")

    roles = relationship("Role", secondary=role_permissions, back_populates="permissions")


# ---------- 业务配置 ----------
class PushConfig(Base):
    """推送渠道配置，每个渠道一条记录。"""

    __tablename__ = "push_configs"

    id = Column(String(32), primary_key=True, default=_uuid)
    channel = Column(String(32), unique=True, nullable=False, index=True)
    # 渠道类型：feishu / dingtalk / wecom / telegram / custom_webhook / email
    channel_type = Column(String(32), nullable=False)
    enabled = Column(Boolean, nullable=False, default=False)
    name = Column(String(128), nullable=False, default="")
    # 安全敏感字段（webhook URL、token、secret）以加密 JSON 存储；
    # 此处演示使用明文 JSON，生产环境应接入 app.core.security 做字段级加密
    config = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)

    def to_dict(self, mask_secrets: bool = True) -> dict:
        cfg = dict(self.config or {})
        if mask_secrets:
            for key in ("webhook_url", "token", "secret", "api_key", "bot_token"):
                if key in cfg and cfg[key]:
                    cfg[key] = "******"
        return {
            "id": self.id,
            "channel": self.channel,
            "channel_type": self.channel_type,
            "enabled": self.enabled,
            "name": self.name,
            "config": cfg,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class AlertConfig(Base):
    """全局提醒设置（弹窗位置、语音、事件类型勾选），单行记录 id=1。"""

    __tablename__ = "alert_configs"

    id = Column(Integer, primary_key=True, default=1)
    popup_position = Column(String(16), nullable=False, default="bottom-right")
    # bottom-right / center / top-right / bottom-left
    voice_enabled = Column(Boolean, nullable=False, default=False)
    voice_id = Column(String(32), nullable=True)  # 默认语音为 null（系统 TTS）
    voice_volume = Column(Float, nullable=False, default=0.8)
    # 勾选的事件类型
    event_types = Column(JSON, nullable=False, default=list)
    # 事件类型 → 自定义语音 id 的映射；
    #   key 未出现或值为 undefined：跟随全局 voice_id
    #   值为 null：该事件静默
    #   值为 voice_id 字符串：播放该自定义音频
    event_voices = Column(JSON, nullable=False, default=dict)
    # 事件类型清单：position_opened / position_closed / order_filled /
    #             stop_loss_triggered / risk_alert / system_error /
    #             agent_signal / circuit_breaker / mode_changed
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)

    def to_dict(self) -> dict:
        return {
            "popup_position": self.popup_position,
            "voice_enabled": self.voice_enabled,
            "voice_id": self.voice_id,
            "voice_volume": self.voice_volume,
            "event_types": self.event_types or [],
            "event_voices": self.event_voices or {},
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class CustomVoice(Base):
    """用户上传的自定义语音文件元数据。"""

    __tablename__ = "custom_voices"

    id = Column(String(32), primary_key=True, default=_uuid)
    name = Column(String(128), nullable=False)
    filename = Column(String(255), nullable=False)
    content_type = Column(String(64), nullable=False, default="audio/mpeg")
    size_bytes = Column(Integer, nullable=False, default=0)
    uploaded_by = Column(String(32), ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now, nullable=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "filename": self.filename,
            "content_type": self.content_type,
            "size_bytes": self.size_bytes,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class AuditLog(Base):
    """配置变更与敏感操作审计日志。"""

    __tablename__ = "audit_logs"

    id = Column(String(32), primary_key=True, default=_uuid)
    actor_id = Column(String(32), nullable=True, index=True)
    actor_username = Column(String(64), nullable=True)
    action = Column(String(64), nullable=False, index=True)
    # 如 config.push.update / coin_config.create / auth.login / operator.mode_change
    target_type = Column(String(32), nullable=True)
    target_id = Column(String(64), nullable=True)
    summary = Column(String(255), default="")
    # 变更前/后快照（JSON），敏感字段脱敏
    before = Column(JSON, nullable=True)
    after = Column(JSON, nullable=True)
    ip = Column(String(64), nullable=True)
    user_agent = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now, nullable=False, index=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "actor_id": self.actor_id,
            "actor_username": self.actor_username,
            "action": self.action,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "summary": self.summary,
            "before": self.before,
            "after": self.after,
            "ip": self.ip,
            "user_agent": self.user_agent,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
