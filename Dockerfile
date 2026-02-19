# ============================================
# KIS-Stock-AI All-in-One Dockerfile
# ============================================
# 베이스: kis-trade-mcp (Python 3.13 + uv + KIS MCP Server)
#
# 포트 구성:
#   80   — 웹 대시보드 (FastAPI/Uvicorn)
#   8000 — 실제 프로그램 (main_auto.py)
#   8001 — MCP 서버 (kis-trade)
#   8002 — Local AI (llama.cpp / BitNet)
# ============================================

FROM kis-trade-mcp:latest

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
    cmake \
    python3-dev \
    nodejs \
    npm \
    procps \
    && rm -rf /var/lib/apt/lists/*

# ==========================
# 2. Install PM2 (Process Manager)
# ==========================
RUN npm install -g pm2

# ==========================
# 3. Build Llama.cpp (Local AI Server)
# ==========================
WORKDIR /app/llama.cpp
RUN git clone https://github.com/ggerganov/llama.cpp.git . && \
    mkdir build && cd build && \
    cmake .. && \
    cmake --build . --config Release --target llama-server -- -j$(nproc) && \
    cp bin/llama-server /usr/local/bin/

# ==========================
# 4. Setup KIS-Stock-AI Application
# ==========================
WORKDIR /app/kis-stock-ai

# Python Dependencies
COPY requirements.txt .
RUN pip install --break-system-packages --no-cache-dir \
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
# 5. Environment Variables
# ==========================
ENV WEB_PORT=80
ENV MCP_PORT=8001
ENV AI_PORT=8002
ENV APP_PORT=8000
ENV LC_ALL=C.UTF-8
ENV LANG=C.UTF-8

# ==========================
# 6. Expose Ports
# ==========================
EXPOSE 80 8000 8001 8002

# ==========================
# 7. Entrypoint
# ==========================
COPY docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

ENTRYPOINT ["docker-entrypoint.sh"]
