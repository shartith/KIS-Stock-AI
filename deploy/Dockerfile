# ============================================
# KIS-Stock-AI Production Dockerfile
# ============================================
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

RUN mkdir -p /app/data /app/logs

ENV PYTHONPATH=/app/src:/app/src/ai:/app/src/web
ENV PYTHONUNBUFFERED=1
ENV TZ=Asia/Seoul

EXPOSE 8080 51121-51130

HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=30s \
    CMD curl -f http://localhost:8080/ || exit 1

COPY deploy/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

ENTRYPOINT ["entrypoint.sh"]
