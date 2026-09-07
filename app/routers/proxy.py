"""
下游服务反向代理路由。
BFF 校验 JWT 与 RBAC 后，将请求转发到 hermes-trader，
并注入内部 X-Internal-Token，下游无需实现 JWT 验证。

SSE 流式接口透传 Transfer-Encoding: chunked，关闭缓冲。
"""
from __future__ import annotations

import logging
import time

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.permissions import (
    _user_permissions,
    get_current_user,
    get_stream_user,
)
from app.models import User

# 认证凭据类 query 参数：仅用于 BFF 自身鉴权（SSE 票据/历史 token），
# 转发到上游 trader 前必须剥离——上游凭据由 BFF 注入的 X-Operator-Token 头承载，
# JWT/票据不应进入上游服务的访问日志。
_SENSITIVE_QUERY_PARAMS = frozenset({"token", "ticket"})


def _forward_query_params(request: Request) -> list[tuple[str, str]]:
    return [
        (k, v)
        for k, v in request.query_params.multi_items()
        if k.lower() not in _SENSITIVE_QUERY_PARAMS
    ]


def _redact_query(query: str) -> str:
    """日志用：脱敏 query 中的凭据参数，避免 access token / SSE 票据落日志。"""
    if not query:
        return "-"
    redacted: list[str] = []
    for pair in query.split("&"):
        key = pair.split("=", 1)[0]
        redacted.append(f"{key}=***" if key.lower() in _SENSITIVE_QUERY_PARAMS else pair)
    return "&".join(redacted)

logger = logging.getLogger("portal.proxy")

router = APIRouter(prefix="/api/portal", tags=["proxy"])
settings = get_settings()

# 权限映射：upstream 路径前缀 → (读权限, 写权限)。
# upstream_path 形如 "/api/dashboard/operator/terminal"（不含 "/api/portal/trader"
# 路由前缀）——旧表白名单误带 "/trader/" 前缀，startswith 永不命中，导致 RBAC 层
# 对整条 trader 代理链失效。
# 权限值含义：None=登录即可；False=该方法一律拒绝（fail-closed）。
# 顺序敏感：更具体的前缀必须排在前面。
_DENY = False
_PATH_RULES: list[tuple[str, "str | None", "str | bool"]] = [
    # ---- 运维台（最高危）----
    ("/api/dashboard/operator/terminal", None, "operator:terminal"),  # 终端命令（含 kill 全平），仅 admin
    ("/api/dashboard/operator/feed-ticket", "operator:mode", "operator:mode"),
    ("/api/dashboard/operator/close", None, "trade:close"),
    ("/api/dashboard/operator/mode", None, "operator:mode"),
    ("/api/dashboard/operator/trackers", "operator:mode", "operator:mode"),
    ("/api/dashboard/operator/config", "operator:mode", "operator:mode"),
    # ---- 配置管理 ----
    ("/api/dashboard/config/backup", "config:read", "config:write"),
    ("/api/dashboard/config/rollback", None, "config:write"),
    ("/api/dashboard/config/history", "config:read", None),
    ("/api/dashboard/config/schema", "config:read", None),
    ("/api/dashboard/config", None, "config:write"),  # GET 为公开投影配置
    # ---- 公开行情/仪表盘只读 ----
    ("/api/dashboard/summary", None, _DENY),
    ("/api/dashboard/positions", None, _DENY),
    ("/api/dashboard/equity-curve", None, _DENY),
    ("/api/dashboard/closed-trades", None, _DENY),
    ("/api/dashboard/risk-status", "dashboard:read", _DENY),  # 风控三卡（熔断/日亏闸/馈送健康度）
    # Audit 2026-09-07 (M2): shadow risk-arm grading center. Grades contain gate
    # posture (off/shadow/enforce) and blind-gap signals -> operator-only read;
    # refresh is a manual recompute (write) -> operator:mode. PROMOTE stays a
    # suggestion; no gate/config mutation happens through these endpoints.
    ("/api/dashboard/shadow-arms", "operator:mode", "operator:mode"),
    ("/api/dashboard/shadow/", "shadow:read", "shadow:manage"),  # SHADOW 影子账本（读=模拟数据查看，写=重置/手动平仓）
    ("/api/feed/", None, _DENY),
    # ---- 交易/账户写操作 ----
    ("/api/hl/place-order", None, "trade:execute"),
    ("/api/hl/close-position", None, "trade:close"),
    ("/api/hl/cancel-order", None, "trade:execute"),
    ("/api/hl/account", "positions:read", _DENY),
    ("/api/hl/portfolio", "positions:read", _DENY),
    ("/api/hl/", None, _DENY),  # all-mids/universe/price/candles/orderbook 公开只读
    # ---- 智能体 ----
    ("/api/agent/execute", None, "trade:execute"),
    ("/api/agent/scan", None, "agent:control"),
    ("/api/agent/start", "dashboard:read", "agent:control"),
    ("/api/agent/stop", None, "agent:control"),
    ("/api/agent/research", None, "agent:research"),
    ("/api/risk/review", None, "agent:control"),
    ("/api/agent/state", "dashboard:read", _DENY),
    ("/api/agent/trades", "trades:read", _DENY),
    ("/api/agent/session-log", "dashboard:read", _DENY),
    ("/api/agent/config", "config:read", "config:write"),
    # ---- 运维/监控（不经 BFF 暴露）----
    ("/metrics", _DENY, _DENY),
    ("/api/postmortems", "postmortems:read", _DENY),  # trader 实际路径带 /api 前缀
    ("/api/health", None, _DENY),
]

