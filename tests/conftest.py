"""
BFF 测试公共夹具。

关键约束：
- 环境变量（PORTAL_DEV / DATABASE_URL）必须在首次 import 任何 app 模块之前设置——
  app.core.config.get_settings() 是 lru_cache 单例，且 proxy/database/main 在导入时
  即取值；本文件顶部的 os.environ 设置在 pytest 收集阶段最先执行，满足该顺序。
- 数据库使用 tmp_path 下的独立 sqlite，严禁触碰生产 /data/portal.db。
- 到下游 hermes-trader 的调用由假 httpx.AsyncClient 拦截（见 fake_upstream），
  测试全程不产生任何真实网络请求。
"""
import os
import sys
import tempfile
import uuid

# --- 1) 必须在任何 app.* import 之前完成环境注入 ---
# app.core.database 在模块导入时即按 DATABASE_URL 创建 sqlite 目录，
# 因此默认值也必须指向可写路径（生产默认 sqlite:////data/portal.db 对本机不可写）。
os.environ["PORTAL_DEV"] = "true"
os.environ.setdefault(
    "DATABASE_URL",
    "sqlite:///" + os.path.join(tempfile.mkdtemp(prefix="portal-test-"), "portal.db"),
)

import types  # noqa: E402

import httpx as _real_httpx  # noqa: E402  仅留存引用，import httpx 不触发 app 导入
import pytest  # noqa: E402

# 确保从仓库根目录可直接 import app（.venv 安装时已 site-packages 化，此处理保险）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture()
def db_path(tmp_path, monkeypatch):
    """每个测试用例独立的临时 sqlite；重置 settings 缓存使新 DATABASE_URL 生效。"""
    path = tmp_path / "portal_test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")

    from app.core import config as config_mod
    from app.core import database as db_mod

    config_mod.get_settings.cache_clear()
    db_mod.settings = config_mod.get_settings()
    return path


@pytest.fixture()
async def init_database(db_path):
    """建表 + 幂等种子（admin/admin123、四角色、18 权限、5 推送渠道）。
    引擎是模块级单例，按本用例的临时 URL 重建；结束后释放，避免 sqlite
    连接跨事件循环复用。"""
    from app.core import database as db_mod
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    url = db_mod.settings.database_url
    if url.startswith("sqlite:///") and not url.startswith("sqlite+aiosqlite:///"):
        url = url.replace("sqlite:///", "sqlite+aiosqlite:///", 1)

    db_mod.engine = create_async_engine(
        url, echo=False, connect_args={"check_same_thread": False}
    )
    db_mod.AsyncSessionLocal = async_sessionmaker(
        bind=db_mod.engine, expire_on_commit=False, autoflush=False
    )
    await db_mod.init_db()
    yield
    await db_mod.engine.dispose()


@pytest.fixture()
async def client(init_database):
    """ASGI 测试客户端（不跑 lifespan，init_database 已完成建表种子）。"""
    import httpx
    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _login(client, username: str, password: str) -> str:
    resp = await client.post(
        "/api/portal/auth/login", json={"username": username, "password": password}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


@pytest.fixture()
async def tokens(client):
    """四角色 access token：viewer / trader / operator / admin。
    admin 由种子提供，其余三用户经 admin 登录后调用用户管理接口创建。"""
    admin_tok = await _login(client, "admin", "admin123")
    created = {}
    for role in ("viewer", "trader", "operator"):
        username = f"t_{role}_{uuid.uuid4().hex[:8]}"
        resp = await client.post(
            "/api/portal/users",
            headers={"Authorization": f"Bearer {admin_tok}"},
            json={"username": username, "password": "passw0rd123", "roles": [role]},
        )
        assert resp.status_code == 200, resp.text
        created[role] = username
    return {
        "viewer": await _login(client, created["viewer"], "passw0rd123"),
        "trader": await _login(client, created["trader"], "passw0rd123"),
        "operator": await _login(client, created["operator"], "passw0rd123"),
        "admin": admin_tok,
    }


# --- 下游 hermes-trader 的假传输层（零真实网络） ---

# 记录所有转发请求，供断言凭据剥离/注入：(method, url, headers, params, content)
UPSTREAM_CALLS: list = []


class _FakeUpstreamResponse:
    def __init__(self, status_code: int = 200):
        self.status_code = status_code
        self.content = b'{"ok": true}'
        self.headers = {"content-type": "application/json"}


class _FakeUpstreamAsyncClient:
    """仅实现代理非流式路径用到的接口；任何"发起请求"都只落记录。"""

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def request(self, method, url, headers=None, params=None, content=None):
        UPSTREAM_CALLS.append(
            {
                "method": method,
                "url": url,
                "headers": dict(headers or {}),
                "params": list(params or []),
                "content": content,
            }
        )
        return _FakeUpstreamResponse(200)

    async def stream(self, *args, **kwargs):  # 测试不覆盖 SSE 流式
        raise AssertionError("测试不应走到上游 SSE 流式")

    async def aclose(self):
        pass


@pytest.fixture(autouse=True)
def fake_upstream(monkeypatch):
    """替换 app.routers.proxy 中模块级 httpx.AsyncClient，
    确保对 trader 的代理请求永不触网；每例清空调用记录。"""
    import app.routers.proxy as proxy_mod

    UPSTREAM_CALLS.clear()
    # 只替换 proxy 模块命名空间内的 httpx 绑定（proxy 仅用到 AsyncClient 与
    # RequestError）；切勿 monkeypatch 全局 httpx 模块，否则测试客户端自身的
    # httpx.AsyncClient 也会被掉包成假类。
    monkeypatch.setattr(
        proxy_mod,
        "httpx",
        types.SimpleNamespace(
            AsyncClient=_FakeUpstreamAsyncClient,
            RequestError=_real_httpx.RequestError,
        ),
    )
    yield UPSTREAM_CALLS
    UPSTREAM_CALLS.clear()
