"""
Web Dashboard Application
FastAPI 기반 주식 대시보드 서버
"""
from fastapi import FastAPI, Request, Body, Query
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
import os
import sys
import json
import asyncio
from datetime import datetime
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pydantic import BaseModel
from typing import Optional

# 상위 경로 추가
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ai.data_collector import StockDataCollector
from ai.database import DatabaseManager
from ai.config import (MARKET_INFO, YAHOO_SUFFIX, KOSDAQ_CODES)

app = FastAPI(title="KIS Stock AI Dashboard")

# 글로벌 실행기 (KIS API 동시 요청용)
executor = ThreadPoolExecutor(max_workers=10)

# 정적 파일 및 템플릿 설정
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

collector = StockDataCollector()
db_manager = DatabaseManager()

# AI 스캐너 엔진 (지연 초기화)
_scanner = None
def get_scanner():
    global _scanner
    if _scanner is None:
        from scanner_engine import ScannerEngine
        _scanner = ScannerEngine(log_fn=ai_log)
    return _scanner

# DB 기본 설정 초기화 (최초 실행 시 .env 값 로드)
db_manager.init_default_settings()

# ==========================
# AI 로그 스트리밍 시스템
# ==========================
_ai_log_buffer = deque(maxlen=200)  # 최근 200개 로그 유지
_ai_log_subscribers = []  # SSE 구독자 목록

def ai_log(level: str, message: str):
    """AI 로그 추가 및 구독자에게 전송"""
    ts = datetime.now().strftime("%H:%M:%S")
    entry = {"time": ts, "level": level, "message": message}
    _ai_log_buffer.append(entry)
    # 구독자에게 전송
    dead = []
    for q in _ai_log_subscribers:
        try:
            q.put_nowait(entry)
        except asyncio.QueueFull:
            dead.append(q)
    for q in dead:
        _ai_log_subscribers.remove(q)

# 국가별 종목 리스트 맵 (MARKET_INFO 기반)
def load_country_stocks():
    """MARKET_INFO에서 지원 국가 목록을 생성"""
    return {code: info for code, info in MARKET_INFO.items()}

COUNTRY_STOCKS = load_country_stocks()

# 학습 상태 글로벌 변수
_training_process = None
_training_status = {"status": "idle", "message": "", "last_run": None}


# ==========================
# 데이터 모델
# ==========================

class SettingsSaveRequest(BaseModel):
    """설정 저장 요청"""
    # KIS API
    kis_app_key: Optional[str] = None
    kis_secret_key: Optional[str] = None
    kis_acct_stock: Optional[str] = None
    # Antigravity
    antigravity_api_key: Optional[str] = None
    antigravity_model: Optional[str] = None
    google_oauth_client_id: Optional[str] = None
    google_oauth_client_secret: Optional[str] = None
    # Discord
    discord_webhook_url: Optional[str] = None
    noti_trade_alerts: Optional[str] = None
    noti_hourly_report: Optional[str] = None
    # AI
    ai_mode: Optional[str] = None
    local_llm_url: Optional[str] = None
    local_llm_model: Optional[str] = None
    # Trading
    allow_leverage: Optional[str] = None
    enable_auto_scan: Optional[str] = None
    enable_auto_buy: Optional[str] = None
    enable_auto_sell: Optional[str] = None
    enable_offmarket: Optional[str] = None
    enable_news_collect: Optional[str] = None

class WebhookTestRequest(BaseModel):
    url: str


# ==========================
# 1. 페이지 라우터 (HTML)
# ==========================

@app.get("/", response_class=HTMLResponse)
async def page_dashboard(request: Request):
    return templates.TemplateResponse("dashboard.html", {"request": request, "page": "dashboard"})

@app.get("/trading", response_class=HTMLResponse)
async def page_trading(request: Request):
    return templates.TemplateResponse("trading.html", {"request": request, "page": "trading"})

@app.get("/portfolio", response_class=HTMLResponse)
async def page_portfolio(request: Request):
    return templates.TemplateResponse("portfolio.html", {"request": request, "page": "portfolio"})

@app.get("/strategy", response_class=HTMLResponse)
async def page_strategy(request: Request):
    return templates.TemplateResponse("strategy.html", {"request": request, "page": "strategy"})

@app.get("/settings", response_class=HTMLResponse)
async def page_settings(request: Request):
    settings = db_manager.get_settings_for_display()
    return templates.TemplateResponse("settings.html", {
        "request": request,
        "page": "settings",
        "settings": settings
    })

@app.get("/ai-strategy", response_class=HTMLResponse)
async def page_ai_strategy(request: Request):
    return templates.TemplateResponse("ai_strategy.html", {"request": request, "page": "backtest"})

# ==========================
# 2. 주식 데이터 API
# ==========================

@app.get("/api/stocks/{code}/chart")
async def get_stock_chart(code: str, timeframe: str = "1m", limit: int = 1000):
    """주식 캔들 데이터 조회 (TradingView용)"""
    candles = db_manager.get_candles(symbol=code, limit=limit)
    
    if not candles:
        price = collector.get_current_price(code, market="KR")
        if price:
            return [{"time": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                      "open": price['open'], "high": price['high'],
                      "low": price['low'], "close": price['price'],
                      "volume": price['volume']}]
        return []
    
    formatted_data = []
    for c in candles:
        time_val = c['time'] 
        if timeframe == '1d':
            time_val = c['time'][:10]
        else:
            dt = datetime.fromisoformat(c['time'])
            time_val = int(dt.timestamp())

        formatted_data.append({
            "time": time_val,
            "open": c['open'], "high": c['high'],
            "low": c['low'], "close": c['close'],
            "volume": c['volume']
        })
        
    return formatted_data

# ==========================
# 계좌 잔고 API
# ==========================
_account_cache = {"data": None, "timestamp": 0}