# 流式路径（SSE）
_STREAM_PATHS = ("/stream",)

_READ_METHODS = {"GET", "HEAD", "OPTIONS"}


def _required_permission(path: str, method: str) -> "str | None | bool":
    """返回所需权限码；None=登录即可；False=一律拒绝（fail-closed）。"""
    is_read = method.upper() in _READ_METHODS
    for prefix, read_perm, write_perm in _PATH_RULES:
        if path.startswith(prefix):
            return read_perm if is_read else write_perm
    # 未登记路径：读放行（登录即可，下游多为公开数据），写默认拒绝
    return None if is_read else _DENY


async def _proxy_request(
    request: Request,
    target_base: str,
    upstream_path: str,
    user: User,
    db: AsyncSession,
) -> Response:
    """通用代理：转发 method/headers/body，注入内部 token。"""
    started = time.perf_counter()
    # 只取路径部分（去掉 query string）做权限匹配
    perm_path = upstream_path.split("?", 1)[0]
    perm = _required_permission(perm_path, request.method)
    logger.info(
        "[proxy] >> %s %s user=%s perm=%s stream=%s target=%s",
        request.method,
        upstream_path,
        user.username,
        "(deny)" if perm is False else (perm or "(none)"),
        any(p in upstream_path for p in _STREAM_PATHS),
        target_base,
    )
    if perm is False:
        # fail-closed：该方法未被白名单显式放行（含未登记路径的写操作）
        logger.warning(
            "[proxy] 403(deny) %s %s user=%s path not allowed",
            request.method, upstream_path, user.username,
        )
        raise HTTPException(403, "该操作未被授权或未登记，已拒绝")
    if perm and perm not in _user_permissions(user):
        logger.warning(
            "[proxy] 403 %s %s user=%s missing=%s",
            request.method, upstream_path, user.username, perm,
        )
        raise HTTPException(403, f"权限不足，缺少：{perm}")

    url = f"{target_base}{upstream_path}"
    # 移除 hop-by-hop 头（同时移除客户端传入的 Authorization/X-Operator-Token，
    # 由 BFF 统一注入服务间信任凭据，避免伪造）
    headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in {
            "host", "authorization", "content-length", "connection",
            "x-operator-token", "x-internal-token",
        }
    }
    # hermes-trader 校验 X-Operator-Token（兼容 Authorization: Bearer）；
    # 同时发送 X-Internal-Token 以便未来其它下游识别。
    headers["X-Operator-Token"] = settings.trader_operator_token
    headers["X-Internal-Token"] = settings.internal_token
    headers["X-Portal-User"] = user.username

    is_stream = any(p in upstream_path for p in _STREAM_PATHS)
    body = await request.body()

    # SSE / 长连接：使用流式转发，避免缓冲整条响应
    if is_stream:
        logger.info("[proxy] stream open %s (upstream=%s)", upstream_path, url)
        client_kwargs = dict(timeout=None, follow_redirects=True)
        request_kwargs = dict(
            content=body,
            params=_forward_query_params(request),
        )

        async def _iter_stream():
            try:
                async with httpx.AsyncClient(**client_kwargs) as client:
                    async with client.stream(
                        request.method, url, headers=headers, **request_kwargs
                    ) as upstream:
                        logger.info(
                            "[proxy] stream upstream %s status=%s",
                            upstream_path, upstream.status_code,
                        )
                        async for chunk in upstream.aiter_raw():
                            yield chunk
            except Exception:
                logger.exception("[proxy] stream error %s", upstream_path)
                raise
            finally:
                logger.info(
                    "[proxy] stream closed %s elapsed=%.3fs",
                    upstream_path, time.perf_counter() - started,
                )

        response_headers = {
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        }
        return StreamingResponse(
            _iter_stream(),
            status_code=200,
            media_type="text/event-stream",
            headers=response_headers,
        )

    # 普通短请求（LLM 研判等调用可能耗时较久，放宽到 180s）
    logger.info(
        "[proxy] -> upstream %s %s body=%db",
        request.method, url, len(body or b""),
    )
    async with httpx.AsyncClient(timeout=180.0, follow_redirects=True) as client:
        try:
            upstream = await client.request(
                request.method,
                url,
                headers=headers,
                params=_forward_query_params(request),
                content=body,
            )
        except httpx.RequestError as exc:
            logger.warning(
                "[proxy] 502 upstream unreachable %s err=%s elapsed=%.3fs",
                url, exc, time.perf_counter() - started,
            )
            raise HTTPException(502, f"上游服务不可达：{exc}") from exc

    elapsed = time.perf_counter() - started
    logger.info(
        "[proxy] <- %s %s status=%d resp=%dB elapsed=%.3fs",
        request.method, upstream_path, upstream.status_code,
        len(upstream.content), elapsed,
    )

    response_headers = {
        k: v for k, v in upstream.headers.items()
        if k.lower() not in {"content-encoding", "transfer-encoding", "connection", "content-length"}
    }

    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=response_headers,
        media_type=upstream.headers.get("content-type"),
    )


