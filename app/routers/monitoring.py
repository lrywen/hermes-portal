"""
系统监控路由：BFF 代查 Prometheus。

- GET /monitoring/query            瞬时查询  ?query=
- GET /monitoring/query_range      区间查询  ?query=&start=&end=&step=

只允许登录且具备 dashboard:read 权限的用户；Prometheus 地址由 BFF 配置，
前端不直接接触 Prometheus（不暴露内网拓扑，也不绕过 JWT/RBAC）。
为防止被当成通用 Prometheus 代理做任意查询，查询表达式必须命中服务端
预注册的查询白名单（精确匹配）。
"""
from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.config import get_settings
from app.core.permissions import get_current_user, require_permissions
from app.models import User

logger = logging.getLogger("portal.monitoring")

router = APIRouter(prefix="/api/portal/monitoring", tags=["monitoring"])
settings = get_settings()

# ── 预注册查询白名单（key -> PromQL）─────────────────────────────────────────
# 前端只传 key；PromQL 在服务端固定，杜绝任意表达式注入 / 越权拉取其他指标。
# 时间窗一律用相对区间（[RANGE] 占位由具体接口的 step/范围控制）。
QUERIES: dict[str, str] = {
    # 总览
    "equity": "hermes_equity_usd",
    "unrealized_pnl": "hermes_unrealized_pnl_usd",
    "open_positions": "hermes_open_positions",
    "open_notional": "hermes_open_notional_usd",
    "drawdown": "hermes_drawdown_fraction",
    "alerts_firing": "hermes_alerts_firing",
    # 执行/决策（区间增量）
    "executor_decisions": "sum by (outcome) (increase(hermes_executor_decisions_total[RANGE]))",
    "executor_entries": "sum by (side) (increase(hermes_executor_entries_total[RANGE]))",
    "risk_gate_blocks": "sum by (gate) (increase(hermes_risk_gate_blocks_total[RANGE]))",
    "dsl_exits": "sum by (reason) (increase(hermes_dsl_exits_total[RANGE]))",
    # LLM
    "llm_requests": "sum by (path, outcome) (increase(hermes_llm_requests_total[RANGE]))",
    "llm_circuit": "hermes_llm_circuit_state",
    "llm_retries": "sum by (cause) (increase(hermes_llm_retries_total[RANGE]))",
    # 行情
    "feed_trustworthy": "hermes_feed_trustworthy",
    "feed_gap": "hermes_feed_gap_fraction",
    # 系统
    "loop_hb_age": "hermes_loop_heartbeat_age_seconds",
    "arm_hb_age": "hermes_arm_heartbeat_age_seconds",
    "rss": "hermes_process_rss_bytes",
    "disk_free": "hermes_data_disk_free_bytes",
}

# 允许的区间占位取值，防止注入非法窗口
_ALLOWED_RANGES = {"5m", "15m", "1h", "6h", "24h"}


def _resolve_promql(key: str, range_s: str | None) -> str:
    if key not in QUERIES:
        raise HTTPException(status_code=400, detail="unknown query key")
    promql = QUERIES[key]
    if "[RANGE]" in promql:
        r = range_s or "1h"
        if r not in _ALLOWED_RANGES:
            raise HTTPException(status_code=400, detail="bad range")
        promql = promql.replace("[RANGE]", f"[{r}]")
    return promql


async def _prom_get(path: str, params: dict) -> dict:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{settings.prometheus_url}{path}", params=params)
    except httpx.HTTPError as exc:
        logger.warning("prometheus upstream error: %s", exc)
        raise HTTPException(status_code=502, detail="prometheus unavailable") from exc
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail="prometheus error")
    body = resp.json()
    if body.get("status") != "success":
        raise HTTPException(status_code=502, detail="prometheus query failed")
    return body.get("data", {})


@router.get("/query")
async def query(
    query_key: str = Query(..., alias="key"),
    range_s: str | None = Query(None, alias="range"),
    user: User = Depends(require_permissions("dashboard:read")),
) -> dict:
    promql = _resolve_promql(query_key, range_s)
    return await _prom_get("/api/v1/query", {"query": promql})


@router.get("/query_range")
async def query_range(
    query_key: str = Query(..., alias="key"),
    start: float = Query(...),
    end: float = Query(...),
    step: str = Query("60s"),
    range_s: str = Query("1h", alias="range"),
    user: User = Depends(require_permissions("dashboard:read")),
) -> dict:
    promql = _resolve_promql(query_key, range_s)
    return await _prom_get(
        "/api/v1/query_range",
        {"query": promql, "start": start, "end": end, "step": step},
    )