@app.get("/api/account/summary")
async def get_account_summary():
    """계좌 잔고 요약 (예수금, 총자산)"""
    import time as _time
    now = _time.time()

    if _account_cache["data"] and (now - _account_cache["timestamp"]) < 10:
        return _account_cache["data"]

    try:
        # 국내 잔고 조회
        balance = collector.kis.inquire_balance()
        # 해외 잔고 실시간 조회 (헤더 일관성 위해 추가)
        overseas = collector.kis.inquire_overseas_balance()
        
        # 1. KIS API에서 받은 기본 값 (fallback용)
        # dnca_tot_amt: 예수금총금액
        # tot_evlu_amt: 총평가금액 (국내 주식 평가 + 예수금)
        # nass_amt: 순자산금액
        # evlu_pfls_smtl_amt: 평가손익합계금액 (국내)
        # scts_evlu_amt: 유가증권평가금액 (국내 주식만)
        
        cash_krw = balance.get("cash", 0)  # 예수금
        domestic_eval = balance.get("domestic_evlu", 0) # 국내주식 평가액
        
        # 통합증거금 API에서 정확한 주문가능금액 및 외화 예수금 확인
        krw_order_avail = cash_krw
        usd_order_avail = 0.0
        
        try:
            margin = collector.kis.inquire_intgr_margin()
            if margin:
                krw_order_avail = margin.get("krw_order_available", 0)
                usd_order_avail = margin.get("usd_order_available", 0)
        except Exception:
            pass

        # 해외 주식 평가액 (USD) 합산
        overseas_eval_usd = 0.0
        try:
            if overseas:
                overseas_eval_usd = sum(h.get("eval_amount", 0) for h in overseas)
        except Exception:
            pass

        # 환율 조회 (없으면 기본값 1400)
        scanner = get_scanner()
        if scanner:
            # 동기적으로 실행하기 위해 executor 사용 안함 (이미 async 함수 내부임)
            # 하지만 scanner._fetch_fx_rate는 동기 함수이므로 바로 호출 가능하나, 
            # 여기서는 편의상 캐시된 값이나 기본값 사용.
            # 정확성을 위해 scanner의 캐시나 DB를 조회하는 것이 좋음.
            # _fetch_fx_rate는 내부적으로 DB 캐시를 쓰므로 호출해도 무방.
            fx_rate = scanner._fetch_fx_rate("US")
        else:
            fx_rate = 1400.0

        if fx_rate <= 0: fx_rate = 1400.0

        # === [공식 적용] 총 자산 계산 ===
        # Total = (KRW 주문가능 + 국내주식 평가) + ((USD 주문가능 + 해외주식 평가USD) * 환율)
        # 주의: KIS의 'tot_evlu_amt'는 해외 자산이 포함되지 않거나 지연될 수 있음. 직접 계산이 가장 정확.
        
        total_assets_calculated = (
            krw_order_avail + domestic_eval + 
            ((usd_order_avail + overseas_eval_usd) * fx_rate)
        )
        
        # 정수형 변환
        total_assets_final = int(total_assets_calculated)

        result = {
            "cash": cash_krw,
            "order_available": krw_order_avail,
            "usd_order_available": usd_order_avail,
            "domestic_evlu": domestic_eval,
            "overseas_evlu_usd": round(overseas_eval_usd, 2),
            "total_assets": total_assets_final, # 재계산된 총자산
            "net_assets": total_assets_final,   # 순자산도 동일하게 처리 (대출 없다고 가정)
            "profit_loss": balance.get("profit_loss", 0), # 국내 손익만 (해외 합산은 복잡하므로 유지)
            "holdings_count": len(balance.get("holdings", [])) + len(overseas or []),
            "fx_rate": fx_rate
        }

        _account_cache["data"] = result
        _account_cache["timestamp"] = now
        return result
    except Exception as e:
        return {"cash": 0, "order_available": 0, "total_assets": 0, "error": str(e)}

# 시장 지수 캐시 (60초)
_indices_cache = {"data": None, "timestamp": 0}

@app.get("/api/market/indices")
async def get_market_indices():
    import time as _time
    now = _time.time()

    if _indices_cache["data"] and (now - _indices_cache["timestamp"]) < 60:
        return _indices_cache["data"]

    indices = {}
    symbols = {
        "KOSPI":   "^KS11",
        "Nikkei":  "^N225",
        "Shanghai": "000001.SS",
        "HSI":     "^HSI",
        "S&P500":  "^GSPC",
        "USD/KRW": "KRW=X",
    }

    try:
        import requests as req
        from concurrent.futures import ThreadPoolExecutor, as_completed

        def _fetch_index(name, symbol):
            try:
                url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=1d&interval=1d"
                headers = {"User-Agent": "Mozilla/5.0"}
                resp = req.get(url, headers=headers, timeout=5)
                if resp.status_code == 200:
                    data = resp.json()
                    result = data["chart"]["result"][0]
                    meta = result["meta"]
                    price = meta.get("regularMarketPrice", 0)
                    prev_close = meta.get("chartPreviousClose") or meta.get("previousClose") or price
                    change = round(((price - prev_close) / prev_close) * 100, 2) if prev_close else 0
                    return name, {"value": f"{price:,.2f}", "change": change}
            except Exception:
                pass
            return name, None

        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = [pool.submit(_fetch_index, name, sym) for name, sym in symbols.items()]
            for future in as_completed(futures):
                name, result = future.result()
                if result:
                    indices[name] = result
    except Exception:
        pass

    # fallback
    for key in symbols:
        if key not in indices:
            indices[key] = {"value": "N/A", "change": 0}

    _indices_cache["data"] = indices
    _indices_cache["timestamp"] = now
    return indices

# 국가별 주식 캐시 (60초)
_stocks_cache_by_country = {}

