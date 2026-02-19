# Off-Market 활동 시스템

장이 닫힌 시간(야간/주말)에 데이터 수집, 분석, 학습을 자동 수행하여 다음 장을 대비합니다.

---

## 1. 개요

장 마감 후 6개 작업을 순차 실행합니다:

```
장 마감 감지 → closing_analysis() → _run_offmarket_tasks()
                                        ├─ 1. 📊 일봉 사전 수집
                                        ├─ 2. 📰 뉴스/공시 수집
                                        ├─ 3. 🎯 AI 정확도 추적
                                        ├─ 4. 🔬 기술적 분석 프리로드
                                        ├─ 5. 🌐 글로벌 시장 연동 분석
                                        └─ 6. ⭐ 프리마켓 후보 선별
```

**실행 조건:**
- 모든 시장이 마감된 후 **1회** 자동 실행
- 다음 장 오픈 시 `_offmarket_done` 리셋
- 약 5~10분 소요

---

## 2. 각 기능 상세

### 2.1 📊 일봉 데이터 사전 수집 (`_prefetch_candle_data`)

**목적:** 다음 장 오픈 시 즉시 분석 가능하도록 캔들 데이터를 미리 캐싱

| 항목 | 내용 |
|---|---|
| 소스 | Yahoo Finance API |
| 범위 | 5개국 전체 관심종목 |
| 기간 | 6개월 일봉 |
| 배치 | 5개씩 병렬 수집, 1초 간격 |
| 캐시 | `self._candle_cache[symbol]` |

### 2.2 📰 뉴스/공시 수집 (`_collect_market_news`)

**목적:** 시장별 주요 종목의 등락률 수집 + AI 감성 분석

| 항목 | 내용 |
|---|---|
| 소스 | Yahoo Finance 시세 데이터 |
| 대상 | 시장별 상위 5개 종목 |
| 분석 | 등락률 상위 10개 → Antigravity AI 감성분석 |
| 캐시 | `self._news_cache[]` |

**AI 분석 출력:**
```json
{
    "market_sentiment": "bullish/bearish/neutral",
    "key_factors": ["요인1", "요인2"],
    "tomorrow_outlook": "전망 요약"
}
```

### 2.3 🎯 AI 판단 정확도 추적 (`_track_ai_accuracy`)

**목적:** AI 매수 예측의 실제 성과를 추적하여 정확도를 측정

| 항목 | 내용 |
|---|---|
| 데이터 | `trade_log` 내 BUY 거래 |
| 비교 | 매수가 vs 현재가 (캔들 캐시 기준) |
| 판정 | ✅수익 / ❌손절 / ❌손실 / ⚪보합 |
| 캐시 | `self._ai_stats` |

**판정 기준:**
- `pnl_pct > 0` 또는 목표가 도달 → ✅ 수익
- 손절가 도달 → ❌ 손절
- `pnl_pct < -3%` → ❌ 손실
- `-3% ~ 0%` → ⚪ 보합 (정상 범위)

### 2.4 🔬 기술적 분석 프리로드 (`_preload_technical_analysis`)

**목적:** 지지/저항, 피보나치, 볼린저밴드 등 기술적 수치를 미리 계산

| 지표 | 계산 방법 |
|---|---|
| **이동평균** | MA5, MA20, MA60 (단순 이동평균) |
| **RSI** | 14일 RSI (과매수 70↑, 과매도 30↓) |
| **볼린저밴드** | SMA20 ± 2σ |
| **지지/저항선** | 최근 60일 최고가/최저가 |
| **피보나치** | 0%, 23.6%, 38.2%, 50%, 61.8%, 100% |
| **거래량 비율** | 오늘 거래량 / 20일 평균 |
| **추세** | strong_up / up / neutral / down / strong_down |

**추세 판단 기준:**
```
MA5 > MA20 > MA60  →  strong_up
MA5 > MA20         →  up
MA5 < MA20 < MA60  →  strong_down
MA5 < MA20         →  down
그 외              →  neutral
```

### 2.5 🌐 글로벌 시장 연동 분석 (`_analyze_global_correlation`)

**목적:** 주요 지수 성과를 수집하고 AI가 크로스마켓 영향을 예측

**수집 지수:**

| 심볼 | 지수 |
|---|---|
| `^GSPC` | S&P 500 |
| `^DJI` | Dow Jones |
| `^IXIC` | NASDAQ |
| `^N225` | Nikkei 225 |
| `^KS11` | KOSPI |
| `^HSI` | Hang Seng |
| `000001.SS` | Shanghai |

**AI 분석 출력:**
```json
{
    "us_to_asia_impact": "미국 → 아시아 영향 분석",
    "recommended_markets": ["KR", "JP"],
    "sector_outlook": {"tech": "bullish", "finance": "neutral"},
    "risk_level": "low/medium/high",
    "summary": "종합 전망 요약"
}
```

### 2.6 ⭐ 프리마켓 후보 선별 (`_preselect_candidates`)

**목적:** 기술적 분석 + 글로벌 분석을 종합하여 다음 장 유망 종목 AI 선별

**스코어링 기준:**

| 조건 | 점수 |
|---|---|
| RSI < 35 (과매도) | +30 |
| RSI < 45 (저위) | +15 |
| 볼린저 하단 2% 이내 | +25 |
| 상승 추세 (up/strong_up) | +20 |
| 지지선 3% 이내 | +20 |
| 거래량 1.5배 이상 | +10 |

**선별 흐름:**
1. TA 캐시에서 score ≥ 30 종목 필터
2. 상위 15개 → AI에 전달
3. AI가 글로벌 분석을 고려하여 Top 5 + 진입전략 수립
4. 결과: `self._premarket_picks[]`

---

## 3. API 엔드포인트

### `GET /api/offmarket/status`

```json
{
    "state": {
        "status": "running|done|idle",
        "current_task": "📊 일봉 데이터 사전 수집",
        "progress": 3,
        "last_run": "2026-02-11 17:05:30",
        "tasks": { ... }
    },
    "ai_stats": {
        "total": 10,
        "correct": 7,
        "accuracy": 70.0,
        "details": [ ... ]
    },
    "premarket_picks": [ ... ],
    "global_analysis": { ... },
    "news_count": 25,
    "candle_cache_count": 250,
    "ta_cache_count": 230
}
```

---

## 4. UI 패널

Trading 페이지 상단에 **🌙 OFF-MARKET** 패널이 표시됩니다.

- 장 운영 중: 패널 숨김
- 장 마감 후: 패널 표시
  - 6개 작업 아이콘 (📊📰🎯🔬🌐⭐)
  - 완료된 작업: 녹색, 진행 중: 노란색 (깜빡임)
  - 모두 완료 시: "✅ 완료" 표시

---

## 5. 데이터 캐시 구조

| 캐시 | 타입 | 키 | 내용 |
|---|---|---|---|
| `_candle_cache` | Dict | symbol | 6개월 일봉 + name/market |
| `_news_cache` | List | - | 등락률 + AI 감성분석 |
| `_ai_stats` | Dict | - | 정확도 통계 + 상세 내역 |
| `_ta_cache` | Dict | symbol | MA/RSI/BB/피보나치/추세 |
| `_global_analysis` | Dict | - | 지수 데이터 + AI 분석 |
| `_premarket_picks` | List | - | AI 선별 Top 5 + 전략 |
