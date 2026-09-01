"""
认证链测试：登录失败/成功、refresh Cookie 轮换、sse-ticket 类型、health。
"""
from app.core.security import decode_token


async def test_health(client):
    resp = await client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] in ("ok", "healthy", "up")


async def test_login_wrong_password(client):
    resp = await client.post(
        "/api/portal/auth/login",
        json={"username": "admin", "password": "wrong-password"},
    )
    assert resp.status_code == 401


async def test_login_and_me(client):
    resp = await client.post(
        "/api/portal/auth/login",
        json={"username": "admin", "password": "admin123"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    token = body["access_token"]
    payload = decode_token(token)
    assert payload["type"] == "access"
    # refresh token 只经 httpOnly Cookie 下发，不应出现在响应体
    assert "refresh_token" not in body
    assert "hermes_portal_refresh" in resp.cookies

    me = await client.get(
        "/api/portal/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert me.status_code == 200
    assert me.json()["username"] == "admin"


async def test_refresh_cookie_flow(client):
    """httpOnly Cookie 中的 refresh token 可换取新 access token。"""
    login = await client.post(
        "/api/portal/auth/login",
        json={"username": "admin", "password": "admin123"},
    )
    assert login.status_code == 200

    refreshed = await client.post("/api/portal/auth/refresh")
    assert refreshed.status_code == 200, refreshed.text
    new_token = refreshed.json()["access_token"]
    assert decode_token(new_token)["type"] == "access"
    # 刷新即轮换 refresh Cookie
    assert "hermes_portal_refresh" in refreshed.cookies


async def test_sse_ticket_type(client, tokens):
    resp = await client.get(
        "/api/portal/auth/sse-ticket",
        headers={"Authorization": f"Bearer {tokens['admin']}"},
    )
    assert resp.status_code == 200, resp.text
    ticket = resp.json()["ticket"]
    payload = decode_token(ticket)
    assert payload["type"] == "sse-ticket"
    # SSE 票据生命周期必须远短于 access token（默认 60s vs 15min）
    assert resp.json()["expires_in"] <= 300


async def test_refresh_without_credentials_rejected(client):
    """无 Cookie 无请求体的 refresh → 401。"""
    resp = await client.post("/api/portal/auth/refresh")
    assert resp.status_code == 401