@app.get("/api/stocks/top")
async def get_top_stocks(country: str = "KR"):
    import time as _time
    import requests as req
    now = _time.time()

    country = country.upper()
    if country not in COUNTRY_STOCKS:
        return []

    # 국가별 캐시
    cache = _stocks_cache_by_country.get(country, {"data": None, "timestamp": 0})
    if cache["data"] and (now - cache["timestamp"]) < 60:
        return cache["data"]

    # 1. DB Watchlist 조회
    stock_list_raw = db_manager.get_watchlist(market=country)
    stock_list = []
    
    # 2. 랭킹 API (선택적 병합) - 여기서는 Watchlist만 우선 보여주거나, ScannerEngine에서 수집한 랭킹을 DB에 저장했다면 그걸 보여줄 수도 있음.
    # 현재는 Watchlist(기본 종목)만 보여주는 구조 유지.
    
    for item in stock_list_raw:
        code = item["symbol"]
        name = item["name"]
        mcap = item.get("mcap", 10)
        stock_list.append((code, name, mcap))

    stocks = []
    suffix_fn = YAHOO_SUFFIX.get(country, lambda c: "")

    # 한국: KIS API 우선
    if country == "KR" and collector.kis.is_configured():
        for code, name, mcap in stock_list:
            try:
                price = collector.get_current_price(code, market="KR")
                if price and price.get("price", 0) > 0:
                    stocks.append({
                        "name": name, "code": code,
                        "price": price.get("price", 0),
                        "change": price.get("change_rate", 0),
                        "volume": price.get("volume", 0),
                        "market_cap": mcap
                    })
            except Exception:
                pass

    # Yahoo Finance (KIS 실패 시 한국 fallback, 또는 해외 주식)
    if not stocks:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        def _fetch_yahoo(code, name, mcap):
            """Yahoo Finance 단일 종목 조회 (병렬 실행용)"""
            try:
                suffix = suffix_fn(code)
                symbol = f"{code}{suffix}"
                url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=1d&interval=1d"
                headers = {"User-Agent": "Mozilla/5.0"}
                resp = req.get(url, headers=headers, timeout=5)
                if resp.status_code == 200:
                    data = resp.json()
                    result = data["chart"]["result"][0]
                    meta = result["meta"]
                    price = meta.get("regularMarketPrice", 0)
                    prev_close = meta.get("chartPreviousClose") or meta.get("previousClose") or price
                    change = round(((price - prev_close) / prev_close) * 100, 2) if prev_close else 0
                    volume = meta.get("regularMarketVolume", 0)
                    return {
                        "name": name, "code": code,
                        "price": round(price, 2) if country in ("US", "HK") else int(price),
                        "change": change,
                        "volume": volume,
                        "market_cap": mcap
                    }
            except Exception:
                pass
            return None

        with ThreadPoolExecutor(max_workers=20) as pool:
            futures = {pool.submit(_fetch_yahoo, code, name, mcap): code for code, name, mcap in stock_list}
            for future in as_completed(futures):
                result = future.result()
                if result:
                    stocks.append(result)

    if stocks:
        _stocks_cache_by_country[country] = {"data": stocks, "timestamp": now}
    return stocks

@app.get("/api/market/info")
async def get_market_info():
    """국가별 시장 정보 반환"""
    return MARKET_INFO

# ==========================
# 3. 설정 관리 API
# ==========================

@app.get("/api/settings")
async def get_settings():
    """전체 설정 조회 (마스킹된 값)"""
    return db_manager.get_settings_for_display()

@app.post("/api/settings/save")
async def save_settings(req: SettingsSaveRequest):
    """설정 저장 (DB에 기록)"""
    field_map = {
        "kis_app_key": "KIS_APP_KEY",
        "kis_secret_key": "KIS_SECRET_KEY",
        "kis_acct_stock": "KIS_ACCT_STOCK",
        "antigravity_api_key": "ANTIGRAVITY_API_KEY",
        "antigravity_model": "ANTIGRAVITY_MODEL",
        "google_oauth_client_id": "GOOGLE_OAUTH_CLIENT_ID",
        "google_oauth_client_secret": "GOOGLE_OAUTH_CLIENT_SECRET",
        "discord_webhook_url": "DISCORD_WEBHOOK_URL",
        "noti_trade_alerts": "NOTI_TRADE_ALERTS",
        "noti_hourly_report": "NOTI_HOURLY_REPORT",
        "ai_mode": "AI_MODE",
        "local_llm_url": "LOCAL_LLM_URL",
        "local_llm_model": "LOCAL_LLM_MODEL",
        "allow_leverage": "ALLOW_LEVERAGE",
        "enable_auto_scan": "ENABLE_AUTO_SCAN",
        "enable_auto_buy": "ENABLE_AUTO_BUY",
        "enable_auto_sell": "ENABLE_AUTO_SELL",
        "enable_offmarket": "ENABLE_OFFMARKET",
        "enable_news_collect": "ENABLE_NEWS_COLLECT",
    }
    
    saved_count = 0
    for field_name, db_key in field_map.items():
        value = getattr(req, field_name, None)
        if value is not None and value != "":
            db_manager.set_setting(db_key, value)
            saved_count += 1
    
    return {"status": "ok", "saved": saved_count}

@app.get("/api/settings/{key}")
async def get_setting(key: str):
    """개별 설정값 조회"""
    value = db_manager.get_setting(key.upper())
    return {"key": key.upper(), "value": value}

@app.post("/api/settings/test-webhook")
async def test_webhook(req: WebhookTestRequest):
    """Discord Webhook 테스트"""
    try:
        import requests
        payload = {
            "content": "🤖 **KIS Stock AI** 테스트 메시지\n"
                       f"시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                       "Webhook 연결 성공! ✅"
        }
        response = requests.post(req.url, json=payload, timeout=10)
        if response.status_code in (200, 204):
            return {"status": "ok", "message": "테스트 메시지 전송 성공"}
        else:
            return JSONResponse(
                status_code=400,
                content={"status": "error", "message": f"HTTP {response.status_code}"}
            )
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": str(e)}
        )

