#!/bin/bash
# 시세 수집만 실행

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"
source venv/bin/activate 2>/dev/null || (python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt --quiet)

cd src/ai
python main.py --mode collect
