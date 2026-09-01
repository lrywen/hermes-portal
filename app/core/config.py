"""
Portal BFF 全局配置。
所有可调参数通过环境变量注入，提供合理默认值便于本地开发。
"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import List

# 内置弱默认值，仅允许在显式开发模式（PORTAL_DEV=true）下使用。
# 生产环境必须通过环境变量注入强随机密钥，否则启动即 fail-closed。
_DEV_JWT_SECRET = "dev-only-change-me-in-prod"
_DEV_INTERNAL_TOKEN = "internal-dev-token"


class Settings:
    """运行期配置（只读单例）。"""

    def __init__(self) -> None:
        # 服务自身
        self.app_name: str = os.getenv("PORTAL_APP_NAME", "Hermes Portal BFF")
        self.host: str = os.getenv("PORTAL_HOST", "0.0.0.0")
        self.port: int = int(os.getenv("PORTAL_PORT", "9000"))
        self.debug: bool = os.getenv("PORTAL_DEBUG", "false").lower() == "true"
        # 显式开发开关：仅本地/CI 开发时置 true，放开弱默认密钥
        self.dev_mode: bool = os.getenv("PORTAL_DEV", "false").lower() == "true"

        # JWT
        self.jwt_secret: str = os.getenv("JWT_SECRET", _DEV_JWT_SECRET)
        self.jwt_algorithm: str = os.getenv("JWT_ALGORITHM", "HS256")
        self.access_token_ttl_min: int = int(os.getenv("ACCESS_TOKEN_TTL_MIN", "15"))
        self.refresh_token_ttl_days: int = int(os.getenv("REFRESH_TOKEN_TTL_DAYS", "7"))
        # SSE 短时票据 TTL（秒）。EventSource 无法携带自定义 Header，只能以
        # ?ticket= 传递凭据；短 TTL 让票据即使出现在网关/上游访问日志中也迅速失效。
        self.sse_ticket_ttl_sec: int = int(os.getenv("SSE_TICKET_TTL_SEC", "60"))

        # refresh token 以 httpOnly Cookie 下发（JS 不可读，XSS 无法窃取）。
        # 生产经 HTTPS 访问时必须置 PORTAL_COOKIE_SECURE=true（Secure 标记）；
        # 本地开发（http://localhost）默认关闭，避免浏览器不下发 Cookie。
        default_cookie_secure = "false" if self.dev_mode else "true"
        self.cookie_secure: bool = os.getenv(
            "PORTAL_COOKIE_SECURE", default_cookie_secure
        ).lower() == "true"

        # 数据库
        self.database_url: str = os.getenv(
            "DATABASE_URL", "sqlite:////data/portal.db"
        )

        # 内部服务鉴权：BFF 转发到下游时注入该 token，下游校验后信任
        self.internal_token: str = os.getenv("INTERNAL_TOKEN", _DEV_INTERNAL_TOKEN)
        # Trader operator token（与 trader 的 HERMES_OPERATOR_TOKEN 一致）。
        # 安全要求：不得与 INTERNAL_TOKEN 复用同一值——两者信任域不同
        # （内部服务间 vs 操作员操作），复用会导致任一泄露即全盘失守。
        self.trader_operator_token: str = os.getenv(
            "HERMES_OPERATOR_TOKEN", ""
        )

        # 下游服务地址
        self.trader_url: str = os.getenv("TRADER_URL", "http://hermes-trader:8000")
        self.llm_url: str = os.getenv("LLM_URL", "http://litellm:4000")
        self.studio_url: str = os.getenv("STUDIO_URL", "http://hermes-studio:6060")

        # CORS（本地开发允许 5173，生产由 Nginx 同源处理）
        cors_raw = os.getenv("PORTAL_CORS_ORIGINS", "http://localhost:5173")
        self.cors_origins: List[str] = [o.strip() for o in cors_raw.split(",") if o.strip()]

        # 推送渠道文件目录（语音上传等）
        self.data_dir: str = os.getenv("PORTAL_DATA_DIR", "/data")
        self.voice_upload_dir: str = os.path.join(self.data_dir, "voices")

        self._validate_secrets()

    def _validate_secrets(self) -> None:
        """生产环境密钥安全校验，不满足即拒绝启动（fail-closed）。"""
        if self.dev_mode:
            # 开发模式：允许弱默认值；operator token 缺省时回退 internal token
            if not self.trader_operator_token:
                self.trader_operator_token = self.internal_token
            return

        problems: list[str] = []

        if not self.jwt_secret or self.jwt_secret == _DEV_JWT_SECRET:
            problems.append(
                "JWT_SECRET 未设置或仍为开发默认值；请用 `openssl rand -hex 32` 生成"
            )
        elif len(self.jwt_secret) < 16:
            problems.append("JWT_SECRET 长度不足（至少 16 字符，建议 32+）")

        if not self.internal_token or self.internal_token == _DEV_INTERNAL_TOKEN:
            problems.append(
                "INTERNAL_TOKEN 未设置或仍为开发默认值；请用 `openssl rand -hex 32` 生成"
            )

        if not self.trader_operator_token:
            problems.append(
                "HERMES_OPERATOR_TOKEN 未设置；它必须与 trader 的 HERMES_OPERATOR_TOKEN 一致"
            )
        elif self.trader_operator_token == self.internal_token:
            problems.append(
                "HERMES_OPERATOR_TOKEN 不得与 INTERNAL_TOKEN 复用同一值（信任域不同）"
            )

        if problems:
            raise RuntimeError(
                "Portal 密钥安全校验失败（生产模式）：\n  - "
                + "\n  - ".join(problems)
                + "\n如确为本地开发，请显式设置 PORTAL_DEV=true。"
            )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """FastAPI 依赖注入用的配置单例。"""
    return Settings()