# ==========================
# 4. 서버 관리 API
# ==========================

@app.post("/api/server/restart")
async def restart_server():
    """서버 재시작 (설정 반영)"""
    import threading
    def _restart():
        import time
        time.sleep(1)
        os.execv(sys.executable, [sys.executable] + sys.argv)
    threading.Thread(target=_restart, daemon=True).start()
    return {"status": "ok", "message": "서버 재시작 중..."}

# ==========================
# 5. 백테스트 API
# ==========================

class BacktestRequest(BaseModel):
    symbol: str = "005930"
    name: str = ""
    start_date: str = ""
    end_date: str = ""
    initial_capital: int = 10_000_000
    strategy: str = "ai_combined"
    confidence_threshold: int = 80
    stop_loss_pct: float = 0.05
    take_profit_pct: float = 0.10

@app.post("/api/backtest/run")
async def run_backtest(req: BacktestRequest):
    """백테스트 실행"""
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ai"))
        from ai.backtest_engine import BacktestEngine, BacktestConfig
        
        config = BacktestConfig(
            symbol=req.symbol,
            name=req.name or req.symbol,
            start_date=req.start_date,
            end_date=req.end_date,
            initial_capital=req.initial_capital,
            strategy=req.strategy,
            confidence_threshold=req.confidence_threshold,
            stop_loss_pct=req.stop_loss_pct,
            take_profit_pct=req.take_profit_pct
        )
        
        engine = BacktestEngine()
        result = engine.run(config)
        
        if result.error:
            return JSONResponse(status_code=400, content={"error": result.error})
        
        # DB에 결과 저장
        backtest_id = db_manager.save_backtest(config, result)
        
        return {
            "id": backtest_id,
            "trades": result.trades,
            "equity_curve": result.equity_curve,
            "metrics": result.metrics,
        }
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.get("/api/backtest/history")
async def get_backtest_history(limit: int = 20, strategy: str = None, symbol: str = None):
    """백테스트 실행 이력"""
    return db_manager.get_backtest_history(limit=limit, strategy=strategy, symbol=symbol)

@app.get("/api/backtest/{backtest_id}")
async def get_backtest_detail(backtest_id: int):
    """백테스트 상세 결과"""
    detail = db_manager.get_backtest_detail(backtest_id)
    if not detail:
        return JSONResponse(status_code=404, content={"error": "Not found"})
    return detail


# ==========================
# 6. AI Scanner API
# ==========================

@app.get("/api/scanner/state")
async def get_scanner_state():
    """스캐너 현재 상태"""
    return get_scanner().get_state_snapshot()

@app.get("/api/offmarket/status")
async def get_offmarket_status():
    """Off-Market 활동 상태"""
    scanner = get_scanner()
    return {
        "state": scanner.offmarket_state,
        "ai_stats": scanner._ai_stats,
        "premarket_picks": scanner._premarket_picks[:10],
        "global_analysis": scanner._global_analysis,
        "news_count": len(scanner._news_cache),
        "candle_cache_count": len(scanner._candle_cache),
        "ta_cache_count": len(scanner._ta_cache),
    }

@app.get("/api/strategies")
async def get_strategies():
    """전략 + 패턴 목록"""
    scanner = get_scanner()
    store = scanner.strategy_store
    return {
        "strategies": store.get_all_strategies(),
        "patterns": store.get_patterns(limit=30),
    }

@app.put("/api/strategies/{sid}/toggle")
async def toggle_strategy(sid: int, active: bool = True):
    """전략 활성화/비활성화"""
    scanner = get_scanner()
    ok = scanner.strategy_store.toggle_strategy(sid, active)
    return {"success": ok}

@app.delete("/api/strategies/{sid}")
async def delete_strategy(sid: int):
    """전략 삭제"""
    scanner = get_scanner()
    ok = scanner.strategy_store.delete_strategy(sid)
    return {"success": ok}

@app.get("/api/patterns")
async def get_patterns(market: str = None, ptype: str = None, result: str = None, limit: int = 30):
    """학습된 패턴 조회"""
    scanner = get_scanner()
    return scanner.strategy_store.get_patterns(market=market, ptype=ptype, result=result, limit=limit)

@app.get("/api/scanner/results")
async def get_scanner_results(limit: int = 100):
    """분석 완료된 종목 결과 (종목별 최신 1건만 유지)"""
    scanner = get_scanner()
    # 스레드 안전을 위해 리스트 복사본 사용
    raw_results = list(scanner.scan_results)
    
    deduped = {}
    for r in reversed(raw_results):
        # symbol, market, name을 표준화하여 키 생성
        s = str(r.get("symbol", "")).strip().upper()
        m = str(r.get("market", "")).strip().upper()
        n = str(r.get("name", "")).strip().upper()
        
        if not s and not n:
            continue
            
        # 가장 확실한 고유 조합 생성
        key = f"{s}_{m}_{n}"
        if key not in deduped:
            deduped[key] = r
    
    # 결과 리스트 생성 및 시간 내림차순 정렬
    results = list(deduped.values())
    results.sort(key=lambda x: str(x.get("analyzed_at", "")), reverse=True)
    
    # 중복 제거 로그 (서버 콘솔 및 SSE 전송)
    if len(raw_results) > len(results):
        msg = f"🧹 분석 결과 중복 제거: {len(raw_results)} -> {len(results)}건"
        # ai_log(f"INFO", msg)  # 루프 방지를 위해 주석 처리하거나 신중히 사용
        print(f"[API] {msg}")
        
    return {"count": len(results), "results": results[:limit]}

@app.get("/api/scanner/candidates")
async def get_scanner_candidates():
    """매수 후보 목록 (AI 점수 75+)"""
    scanner = get_scanner()
    return {"count": len(scanner.candidates), "candidates": scanner.candidates}

