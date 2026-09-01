# (supplemental audit 2026-09-02) 暂时移除 `# syntax=docker/dockerfile:1.6`：
# docker.io 不可达时 BuildKit 会联网拉取该前端镜像导致构建在解析阶段即失败。
# 当前 BuildKit(v0.30) 内置前端已原生支持本文件用到的 --mount=type=cache，
# 无需外部 syntax 前端即可离线构建；网络恢复后可恢复该指令。
# =============================================================================
# Hermes Portal 多阶段构建
#   stage 1 (web-builder) : Node 构建前端静态资源
#   stage 2 (runtime)     : Python 3.11-slim 运行 FastAPI BFF + 托管前端
# 最终镜像仅包含运行所需内容，体积更小、攻击面更窄。
# =============================================================================

# ---------- Stage 1: 前端构建 ----------
FROM node:20-alpine AS web-builder
WORKDIR /web

# 先拷贝依赖清单以利用 Docker 层缓存
COPY package.json package-lock.json* ./
# BuildKit 缓存挂载：npm 全局缓存跨构建复用，依赖未变时秒级命中
RUN --mount=type=cache,target=/root/.npm \
    npm ci --no-audit --no-fund --prefer-offline

# 拷贝源码并构建（输出到 /web/dist）
COPY tsconfig*.json vite.config.ts postcss.config.js tailwind.config.js index.html ./
COPY src ./src
RUN npm run build


# ---------- Stage 2: Python 运行时 ----------
FROM python:3.11-slim AS runtime

# 运行期环境变量（可被 docker-compose / k8s 覆盖）
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=0 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORTAL_HOST=0.0.0.0 \
    PORTAL_PORT=9000 \
    PORTAL_WEB_DIST=/app/web-portal \
    PORTAL_DATA_DIR=/data

WORKDIR /app

# 先装 Python 依赖，利用层缓存；BuildKit 缓存挂载复用 pip wheel 缓存，
# 依赖未变时增量构建跳过下载与编译。无需 curl（健康检查改用 Python 标准库），
# 去掉 apt 安装步骤，缩小镜像体积并减少一层。
COPY requirements.txt ./
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt

# 拷贝后端代码
COPY app ./app

# 拷贝前端构建产物到 BFF 静态托管目录
COPY --from=web-builder /web/dist /app/web-portal

# 数据卷（SQLite、上传语音等）
VOLUME ["/data"]

EXPOSE 9000

# 健康检查用 Python 标准库，无需在镜像内预装 curl
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python3 -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:9000/api/health', timeout=4).status==200 else 1)" || exit 1

# 使用 uvicorn 启动（生产建议前置 Nginx 终止 TLS）
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "9000", "--proxy-headers", "--forwarded-allow-ips=*"]
