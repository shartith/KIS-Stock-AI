#!/bin/bash
# ============================================
# KIS-Stock-AI 전체 중지 스크립트
# ============================================

echo "🛑 KIS-Stock-AI 시스템 중지..."

pm2 delete kis-stock-ai 2>/dev/null
pm2 delete kis-dashboard 2>/dev/null

pm2 save

echo "✅ 모든 서비스가 중지되었습니다."
pm2 list
