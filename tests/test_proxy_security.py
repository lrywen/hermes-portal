"""
代理链凭据安全测试：
- 客户端传入的 Authorization / X-Operator-Token / X-Internal-Token 在转发上游前
  必须被剥离，改由 BFF 注入服务端凭据（X-Operator-Token/X-Internal-Token/X-Portal-User）；
- SSE 短时票据（?ticket=）可完成认证，但票据/历史 ?token= 不得作为 query 转发上游；
- 历史 ?token=<access> 已不再被接受（401）；access token 不能冒充 sse-ticket（401）。
"""


async def test_client_credentials_stripped_and_injected(client, tokens, fake_upstream):
    token = tokens["trader"]
    resp = await client.get(
        "/api/portal/trader/api/dashboard/summary",
        headers={
            "Authorization": f"Bearer {token}",
            # 客户端伪造的服务间凭据，必须被剥掉
            "X-Operator-Token": "forged-operator",
            "X-Internal-Token": "forged-internal",
        },
    )
    assert resp.status_code == 200, resp.text
    assert len(fake_upstream) == 1
    fwd = fake_upstream[0]
    fwd_headers = {k.lower(): v for k, v in fwd["headers"].items()}
    # 客户端 JWT 不得到达上游
    assert "authorization" not in fwd_headers
    # 伪造值不得透传
    assert fwd_headers.get("x-operator-token") != "forged-operator"
    assert fwd_headers.get("x-internal-token") != "forged-internal"
    # BFF 注入服务间凭据与用户标识
    assert "x-operator-token" in fwd_headers
    assert "x-internal-token" in fwd_headers
    assert fwd_headers["x-portal-user"], "应注入 X-Portal-User"


async def test_sse_ticket_accepted_and_stripped_from_query(client, tokens, fake_upstream):
    """?ticket=<sse-ticket> 可鉴权，且 ticket 不转发到上游 query。"""
    token = tokens["viewer"]
    ticket_resp = await client.get(
        "/api/portal/auth/sse-ticket",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert ticket_resp.status_code == 200, ticket_resp.text
    ticket = ticket_resp.json()["ticket"]
    assert ticket_resp.json()["expires_in"] > 0

    resp = await client.get(
        f"/api/portal/trader/api/dashboard/summary?ticket={ticket}",
    )
    assert resp.status_code == 200, resp.text
    fwd_params = fake_upstream[-1]["params"]
    param_keys = {k for k, _ in fwd_params}
    assert "ticket" not in param_keys
    assert "token" not in param_keys


async def test_legacy_token_query_rejected(client, tokens, fake_upstream):
    """历史 ?token=<access> 不再被接受 → 401，且不触达上游。"""
    token = tokens["viewer"]
    resp = await client.get(
        f"/api/portal/trader/api/dashboard/summary?token={token}",
    )
    assert resp.status_code == 401
    assert len(fake_upstream) == 0


async def test_access_token_as_ticket_rejected(client, tokens, fake_upstream):
    """access token 类型不匹配 sse-ticket → 401。"""
    token = tokens["viewer"]
    resp = await client.get(
        f"/api/portal/trader/api/dashboard/summary?ticket={token}",
    )
    assert resp.status_code == 401
    assert len(fake_upstream) == 0
