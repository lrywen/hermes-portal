"""
BFF 代理链 RBAC 矩阵测试。

覆盖本轮两处规则修正与既有安全边界：
- GET /api/dashboard/risk-status 需要 dashboard:read（四角色全有，匿名 401）；
- GET /api/postmortems* 被 postmortems:read 规则命中（旧表白名单误带 /trader/
  前缀导致 startswith 永不命中——已修正）；
- POST /api/hl/flatten-all 未登记为白名单写操作 → fail-closed 403（含 admin；
  一键全平仅能经运维台 terminal 路径）；
- operator/terminal 写仅 admin（operator 角色无 operator:terminal）；
- 未登记路径：读登录即放行、写一律 403。

下游 hermes-trader 由 conftest.fake_upstream 拦截，不产生真实网络请求。
"""
import pytest

from app.routers.proxy import _required_permission

ROLES = ("viewer", "trader", "operator", "admin")

# (方法, 上游路径, {角色: 期望状态码})
# 200 = 经 RBAC 放行（假上游固定回 200）；403 = 权限不足/写未登记；
MATRIX = [
    ("GET", "/api/dashboard/risk-status",
     {"viewer": 200, "trader": 200, "operator": 200, "admin": 200}),
    ("GET", "/api/postmortems/list",
     {"viewer": 200, "trader": 200, "operator": 200, "admin": 200}),
    ("GET", "/api/hl/all-mids",
     {"viewer": 200, "trader": 200, "operator": 200, "admin": 200}),
    ("POST", "/api/hl/place-order",
     {"viewer": 403, "trader": 200, "operator": 200, "admin": 200}),
    ("POST", "/api/agent/scan",
     {"viewer": 403, "trader": 403, "operator": 200, "admin": 200}),
    ("POST", "/api/dashboard/operator/mode",
     {"viewer": 403, "trader": 403, "operator": 200, "admin": 200}),
    ("POST", "/api/dashboard/operator/terminal",
     {"viewer": 403, "trader": 403, "operator": 403, "admin": 200}),
    ("PUT", "/api/dashboard/config",
     {"viewer": 403, "trader": 403, "operator": 200, "admin": 200}),
    # 未登记路径：读登录放行、写 fail-closed
    ("GET", "/api/some/new/read/endpoint",
     {"viewer": 200, "trader": 200, "operator": 200, "admin": 200}),
    ("POST", "/api/some/new/write/endpoint",
     {"viewer": 403, "trader": 403, "operator": 403, "admin": 403}),
    # 本轮决策：flatten-all 不开放（fail-closed），admin 走代理链同样 403
    ("POST", "/api/hl/flatten-all",
     {"viewer": 403, "trader": 403, "operator": 403, "admin": 403}),
    # /metrics 上下游监控不经 BFF 暴露
    ("GET", "/metrics",
     {"viewer": 403, "trader": 403, "operator": 403, "admin": 403}),
    # Audit 2026-09-07 (M2): 影子臂评级中心含闸门姿态/blind 信号，读/写均 operator:mode
    ("GET", "/api/dashboard/shadow-arms/grades",
     {"viewer": 403, "trader": 403, "operator": 200, "admin": 200}),
    ("GET", "/api/dashboard/shadow-arms/grade-history?days=30",
     {"viewer": 403, "trader": 403, "operator": 200, "admin": 200}),
    ("POST", "/api/dashboard/shadow-arms/refresh",
     {"viewer": 403, "trader": 403, "operator": 200, "admin": 200}),
    # 影子账本（shadow/，连字符不同前缀）不受 shadow-arms 规则影响，仍四角色可读
    ("GET", "/api/dashboard/shadow/book",
     {"viewer": 200, "trader": 200, "operator": 200, "admin": 200}),
    ("POST", "/api/dashboard/shadow/reset",
     {"viewer": 403, "trader": 403, "operator": 200, "admin": 200}),
]


def _proxy_url(upstream_path: str) -> str:
    return f"/api/portal/trader{upstream_path}"


async def test_rbac_matrix(client, tokens):
    for method, path, expected in MATRIX:
        for role in ROLES:
            resp = await client.request(
                method,
                _proxy_url(path),
                headers={"Authorization": f"Bearer {tokens[role]}"},
            )
            assert resp.status_code == expected[role], (
                f"{method} {path} 角色={role} 期望 {expected[role]} 实得 {resp.status_code}："
                f"{resp.text[:200]}"
            )


async def test_proxy_requires_auth(client):
    """匿名访问受保护代理链 → 401（认证在 RBAC 之前）。"""
    resp = await client.get(_proxy_url("/api/dashboard/risk-status"))
    assert resp.status_code == 401
    resp = await client.post(_proxy_url("/api/hl/place-order"))
    assert resp.status_code == 401


def test_required_permission_rules_unit():
    """权限规则表的直接单元断言（不依赖 DB/网络），锁死本轮修正点。"""
    # risk-status 显式读规则
    assert _required_permission("/api/dashboard/risk-status", "GET") == "dashboard:read"
    assert _required_permission("/api/dashboard/risk-status", "POST") is False
    # postmortems 前缀规则正确命中（旧 bug：前缀误带 /trader/ 永不命中）
    assert _required_permission("/api/postmortems/list", "GET") == "postmortems:read"
    assert _required_permission("/api/postmortems/list", "POST") is False
    # operator/terminal 写仅 operator:terminal（只授给 admin）
    assert _required_permission("/api/dashboard/operator/terminal", "POST") == "operator:terminal"
    # flatten-all 未登记写 → fail-closed
    assert _required_permission("/api/hl/flatten-all", "POST") is False
    # /api/hl/ 读兜底为登录即可
    assert _required_permission("/api/hl/all-mids", "GET") is None
    # 未登记写默认拒绝、未登记读登录放行
    assert _required_permission("/api/totally/new/path", "POST") is False
    assert _required_permission("/api/totally/new/path", "GET") is None
    # Audit 2026-09-07 (M2): 影子臂评级中心读/写均 operator:mode（含闸门姿态，operator-only）
    assert _required_permission("/api/dashboard/shadow-arms/grades", "GET") == "operator:mode"
    assert _required_permission("/api/dashboard/shadow-arms/grade-history", "GET") == "operator:mode"
    assert _required_permission("/api/dashboard/shadow-arms/refresh", "POST") == "operator:mode"
    # 连字符前缀不得误伤影子账本 shadow/（后者读 shadow:read、写 shadow:manage）
    assert _required_permission("/api/dashboard/shadow/book", "GET") == "shadow:read"
    assert _required_permission("/api/dashboard/shadow/reset", "POST") == "shadow:manage"
