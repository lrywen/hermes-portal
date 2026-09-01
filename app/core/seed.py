"""
数据库种子数据：
- 内置角色与权限
- 默认管理员账号（admin / admin123，首次登录后应改密）
- 全局提醒默认配置
- 内置推送渠道占位
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import PERMISSION_ROLES
from app.core.security import hash_password
from app.models import AlertConfig, Permission, PushConfig, Role, User

# 角色元数据：code → 显示名
ROLE_META = {
    "viewer": "只读访客",
    "trader": "交易员",
    "operator": "运维员",
    "admin": "管理员",
}

# 权限元数据：code → (显示名, 分类, 描述)
PERMISSION_META = {
    "dashboard:read": ("仪表盘查看", "dashboard", "查看总览/持仓/交易/分析"),
    "trades:read": ("交易记录查看", "trading", "查看交易历史"),
    "positions:read": ("持仓查看", "trading", "查看当前持仓"),
    "analysis:read": ("分析查看", "trading", "查看深度分析"),
    "channels:read": ("渠道消息查看", "agents", "查看渠道事件流"),
    "postmortems:read": ("复盘报告查看", "operations", "查看复盘报告"),
    "trade:execute": ("下单", "trading", "提交手动交易订单"),
    "trade:close": ("平仓", "trading", "平掉持仓/撤单"),
    "agent:control": ("智能体控制", "agents", "启停自主循环/触发扫描"),
    "agent:research": ("AI 研判", "agents", "触发多视角研判"),
    "config:read": ("配置查看", "operations", "查看系统配置"),
    "config:write": ("配置修改", "operations", "修改系统配置"),
    "push:manage": ("推送渠道管理", "operations", "增删改推送渠道"),
    "alert:manage": ("提醒设置管理", "operations", "配置弹窗与语音提醒"),
    "operator:mode": ("模式切换", "operations", "切换 LIVE/PAUSED"),
    "operator:terminal": ("终端操作", "operations", "高危终端命令"),
    "admin:users": ("用户管理", "admin", "创建/禁用用户与分配角色"),
    "admin:audit": ("审计日志", "admin", "查看审计日志"),
}

DEFAULT_ALERT_EVENTS = [
    "position_opened",
    "position_closed",
    "order_filled",
    "stop_loss_triggered",
    "risk_alert",
    "system_error",
    "agent_signal",
    "circuit_breaker",
    "mode_changed",
    "feed_status",
]


async def run_seed(db: AsyncSession) -> None:
    """幂等种子初始化。"""
    # 1. 权限
    existing_perms = {p.code: p for p in (await db.execute(select(Permission))).scalars().all()}
    for code, (name, category, desc) in PERMISSION_META.items():
        if code not in existing_perms:
            db.add(Permission(code=code, name=name, category=category, description=desc))

    # 2. 角色
    existing_roles = {r.code: r for r in (await db.execute(select(Role))).scalars().all()}
    for code, label in ROLE_META.items():
        if code not in existing_roles:
            db.add(Role(code=code, name=label, description=f"内置角色：{label}"))

    await db.flush()

    # 重新加载（拿到新建对象的 id）
    all_perms = {p.code: p for p in (await db.execute(select(Permission))).scalars().all()}
    all_roles = {r.code: r for r in (await db.execute(select(Role))).scalars().all()}

    # 3. 角色 ↔ 权限
    for perm_code, role_codes in PERMISSION_ROLES.items():
        perm = all_perms.get(perm_code)
        if perm is None:
            continue
        for role_code in role_codes:
            role = all_roles.get(role_code)
            if role and perm not in role.permissions:
                role.permissions.append(perm)

    # 4. 默认管理员
    admin = (await db.execute(select(User).where(User.username == "admin"))).scalar_one_or_none()
    if admin is None:
        admin = User(
            username="admin",
            display_name="超级管理员",
            password_hash=hash_password("admin123"),
            is_active=True,
        )
        admin.roles = [all_roles["admin"]]
        db.add(admin)

    # 5. 默认提醒配置
    alert = (await db.execute(select(AlertConfig).where(AlertConfig.id == 1))).scalar_one_or_none()
    if alert is None:
        db.add(
            AlertConfig(
                id=1,
                popup_position="bottom-right",
                voice_enabled=True,
                voice_id=None,
                voice_volume=0.8,
                event_types=DEFAULT_ALERT_EVENTS,
                event_voices={},
            )
        )

    # 6. 内置推送渠道占位
    for ch, ch_type, name in [
        ("feishu", "feishu", "飞书机器人"),
        ("dingtalk", "dingtalk", "钉钉机器人"),
        ("wecom", "wecom", "企业微信机器人"),
        ("telegram", "telegram", "Telegram Bot"),
        ("custom_webhook", "custom_webhook", "自定义 Webhook"),
    ]:
        exists = (await db.execute(select(PushConfig).where(PushConfig.channel == ch))).scalar_one_or_none()
        if exists is None:
            db.add(
                PushConfig(
                    channel=ch,
                    channel_type=ch_type,
                    enabled=False,
                    name=name,
                    config={},
                )
            )
