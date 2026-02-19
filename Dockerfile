# ============================================
# KIS-Stock-AI All-in-One Dockerfile
# ============================================
# 베이스: Python 3.10 (Slim)
#
# 포트 구성:
#   8000 — 웹 대시보드 (FastAPI/Uvicorn)
# ============================================

FROM python:3.10-slim

# ==========================
# 1. System Dependencies
# ==========================
USER root
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y \
    curl \
    wget \
    git \
    build-essential \
    nodejs \
    npm \
    procps \
    && rm -rf /var/lib/apt/lists/*

# ==========================
# 2. Install PM2 (Process Manager)
# ==========================
RUN npm install -g pm2

# ==========================
# 3. Setup KIS-Stock-AI Application
# ==========================
WORKDIR /app/kis-stock-ai

# Python Dependencies
COPY requirements.txt .
# pip 업그레이드 및 패키지 설치
RUN pip install --upgrade pip && \
    pip install --no-cache-dir \
    -r requirements.txt \
    fastapi \
    uvicorn \
    jinja2 \
    websockets \
    python-multipart \
    google-generativeai

# Copy source code
COPY . .

# ==========================
# 4. Environment Variables
# ==========================
ENV WEB_PORT=8000
ENV LC_ALL=C.UTF-8
ENV LANG=C.UTF-8

# ==========================
# 5. Expose Ports
# ==========================
EXPOSE 8000

# ==========================
# 6. Entrypoint
# ==========================
COPY docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

ENTRYPOINT ["docker-entrypoint.sh"]