@app.post("/api/scanner/control")
async def control_scanner(action: str = Body(..., embed=True)):
    """스캐너 제어 (start/pause/resume/stop/reset)"""
    scanner = get_scanner()
    if action == "pause":
        scanner.pause()
    elif action == "resume":
        scanner.resume()
    elif action == "stop":
        scanner.stop()
    elif action == "reset":
        scanner.reset_results()
    elif action == "start":
        scanner.resume()
    else:
        return JSONResponse(status_code=400, content={"error": f"Unknown action: {action}"})
    return {"status": scanner.state["status"], "action": action}

@app.get("/api/scanner/stream")
async def stream_scanner():
    """SSE 실시간 스캐너 로그"""
    scanner = get_scanner()
    queue = asyncio.Queue(maxsize=100)
    scanner._subscribers.append(queue)

    async def event_generator():
        try:
            while True:
                try:
                    entry = await asyncio.wait_for(queue.get(), timeout=25)
                    yield f"data: {json.dumps(entry, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    # 타임아웃 시 ping 전송 후 계속 대기 (연결 유지)
                    yield f"data: {json.dumps({'time': '', 'level': 'ping', 'message': ''}, ensure_ascii=False)}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            if queue in scanner._subscribers:
                scanner._subscribers.remove(queue)

    return StreamingResponse(event_generator(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ==========================
# 7. Antigravity Ultra Auth API
# ==========================

@app.get("/api/antigravity/status")
async def antigravity_status():
    """Antigravity 인증 상태"""
    try:
        from antigravity_auth import get_antigravity_auth
        auth = get_antigravity_auth()
        return auth.get_status()
    except ImportError:
        return {"authenticated": False, "error": "antigravity_auth module not found"}

@app.post("/api/antigravity/login")
async def antigravity_login():
    """Antigravity Google OAuth 로그인 시작"""
    try:
        from antigravity_auth import get_antigravity_auth
        auth = get_antigravity_auth()
        auth_url, port = auth.start_login()
        ai_log("INFO", f"🔐 Antigravity 로그인 시작 (callback port: {port})")
        return {"status": "login_started", "auth_url": auth_url, "callback_port": port}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.get("/api/antigravity/callback-status")
async def antigravity_callback_status():
    """OAuth 콜백 완료 여부 폴링"""
    try:
        from antigravity_auth import get_antigravity_auth
        auth = get_antigravity_auth()
        if auth._oauth_result:
            result = auth._oauth_result
            if result.get("success"):
                # 클라이언트 모드 갱신
                if hasattr(collector, 'antigravity') and collector.antigravity:
                    collector.antigravity.refresh_mode()
                ai_log("INFO", f"✅ Antigravity 로그인 성공: {auth.email}")
                return {"completed": True, "success": True, "email": auth.email}
            else:
                ai_log("WARN", f"❌ Antigravity 로그인 실패: {result.get('error')}")
                return {"completed": True, "success": False, "error": result.get("error")}
        return {"completed": False}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.post("/api/antigravity/logout")
async def antigravity_logout():
    """Antigravity 로그아웃"""
    try:
        from antigravity_auth import get_antigravity_auth
        auth = get_antigravity_auth()
        auth.logout()
        # 클라이언트 모드 갱신
        if hasattr(collector, 'antigravity') and collector.antigravity:
            collector.antigravity.refresh_mode()
        ai_log("INFO", "🔓 Antigravity 로그아웃")
        return {"status": "logged_out"}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.post("/api/antigravity/model")
async def antigravity_set_model(model: str = Body(..., embed=True)):
    """AI 모델 변경"""
    try:
        from antigravity_auth import get_antigravity_auth
        auth = get_antigravity_auth()
        if auth.set_model(model):
            # 클라이언트 모델도 동기화
            if hasattr(collector, 'antigravity') and collector.antigravity:
                collector.antigravity.config.model = model
            ai_log("INFO", f"🤖 AI 모델 변경: {model}")
            return {"status": "ok", "model": model}
        return JSONResponse(status_code=400, content={"error": "Invalid model"})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

# ==========================
# 8. 로컬 모델 학습 API
# ==========================

@app.get("/api/ai/train/status")
async def get_training_status():
    """학습 상태 조회"""
    return _training_status

@app.post("/api/ai/train/start")
async def start_training_model():
    """로컬 모델 학습 시작 (백그라운드 프로세스)"""
    global _training_process, _training_status
    
    if _training_status["status"] == "running":
        return JSONResponse(status_code=400, content={"error": "이미 학습이 진행 중입니다."})

    try:
        import subprocess
        
        # 스크립트 경로
        script_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ai", "train_local_model.py")
        
        # 백그라운드 실행
        _training_process = subprocess.Popen(
            [sys.executable, script_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        
        _training_status["status"] = "running"
        _training_status["message"] = "학습 프로세스가 시작되었습니다."
        _training_status["last_run"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # 상태 모니터링 태스크 시작
        asyncio.create_task(_monitor_training(_training_process))
        
        ai_log("SYSTEM", "🚀 로컬 모델 학습 프로세스 시작")
        return {"status": "started", "pid": _training_process.pid}
        
    except Exception as e:
        _training_status["status"] = "error"
        _training_status["message"] = str(e)
        return JSONResponse(status_code=500, content={"error": str(e)})

async def _monitor_training(process):
    """학습 프로세스 모니터링"""
    global _training_status
    
    # 비동기로 프로세스 완료 대기
    loop = asyncio.get_event_loop()
    stdout, stderr = await loop.run_in_executor(None, process.communicate)
    
    if process.returncode == 0:
        _training_status["status"] = "completed"
        _training_status["message"] = "학습이 성공적으로 완료되었습니다."
        ai_log("SYSTEM", "✅ 로컬 모델 학습 완료")
    else:
        _training_status["status"] = "error"
        _training_status["message"] = f"학습 실패 (Code: {process.returncode})"
        ai_log("ERROR", f"❌ 로컬 모델 학습 실패: {stderr[-200:] if stderr else 'Unknown error'}")


from fastapi import FastAPI, Request, Body, Query, File, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, FileResponse
import shutil

# ... (기존 코드)

# ==========================
# 9. 학습 데이터 관리 (Export/Import)
# ==========================

@app.get("/api/ai/dataset/export")
async def export_training_data():
    """학습 데이터셋 다운로드 (JSONL)"""
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ai"))
        from ai.dataset_builder import DatasetBuilder
        
        builder = DatasetBuilder()
        # 현재 DB 데이터를 최신 JSONL로 생성
        file_path = builder.build_jsonl(filename="training_data_export.jsonl")
        
        return FileResponse(
            path=file_path,
            filename=f"stock_ai_dataset_{datetime.now().strftime('%Y%m%d')}.jsonl",
            media_type='application/json'
        )
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.post("/api/ai/dataset/import")
async def import_training_data(file: UploadFile = File(...)):
    """외부 학습 데이터셋 업로드"""
    try:
        if not file.filename.endswith('.jsonl'):
            return JSONResponse(status_code=400, content={"error": "JSONL 파일만 업로드 가능합니다."})
            
        save_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ai", "datasets")
        os.makedirs(save_dir, exist_ok=True)
        
        # 파일명 충돌 방지 (timestamp 추가)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_filename = f"imported_{timestamp}_{file.filename}"
        file_path = os.path.join(save_dir, safe_filename)
        
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
            
        ai_log("INFO", f"📂 학습 데이터 업로드 완료: {safe_filename}")
        
        # 유효성 검증 (선택사항)
        valid_count = 0
        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip(): valid_count += 1
                
        return {"success": True, "filename": safe_filename, "count": valid_count}
        
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


# ==========================
# SSE 로그 스트리밍 엔드포인트
# ==========================

@app.get("/api/logs/stream")
async def stream_logs():
    """SSE 실시간 AI 로그 스트림"""
    queue = asyncio.Queue(maxsize=100)
    _ai_log_subscribers.append(queue)

    async def event_generator():
        try:
            # 기존 로그 전송
            for entry in list(_ai_log_buffer):
                yield f"data: {json.dumps(entry, ensure_ascii=False)}\n\n"
            # 실시간 스트림
            while True:
                try:
                    entry = await asyncio.wait_for(queue.get(), timeout=25)
                    yield f"data: {json.dumps(entry, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield f"data: {json.dumps({'time': '', 'level': 'ping', 'message': ''}, ensure_ascii=False)}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            if queue in _ai_log_subscribers:
                _ai_log_subscribers.remove(queue)

    return StreamingResponse(event_generator(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

@app.get("/api/logs/recent")
async def get_recent_logs(limit: int = 50):
    """최근 AI 로그 조회"""
    return list(_ai_log_buffer)[-limit:]


# ==========================
# 백그라운드 시장 모니터
# ==========================

async def _market_monitor():
    """60초마다 시장 상태 확인 및 주요 변동 종목 로깅"""
    import time as _time
    import requests as req
    from ai.config import MARKET_INFO

    await asyncio.sleep(2)  # 서버 시작 대기
    ai_log("SYSTEM", "🚀 AI Market Monitor 시작")
    ai_log("INFO", f"모니터링 중: 🇰🇷 한국, 🇯🇵 일본, 🇨🇳 중국, 🇭🇰 홍콩, 🇺🇸 미국")

    cycle = 0
    while True:
        try:
            cycle += 1
            now = datetime.now()
            hour = now.hour
            weekday = now.weekday()

            # 개장 시장 확인
            active = []
            if weekday < 5:
                if 9 <= hour < 15 or (hour == 15 and now.minute <= 30): active.append("KR")
                if 9 <= hour < 15: active.append("JP")
                if 10 <= hour < 16: active.append("CN")
                if 10 <= hour < 17: active.append("HK")
                if hour >= 23: active.append("US")  # 월~금 밤 23시~
            # 미국장: KST 새벽 0~6시 → 미국 전일 오전~오후 (화~토 새벽 = 미국 월~금)
            if (weekday < 5 or weekday == 5) and hour < 6 and weekday != 6:
                if "US" not in active:
                    active.append("US")

            if not active:
                if cycle % 5 == 1:  # 5분마다만 로깅
                    ai_log("INFO", f"💤 모든 시장 휴장 ({now.strftime('%H:%M')})")
                await asyncio.sleep(60)
                continue

            flags = {"KR": "🇰🇷", "JP": "🇯🇵", "CN": "🇨🇳", "HK": "🇭🇰", "US": "🇺🇸"}
            market_str = " ".join(f"{flags.get(m, m)} {m}" for m in active)
            ai_log("SCAN", f"📡 활성 시장: {market_str}")

            # 각 활성 시장의 캐시된 데이터에서 주요 변동 종목 확인
            for market in active:
                cache = _stocks_cache_by_country.get(market, {})
                stocks = cache.get("data", [])
                if not stocks:
                    ai_log("WARN", f"[{market}] 데이터 없음 — 탭 클릭 시 로드됨")
                    continue

                # 상승/하락 상위
                sorted_up = sorted(stocks, key=lambda s: s.get("change", 0), reverse=True)
                sorted_dn = sorted(stocks, key=lambda s: s.get("change", 0))

                top_up = sorted_up[0] if sorted_up else None
                top_dn = sorted_dn[0] if sorted_dn else None

                if top_up and top_up.get("change", 0) > 0:
                    ai_log("BULL", f"[{market}] 📈 {top_up['name']} +{top_up['change']}%")
                if top_dn and top_dn.get("change", 0) < 0:
                    ai_log("BEAR", f"[{market}] 📉 {top_dn['name']} {top_dn['change']}%")

                # 급등/급락 종목 (5% 이상)
                alerts = [s for s in stocks if abs(s.get("change", 0)) >= 5]
                for s in alerts[:3]:
                    emoji = "🔥" if s["change"] > 0 else "⚠️"
                    ai_log("ALERT", f"[{market}] {emoji} {s['name']} {s['change']:+.1f}% (급변동)")

            # 토큰 상태
            if cycle % 10 == 1:  # 10분마다
                if collector.kis.is_configured():
                    token = collector.kis.get_access_token()
                    if token:
                        ai_log("TOKEN", "🔑 KIS API 토큰 유효")
                    else:
                        ai_log("WARN", "🔑 KIS API 토큰 만료 — 갱신 필요")

        except Exception as e:
            ai_log("ERROR", f"❌ 모니터 에러: {str(e)[:80]}")

        await asyncio.sleep(60)

# ==========================
# 포트폴리오 & 트레이드 API
# ==========================

@app.get("/api/portfolio/holdings")
async def get_portfolio_holdings():
    """실제 KIS API 실시간 보유종목 조회 (국내/해외 통합)"""
    try:
        loop = asyncio.get_event_loop()
        
        # 1. KIS API 실시간 조회 (국내/해외 잔고)
        domestic = await loop.run_in_executor(executor, collector.kis.inquire_balance)
        overseas = await loop.run_in_executor(executor, collector.kis.inquire_overseas_balance)

        domestic_holdings = domestic.get("holdings", [])
        for h in domestic_holdings:
            h["market_type"] = "domestic"
            h["exchange"] = "KRX"
        
        overseas_holdings = overseas or []
        for h in overseas_holdings:
            h["market_type"] = "overseas"
            # exchange는 inquire_overseas_balance에서 NASD 등으로 채워져 옴

        all_holdings = domestic_holdings + overseas_holdings

        # 2. 스캐너 매도 추적 데이터와 병합 (실시간 시세 등)
        scanner = get_scanner()
        if scanner:
            # 스캐너의 holdings와 KIS 실제 holdings 동기화 시도
            # (추후 ScannerEngine._track_holdings에서 주기적으로 수행하겠지만 여기서도 병합)
            for h in all_holdings:
                target = next((sh for sh in scanner.holdings if sh["symbol"] == h["symbol"]), None)
                if target:
                    h["live_price"] = target.get("live_price", 0)
                    h["last_updated"] = target.get("last_updated", "")
                    h["sell_status"] = target.get("sell_status", "watching")
                    h["trade_type"] = target.get("trade_type", "스윙")
                    h["ai_sell_price"] = target.get("ai_sell_price", 0)
                    h["stop_loss"] = target.get("stop_loss", 0)
                    h["break_even_price"] = target.get("break_even_price", 0)
                    h["total_fees"] = target.get("total_fees", 0)
                    h["net_profit"] = target.get("net_profit", 0)
                    h["net_profit_rate"] = target.get("net_profit_rate", 0)
                else:
                    h["live_price"] = h.get("current_price", 0)
                    h["sell_status"] = "watching"
                    h["ai_sell_price"] = 0
                    h["stop_loss"] = 0
                    h["break_even_price"] = h.get("avg_price", 0) # Default to buy price if no fee data
                    h["total_fees"] = 0

        # 3. 금액 합계 (공식 적용 재계산)
        domestic_eval = domestic.get("domestic_evlu", 0)  # 국내 평가액
        profit_loss = domestic.get("profit_loss", 0)      # 국내 손익
        
        # 통합증거금 기준 주문가능금액
        order_available = domestic.get("cash", 0)  
        usd_order_available = 0.0
        try:
            margin = await loop.run_in_executor(executor, collector.kis.inquire_intgr_margin)
            if margin:
                krw_avail = margin.get("krw_order_available", 0)
                if krw_avail > 0:
                    order_available = krw_avail
                usd_order_available = margin.get("usd_order_available", 0)
        except Exception:
            pass
            
        fx_rate = (await loop.run_in_executor(executor, scanner._fetch_fx_rate, "US")) if scanner else 1450.0
        if fx_rate <= 0: fx_rate = 1450.0

        overseas_eval_usd = round(sum(h.get("eval_amount", 0) for h in overseas_holdings), 2)
        
        # [공식 적용] 총자산 재계산
        total_assets_calculated = (
            order_available + domestic_eval + 
            ((usd_order_available + overseas_eval_usd) * fx_rate)
        )
        total_assets = int(total_assets_calculated)

        return {
            "holdings": all_holdings,
            "order_available": order_available,
            "usd_order_available": usd_order_available,
            "domestic_eval": domestic_eval,
            "overseas_eval_usd": overseas_eval_usd,
            "total_assets": total_assets, # 재계산된 값
            "profit_loss": profit_loss,
            "fx_rate": fx_rate,
            "timestamp": datetime.now().strftime("%H:%M:%S")
        }

    except Exception as e:
        ai_log("ERROR", f"보유종목 조회 실패: {str(e)}")
        return {"holdings": [], "error": str(e)}

@app.get("/api/scanner/trades")
async def get_recent_trades():
    """실제 KIS API 최근 1개월 체결 내역 조회 (로컬 DB 전략 정보 병합)"""
    try:
        loop = asyncio.get_event_loop()
        # 최근 30일 내역 조회
        kis_trades = await loop.run_in_executor(executor, lambda: collector.kis.inquire_history(days=30))
        
        # 로컬 DB에서 자동매매 기록 가져오기 (전략명 등 확인용) - 1달치 대응 위해 리미트 상향
        db_trades = db_manager.get_trades(limit=200)
        
        # KIS 내역을 기반으로 반환
        results = []
        for kt in kis_trades:
            # 날짜와 시간 결합 및 포맷팅
            d = kt.get('date', '')
            t = kt.get('time', '')
            dt_str = f"{d} {t}"
            try:
                if len(d) == 8 and len(t) == 6:
                    dt_str = f"{d[:4]}-{d[4:6]}-{d[6:8]} {t[:2]}:{t[2:4]}:{t[4:6]}"
            except:
                pass

            # 로컬 DB 기록과 매칭 (order_no 기준)
            match = next((dt for dt in db_trades if dt.get("order_no") == kt.get("order_no")), None)
            
            results.append({
                "time": dt_str,
                "symbol": kt.get("symbol", ""),
                "name": kt.get("name", ""),
                "side": kt.get("side", ""),  # 'buy' 또는 'sell'
                "qty": kt.get("quantity", 0),
                "price": kt.get("price", 0),
                "order_no": kt.get("order_no", "-"),
                "market": kt.get("market", ""),
                "strategy": match.get("trade_type", "수동/외부") if match else "수동/외부",
                "trade_type": match.get("trade_type", "-") if match else "-"
            })
            
        # 시간순 정렬 (최신순)
        sorted_results = sorted(results, key=lambda x: x["time"], reverse=True)
        return {"trades": sorted_results}
    except Exception as e:
        ai_log("ERROR", f"거래내역 조회 실패: {str(e)}")
        return {"trades": [], "error": str(e)}


@app.get("/api/portfolio/pending")
async def get_pending_orders():
    """국내/해외 미체결 주문 조회"""
    try:
        domestic = collector.kis.inquire_pending_domestic()
        overseas = collector.kis.inquire_pending_overseas()
        all_pending = domestic + overseas
        return {"pending": all_pending, "count": len(all_pending)}
    except Exception as e:
        return {"error": str(e), "pending": [], "count": 0}


# ==========================
# 3. AI 전략 관리 API
# ==========================

@app.get("/api/strategy/list")
async def list_strategies():
    """저장된 전체 전략 목록 반환"""
    return db_manager.get_strategies()

@app.post("/api/strategy/toggle")
async def toggle_strategy(id: int, active: bool):
    """전략 활성/비활성 토글"""
    db_manager.toggle_strategy(id, active)
    return {"success": True}

@app.post("/api/strategy/delete")
async def delete_strategy(id: int):
    """전략 삭제"""
    try:
        db_manager.delete_strategy(id)
        return {"success": True}
    except Exception as e:
        ai_log("ERROR", f"전략 삭제 실패: {str(e)}")
        return {"success": False, "error": str(e)}

@app.post("/api/strategy/save")
async def save_strategy(strategy: dict = Body(...)):
    """추출된 전략 저장"""
    sid = db_manager.save_strategy(strategy)
    return {"success": sid != -1, "id": sid}

class YoutubeRequest(BaseModel):
    url: str

@app.post("/api/strategy/youtube")
async def extract_youtube_strategy(req: YoutubeRequest):
    """유튜브 URL → 자막 추출 → AI 전략화"""
    try:
        ai_log("INFO", f"유튜브 전략 추출 시작: {req.url}")
        
        # 실제 추출 로직 실행 (상위 모듈의 함수 호출)
        result = extract_from_youtube(req.url)
        
        if "error" in result:
            return JSONResponse({"error": result["error"]}, status_code=400)
            
        return {"success": True, "strategy": result}
        
    except Exception as e:
        ai_log("ERROR", f"유튜브 분석 실패: {str(e)}")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/ai/dataset/count")
async def count_training_data():
    """현재 학습 가능한 데이터 총 개수 조회"""
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ai"))
        from ai.dataset_builder import DatasetBuilder
        
        builder = DatasetBuilder()
        # DB 데이터 + 파일 데이터 합산
        db_count = len(builder.fetch_raw_data())
        file_count = 0
        
        # 파일 데이터 카운트 (중복 제거 없이 단순 합산)
        files = builder.get_all_data_files()
        for path in files:
            # db_latest.jsonl은 제외 (중복 방지)
            if "db_latest.jsonl" in path: continue
            try:
                with open(path, "r", encoding="utf-8") as f:
                    file_count += sum(1 for line in f if line.strip())
            except: pass
            
        total = db_count + file_count
        recommended = 100 # 최소 권장 수량
        
        return {
            "total": total,
            "db_count": db_count,
            "file_count": file_count,
            "status": "ready" if total >= recommended else "insufficient",
            "recommended": recommended
        }
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

# ==========================
# 9. 시스템 상태 API (AI 연결 확인)
# ==========================

@app.get("/api/system/status")
async def get_system_status():
    """AI 모델 연결 상태 확인"""
    status = {
        "local_ai": False,
        "antigravity": False,
        "kis_api": False
    }
    
    # 1. Local AI Check
    try:
        scanner = get_scanner()
        if scanner.local_llm.is_available():
            status["local_ai"] = True
    except: pass

    # 2. Antigravity Check
    try:
        from antigravity_auth import get_antigravity_auth
        auth = get_antigravity_auth()
        if auth.is_authenticated:
            status["antigravity"] = True
    except: pass

    # 3. KIS API Check
    try:
        if collector.kis.is_configured() and collector.kis.get_access_token():
            status["kis_api"] = True
    except: pass

    return status

async def _weekend_training_scheduler():
    """매주 토요일 오전 9시에 학습 트리거"""
    while True:
        now = datetime.now()
        # 토요일(5)이고 9시 0분 ~ 9시 59분 사이인지 확인
        if now.weekday() == 5 and now.hour == 9:
            # 이미 실행 중이 아니면 실행
            global _training_status
            if _training_status["status"] != "running":
                ai_log("SYSTEM", "📅 주말 정기 학습 스케줄러 가동")
                await start_training_model()
                # 중복 실행 방지를 위해 1시간 대기
                await asyncio.sleep(3600)
        
        # 10분마다 체크
        await asyncio.sleep(600)

@app.on_event("startup")
async def startup_event():
    """서버 시작 시 백그라운드 태스크 등록"""
    asyncio.create_task(_market_monitor())
    asyncio.create_task(_weekend_training_scheduler()) # 스케줄러 추가
    asyncio.create_task(get_scanner().run())  # AI Trading Scanner


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
