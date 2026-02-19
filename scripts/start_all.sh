#!/bin/bash
# ============================================
# KIS-Stock-AI 전체 자동 실행 스크립트
# 서비스를 PM2로 한번에 실행
# ============================================
# 포트 구성:
#   80   — 웹 대시보드 (FastAPI)
#   8000 — 실제 프로그램 (main_auto.py)
# ============================================

set -e

# 경로 설정
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$PROJECT_DIR/logs"
PYTHON_BIN="$PROJECT_DIR/venv/bin/python3"

echo "🚀 KIS-Stock-AI 전체 시스템 시작..."
echo "📁 프로젝트: $PROJECT_DIR"
echo ""

# ==========================
# 1. 사전 준비
# ==========================

# 로그 디렉토리
mkdir -p "$LOG_DIR"

# 가상환경 확인
if [ ! -d "$PROJECT_DIR/venv" ]; then
    echo "📦 Python 가상환경 생성 중..."
    python3 -m venv "$PROJECT_DIR/venv"
    source "$PROJECT_DIR/venv/bin/activate"
    pip install -r "$PROJECT_DIR/requirements.txt" --quiet
    pip install fastapi uvicorn jinja2 --quiet
else
    source "$PROJECT_DIR/venv/bin/activate"
fi

# 환경변수 로드
if [ -f "$PROJECT_DIR/.env" ]; then
    source "$PROJECT_DIR/.env"
    echo "✅ 환경변수 로드 완료"
fi

# PM2 확인
if ! command -v pm2 &> /dev/null; then
    echo "❌ PM2가 설치되어 있지 않습니다."
    echo "   설치: npm install -g pm2"
    exit 1
fi

# ==========================
# 2. 기존 프로세스 정리
# ==========================
echo "🧹 기존 프로세스 정리..."
pm2 delete kis-stock-ai kis-dashboard 2>/dev/null || true

# ==========================
# 3. 서비스 실행
# ==========================

# [1] 웹 대시보드 (FastAPI) — Port 80
echo "🌐 [1/2] 웹 대시보드 시작 (Port 80)..."
pm2 start "$PYTHON_BIN" --name "kis-dashboard" \
    --output "$LOG_DIR/web_access.log" \
    --error "$LOG_DIR/web_error.log" \
    -- "$PROJECT_DIR/src/web/app.py"

# [2] 자동 매매 프로그램 — Port 8000
echo "🤖 [2/2] 자동 매매 시작 (Port 8000)..."
pm2 start "$PYTHON_BIN" --name "kis-stock-ai" \
    --output "$LOG_DIR/app.log" \
    --error "$LOG_DIR/error.log" \
    --restart-delay 5000 \
    -- "$PROJECT_DIR/src/ai/main_auto.py" --live

# ==========================
# 4. 상태 확인
# ==========================
pm2 save
echo ""
echo "============================================"
echo "✅ KIS-Stock-AI 시스템 시작 완료!"
echo "============================================"
echo ""
pm2 list
echo ""
echo "📌 서비스 포트 구성:"
echo "   🌐 웹 대시보드  : http://localhost:80"
echo "   🤖 자동 매매    : Port 8000"
echo ""
echo "📋 유용한 명령어:"
echo "   pm2 logs              # 전체 로그 실시간"
echo "   pm2 logs kis-stock-ai # AI 매매 로그"
echo "   pm2 monit             # 모니터링 대시보드"
echo "   pm2 restart all       # 전체 재시작"
echo "   ./scripts/stop_all.sh # 전체 중지"
echo "============================================"
