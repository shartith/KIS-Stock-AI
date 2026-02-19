# 📈 KIS Stock AI

한국투자증권 Open API + AI 기반 **글로벌 자동 주식 매매 시스템**

5개국(🇰🇷🇺🇸🇯🇵🇨🇳🇭🇰) 주식 시장을 24시간 AI가 모니터링하고 자동 매매를 수행합니다.

---

## 🏗️ 프로젝트 구조

```
KIS-Stock-AI/
├── src/
│   ├── ai/                     # AI 트레이딩 시스템 (20개 모듈)
│   │   ├── main.py             # CLI 수동 모드
│   │   ├── main_auto.py        # 글로벌 자동 매매 메인 루프
│   │   ├── config.py           # 설정 (종목, AI 모드, 경로)
│   │   ├── data_collector.py   # MCP 기반 글로벌 시세 수집
│   │   ├── ai_analyzer.py      # 수치/기술적 분석 (로컬 AI)
│   │   ├── ai_trader.py        # AI 매매 판단 (Antigravity)
│   │   ├── antigravity_client.py # Google AI 클라이언트
│   │   ├── local_llm.py        # 로컬 LLM 클라이언트
│   │   ├── news_collector.py   # 뉴스 수집 & 감성 분석
│   │   ├── trading_engine.py   # 국내/해외 주문 실행
│   │   ├── trading_system.py   # 통합 자동 투자 시스템
│   │   ├── risk_manager.py     # 리스크 관리 & 포지션 사이징
│   │   └── ...                 # 기타 모듈
│   ├── web/                    # 웹 대시보드 (FastAPI)
│   └── api/examples_llm/       # KIS 공식 LLM 예제 코드
├── data/                       # DB & 종목 데이터
├── docs/                       # 문서
├── scripts/                    # 실행 스크립트
├── Dockerfile                  # All-in-One 도커 이미지
├── docker-compose.yml          # Docker 원클릭 실행
└── requirements.txt            # Python 의존성
```

---

## 🤖 AI 모델 아키텍처

역할에 따라 AI 모델을 분리하여 사용합니다:

| 역할 | AI 모델 | 용도 |
|---|---|---|
| **수치/기술적 분석** | 로컬 AI (BitNet 3B) | OHLCV, 지표, 패턴 분석 |
| **매매 판단** | Antigravity (Google AI) | BUY/SELL/HOLD 종합 판단 |
| **뉴스 감성 분석** | Antigravity (Google AI) | 뉴스 NLP, 감성 점수 |
| **시장 리포트** | Antigravity (Google AI) | 종합 분석 리포트 생성 |
| **임베딩** | OpenAI | 벡터 DB용 텍스트 임베딩 |

---

## 🚀 빠른 시작

### 1. Docker (권장)

```bash
# 사전 준비: kis-trade MCP 베이스 이미지 빌드
cd /path/to/kis-trade && docker build -t kis-trade-mcp .

# 환경변수 설정
cp .env.example .env
# .env에 API 키 입력

# 실행
docker compose up -d

# 로그 확인
docker compose logs -f

# 중지
docker compose down
```

### 2. 로컬 실행

```bash
# 가상환경
source venv/bin/activate
pip install -r requirements.txt

# 수동 모드 (CLI)
cd src/ai
python main.py --mode collect              # 시세 수집
python main.py --mode analyze --symbol 005930  # 종목 분석
python main.py --mode report               # 일일 리포트
python main.py --mode monitor --interval 300   # 실시간 모니터링

# 글로벌 자동 매매
python main_auto.py --live    # 실전 투자
python main_auto.py           # 모의 투자 (dry-run)

# PM2 전체 실행
./scripts/start_all.sh
./scripts/stop_all.sh
```

---

## 🔌 포트 구성

| 포트 | 서비스 |
|---|---|
| **80** | 웹 대시보드 (FastAPI) |
| **8000** | 실제 프로그램 (main_auto.py) |
| **8001** | MCP 서버 (kis-trade) |
| **8002** | Local AI (llama-server / BitNet) |

---

## ⚙️ 환경변수 (.env)

```bash
# [필수] 한국투자증권 API
KIS_APP_KEY=your_kis_app_key_here
KIS_SECRET_KEY=your_kis_secret_key_here

# [선택] Antigravity (Google AI) — 뉴스/감성/매매 판단
ANTIGRAVITY_API_KEY=your_google_api_key_here
ANTIGRAVITY_MODEL=gemini-2.0-flash

# [선택] OpenAI (임베딩용)
OPENAI_API_KEY=your_openai_api_key_here

# [선택] Discord 알림
DISCORD_WEBHOOK_URL=your_discord_webhook_url_here
```

---

## 📊 주요 기능

- **글로벌 자동 매매** — 5개국 시장 24시간 스캔 → AI 분석 → 자동 체결
- **AI 종목 분석** — 로컬 LLM (기술적) + Antigravity (종합)
- **뉴스 감성 분석** — 뉴스 수집 → 감성 점수화 → 이상 변동 탐지
- **벡터 DB (RAG)** — 종목 패턴 저장 및 유사 패턴 검색 (ChromaDB)
- **리스크 관리** — 손절/익절/포지션 사이징 자동화
- **웹 대시보드** — 실시간 차트, 포트폴리오, AI 전략 로그
- **Discord 알림** — 매매 체결, 이상 감지 시 실시간 알림

---

## 📌 주의사항

- ⚠️ **실전투자 모드**에서는 실제 매매가 체결됩니다
- 투자 판단은 본인 책임이며, AI 분석은 참고용입니다
- Antigravity API 키가 없으면 OpenClaw 게이트웨이로 fallback됩니다

---

## 🔗 참고

- [한국투자증권 Open API](https://apiportal.koreainvestment.com/)
- [Google AI Studio](https://aistudio.google.com/)
- [ChromaDB](https://www.trychroma.com/)