# ---------- trader 代理 ----------
@router.api_route("/trader/{full_path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def proxy_trader(
    full_path: str,
    request: Request,
    user: User = Depends(get_stream_user),
    db: AsyncSession = Depends(get_db),
):
    # query（含 SSE ticket）只在 BFF 鉴权时使用，转发与日志均不携带凭据原文
    logger.info(
        "[proxy] trader route full_path=%s method=%s query=%s client=%s",
        full_path, request.method, _redact_query(request.url.query),
        request.client.host if request.client else "-",
    )
    return await _proxy_request(request, settings.trader_url, f"/{full_path}", user, db)


# ---------- 菜单 ----------
@router.get("/menu")
async def menu(user: User = Depends(get_current_user)):
    """根据用户权限返回可见菜单。"""
    perms = _user_permissions(user)

    def can(*required: str) -> bool:
        return any(p in perms for p in required)

    items = [
        {"id": "overview", "label": "总览", "path": "/overview", "icon": "LayoutDashboard"},
        {
            "id": "trading",
            "label": "交易管理",
            "icon": "TrendingUp",
            "children": [
                {"id": "positions", "label": "持仓", "path": "/positions", "icon": "Wallet"},
                {"id": "trades", "label": "交易历史", "path": "/trades", "icon": "History"},
                {"id": "shadow-book", "label": "影子账本", "path": "/shadow-book", "icon": "Ghost"},
                {"id": "analysis", "label": "深度分析", "path": "/analysis", "icon": "BarChart3"},
            ],
        },
        {
            "id": "agents",
            "label": "智能体",
            "icon": "Bot",
            "children": [
                {"id": "agents", "label": "编排控制台", "path": "/agents", "icon": "Cpu"},
                {"id": "research", "label": "多视角研判", "path": "/hta-research", "icon": "Brain"},
                {"id": "channels", "label": "渠道消息", "path": "/channels", "icon": "Radio"},
            ],
        },
        {
            "id": "operations",
            "label": "运维管理",
            "icon": "Settings",
            "children": [
                item for item in [
                    {"id": "operator", "label": "操作员控制台", "path": "/operator", "icon": "ShieldAlert"} if can("operator:mode") else None,
                    {"id": "config", "label": "系统配置", "path": "/config", "icon": "Sliders"},
                    {"id": "push", "label": "推送设置", "path": "/push", "icon": "BellRing"},
                    {"id": "alerts", "label": "提醒设置", "path": "/alerts", "icon": "Bell"},
                    {"id": "postmortems", "label": "复盘报告", "path": "/postmortems", "icon": "FileText", "embedded": True},
                ] if item
            ],
        },
    ]
    if can("admin:audit"):
        items.append({
            "id": "system",
            "label": "系统管理",
            "icon": "Lock",
            "children": [
                {"id": "users", "label": "用户管理", "path": "/users", "icon": "Users"},
                {"id": "audit", "label": "审计日志", "path": "/audit", "icon": "ScrollText"},
            ],
        })
    return {"items": items, "user": user.to_dict()}
