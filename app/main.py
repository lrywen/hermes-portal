"""
Hermes Portal BFF 入口。

职责：
1. JWT 认证 + RBAC 权限校验
2. 推送渠道、提醒设置、审计日志 API
3. 反向代理 hermes-trader（注入内部 token）
4. 生产环境托管 Portal 前端静态资源（/portal/）
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.config import get_settings
from app.core.database import init_db
from app.routers import alerts, audit, auth, proxy, push

settings = get_settings()
logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("portal")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动：建表 + 种子；关闭：释放引擎。"""
    logger.info("Initializing database ...")
    await init_db()
    os.makedirs(settings.voice_upload_dir, exist_ok=True)
    logger.info("Hermes Portal BFF ready on %s:%s", settings.host, settings.port)
    yield
    logger.info("Shutting down.")


app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/api/docs" if settings.debug else None,
    redoc_url=None,
)

# CORS（开发环境；生产同源无需）
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 路由注册
app.include_router(auth.router)
app.include_router(push.router)
app.include_router(alerts.router)
app.include_router(audit.router)
app.include_router(proxy.router)


@app.get("/api/health", tags=["meta"])
async def health():
    return {"status": "ok", "service": "hermes-portal", "version": "1.0.0"}


# 前端静态资源（构建产物挂载在 /web-portal）
_WEB_DIST = os.getenv("PORTAL_WEB_DIST", "/app/web-portal")

if os.path.isdir(_WEB_DIST):
    app.mount(
        "/portal",
        StaticFiles(directory=_WEB_DIST, html=True),
        name="portal-web",
    )

    # Vue SPA history mode：非 API 的 404 请求回退到 index.html，由前端路由处理
    @app.exception_handler(StarletteHTTPException)
    async def _spa_fallback(request: Request, exc: StarletteHTTPException):
        if exc.status_code == 404 and not request.url.path.startswith("/api/"):
            index = os.path.join(_WEB_DIST, "index.html")
            if os.path.isfile(index):
                return FileResponse(index)
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
else:
    @app.get("/portal", include_in_schema=False)
    async def _portal_placeholder():
        return JSONResponse(
            {"message": "Portal 前端尚未构建。请在 src/ 执行 npm run build 并挂载到 " + _WEB_DIST},
            status_code=503,
        )
