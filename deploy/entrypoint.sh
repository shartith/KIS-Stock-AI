#!/bin/bash
set -e

echo "🚀 KIS-Stock-AI Starting..."
echo "   Port: ${PORT:-8080}"
echo "   TZ:   ${TZ:-Asia/Seoul}"
echo ""

mkdir -p /app/data /app/logs

# 이전 토큰 파일 삭제 — 시작 시 항상 새 토큰 발급
rm -f /app/src/ai/kis_token.json

cd /app
exec python3 src/web/app.py
