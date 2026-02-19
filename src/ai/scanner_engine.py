"""
Scanner Engine - AI 매수 대상 탐색 백그라운드 엔진

장 운영시간 중 국가별 Top 50 종목을 자동 스캔하고,
차트 데이터 + AI 분석을 통해 매수 후보를 선별합니다.
"""
import asyncio
import json
import os
import time
import requests
from strategy_store import StrategyStore
from database import DatabaseManager
from vector_store import StockVectorStore
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Callable
from concurrent.futures import ThreadPoolExecutor
from fee_calculator import FeeCalculator
from notification import NotificationService

from config import (
    MARKET_INFO, YAHOO_SUFFIX, KOSDAQ_CODES,
    HARD_STOP_LOSS_PERCENT, DEFAULT_FX_RATES
)
import json
from data_collector import StockDataCollector
from antigravity_client import AntigravityClient
from ta_utils import analyze_candles
from scanner_engine_helper import ScannerHelper


# ──────────────────────────────────────────
# 상수
# ──────────────────────────────────────────
BATCH_SIZE = 5          # 종목 동시 수집 수
BATCH_DELAY = 3         # 배치 간 딜레이(초)
CYCLE_INTERVAL = 300    # 스캔 사이클 간격(초) = 5분
AI_BATCH_SIZE = 3       # AI 분석 동시 처리 수
AI_BATCH_DELAY = 5      # AI 배치 간 딜레이(초)
BUY_SCORE_THRESHOLD = 75  # 매수 후보 최소 AI 점수
MAX_TARGETS_PER_MARKET = 50

# 시장별 운영시간 (KST 기준, 시:분)
MARKET_HOURS_KST = {
    "KR": {"open": (9, 0),   "close": (15, 30)},
    "JP": {"open": (9, 0),   "close": (15, 0)},
    "CN": {"open": (10, 0),  "close": (16, 0)},
    "HK": {"open": (10, 0),  "close": (17, 0)},
    "US": {"open": (23, 30), "close": (6, 0)},   # 다음날 새벽 (야간)
}

# [Step 3] stocks.json에서 종목 로드 (동적 관리)
def load_country_stocks():
    try:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(base_dir, "stocks.json"), "r", encoding="utf-8") as f:
            data = json.load(f)
            # Tuple 형태로 변환 (코드, 이름, 시가총액, [거래소])
            converted = {}
            for country, stocks in data.items():
                stock_list = []
                for s in stocks:
                    item = (s["code"], s["name"], s.get("mcap", 10))
                    if "exchange" in s:
                        item += (s["exchange"],)
                    stock_list.append(item)
                converted[country] = stock_list
            return converted
    except Exception as e:
        print(f"[Scanner] stocks.json 로드 실패: {e}")
        return {}

COUNTRY_STOCKS = load_country_stocks()


# 통화별 Yahoo Finance 환율 심볼 (→ KRW)
FX_SYMBOLS = {
    "US": "USDKRW=X",
    "JP": "JPYKRW=X",
    "CN": "CNYKRW=X",
    "HK": "HKDKRW=X",
}


class ScannerEngine:
    """AI 매수 대상 탐색 엔진"""

    def __init__(self, log_fn: Callable = None):
        self.collector = StockDataCollector()
        self.antigravity = AntigravityClient()
        self._log_fn = log_fn  # ai_log 함수 인젝션
        self._executor = ThreadPoolExecutor(max_workers=6)
        self._helper = ScannerHelper(self) # Helper 초기화

        # 상태
        self.state = {
            "status": "idle",        # idle / scanning / paused / stopped
            "phase": "",             # target_select / candle_collect / ai_analysis / closing
            "current_market": "",
            "current_stock": "",
            "progress": 0,           # 0~100
            "total_targets": 0,
            "analyzed_count": 0,
            "skipped_by_budget": 0,  # 잔고 부족으로 스킵된 종목 수
            "cycle_count": 0,
            "started_at": "",
            "last_scan_at": "",
            "available_cash": 0,     # 현재 예수금 (KRW)
            "cheapest_skipped": "",  # 가장 저렴했지만 스킵된 종목 정보
        }

        # 결과 저장
        self.scan_results: List[Dict] = []   # BUY 분석 결과만 저장
        self.candidates: List[Dict] = []      # 최종 매수 대상 (전략별 비교 후 선별)
        self._buy_pool: List[Dict] = []       # BUY 종목 풀 (후보 선별 전)
        self.trade_log: List[Dict] = []       # 거래 기록 (인메모리, DB 저장)

        # 보유종목 매도 추적
        self.holdings: List[Dict] = []        # 현재 보유종목 (매도추적용)
        self.fee_calc = FeeCalculator()

        # SSE 구독자
        self._subscribers: List[asyncio.Queue] = []

        # 환율 캐시 {market: {"rate": float, "updated_at": float}}
        self._fx_cache: Dict[str, Dict] = {}
        self._available_cash: int = 0  # 주문가능금액 (KRW)
        self._margin_by_market: Dict = {}  # 시장별 주문가능금액

        # ── 포트폴리오 분배 (스윙 50% / 단타 50%) ──
        self._portfolio_alloc = {"스윙": 0.50, "단타": 0.50}
        self._portfolio_used: Dict[str, int] = {"스윙": 0, "단타": 0}  # 전략별 사용 금액

        # ── 블랙리스트 (종목 정보 없음 등 영구 에러) ──
        self._symbol_blacklist: set = set()  # 세션 동안 매수 제외 종목

        # ── Off-Market 활동 데이터 ──
        self._candle_cache: Dict[str, Dict] = {}   # 사전 수집 캔들
        self._news_cache: List[Dict] = []           # 뉴스/공시
        self._ai_stats: Dict = {"total": 0, "correct": 0, "accuracy": 0, "details": []}  # AI 정확도
        self._premarket_picks: List[Dict] = []      # 프리마켓 후보
        self._ta_cache: Dict[str, Dict] = {}        # 기술적 분석 캐시
        self._global_analysis: Dict = {}            # 글로벌 연동 분석
        self._offmarket_done: bool = False           # 이미 실행 여부

        # ── DB + 벡터 스토어 ──
        self._db = DatabaseManager()
        try:
            self._vector_store = StockVectorStore()
        except Exception:
            self._vector_store = None
        self.strategy_store = StrategyStore(db=self._db, vector_store=self._vector_store)
        self.notifier = NotificationService(db=self._db)
        self._load_scanner_state()  # DB에서 이전 스캔 결과 복원
        self.offmarket_state: Dict = {
            "status": "idle",          # idle / running / done
            "current_task": "",
            "progress": 0,             # 0~6
            "last_run": "",
            "tasks": {}
        }

    # ──────────────────────────────────────
    # 로깅
    # ──────────────────────────────────────
    def _log(self, level: str, message: str):
        """로그 기록 + SSE 전송 + ai_log 연동"""
        ts = datetime.now().strftime("%H:%M:%S")
        entry = {"time": ts, "level": level, "message": message}

        # ai_log 함수로 전달 (app.py에서 주입)
        if self._log_fn:
            self._log_fn(level, f"[Scanner] {message}")

        # SSE 구독자에게 전송
        dead = []
        for q in self._subscribers:
            try:
                q.put_nowait(entry)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            self._subscribers.remove(q)

    # ──────────────────────────────────────
    # 스캔 결과 영속화 (DB)
    # ──────────────────────────────────────
    def _save_scanner_state(self):
        """스캔 결과를 DB에 저장 (서버 재시작 시 복원용)"""
        try:
            cycle_id = self.state.get("cycle_count", 0)
            saved = self._db.save_scan_results(
                cycle_id=cycle_id,
                results=self.scan_results[-200:],
                candidates=self.candidates,
            )
            if saved > 0:
                self._log("INFO", f"💾 스캔결과 DB 저장: {saved}건 (사이클 #{cycle_id})")
                # 오래된 사이클 정리 (최근 10개만 유지)
                self._db.cleanup_old_scans(keep_cycles=10)
        except Exception as e:
            self._log("WARN", f"스캔결과 DB 저장 실패: {str(e)[:40]}")

    def _load_scanner_state(self):
        """DB에서 스캔 결과 복원 + _refine_candidates로 후보 재선별"""
        try:
            results, candidates, cycle_id = self._db.load_latest_scan_results()
            if results:
                # BUY 결과만 scan_results에 복원
                self.scan_results = [r for r in results if r.get("ai_action") == "BUY"]
                self.state["cycle_count"] = cycle_id

                # 잔고 조회 후 후보 재선별
                self._refresh_cash()
                if candidates:
                    self._buy_pool = candidates
                    self._refine_candidates()

                self._log("INFO",
                    f"📂 DB 복원: BUY분석 {len(self.scan_results)}건, "
                    f"후보 {len(self.candidates)}건 (사이클 #{cycle_id})")
        except Exception as e:
            self._log("WARN", f"스캔결과 DB 복원 실패: {str(e)[:40]}")

    # ──────────────────────────────────────
    # 후보 선별 (전략별 비교)
    # ──────────────────────────────────────
    def _refine_candidates(self):
        """_buy_pool에서 예산/전략 검증하여 candidates 최신화"""
        if not self._buy_pool:
            return

        LOT_BY_MARKET = {"JP": 100, "CN": 100, "HK": 100}
        cash = self._available_cash
        if cash <= 0:
            return

        # 1) 예산 필터링 (기존 로직 유지)
        affordable = []
        for item in self._buy_pool:
            p_krw = item.get("price_krw", 0) or 0
            if p_krw <= 0:
                raw_p = item.get("price", 0) or 0
                mkt = item.get("market", "")
                if mkt == "KR" and raw_p > 0:
                    p_krw = int(raw_p)
                elif raw_p > 0:
                    p_krw = int(raw_p * DEFAULT_FX_RATES.get(mkt, 1400)) # 매직 넘버 제거
            if p_krw <= 0:
                continue
            lot = LOT_BY_MARKET.get(item.get("market", ""), 1)
            min_cost = p_krw * lot
            if min_cost <= cash:
                item["_min_cost_krw"] = min_cost
                affordable.append(item)
            else:
                self._log("INFO",
                    f"💰 예산 초과: {item.get('name', '')} "
                    f"₩{p_krw:,}×{lot}=₩{min_cost:,} > ₩{cash:,}")

        # 2) 전략별 예산 한도 내 선정 (메소드 분리 적용)
        # Helper 메소드 호출
        selected = self._helper.select_balanced_portfolio(affordable, cash)

        # 기존 추적 중인 후보는 유지 (단, 'filled'는 제외 - 보유항목 탭에서 관리)
        existing_tracked = [
            c for c in self.candidates
            if c.get("tracking_status") in ("tracking", "analyzing", "watching", "ordering")
            and c.get("symbol", "") not in {s.get("symbol") for s in selected}
        ]
        self.candidates = existing_tracked + selected

        self._log("INFO",
            f"📋 후보 선별: 풀 {len(self._buy_pool)}→예산필터 {len(affordable)}"
            f"→선정 {len(selected)} (스윙 {len([s for s in selected if s.get('buy_trade_type')=='스윙'])}"
            f"+단타 {len([s for s in selected if s.get('buy_trade_type')=='단타'])})"
            f" / 기존추적 {len(existing_tracked)}건 유지"
            f" / 총 {len(self.candidates)}건")

    # ──────────────────────────────────────
    # Helper Methods Injection (from Refactoring Step 1)
    # ──────────────────────────────────────
    def _select_balanced_portfolio(self, affordable_candidates: List[Dict], cash: int) -> List[Dict]:
        """
        예산과 밸런싱 비율에 맞춰 매수 후보 선정
        Args:
            affordable_candidates: 예산 내 매수 가능한 후보 목록
            cash: 가용 예산
        Returns:
            List[Dict]: 최종 선정된 매수 후보
        """
        # 현재 보유/추적 중인 수량 파악
        current_swing = len([h for h in self.holdings if h.get("trade_type") == "스윙"])
        current_day = len([h for h in self.holdings if h.get("trade_type") == "단타"])
        
        existing_tracked = [
            c for c in self.candidates
            if c.get("tracking_status") in ("tracking", "analyzing", "watching", "ordering")
        ]
        
        seen_symbols = set()
        for c in existing_tracked:
            if c.get("buy_trade_type") == "단타":
                current_day += 1
            else:
                current_swing += 1
            seen_symbols.add(c.get("symbol"))

        # 유효 후보 풀 분리
        pool_day = [
            x for x in affordable_candidates 
            if x.get("buy_trade_type") == "단타" 
            and x.get("symbol") not in seen_symbols 
            and x.get("symbol") not in self._symbol_blacklist
        ]
        pool_swing = [
            x for x in affordable_candidates 
            if x.get("buy_trade_type") == "스윙" 
            and x.get("symbol") not in seen_symbols 
            and x.get("symbol") not in self._symbol_blacklist
        ]
        
        # 점수순 정렬
        pool_day.sort(key=lambda x: x.get("ai_score", 0), reverse=True)
        pool_swing.sort(key=lambda x: x.get("ai_score", 0), reverse=True)
        
        selected = []
        current_used = 0
        
        # 예산 내에서 밸런싱하며 선택
        while current_used < cash:
            if not pool_day and not pool_swing:
                break
                
            item = None
            item_type = ""
            
            # 밸런싱 로직: 적은 쪽 우선, 같으면 점수 높은 쪽, 예외적으로 단타 과다 시 스윙 우선
            if current_day < current_swing:
                if pool_day:
                    item = pool_day.pop(0)
                    item_type = "단타"
                elif pool_swing:
                    item = pool_swing.pop(0)
                    item_type = "스윙"
            elif current_swing < current_day:
                if pool_swing:
                    item = pool_swing.pop(0)
                    item_type = "스윙"
                elif pool_day:
                    item = pool_day.pop(0)
                    item_type = "단타"
            else:
                # 개수 동일: 점수 비교
                score_day = pool_day[0].get("ai_score", 0) if pool_day else -1
                score_swing = pool_swing[0].get("ai_score", 0) if pool_swing else -1
                
                if score_day >= score_swing and pool_day:
                    item = pool_day.pop(0)
                    item_type = "단타"
                elif pool_swing:
                    item = pool_swing.pop(0)
                    item_type = "스윙"
            
            if item:
                cost = item.get("_min_cost_krw", 0)
                if current_used + cost <= cash:
                    selected.append(item)
                    current_used += cost
                    if item_type == "단타":
                        current_day += 1
                    else:
                        current_swing += 1
                else:
                    continue
        
        return selected

    def _update_candidate_with_prediction(self, candidate: Dict, predicted: Dict):
        """AI 예측 결과로 후보 정보 업데이트"""
        candidate["predicted_buy_price"] = float(predicted["buy_price"])
        candidate["buy_strategy_type"] = predicted.get("strategy_type", "pullback")
        candidate["buy_trade_type"] = predicted.get("trade_type", "스윙")
        candidate["buy_risk_level"] = predicted.get("risk_level", 5)
        candidate["buy_recommended_qty"] = predicted.get("recommended_qty", 1)
        candidate["buy_stop_loss"] = predicted.get("stop_loss")
        candidate["buy_target_price"] = predicted.get("target_price")
        candidate["buy_reason"] = predicted.get("reason", "")
        candidate["buy_confidence"] = predicted.get("confidence", 50)
        candidate["tracking_status"] = "watching"

    def _log_buy_signal(self, candidate: Dict, predicted: Dict):
        """매수 신호 로깅"""
        symbol = candidate.get("symbol", "")
        buy_price = candidate["predicted_buy_price"]
        qty = candidate["buy_recommended_qty"]
        risk = candidate["buy_risk_level"]
        trade_label = candidate["buy_trade_type"]
        strategy_label = "돌파" if candidate["buy_strategy_type"] == "breakout" else "눌림목"
        
        self._log("BULL",
            f"🎯 [{trade_label}/{strategy_label}] {candidate.get('name', symbol)} "
            f"매수가 ${buy_price:.2f} / {qty}주 "
            f"(위험도 {risk}/10, "
            f"목표 ${predicted.get('target_price', 0):.2f}, "
            f"손절 ${predicted.get('stop_loss', 0):.2f})")

    def _check_buy_condition(self, candidate: Dict) -> bool:
        """매수 조건 도달 여부 확인"""
        pred_price = candidate.get("predicted_buy_price", 0)
        current = candidate.get("live_price", 0)
        strategy = candidate.get("buy_strategy_type", "pullback")
        status = candidate.get("tracking_status")

        if pred_price > 0 and current > 0 and status == "watching":
            if strategy == "breakout":
                if current >= pred_price:
                    self._log("ALERT", f"🚀 {candidate.get('name')} 🔥 돌파 매매! ${current:.2f} ≥ ${pred_price:.2f}")
                    return True
            else: # pullback
                if current <= pred_price:
                    self._log("ALERT", f"🚀 {candidate.get('name')} 💰 눌림목 매칭! ${current:.2f} ≤ ${pred_price:.2f}")
                    return True
        return False

    async def _process_individual_candidate(self, candidate: Dict, market: str, active_markets: List[str]) -> bool:
        """
        개별 매수 후보의 실시간 처리 (가격 갱신, 손절 체크, 매수 판단)
        Returns:
            bool: 처리 완료 여부 (True면 상위 루프에서 continue 가능)
        """
        symbol = candidate.get("symbol", "")
        is_filled = candidate.get("tracking_status") == "filled"

        # 1. 실시간 가격 조회
        ref = candidate.get("price", 0)
        live_price = await self._fetch_live_price(symbol, market, ref_price=ref)
        
        if live_price and live_price > 0:
            candidate["live_price"] = live_price
            if is_filled and candidate.get("order_price", 0) > 0:
                base = candidate["order_price"]
            else:
                base = candidate.get("price", live_price)
            candidate["live_change"] = round(((live_price - base) / base) * 100, 2) if base > 0 else 0
            candidate["last_updated"] = datetime.now().strftime("%H:%M:%S")

        # 2. 체결된 종목: 하드 손절 체크만 수행
        if is_filled:
            if candidate.get("live_change", 0) <= -5.0: # TODO: Configurable Hard Stop
                self._log("ALERT", f"🛑 [HARD STOP] {symbol} 수익률 {candidate['live_change']}% 도달 — 긴급 손절 실행")
                holding_data = {
                    "symbol": symbol,
                    "name": candidate.get("name", symbol),
                    "market": market,
                    "exchange": candidate.get("exchange", "NASD"),
                    "quantity": candidate.get("qty", 0),
                    "current_price": live_price,
                    "avg_price": candidate.get("order_price", 0),
                    "lot_size": candidate.get("lot_size", 1),
                    "sell_status": "selling"
                }
                await self._execute_sell(holding_data)
                candidate["tracking_status"] = "sold"
            return True

        # 3. 미체결 종목: AI 매수 타이밍 예측
        if not candidate.get("predicted_buy_price") and candidate.get("ai_action") == "BUY":
            candidate["tracking_status"] = "analyzing"
            predicted = await self._predict_buy_timing(candidate)
            if predicted and predicted.get("buy_price", 0) > 0:
                self._update_candidate_with_prediction(candidate, predicted)
                self._log_buy_signal(candidate, predicted)
            else:
                candidate["tracking_status"] = "watching"

        # 4. 매수 조건 확인 및 실행
        if self._check_buy_condition(candidate):
            candidate["tracking_status"] = "ordering"
            await self._execute_buy(candidate)
            
        return False

    # ──────────────────────────────────────
    # 환율 및 잔고
    # ──────────────────────────────────────
    def _fetch_fx_rate(self, market: str) -> float:
        """Yahoo Finance에서 환율 조회 (→ KRW). KR은 1.0 반환."""
        if market == "KR":
            return 1.0

        # 캐시 확인 (1시간 유효)
        cached = self._fx_cache.get(market)
        if cached and (time.time() - cached["updated_at"]) < 3600:
            return cached["rate"]

        symbol = FX_SYMBOLS.get(market)
        if not symbol:
            return 1.0

        # KIS API를 통한 환율 조회 시도 (1순위) - 더 정확함
        try:
            # 여기서는 예시로 남겨두지만, 실제 KIS API에 환율 조회 기능이 있다면 그것을 우선 사용하는 것이 좋음.
            # 현재 구현된 KISApi에는 명시적인 환율 조회 메서드가 없으므로 Yahoo Finance 유지하되, 
            # 실패 시 하드코딩된 값보다는 이전 캐시값이나 DB 저장값을 활용하는 로직 추가 가능.
            pass 
        except Exception:
            pass

        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=1d"
            resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code == 200:
                data = resp.json()
                result = data.get("chart", {}).get("result", [])
                if result:
                    close = result[0].get("indicators", {}).get("quote", [{}])[0].get("close", [])
                    # None이 아닌 마지막 값 찾기
                    valid_closes = [c for c in close if c is not None]
                    if valid_closes:
                        rate = valid_closes[-1]
                        self._fx_cache[market] = {"rate": rate, "updated_at": time.time()}
                        self._log("INFO", f"💱 환율 [{market}→KRW]: {rate:,.2f}")
                        return rate
        except Exception as e:
            self._log("WARN", f"환율 조회 실패 [{market}]: {str(e)[:40]}")

        # 조회 실패 시, 기존 캐시가 있다면 만료되었더라도 사용 (급격한 변동보다는 나음)
        if cached:
            self._log("WARN", f"환율 조회 실패로 만료된 캐시 사용 [{market}]: {cached['rate']}")
            return cached["rate"]

        # 기본 환율 (fallback) - 최후의 수단
        return DEFAULT_FX_RATES.get(market, 1.0)

    def _refresh_cash(self):
        """KIS API에서 주문가능금액 조회 (통합증거금 우선)"""
        try:
            # 1순위: 통합증거금 API (정확한 주문가능금액)
            margin = self.collector.kis.inquire_intgr_margin()
            if margin:
                krw_avail = margin.get("krw_order_available", 0)
                usd_avail = margin.get("usd_order_available", 0)

                # 외화 → 원화 환산 후 합산
                fx_usd = self._fetch_fx_rate("US")  # USD/KRW 환율
                usd_in_krw = int(usd_avail * fx_usd) if fx_usd > 0 else 0

                total_avail = krw_avail + usd_in_krw

                if total_avail > 0:
                    self._available_cash = total_avail
                    # 시장별 주문가능금액 저장
                    self._margin_by_market = margin
                    self._log("INFO",
                        f"💰 주문가능금액: {total_avail:,}원 "
                        f"(KRW:{krw_avail:,} + USD:${usd_avail:,.2f}×{fx_usd:,.0f}={usd_in_krw:,}원)")
                    return
        except Exception as e:
            self._log("WARN", f"통합증거금 조회 실패: {str(e)[:50]}")

        try:
            # 2순위: 기본 잔고 조회
            balance = self.collector.kis.inquire_balance()
            order_avail = balance.get("order_available", 0) or balance.get("cash", 0)
            self._available_cash = order_avail
        except Exception:
            pass

    def _price_to_krw(self, price: float, market: str) -> float:
        """외화 가격을 KRW로 환산"""
        return price * self._fetch_fx_rate(market)

    # ──────────────────────────────────────
    # 시장 상태 체크
    # ──────────────────────────────────────
    def get_active_markets(self) -> List[str]:
        """현재 KST 시간 기준 활성 시장 반환"""
        now = datetime.now()
        hour, minute = now.hour, now.minute
        current = hour * 60 + minute
        weekday = now.weekday()

        active = []

        # 월~금: 아시아/미국 시장 체크
        if weekday < 5:
            for market, hours in MARKET_HOURS_KST.items():
                open_m = hours["open"][0] * 60 + hours["open"][1]
                close_m = hours["close"][0] * 60 + hours["close"][1]

                if market == "US":
                    # US 시장은 KST 기준 23:30 (당일) ~ 06:00 (다음날)
                    # 따라서, 현재 시간이 23:30 이후이거나 06:00 이전이면 활성
                    if current >= open_m or current < close_m:
                        active.append(market)
                else:
                    if open_m <= current < close_m:
                        active.append(market)

        # 토요일 새벽 0~6시 = 미국 금요일 오후 (개장 중)
        if weekday == 5:
            us_close_m = MARKET_HOURS_KST["US"]["close"][0] * 60 + MARKET_HOURS_KST["US"]["close"][1]
            if current < us_close_m:
                if "US" not in active:
                    active.append("US")

        return active

    def get_all_market_status(self) -> Dict:
        """모든 시장의 개장/폐장 상태"""
        active = self.get_active_markets()
        result = {}
        for market, info in MARKET_INFO.items():
            result[market] = {
                "name": info["name"],
                "flag": info["flag"],
                "hours": info["hours"],
                "active": market in active,
            }
        return result

    # ──────────────────────────────────────
    # Phase 1: 종목 선정
    # ──────────────────────────────────────
    def _fetch_affordable_stocks(self, market: str, max_price_usd: float) -> List[Dict]:
        """Yahoo Finance Screener로 예수금 내 매수 가능 종목 검색"""
        if market != "US":
            return []

        headers = {"User-Agent": "Mozilla/5.0"}
        affordable = []
        seen_symbols = set()

        # 여러 Yahoo Screener 카테고리에서 종목 수집
        screener_ids = ["most_actives", "day_gainers", "day_losers",
                        "small_cap_gainers"]
        for scr_id in screener_ids:
            try:
                url = "https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved"
                params = {
                    "formatted": "false",
                    "lang": "en-US",
                    "region": "US",
                    "scrIds": scr_id,
                    "count": 100,
                }
                resp = requests.get(url, params=params, headers=headers, timeout=10)
                if resp.status_code != 200:
                    continue

                data = resp.json()
                quotes = data.get("finance", {}).get("result", [{}])[0].get("quotes", [])
                for q in quotes:
                    sym = q.get("symbol", "")
                    if sym in seen_symbols:
                        continue
                    price = q.get("regularMarketPrice", 0)
                    if price and 0.1 < price <= max_price_usd:
                        vol = q.get("regularMarketVolume", 0)
                        mcap = q.get("marketCap", 0)
                        # 최소 거래량 5만, 시가총액 100만 달러
                        if vol >= 50000 and mcap >= 1_000_000:
                            seen_symbols.add(sym)
                            affordable.append({
                                "symbol": sym,
                                "name": q.get("shortName", q.get("longName", sym)),
                                "price": price,
                                "change_rate": round(q.get("regularMarketChangePercent", 0), 2),
                                "volume": vol,
                                "market": market,
                                "mcap": round(mcap / 1e9, 1),
                            })
            except Exception:
                continue

        if affordable:
            self._log("INFO",
                f"📡 Yahoo Screener: {len(affordable)}개 매수가능 종목 발견 "
                f"(${max_price_usd:.2f} 이하)"
            )
            # 거래량 기준 정렬
            affordable.sort(key=lambda x: x["volume"], reverse=True)
            return affordable[:MAX_TARGETS_PER_MARKET]

        # Screener 결과 없으면 → 개별 저가 종목 차트 API로 가격 확인
        self._log("INFO", "📡 저가 종목 직접 조회 중...")
        penny_candidates = [
            "SIRI", "SNAP", "SOFI", "F", "NIO", "RIVN", "LCID",
            "GRAB", "NU", "MARA", "RIOT", "CLSK", "DNA", "TELL",
            "GSAT", "BB", "NOK", "PLUG", "OPEN", "SNDL", "RIG",
            "QNCX", "FFIE", "MULN", "GOEV", "LYG", "GOLD", "KGC",
        ]
        for sym in penny_candidates:
            try:
                url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
                params = {"interval": "1d", "range": "2d"}
                resp = requests.get(url, params=params, headers=headers, timeout=5)
                if resp.status_code != 200:
                    continue
                result = resp.json().get("chart", {}).get("result", [])
                if not result:
                    continue
                meta = result[0].get("meta", {})
                price = meta.get("regularMarketPrice", 0)
                if price and 0.1 < price <= max_price_usd:
                    affordable.append({
                        "symbol": sym,
                        "name": meta.get("shortName", sym),
                        "price": price,
                        "change_rate": 0,
                        "volume": meta.get("regularMarketVolume", 0),
                        "market": market,
                        "mcap": 0,
                    })
            except Exception:
                continue

        if affordable:
            self._log("INFO",
                f"📡 저가 종목 직접 조회: {len(affordable)}개 발견 "
                f"(${max_price_usd:.2f} 이하)"
            )
        return affordable[:MAX_TARGETS_PER_MARKET]

    async def select_targets(self, market: str) -> List[Dict]:
        """국가별 매수 가능 종목 선정 (예수금 기반 필터링 + 실시간 랭킹 포함)"""
        self.state["phase"] = "target_select"
        self.state["current_market"] = market
        self._log("SCAN", f"🎯 [{market}] 종목 선정 시작")

        targets = []
        seen_symbols = set()

        # 1. 예수금 및 환율 확인
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(self._executor, self._refresh_cash)
        fx_rate = await loop.run_in_executor(self._executor, self._fetch_fx_rate, market)
        max_price_local = self._available_cash / fx_rate if fx_rate > 0 else 0

        # 2. 동적 랭킹 수집 (KIS or Yahoo)
        try:
            rankings = await loop.run_in_executor(
                self._executor,
                lambda: self.collector.get_market_rankings(
                    market, top_n=MAX_TARGETS_PER_MARKET, max_price=max_price_local
                )
            )
            for r in rankings:
                if r["symbol"] not in seen_symbols:
                    targets.append(r)
                    seen_symbols.add(r["symbol"])
            
            if rankings:
                self._log("INFO", f"🔥 [{market}] 실시간 랭킹/급등주 {len(rankings)}개 로드")
        except Exception as e:
            self._log("WARN", f"[{market}] 랭킹 조회 실패: {str(e)[:60]}")

        # 3. 고정 리스트(stocks.json) 병합 (랭킹에 없는 우량주 보완)
        stock_list = COUNTRY_STOCKS.get(market, [])
        added_fixed = 0
        
        for stock_tuple in stock_list:
            code = stock_tuple[0]
            name = stock_tuple[1]
            mcap = stock_tuple[2]
            exch = stock_tuple[3] if len(stock_tuple) > 3 else None
            
            if code not in seen_symbols:
                # 가격 정보가 없으므로 일단 추가하고 나중에 필터링하거나,
                # 여기서 간단히 mcap 등으로 1차 필터링
                t = {
                    "symbol": code,
                    "name": name,
                    "price": 0, # 가격 미확인 상태
                    "change_rate": 0,
                    "volume": 0,
                    "market": market,
                    "mcap": mcap,
                }
                if exch:
                    t["exchange"] = exch
                targets.append(t)
                seen_symbols.add(code)
                added_fixed += 1
                
        if added_fixed > 0:
            self._log("INFO", f"📋 [{market}] 고정 리스트에서 {added_fixed}개 추가")

        # 4. 잔고 기반 저가주 검색 (미국장 한정, 잔고가 적을 때)
        if market == "US" and max_price_local > 0 and self._available_cash > 0 and len(targets) < 10:
             affordable = await loop.run_in_executor(
                self._executor,
                lambda: self._fetch_affordable_stocks(market, max_price_local)
            )
             for stock in affordable:
                if stock["symbol"] not in seen_symbols:
                    targets.append(stock)
                    seen_symbols.add(stock["symbol"])
             if affordable:
                 self._log("INFO", f"🔍 [{market}] 잔고 맞춤 저가주 {len(affordable)}개 추가")

        self.state["total_targets"] = len(targets)
        self._log("SCAN", f"[{market}] 최종 분석 대상 {len(targets)}개 선정 완료")
        return targets

    # ──────────────────────────────────────
    # Phase 2: 차트 데이터 수집
    # ──────────────────────────────────────
    def _fetch_yahoo_candles(self, symbol: str, market: str,
                              interval: str, range_str: str) -> List[Dict]:
        """Yahoo Finance에서 캔들 데이터 수집 (동기)"""
        suffix_fn = YAHOO_SUFFIX.get(market, lambda c: "")
        yahoo_symbol = symbol + suffix_fn(symbol)

        url = (
            f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_symbol}"
            f"?interval={interval}&range={range_str}"
        )
        try:
            resp = requests.get(url, timeout=10, headers={
                "User-Agent": "Mozilla/5.0"
            })
            if resp.status_code != 200:
                return []

            data = resp.json()
            result_data = data.get("chart", {}).get("result", [])
            if not result_data:
                return []

            r = result_data[0]
            timestamps = r.get("timestamp", [])
            quote = r.get("indicators", {}).get("quote", [{}])[0]

            opens = quote.get("open", [])
            highs = quote.get("high", [])
            lows = quote.get("low", [])
            closes = quote.get("close", [])
            volumes = quote.get("volume", [])

            candles = []
            for i in range(len(timestamps)):
                if all(v is not None for v in [opens[i], highs[i], lows[i], closes[i]]):
                    candles.append({
                        "time": timestamps[i],
                        "open": opens[i],
                        "high": highs[i],
                        "low": lows[i],
                        "close": closes[i],
                        "volume": volumes[i] or 0,
                    })

            return candles[-200:]  # 최근 200개

        except Exception:
            return []

    async def collect_candles(self, symbol: str, market: str) -> Dict:
        """종목의 5분/1시간/1일 캔들 수집"""
        loop = asyncio.get_event_loop()

        # 병렬로 3개 타임프레임 수집
        intervals = [
            ("5m", "5d"),     # 5분봉, 5일치 → ~200개
            ("1h", "1mo"),    # 1시간봉, 1개월 → ~200개
            ("1d", "1y"),     # 1일봉, 1년 → ~250개
        ]

        results = {}
        futures = []
        for interval, range_str in intervals:
            futures.append(
                loop.run_in_executor(
                    self._executor,
                    self._fetch_yahoo_candles,
                    symbol, market, interval, range_str
                )
            )

        fetched = await asyncio.gather(*futures, return_exceptions=True)
        labels = ["5m", "1h", "1d"]
        for i, data in enumerate(fetched):
            if isinstance(data, Exception):
                results[labels[i]] = []
            else:
                results[labels[i]] = data

        total = sum(len(v) for v in results.values())
        return {"symbol": symbol, "market": market, "candles": results, "total_candles": total}

    # ──────────────────────────────────────
    # Phase 3: AI 분석 + 매수 판단
    # ──────────────────────────────────────
    def _build_analysis_prompt(self, stock: Dict, candle_data: Dict) -> str:
        """AI 분석용 프롬프트 생성"""
        candles = candle_data.get("candles", {})

        # 캔들 요약 텍스트 생성 (Technical Analysis 적용)
        summaries = []
        for tf in ["5m", "1h", "1d"]:
            tf_candles = candles.get(tf, [])
            if not tf_candles:
                summaries.append(f"[{tf}] 데이터 없음")
                continue
            
            # [Step 2] 기술적 지표 계산 (Pandas 기반)
            ta_result = analyze_candles(tf_candles)
            
            # 기본 데이터
            closes = [c["close"] for c in tf_candles]
            latest = closes[-1] if closes else 0
            
            summary_text = (
                f"[{tf}봉 {len(tf_candles)}개] 현재가: {latest:,.0f}\n"
                f"  기술적 지표: {ta_result.get('summary', '분석불가')}\n"
                f"  RSI: {ta_result.get('rsi', 0):.1f} | MACD: {ta_result.get('macd', 0):.2f}\n"
                f"  MA5: {ta_result.get('ma5', 0):,.0f} | MA20: {ta_result.get('ma20', 0):,.0f} | MA60: {ta_result.get('ma60', 0):,.0f}"
            )
            summaries.append(summary_text)

        # 캔들 데이터 + 수수료 정보 포함
        candle_text = "\n".join(summaries)
        
        # 왕복 수수료 예상
        price = stock.get("price", 0)
        market = stock.get("market", "US")
        exchange_map = {"US": "NASD", "JP": "TKSE", "HK": "SEHK", "CN": "SHAA"}
        exchange = exchange_map.get(market, "NASD")
        
        fee_info = self.fee_calc.estimate_round_trip_fee(price, 1, market=market, exchange=exchange) if price > 0 else {"message": "수수료 확인 불가"}
        fee_context = f"=== 거래 비용 정보 ===\n- 왕복 예상 수수료: {fee_info.get('round_trip_rate', 0)*100:.3f}% ({fee_info.get('message', '')})"

        # 활성 전략 정보 포함
        active_strats = self._db.get_strategies(active_only=True)
        strat_context = ""
        if active_strats:
            strat_lines = []
            for s in active_strats:
                strat_lines.append(f"- [{s['name']}]: {s.get('conditions', '{}')}")
            strat_context = "=== 활성 매매 전략 (준수 필수) ===\n" + "\n".join(strat_lines)

        return f"""역할: 당신은 20년 경력의 퀀트 트레이더입니다.
종목: {stock.get('name', 'N/A')} ({stock.get('symbol', '')}) [{stock.get('market', '')}]
현재가: {stock.get('price', 0):,} | 등락률: {stock.get('change_rate', 0):+.2f}%

=== 차트 데이터 분석 ===
{candle_text}

{fee_context}

{strat_context}

=== 분석 요청 ===
위 멀티-타임프레임 데이터와 거래 비용을 종합 분석하여 매수 여부를 판단하세요.
- 핵심 지침: 예상 수익률이 왕복 수수료를 충분히 상회하는 '기대 수익비'가 높은 구간에서만 BUY를 추천하세요.
- 전략 준수: 활성 매매 전략이 있는 경우, 해당 조건에 얼마나 부합하는지 비중있게 검토하세요.

JSON 형식으로 응답:
{{
  "action": "BUY" | "HOLD" | "AVOID", 
  "score": 0~100, 
  "confidence": 0~100, 
  "reason": "판단 근거 (전략 부합 여부 포함, 2~3문장)", 
  "target_price": 목표가, 
  "stop_loss": 손절가, 
  "timeframe": "단기|중기|장기",
  "matched_strategy_id": 부합하는 전략 ID (없으면 null)
}}"""

    async def analyze_stock(self, stock: Dict, candle_data: Dict) -> Dict:
        """AI를 이용한 종목 분석"""
        prompt = self._build_analysis_prompt(stock, candle_data)

        loop = asyncio.get_event_loop()
        try:
            result = await loop.run_in_executor(
                self._executor,
                lambda: self.antigravity._call_ai(
                    prompt,
                    system_prompt="한국/미국/일본/중국/홍콩 주식 시장 전문 퀀트 트레이더",
                    json_mode=True
                )
            )

            if result.get("success"):
                parsed = self.antigravity._extract_json(result.get("content", ""))
                if parsed:
                    return {
                        **stock,
                        "ai_action": parsed.get("action", "HOLD"),
                        "ai_score": parsed.get("score", 0),
                        "ai_confidence": parsed.get("confidence", 0),
                        "ai_reason": parsed.get("reason", ""),
                        "target_price": parsed.get("target_price", 0),
                        "stop_loss": parsed.get("stop_loss", 0),
                        "timeframe": parsed.get("timeframe", ""),
                        "candle_count": candle_data.get("total_candles", 0),
                        "analyzed_at": datetime.now().strftime("%H:%M:%S"),
                    }
            return {
                **stock,
                "ai_action": "ERROR",
                "ai_score": 0,
                "ai_confidence": 0,
                "ai_reason": result.get("error", "분석 실패"),
                "analyzed_at": datetime.now().strftime("%H:%M:%S"),
            }

        except Exception as e:
            return {
                **stock,
                "ai_action": "ERROR",
                "ai_score": 0,
                "ai_reason": str(e)[:80],
                "analyzed_at": datetime.now().strftime("%H:%M:%S"),
            }

    # ──────────────────────────────────────
    # Phase 4: 장마감 분석
    # ──────────────────────────────────────
    async def closing_analysis(self) -> List[Dict]:
        """장마감 후 최종 분석 — 다음 장 매수 후보 선정"""
        self.state["phase"] = "closing"
        self._log("SYSTEM", "📊 장마감 최종 분석 시작")

        # 오늘 분석된 BUY 후보들 중 상위 정렬
        buy_candidates = [
            r for r in self.scan_results
            if r.get("ai_action") == "BUY" and r.get("ai_score", 0) >= BUY_SCORE_THRESHOLD
        ]

        buy_candidates.sort(key=lambda x: x.get("ai_score", 0), reverse=True)

        if buy_candidates:
            self._log("BULL", f"📋 장마감 매수 후보 {len(buy_candidates)}개:")
            for i, c in enumerate(buy_candidates[:10], 1):
                self._log("BULL",
                    f"  {i}. {c['name']} ({c['symbol']}) "
                    f"Score:{c.get('ai_score', 0)} "
                    f"Action:{c.get('ai_action', '')} "
                    f"Reason:{c.get('ai_reason', '')[:40]}"
                )
            # _buy_pool에 추가 후 전략별 비교 선별
            self._buy_pool = buy_candidates[:20]
            self._refine_candidates()
        else:
            self._log("INFO", "장마감 분석: 매수 후보 없음")

        self._save_scanner_state()  # 후보 목록 영속화
        return buy_candidates

    # ──────────────────────────────────────
    # 메인 스캔 사이클
    # ──────────────────────────────────────
    async def run_scan_cycle(self, markets: List[str]):
        """한 사이클: 종목 선정 → 캔들 수집 → AI 분석"""
        # ── 자동 스캔 설정 체크 ──
        if self._db.get_setting("ENABLE_AUTO_SCAN", "1") != "1":
            self._log("INFO", "⏸️ 자동 종목 스캔이 비활성화 상태입니다 (설정에서 변경 가능)")
            return

        self.state["status"] = "scanning"
        self.state["cycle_count"] += 1
        cycle = self.state["cycle_count"]
        self._log("SYSTEM", f"🔄 스캔 사이클 #{cycle} 시작 (시장: {', '.join(markets)})")

        for market in markets:
            self.state["current_market"] = market
            flag = MARKET_INFO.get(market, {}).get("flag", "")

            # Phase 1: 종목 선정
            targets = await self.select_targets(market)
            if not targets:
                self._log("WARN", f"{flag} [{market}] 분석 대상 없음 — 스킵")
                continue

            # 예수금 조회 + 환율 조회
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(self._executor, self._refresh_cash)
            self.state["available_cash"] = self._available_cash
            fx_rate = await loop.run_in_executor(self._executor, self._fetch_fx_rate, market)
            self._log("INFO",
                f"💰 예수금: {self._available_cash:,}원"
                + (f" | {market} 환율: {fx_rate:,.2f}원" if market != "KR" else "")
            )
            budget_skip_count = 0
            cheapest_skip = None  # {"name": ..., "price_krw": ...}

            # Phase 2 + 3: 배치 처리 (수집 → 분석)
            self.state["phase"] = "candle_collect"
            self._log("SCAN", f"{flag} [{market}] 차트 데이터 수집 시작 ({len(targets)}개)")

            for batch_idx in range(0, len(targets), BATCH_SIZE):
                batch = targets[batch_idx:batch_idx + BATCH_SIZE]
                batch_num = batch_idx // BATCH_SIZE + 1
                total_batches = (len(targets) + BATCH_SIZE - 1) // BATCH_SIZE

                # Progress 계산
                progress = int((batch_idx / len(targets)) * 100)
                self.state["progress"] = progress

                # 캔들 수집 (배치 병렬)
                candle_tasks = [
                    self.collect_candles(s["symbol"], s["market"])
                    for s in batch
                ]
                candle_results = await asyncio.gather(*candle_tasks, return_exceptions=True)

                # AI 분석 (수집 완료된 것들)
                self.state["phase"] = "ai_analysis"
                for i, (stock, candle_data) in enumerate(zip(batch, candle_results)):
                    if isinstance(candle_data, Exception):
                        self._log("WARN", f"[{stock['name']}] 캔들 수집 실패")
                        continue

                    total_c = candle_data.get("total_candles", 0)
                    if total_c == 0:
                        self._log("WARN", f"[{stock['name']}] 캔들 데이터 없음 — 스킵")
                        continue

                    # ──────────────────────────────────────────────────────────
                    # [Step 1] 데이터 신뢰성 강화: KIS API 실시간 현재가 조회
                    # ──────────────────────────────────────────────────────────
                    try:
                        # 캔들 데이터(Yahoo)는 지연될 수 있으므로, 판단 직전 실시간가 확인
                        loop = asyncio.get_event_loop()
                        live_data = await loop.run_in_executor(
                            self._executor,
                            lambda: self.collector.get_current_price(stock["symbol"], stock["market"])
                        )
                        
                        if live_data and live_data.get("price", 0) > 0:
                            live_price = live_data["price"]
                            live_change = live_data.get("change_rate", 0)
                            
                            # 기존 stock 정보 업데이트 (AI 프롬프트 및 로그용)
                            stock["price"] = live_price
                            stock["change_rate"] = live_change
                            stock["live_data_updated"] = True
                            
                            # Yahoo 데이터와 괴리율 로깅 (디버깅용)
                            yahoo_price = 0
                            if candle_data.get("candles", {}).get("1m"):
                                yahoo_price = candle_data["candles"]["1m"][-1]["close"]
                            elif candle_data.get("candles", {}).get("5m"):
                                yahoo_price = candle_data["candles"]["5m"][-1]["close"]
                                
                            if yahoo_price > 0:
                                diff = abs(live_price - yahoo_price) / yahoo_price * 100
                                if diff >= 1.0:
                                    self._log("INFO", f"⚡ 시세보정: Yahoo {yahoo_price} → KIS {live_price} (괴리율 {diff:.1f}%)")
                    except Exception as e:
                        self._log("WARN", f"실시간 시세 조회 실패 ({stock['symbol']}): {str(e)}")
                        # 실패 시 Yahoo 캔들 데이터의 최신값 사용 (기존 로직 유지)

                    # 캔들에서 현재가/등락률 추출 (price가 0인 경우)
                    # ──────────────────────────────────────────────────────────
                    # [Step 1] 데이터 신뢰성 강화: KIS API 실시간 현재가 조회
                    # ──────────────────────────────────────────────────────────
                    try:
                        # 캔들 데이터(Yahoo)는 지연될 수 있으므로, 판단 직전 실시간가 확인
                        loop = asyncio.get_event_loop()
                        live_data = await loop.run_in_executor(
                            self._executor,
                            lambda: self.collector.get_current_price(stock["symbol"], stock["market"])
                        )
                        
                        if live_data and live_data.get("price", 0) > 0:
                            live_price = live_data["price"]
                            live_change = live_data.get("change_rate", 0)
                            
                            # 기존 stock 정보 업데이트 (AI 프롬프트 및 로그용)
                            stock["price"] = live_price
                            stock["change_rate"] = live_change
                            stock["live_data_updated"] = True
                            
                            # Yahoo 데이터와 괴리율 로깅 (디버깅용)
                            yahoo_price = 0
                            if candle_data.get("candles", {}).get("1m"):
                                yahoo_price = candle_data["candles"]["1m"][-1]["close"]
                            elif candle_data.get("candles", {}).get("5m"):
                                yahoo_price = candle_data["candles"]["5m"][-1]["close"]
                                
                            if yahoo_price > 0:
                                diff = abs(live_price - yahoo_price) / yahoo_price * 100
                                if diff >= 1.0:
                                    self._log("INFO", f"⚡ 시세보정: Yahoo {yahoo_price} → KIS {live_price} (괴리율 {diff:.1f}%)")
                    except Exception as e:
                        self._log("WARN", f"실시간 시세 조회 실패 ({stock['symbol']}): {str(e)}")
                        # 실패 시 Yahoo 캔들 데이터의 최신값 사용 (기존 로직 유지)

                    if not stock.get("price") or stock["price"] == 0:
                        candles = candle_data.get("candles", {})
                        # 5분봉 → 1시간봉 → 일봉 순으로 최신 종가 탐색
                        for tf in ["5m", "1h", "1d"]:
                            tf_candles = candles.get(tf, [])
                            if tf_candles:
                                stock["price"] = tf_candles[-1]["close"]
                                stock["volume"] = tf_candles[-1].get("volume", 0)
                                break
                        # 일봉에서 등락률 계산 (전일 종가 대비)
                        daily = candles.get("1d", [])
                        if len(daily) >= 2 and daily[-2]["close"]:
                            prev_close = daily[-2]["close"]
                            curr_close = daily[-1]["close"]
                            stock["change_rate"] = round(
                                (curr_close - prev_close) / prev_close * 100, 2
                            )

                    # 환율 변환 + 매수 가능 여부 체크 (최소주문단위 포함)
                    price = stock.get("price", 0)
                    if price and self._available_cash > 0:
                        # 거래소별 최소주문단위
                        LOT_BY_MARKET = {"JP": 100, "CN": 100, "HK": 100}
                        lot_size = LOT_BY_MARKET.get(market, 1)

                        if market != "KR":
                            min_cost_krw = round(price * lot_size * fx_rate)
                        else:
                            min_cost_krw = int(price) * lot_size
                        stock["price_krw"] = round(price * fx_rate) if market != "KR" else int(price)

                        if min_cost_krw > self._available_cash:
                            budget_skip_count += 1
                            # 가장 저렴했지만 스킵된 종목 추적
                            if cheapest_skip is None or min_cost_krw < cheapest_skip.get("min_cost_krw", float("inf")):
                                cheapest_skip = {
                                    "name": stock["name"],
                                    "symbol": stock["symbol"],
                                    "price_krw": stock["price_krw"],
                                    "min_cost_krw": min_cost_krw,
                                    "price_orig": price,
                                    "lot_size": lot_size,
                                    "market": market,
                                }
                            continue

                    self.state["current_stock"] = stock["name"]
                    self._log("INFO",
                        f"🤖 [{market}] 분석 중: {stock['name']} "
                        f"({batch_num}/{total_batches}) "
                        f"캔들 {total_c}개"
                    )

                    # AI 분석
                    analysis = await self.analyze_stock(stock, candle_data)
                    self.state["analyzed_count"] += 1

                    action = analysis.get("ai_action", "HOLD")
                    score = analysis.get("ai_score", 0)

                    if action == "BUY":
                        # BUY 결과만 Analysis Results에 저장
                        self.scan_results.append(analysis)

                        if score >= BUY_SCORE_THRESHOLD:
                            # 매수 풀에 추가 (후보 선별은 _refine_candidates에서)
                            self._buy_pool.append(analysis)
                            self._log("BULL",
                                f"🎯 BUY 발견! {stock['name']} "
                                f"Score:{score} — {analysis.get('ai_reason', '')[:50]}"
                            )
                        else:
                            self._log("INFO",
                                f"📊 {stock['name']} BUY(Score:{score}) "
                                f"— 임계값({BUY_SCORE_THRESHOLD}) 미달"
                            )
                    else:
                        self._log("INFO",
                            f"📊 {stock['name']} → {action}(Score:{score})"
                        )

                # 배치 간 딜레이 (rate limit)
                if batch_idx + BATCH_SIZE < len(targets):
                    await asyncio.sleep(BATCH_DELAY)

            # 잔고 필터 요약
            self.state["skipped_by_budget"] += budget_skip_count
            if budget_skip_count > 0:
                skip_msg = (
                    f"💸 [{market}] 잔고 부족 필터: "
                    f"{budget_skip_count}개 종목 스킵 "
                    f"(잔고: {self._available_cash:,}원"
                )
                if cheapest_skip:
                    lot = cheapest_skip.get("lot_size", 1)
                    min_cost = cheapest_skip.get("min_cost_krw", cheapest_skip["price_krw"])
                    lot_info = f" ×{lot}주" if lot > 1 else ""
                    self.state["cheapest_skipped"] = (
                        f"{cheapest_skip['name']} "
                        f"({cheapest_skip['price_krw']:,}원{lot_info} = {min_cost:,}원)"
                    )
                    skip_msg += f", 최저가: {cheapest_skip['name']} {min_cost:,}원{lot_info}"
                skip_msg += ")"
                self._log("WARN", skip_msg)

            self._log("SCAN",
                f"{flag} [{market}] 스캔 완료 — "
                f"분석 {self.state['analyzed_count']}개, "
                f"잔고부족 {budget_skip_count}개, "
                f"BUY풀 {len(self._buy_pool)}개"
            )

            # 시장별 스캔 후 후보 최신화
            self._refine_candidates()

        self.state["progress"] = 100
        self.state["last_scan_at"] = datetime.now().strftime("%H:%M:%S")
        self._log("SYSTEM",
            f"✅ 사이클 #{cycle} 완료 — "
            f"총 분석 {self.state['analyzed_count']}개, "
            f"매수 후보 {len(self.candidates)}개"
        )
        self._save_scanner_state()  # 스캔 결과 영속화

    # ──────────────────────────────────────
    # Buy Candidate 실시간 추적 + 자동 매수
    # ──────────────────────────────────────

    async def _track_candidates(self):
        """Buy Candidates 실시간 가격 추적 + 자동 매수"""
        await asyncio.sleep(10)  # 스캐너 시작 대기
        self._log("SYSTEM", "📡 Buy Candidate 실시간 추적 시작")

        while True:
            try:
                if not self.candidates or self.state["status"] == "stopped":
                    await asyncio.sleep(15)
                    continue

                # ── 장 마감된 시장의 후보 제거 ──
                active_markets = self.get_active_markets()
                before_count = len(self.candidates)
                removed = []
                self.candidates = [
                    c for c in self.candidates
                    if c.get("tracking_status") not in ("filled", "blacklisted")  # 체결/블랙리스트 제거
                    and (c.get("market", "US") in active_markets)
                    or (removed.append(c.get("name", c.get("symbol", ""))) and False)
                ]
                if removed:
                    self._log("INFO",
                        f"🕐 장 마감으로 후보 {len(removed)}개 제거: "
                        f"{', '.join(removed[:5])}"
                        + (f" 외 {len(removed)-5}개" if len(removed) > 5 else ""))

                for candidate in self.candidates:
                    market = candidate.get("market", "US")
                    is_filled = await self._helper.process_individual_candidate(candidate, market, active_markets)
                    if is_filled:
                        continue

                await asyncio.sleep(5)  # 5초 간격 추적

            except Exception as e:
                self._log("ERROR", f"추적 오류: {str(e)[:60]}")
                await asyncio.sleep(30)

    async def _fetch_live_price(self, symbol: str, market: str, ref_price: float = 0) -> float:
        """KIS API를 통한 실시간 시세 조회 (추적용)"""
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(
                self._executor,
                lambda: self.collector.get_current_price(symbol, market)
            )
            if data and data.get("price", 0) > 0:
                return data["price"]
        except Exception:
            pass
        
        # KIS 실패 시 Yahoo 캔들 크롤링 (fallback)
        return ref_price

    async def _predict_buy_timing(self, candidate: Dict) -> Optional[Dict]:
        """AI에 매수 적정가 + 수량 예측 요청 (캔들 분석 + 자율 판단)"""
        # AI 분석 결과
        ai_action = candidate.get("ai_action", "HOLD")
        score = candidate.get("ai_score", 0)
        risk_level = candidate.get("buy_risk_level", 5)
        trade_type = candidate.get("buy_trade_type", "스윙")
        strategy_id = candidate.get("matched_strategy_id") # 전략 ID 기록
        symbol = candidate.get("symbol", "")
        name = candidate.get("name", symbol)
        market = candidate.get("market", "US")
        price = candidate.get("live_price", candidate.get("price", 0))
        score = candidate.get("ai_score", 0)
        reason = candidate.get("ai_reason", "")

        # ── 캔들 데이터 수집 ──
        candle_text = "차트 데이터 없음"
        try:
            candle_data = await self.collect_candles(symbol, market)
            candles = candle_data.get("candles", {})

            summaries = []
            for tf in ["5m", "1h", "1d"]:
                tf_candles = candles.get(tf, [])
                if not tf_candles:
                    summaries.append(f"[{tf}] 데이터 없음")
                    continue

                closes = [c["close"] for c in tf_candles]
                volumes = [c["volume"] for c in tf_candles]
                highs = [c["high"] for c in tf_candles]
                lows = [c["low"] for c in tf_candles]

                latest = closes[-1] if closes else 0
                earliest = closes[0] if closes else 0
                pct_change = ((latest - earliest) / earliest * 100) if earliest else 0
                avg_vol = sum(volumes) / len(volumes) if volumes else 0
                high_max = max(highs) if highs else 0
                low_min = min(lows) if lows else 0

                ma5 = sum(closes[-5:]) / min(5, len(closes)) if closes else 0
                ma20 = sum(closes[-20:]) / min(20, len(closes)) if closes else 0
                ma60 = sum(closes[-60:]) / min(60, len(closes)) if len(closes) >= 10 else 0

                # RSI (14)
                rsi = 50
                if len(closes) >= 15:
                    gains, losses = [], []
                    for i in range(1, min(15, len(closes))):
                        diff = closes[-i] - closes[-i-1]
                        if diff > 0:
                            gains.append(diff)
                        else:
                            losses.append(abs(diff))
                    avg_gain = sum(gains) / 14 if gains else 0.001
                    avg_loss = sum(losses) / 14 if losses else 0.001
                    rsi = 100 - (100 / (1 + avg_gain / avg_loss))

                recent_5 = closes[-5:] if len(closes) >= 5 else closes
                pattern = ""
                if len(recent_5) >= 3:
                    up_cnt = sum(1 for i in range(1, len(recent_5)) if recent_5[i] > recent_5[i-1])
                    dn_cnt = len(recent_5) - 1 - up_cnt
                    pattern = f"최근{len(recent_5)}봉: ↑{up_cnt}/↓{dn_cnt}"

                summaries.append(
                    f"[{tf}봉 {len(tf_candles)}개]\n"
                    f"  현재가: ${latest:.2f} | 구간변동: {pct_change:+.2f}%\n"
                    f"  고가: ${high_max:.2f} | 저가: ${low_min:.2f}\n"
                    f"  MA5: ${ma5:.2f} | MA20: ${ma20:.2f}"
                    + (f" | MA60: ${ma60:.2f}" if ma60 > 0 else "") + "\n"
                    f"  RSI(14): {rsi:.1f} | 평균거래량: {avg_vol:,.0f}\n"
                    f"  {pattern}"
                )

            candle_text = "\n".join(summaries)
        except Exception as e:
            self._log("WARN", f"매수예측 캔들수집 실패 ({symbol}): {str(e)[:40]}")

        # ── 잔고 정보 ──
        _loop = asyncio.get_event_loop()
        fx_rate = (await _loop.run_in_executor(self._executor, self._fetch_fx_rate, market)) or 1450
        avail_usd = round(self._available_cash / fx_rate, 2) if fx_rate > 0 else 0

        # ── 전략 + 패턴 컨텍스트 생성 ──
        strategy_ctx = self.strategy_store.build_strategy_context(market)
        try:
            _candle_for_ind = await self.collect_candles(symbol, market)
            current_indicators = StrategyStore.extract_indicators(_candle_for_ind)
        except Exception:
            current_indicators = {"rsi": 50, "trend": "neutral", "ma5_vs_ma20": "neutral", "bb_position": "middle"}
        pattern_ctx = self.strategy_store.build_pattern_context(symbol, current_indicators, market)

        # ── AI 프롬프트 (자율 판단) ──
        prompt = f"""역할: 20년 경력의 퀀트 트레이더. 매수 진입 전략을 수립하세요.

=== 종목 정보 ===
종목: {name} ({symbol})
현재가: ${price:.2f}
AI 매수 점수: {score}/100
1차 분석 사유: {reason}

=== 계좌 정보 ===
주문가능금액: ${avail_usd:.2f} (USD)
환율: ₩{fx_rate:,.0f}/USD

=== 멀티 타임프레임 차트 분석 ===
{candle_text}

=== 활성 전략 (참고) ===
{strategy_ctx}

=== 학습된 유사 패턴 (참고) ===
{pattern_ctx}

=== 매수 전략 수립 지침 ===
아래 항목에 따라 자율적으로 매수 전략을 수립하세요:

1. **매수 진입가 및 전략**: 지지선, 이동평균, 캔들 패턴(돌파/눌림목)을 분석하여 최적 진입가 제시
   - **눌림목(pullback)**: 조정 시 매수. 현재가보다 낮은 지지선 가격 제시.
   - **돌파(breakout)**: 저항선 돌파 시 매수. 현재가보다 높은 저항선 돌파 가격 제시. 현재 거래량이 실리며 돌파 중이면 현재가 제시 가능.
2. **거래 유형**: 차트 패턴 기반 단타(day trade) vs 스윙(swing trade) 결정
3. **위험도 평가**: 변동성, 유동성, 추세 강도 기반 1~10 점수 (10=최고위험)
4. **매수 수량**: 위험도와 잔고를 고려한 적정 수량 결정
   - 고위험(7~10): 총 잔고의 5~10% 이내
   - 중위험(4~6): 총 잔고의 10~20%
   - 저위험(1~3): 총 잔고의 20~30%
   - 일본/중국/홍콩 주식은 반드시 100주 단위로 추천 (최소 100주)
   - 미국 주식은 1주 단위 가능
   - 최대 잔고의 30%
5. **손절가**: 진입가 기준 기술적 손절 라인
6. **목표가**: 저항선/피보나치 기반 1차 목표가

JSON 형식:
{{
  "strategy_type": "pullback" 또는 "breakout",
  "buy_price": 최적매수가(진입가),
  "trade_type": "단타" 또는 "스윙",
  "risk_level": 1~10,
  "recommended_qty": 추천수량,
  "stop_loss": 손절가,
  "target_price": 1차목표가,
  "reason": "매수 전략 근거 (전략타입 포함, 한국어 2~3문장)",
  "confidence": 0~100
}}"""

        loop = asyncio.get_event_loop()
        try:
            result = await loop.run_in_executor(
                self._executor,
                lambda: self.antigravity._call_ai(
                    prompt,
                    system_prompt="주식 매수 전략 전문가. 기술적 분석(캔들패턴, RSI, MA, 지지/저항)과 자금관리를 종합하여 최적 진입가와 수량을 결정. 수수료를 반드시 고려.",
                    json_mode=True
                )
            )
            if result.get("success"):
                parsed = self.antigravity._extract_json(result.get("content", ""))
                if parsed and parsed.get("buy_price"):
                    return parsed
        except Exception as e:
            self._log("WARN", f"매수가 예측 실패 ({symbol}): {str(e)[:40]}")
        return None

    # ── NYSE / AMEX 종목 거래소 매핑 ──
    _NYSE_SYMBOLS = {
        # 금융
        "JPM", "BAC", "GS", "V", "MA", "BRK-B", "BLK", "C", "WFC", "MS",
        # 산업/에너지
        "XOM", "CVX", "CAT", "BA", "GE", "RTX", "HON", "UPS", "LMT", "MMM",
        # 소비재
        "WMT", "KO", "PEP", "PG", "JNJ", "NKE", "DIS", "HD", "MCD", "PM",
        "ABBV", "LLY", "UNH", "MRK", "PFE", "TMO", "ABT",
        # 기술/통신
        "IBM", "ACN", "CRM", "ORCL", "T", "VZ",
        # 기타
        "UBER",
    }
    _AMEX_SYMBOLS = set()  # 필요 시 추가

    def _detect_us_exchange(self, symbol: str) -> str:
        """미국 종목의 거래소 코드 판별 (NASD / NYSE / AMEX)"""
        if symbol in self._AMEX_SYMBOLS:
            return "AMEX"
        if symbol in self._NYSE_SYMBOLS:
            return "NYSE"
        return "NASD"  # 기본값: 나스닥

    async def _execute_buy(self, candidate: Dict):
        """KIS API로 자동 매수 실행 (위험도 기반 동적 수량)"""
        symbol = candidate.get("symbol", "")
        name = candidate.get("name", symbol)
        market = candidate.get("market", "US")
        is_domestic = (market == "KR")

        # ── 자동 매수 설정 체크 ──
        if self._db.get_setting("ENABLE_AUTO_BUY", "0") != "1":
            self._log("INFO",
                f"🔒 [시뮬레이션] {name} 매수 신호 감지 — 자동 매수 비활성화 (설정에서 변경 가능)")
            candidate["tracking_status"] = "watching"
            return

        # ── 장운영시간 체크 ──
        if market not in self.get_active_markets():
            self._log("WARN", f"⚠️ {name} ({market}): 현재 장운영시간이 아닙니다 — 매수 취소")
            candidate["tracking_status"] = "watching"
            return

        # ── 레버리지/인버스 종목 차단 ──
        allow_leverage = self.collector.db.get_setting("ALLOW_LEVERAGE", "0")
        if allow_leverage != "1":
            name_upper = name.upper()
            # 국내 레버리지/인버스 키워드
            kr_keywords = ["레버리지", "인버스", "곱버스", "2X", "3X",
                           "LEVERAGED", "INVERSE", "울트라숏", "울트라롱",
                           "베어", "BEAR"]
            # 해외 레버리지/인버스 키워드
            us_keywords = ["LEVERAGED", "INVERSE", "ULTRA", "BEAR", "SHORT",
                           "DIREXION", "PROSHARES", "2X", "3X", "-2X", "-3X",
                           "BULL 2X", "BULL 3X", "BEAR 2X", "BEAR 3X"]
            keywords = kr_keywords if is_domestic else us_keywords
            if any(kw.upper() in name_upper for kw in keywords):
                self._log("WARN",
                    f"🚫 {name} — 레버리지/인버스 종목 매수 차단 (설정에서 허용 가능)")
                candidate["tracking_status"] = "watching"
                return

        # ── 최신 실시간 가격 조회 (주문 직전 필수) ──
        ref = candidate.get("predicted_buy_price", 0) or candidate.get("price", 0)
        fresh_price = await self._fetch_live_price(symbol, market, ref_price=ref)
        
        if not fresh_price or fresh_price <= 0:
            self._log("WARN", f"⚠️ {name}: 실시간 가격 조회 실패 — 매수 보류 (정확한 단가 확보 불가)")
            candidate["tracking_status"] = "watching"
            return
            
        # 가격 급변동 체크 (참조가 대비 15% 이상 차이나면 이상 데이터로 간주)
        if ref > 0:
            deviation = abs(fresh_price - ref) / ref
            if deviation > 0.15:
                self._log("WARN", 
                    f"⚠️ {name}: 시세 급변 또는 데이터 오류 의심 (참조 ₩{ref:,.0f} vs 실시간 ₩{fresh_price:,.0f}) — 매수 취소")
                candidate["tracking_status"] = "watching"
                return

        price = fresh_price
        candidate["live_price"] = price

        # 거래소 코드 결정 (해외만 사용)
        if market == "US":
            exchange = candidate.get("exchange") or self._detect_us_exchange(symbol)
        else:
            exchange_map = {"JP": "TKSE", "HK": "SEHK", "CN": "SHAA"}
            exchange = candidate.get("exchange") or exchange_map.get(market, "NASD")

        # 통화 기호
        currency = "₩" if is_domestic else "$"

        # ── 위험도 기반 동적 수량 계산 ──
        risk_level = candidate.get("buy_risk_level", 5)
        ai_qty = candidate.get("buy_recommended_qty", 1)
        _loop = asyncio.get_event_loop()

        # ── 포트폴리오 분배 예산 적용 ──
        trade_type = candidate.get("buy_trade_type", "스윙")
        alloc_pct = self._portfolio_alloc.get(trade_type, 0.50)
        strategy_budget = int(self._available_cash * alloc_pct)
        used = self._portfolio_used.get(trade_type, 0)
        strategy_avail = max(0, strategy_budget - used)

        if strategy_avail <= 0:
            self._log("WARN",
                f"⚠️ [{trade_type}] 예산 소진: "
                f"배정 ₩{strategy_budget:,} / 사용 ₩{used:,} — {name} 매수 불가")
            candidate["tracking_status"] = "watching"
            return

        if is_domestic:
            avail_local = strategy_avail
        else:
            fx_rate = (await _loop.run_in_executor(self._executor, self._fetch_fx_rate, market)) or 1450
            avail_local = strategy_avail / fx_rate if fx_rate > 0 else 0

        # 위험도별 최대 투자 비율
        if risk_level >= 7:     # 고위험: 5~10%
            max_pct = 0.10
        elif risk_level >= 4:   # 중위험: 10~20%
            max_pct = 0.20
        else:                   # 저위험: 20~30%
            max_pct = 0.30

        # 잔고 기준 최대 수량 계산
        max_invest = avail_local * max_pct
        max_qty_by_cash = int(max_invest / price) if price > 0 else 1

        # AI 추천 수량과 잔고 기반 수량 중 작은 값 선택
        qty = min(ai_qty, max_qty_by_cash) if max_qty_by_cash > 0 else ai_qty
        qty = max(1, qty)  # 최소 1주

        # 주문금액이 잔고 초과 방지
        order_amt = price * qty
        if order_amt > avail_local and avail_local > price:
            qty = int(avail_local / price)
            qty = max(1, qty)

        # ── 거래소별 주문 단위(Lot Size) 적용 ──
        # 기본값 (API 조회 실패 시 폴백)
        DEFAULT_LOT_SIZES = {
            "TKSE": 100,   # 일본: 100주 단위
            "SHAA": 100,   # 중국 상해: 100주 단위
            "SZAA": 100,   # 중국 심천: 100주 단위
            "SEHK": 100,   # 홍콩: 기본값 (종목마다 다름)
        }
        lot_size = candidate.get("lot_size", 0)
        if not lot_size and not is_domestic and exchange in DEFAULT_LOT_SIZES:
            # KIS API에서 실제 주문단위(vnit) 조회
            try:
                market_excd_map = {"NASD": "NAS", "NYSE": "NYS", "AMEX": "AMS",
                                   "SEHK": "HKS", "TKSE": "TSE", "SHAA": "SHS", "SZAA": "SZS"}
                excd = market_excd_map.get(exchange, exchange)
                price_info = await _loop.run_in_executor(
                    self._executor,
                    lambda: self.collector.kis.inquire_overseas_price(symbol, excd)
                )
                lot_size = price_info.get("lot_size", 0)
                if lot_size > 0:
                    candidate["lot_size"] = lot_size
                    self._log("INFO", f"📏 {name} 주문단위: {lot_size}주 (KIS API)")
            except Exception:
                pass
        if not lot_size:
            lot_size = DEFAULT_LOT_SIZES.get(exchange, 1)
        if lot_size > 1:
            qty_rounded = max(lot_size, ((qty + lot_size - 1) // lot_size) * lot_size)
            if price * qty_rounded > avail_local and qty_rounded > lot_size:
                qty_rounded = (qty // lot_size) * lot_size
                qty_rounded = max(lot_size, qty_rounded)
            if price * qty_rounded > avail_local:
                self._log("WARN",
                    f"⚠️ {name} 최소 주문단위 {lot_size}주 × {currency}{price:,.0f} = "
                    f"{currency}{price * lot_size:,.0f} > 잔고 {currency}{avail_local:,.0f} — 매수 불가")
                candidate["tracking_status"] = "watching"
                return
            qty = qty_rounded

        self._log("ALERT",
            f"🛒 매수 주문: [{trade_type}] {name} ({symbol}) "
            f"{qty}주 @{currency}{price:,.0f} = {currency}{price * qty:,.0f} "
            f"(위험도 {risk_level}/10, AI추천 {ai_qty}주, "
            f"{'단위 '+str(lot_size)+'주, ' if lot_size > 1 else ''}"
            f"잔고 {currency}{avail_local:,.0f})")

        loop = asyncio.get_event_loop()
        try:
            if is_domestic:
                # 국내주식: 시장가 주문 (체결 확실성 우선)
                result = await loop.run_in_executor(
                    self._executor,
                    lambda: self.collector.kis.place_domestic_order(
                        symbol=symbol, qty=qty,
                        price=0, side="buy", order_type="01"  # 시장가
                    )
                )
            else:
                # 해외주식: 지정가 주문 (현재가 기준)
                result = await loop.run_in_executor(
                    self._executor,
                    lambda: self.collector.kis.place_overseas_order(
                        symbol=symbol, exchange=exchange,
                        qty=qty, price=price, side="buy"
                    )
                )

            if result.get("success"):
                order_no = result.get("order_no", "")
                candidate["tracking_status"] = "pending"
                candidate["order_id"] = order_no
                candidate["order_price"] = price
                candidate["order_qty"] = qty
                now_str = datetime.now().strftime("%H:%M:%S")
                candidate["ordered_at"] = now_str
                candidate["order_timestamp"] = time.time()  # 미체결 자동취소용

                order_type_label = "시장가" if is_domestic else "지정가"
                self._log("BULL",
                    f"📋 매수 주문접수({order_type_label}): [{trade_type}] {name} {qty}주 "
                    f"@{currency}{price:,.0f} ({currency}{price * qty:,.0f}) "
                    f"주문번호: {order_no}")

                # ── 국내 시장가는 즉시 체결 간주 ──
                if is_domestic:
                    # 시장가(01) → 높은 확률로 즉시 체결
                    candidate["tracking_status"] = "filled"
                    self._log("BULL",
                        f"✅ 매수 체결(시장가): [{trade_type}] {name} {qty}주 @{currency}{price:,.0f}")

                # 포트폴리오 사용금액 기록
                if is_domestic:
                    order_krw = int(price * qty)
                else:
                    _fx = fx_rate if not is_domestic else 1
                    order_krw = int(price * qty * _fx)
                self._portfolio_used[trade_type] = self._portfolio_used.get(trade_type, 0) + order_krw
                self._log("INFO",
                    f"📊 [{trade_type}] 사용: ₩{self._portfolio_used[trade_type]:,} / "
                    f"배정: ₩{strategy_budget:,}")

                # 잔고 갱신
                await asyncio.get_event_loop().run_in_executor(self._executor, self._refresh_cash)

                # 수수료 계산
                ex = exchange if not is_domestic else "KR"
                buy_fee = self.fee_calc.calculate_buy_fee(price, qty, symbol, name, market=market, exchange=ex)
                
                # 거래 기록
                trade_record = {
                    "symbol": symbol, "name": name, "market": market,
                    "side": "buy", "qty": qty, "price": price,
                    "order_no": order_no,
                    "risk_level": risk_level,
                    "trade_type": trade_type,
                    "strategy_id": candidate.get("matched_strategy_id"), # DB 저장을 위해 포함
                    "total_fees": buy_fee.total_fee,
                    "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }
                self.trade_log.append(trade_record)
                self._save_scanner_state()
                try:
                    self._db.save_trade(trade_record)
                except Exception:
                    pass

                # ── Discord 매수 알림 ──
                try:
                    self.notifier.send_trade_alert(
                        action="BUY", symbol=symbol, name=name,
                        price=price, quantity=qty,
                        reason=candidate.get('buy_reason', ''),
                        market=market
                    )
                except Exception as e:
                    self._log("WARN", f"Discord 매수 알림 실패: {str(e)[:40]}")
                # 캔들 패턴 캡처
                try:
                    _cd = await self.collect_candles(symbol, market)
                    _ind = StrategyStore.extract_indicators(_cd)
                    _snap = StrategyStore.build_candle_snapshot(_cd, _ind)
                    self.strategy_store.save_pattern({
                        "symbol": symbol, "name": name, "market": market,
                        "type": "buy", "result": "pending",
                        "candle_snapshot": _snap,
                        "pattern_label": self.strategy_store.auto_label_pattern(_ind),
                    })
                    self._log("INFO", f"📈 매수 패턴 저장: {name} ({self.strategy_store.auto_label_pattern(_ind)})")
                except Exception as e:
                    self._log("WARN", f"패턴 저장 실패: {str(e)[:40]}")

            else:
                msg = result.get('message', '')
                # 영구 에러: 종목 정보 없음, 거래 불가 → 블랙리스트
                permanent_errors = ['종목', '정보', '없', '미지원', '거래불가', '취급', 'not found', 'invalid']
                if any(kw in msg for kw in permanent_errors):
                    self._symbol_blacklist.add(symbol)
                    candidate["tracking_status"] = "blacklisted"
                    self._log("WARN",
                        f"🚫 {name} ({symbol}) 블랙리스트 등록 — {msg}")
                else:
                    candidate["tracking_status"] = "watching"  # 일시적 오류만 재시도
                self._log("ERROR", f"❌ 매수 실패: {name} - {msg}")

        except Exception as e:
            err_msg = str(e)
            permanent_errors = ['종목', '정보', '없', '미지원', '거래불가', '취급', 'not found', 'invalid']
            if any(kw in err_msg for kw in permanent_errors):
                self._symbol_blacklist.add(symbol)
                candidate["tracking_status"] = "blacklisted"
                self._log("WARN",
                    f"🚫 {name} ({symbol}) 블랙리스트 등록 — {err_msg[:60]}")
            else:
                candidate["tracking_status"] = "watching"
            self._log("ERROR", f"매수 주문 오류: {err_msg[:60]}")

    # ──────────────────────────────────────
    # 미체결 주문 자동 취소 (60초 초과)
    # ──────────────────────────────────────

    async def _auto_cancel_pending(self):
        """미체결 주문이 60초 이상 지속되면 자동 취소 (실제 계좌 미체결 조회 기반)"""
        await asyncio.sleep(20)  # 초기 대기
        self._log("SYSTEM", "🔄 미체결 자동취소 모니터 시작 (실제 계좌 기반, 60초 기준)")

        while True:
            try:
                loop = asyncio.get_event_loop()
                pending_orders = []

                # 1. KIS API로 실제 미체결 내역 조회
                try:
                    # 국내 미체결
                    domestic = await loop.run_in_executor(
                        self._executor, self.collector.kis.inquire_pending_domestic
                    )
                    if domestic:
                        pending_orders.extend(domestic)

                    # 해외 미체결
                    overseas = await loop.run_in_executor(
                        self._executor, self.collector.kis.inquire_pending_overseas
                    )
                    if overseas:
                        pending_orders.extend(overseas)
                except Exception as e:
                    self._log("WARN", f"미체결 내역 조회 실패: {str(e)[:40]}")

                if not pending_orders:
                    await asyncio.sleep(30)
                    continue

                now = datetime.now()
                for order in pending_orders:
                    # KIS order_time은 "HHMMSS" 형식
                    order_time_str = order.get("order_time", "")
                    if not order_time_str or len(order_time_str) < 6:
                        continue

                    try:
                        # 주문 시각을 오늘 날짜의 datetime으로 변환
                        order_dt = now.replace(
                            hour=int(order_time_str[0:2]),
                            minute=int(order_time_str[2:4]),
                            second=int(order_time_str[4:6]),
                            microsecond=0
                        )
                        # 만약 주문 시각이 현재보다 뒤라면 (자정 부근 등) 어제 주문으로 간주하거나 무시
                        if order_dt > now:
                            # 당일 미체결 조회의 경우 하루 전일 가능성은 낮지만 방어 코드
                            order_dt -= timedelta(days=1)

                        elapsed = (now - order_dt).total_seconds()
                    except (ValueError, TypeError):
                        continue

                    if elapsed < 60:
                        continue  # 아직 60초 미경과

                    order_no = order.get("order_no", "")
                    name = order.get("name", order.get("symbol", ""))
                    market_type = order.get("market_type", "overseas")
                    qty = order.get("remaining_qty", 0)
                    order_price = order.get("order_price", 0)
                    symbol = order.get("symbol", "")

                    self._log("WARN",
                        f"⏰ 미체결 {int(elapsed)}초 → 자동취소: {name} "
                        f"({symbol}, 주문#{order_no})")

                    try:
                        if market_type == "domestic":
                            cancel = await loop.run_in_executor(
                                self._executor,
                                lambda: self.collector.kis.cancel_domestic_order(
                                    order_no, qty
                                )
                            )
                        else:
                            exchange = order.get("exchange") or self._detect_us_exchange(symbol)
                            cancel = await loop.run_in_executor(
                                self._executor,
                                lambda: self.collector.kis.cancel_overseas_order(
                                    order_no, exchange, symbol, qty, order_price
                                )
                            )

                        if cancel.get("success"):
                            self._log("INFO", f"🚫 자동취소 완료: {name} ({symbol})")
                            
                            # candidate 상태 업데이트 (있는 경우에만)
                            target_candidate = next(
                                (c for c in self.candidates if c.get("symbol") == symbol),
                                None
                            )
                            if target_candidate:
                                target_candidate["tracking_status"] = "watching"
                            
                            # 잔고 갱신
                            await loop.run_in_executor(
                                self._executor, self._refresh_cash)
                        else:
                            self._log("WARN",
                                f"취소 실패 (이미 체결?): {name} - "
                                f"{cancel.get('message', '')}")
                    except Exception as e:
                        self._log("ERROR", f"자동취소 오류: {name} - {str(e)[:40]}")

                await asyncio.sleep(20)  # 20초 간격 체크

            except Exception as e:
                self._log("ERROR", f"자동취소 루프 오류: {str(e)[:40]}")
                await asyncio.sleep(30)

    # ──────────────────────────────────────
    # Holdings 실시간 추적 + 자동 매도
    # ──────────────────────────────────────

    async def _track_holdings(self):
        """보유종목 실시간 추적 + AI 매도시점 판단 + 자동 매도 (국내/해외 통합)"""
        await asyncio.sleep(10)  # 시작 대기
        self._log("SYSTEM", "📊 보유종목 매도 추적 시작 (국내/해외 통합)")
        
        while True:
            try:
                if self.state["status"] == "stopped":
                    await asyncio.sleep(30)
                    continue

                # 1. KIS API로 보유종목 조회 (해외 + 국내)
                loop = asyncio.get_event_loop()
                raw_holdings = []

                # 해외주식
                try:
                    overseas = await loop.run_in_executor(
                        self._executor,
                        self.collector.kis.inquire_overseas_balance
                    )
                    if overseas:
                        for h in overseas:
                            h["market_type"] = "overseas"
                        raw_holdings.extend(overseas)
                except Exception as e:
                    self._log("WARN", f"해외 보유종목 조회 실패: {str(e)[:50]}")

                # 국내주식
                try:
                    domestic = await loop.run_in_executor(
                        self._executor,
                        self.collector.kis.inquire_balance
                    )
                    domestic_holdings = domestic.get("holdings", [])
                    for h in domestic_holdings:
                        h["market_type"] = "domestic"
                        h["exchange"] = "KRX"
                        h["market"] = "KR"
                    raw_holdings.extend(domestic_holdings)
                except Exception as e:
                    self._log("WARN", f"국내 보유종목 조회 실패: {str(e)[:50]}")

                if not raw_holdings:
                    self.holdings = []
                    await asyncio.sleep(60)
                    continue

                # 2. 보유종목별 매도 분석 (국내/해외 동일 파이프라인)
                for holding in raw_holdings:
                    symbol = holding.get("symbol", "")
                    exchange = holding.get("exchange", "NASD")
                    market_type = holding.get("market_type", "overseas")
                    is_domestic = market_type == "domestic"
                    qty = holding.get("quantity", 0)
                    avg_price = holding.get("avg_price", 0)
                    current_price = holding.get("current_price", 0)

                    if qty <= 0 or avg_price <= 0:
                        continue

                    # 기존 추적 데이터 병합
                    existing = next(
                        (h for h in self.holdings if h.get("symbol") == symbol), None
                    )

                    # 실시간 가격 업데이트 (Yahoo Finance)
                    yahoo_market = "KR" if is_domestic else "US"
                    live = await self._fetch_live_price(symbol, yahoo_market, ref_price=current_price)
                    if live and live > 0:
                        current_price = live

                    # 수수료 포함 순이익 계산
                    profit_info = self.fee_calc.calculate_net_profit(
                        buy_price=avg_price,
                        sell_price=current_price,
                        quantity=qty,
                        exchange=exchange
                    )

                    # 통화 기호
                    currency = "₩" if is_domestic else "$"

                    # 보유종목 데이터 갱신
                    h_data = {
                        **holding,
                        "current_price": current_price,
                        "live_price": current_price,
                        "profit_rate": round(
                            ((current_price - avg_price) / avg_price) * 100, 2
                        ) if avg_price > 0 else 0,
                        "net_profit": profit_info["net_profit"],
                        "net_profit_rate": profit_info["net_profit_rate"],
                        "total_fees": profit_info["total_fees"],
                        "break_even_price": profit_info["break_even_price"],
                        "profitable": profit_info["profitable"],
                        "last_updated": datetime.now().strftime("%H:%M:%S"),
                        "sell_status": existing.get("sell_status", "watching") if existing else "watching",
                        "ai_sell_price": existing.get("ai_sell_price") if existing else None,
                        "ai_sell_reason": existing.get("ai_sell_reason") if existing else None,
                        "ai_sell_action": existing.get("ai_sell_action") if existing else None,
                        "trade_type": existing.get("trade_type") if existing else None,
                        "stop_loss": existing.get("stop_loss") if existing else None,
                        "target_profit_rate": existing.get("target_profit_rate") if existing else None,
                        "hold_duration": existing.get("hold_duration") if existing else None,
                        "strategy_id": existing.get("strategy_id") if existing else None, # 전략 ID 유지
                    }

                    # 이미 매도 완료된 건 스킵
                    if h_data["sell_status"] == "sold":
                        if existing:
                            idx = self.holdings.index(existing)
                            self.holdings[idx] = h_data
                        else: # Should not happen if sell_status is 'sold' but no existing
                            self.holdings.append(h_data) # Add it if it's a new 'sold' item
                        continue

                    # 3. AI 매도시점 예측 (아직 안했으면)
                    if not h_data.get("ai_sell_price") and h_data["sell_status"] == "watching":
                        h_data["sell_status"] = "analyzing"
                        predicted = await self._predict_sell_timing(h_data)
                        if predicted:
                            h_data["ai_sell_price"] = predicted.get("sell_price")
                            h_data["ai_sell_reason"] = predicted.get("reason", "")
                            h_data["ai_sell_action"] = predicted.get("action", "HOLD")
                            h_data["trade_type"] = predicted.get("trade_type", "스윙")
                            h_data["stop_loss"] = predicted.get("stop_loss")
                            h_data["target_profit_rate"] = predicted.get("target_profit_rate")
                            h_data["hold_duration"] = predicted.get("hold_duration", "")
                            h_data["sell_status"] = "watching"

                            action_icon = "🔴" if predicted["action"] == "SELL" else "🟡"
                            trade_label = predicted.get("trade_type", "")
                            self._log("ALERT",
                                f"{action_icon} [{trade_label}] {holding.get('name', symbol)} "
                                f"AI: {predicted['action']} "
                                f"목표 {currency}{predicted.get('sell_price', 0):,.0f} "
                                f"손절 {currency}{predicted.get('stop_loss', 0):,.0f} "
                                f"(수익률 {predicted.get('target_profit_rate', 0):.1f}%)")
                        else:
                            h_data["sell_status"] = "watching"

                    # 4. 매도 조건 도달 → 자동 매도
                    sell_price = h_data.get("ai_sell_price") or 0
                    stop_loss = h_data.get("stop_loss") or 0

                    # 익절: 현재가 >= 목표 매도가
                    if (sell_price > 0 and current_price >= sell_price
                            and h_data["sell_status"] == "watching"
                            and h_data.get("ai_sell_action") == "SELL"):
                        final_profit = self.fee_calc.calculate_net_profit(
                            buy_price=avg_price,
                            sell_price=current_price,
                            quantity=qty,
                            exchange=exchange
                        )
                        if final_profit["profitable"]:
                            h_data["sell_status"] = "selling"
                            self._log("ALERT",
                                f"💰 {holding.get('name', symbol)} 익절 매도! "
                                f"{currency}{current_price:,.0f} ≥ {currency}{sell_price:,.0f} "
                                f"(순이익 {currency}{final_profit['net_profit']:,.0f})")
                            await self._execute_sell(h_data)
                        else:
                            self._log("WARN",
                                f"⚠️ {holding.get('name', symbol)} "
                                f"목표가 도달했으나 수수료 후 손실 "
                                f"(순이익 {currency}{final_profit['net_profit']:,.0f})")

                    # 손절: 현재가 <= AI 손절가
                    elif (stop_loss > 0 and current_price <= stop_loss
                            and h_data["sell_status"] == "watching"):
                        h_data["sell_status"] = "selling"
                        self._log("ALERT",
                            f"🛑 {holding.get('name', symbol)} 손절 매도! "
                            f"{currency}{current_price:,.0f} ≤ 손절선 {currency}{stop_loss:,.0f}")
                        await self._execute_sell(h_data)

                    # holdings 리스트 업데이트
                    if existing:
                        idx = self.holdings.index(existing)
                        self.holdings[idx] = h_data
                    else:
                        self.holdings.append(h_data)

                # 5. 삭제된 종목 제거 (매도 완료되어 KIS에서 사라진 경우)
                active_symbols = {h["symbol"] for h in raw_holdings}
                self.holdings = [
                    h for h in self.holdings
                    if h["symbol"] in active_symbols or h.get("sell_status") == "sold"
                ]

                await asyncio.sleep(10)  # 10초 간격 추적

            except Exception as e:
                self._log("ERROR", f"매도 추적 오류: {str(e)[:60]}")
                await asyncio.sleep(15)

    async def _predict_sell_timing(self, holding: Dict) -> Optional[Dict]:
        """AI에 매도 시점 예측 요청 (캔들 분석 + 자율 전략 + 포트폴리오 밸런싱)"""
        symbol = holding.get("symbol", "")
        name = holding.get("name", symbol)
        market = holding.get("market", "NASD")
        avg_price = holding.get("avg_price", 0)
        current_price = holding.get("current_price", 0)
        qty = holding.get("quantity", 0)
        profit_rate = holding.get("profit_rate", 0)
        net_profit = holding.get("net_profit", 0)
        total_fees = holding.get("total_fees", 0)
        break_even = holding.get("break_even_price", 0)
        trade_type = holding.get("trade_type", "스윙") # 현재 종목의 타입

        # ── 포트폴리오 상태 분석 (리밸런싱 필요성) ──
        current_swing = len([h for h in self.holdings if h.get("trade_type") == "스윙"])
        current_day = len([h for h in self.holdings if h.get("trade_type") == "단타"])
        
        rebalance_msg = ""
        if trade_type == "스윙" and current_swing > current_day + 2: # 스윙이 단타보다 3개 이상 많으면
            rebalance_msg = f"⚠️ [리밸런싱 경고] 현재 스윙 비중이 과다합니다 (스윙 {current_swing} vs 단타 {current_day}). 현금 확보를 위해 매도 기준을 낮추는 것을 고려하세요."
        elif trade_type == "단타" and current_day > current_swing + 2:
            rebalance_msg = f"⚠️ [리밸런싱 경고] 현재 단타 비중이 과다합니다 (단타 {current_day} vs 스윙 {current_swing}). 이익 실현을 적극적으로 고려하세요."

        # ── 캔들 데이터 수집 ──
        candle_text = "차트 데이터 없음"
        try:
            candle_data = await self.collect_candles(symbol, market)
            candles = candle_data.get("candles", {})

            # 캔들 요약 텍스트 생성 (Technical Analysis 적용)
            summaries = []
            for tf in ["5m", "1h", "1d"]:
                tf_candles = candles.get(tf, [])
                if not tf_candles:
                    summaries.append(f"[{tf}] 데이터 없음")
                    continue
                
                # [Step 2] 기술적 지표 계산 (Pandas 기반)
                ta_result = analyze_candles(tf_candles)
                
                # 기본 데이터
                closes = [c["close"] for c in tf_candles]
                latest = closes[-1] if closes else 0
                
                summary_text = (
                    f"[{tf}봉 {len(tf_candles)}개] 현재가: {latest:,.0f}\n"
                    f"  기술적 지표: {ta_result.get('summary', '분석불가')}\n"
                    f"  RSI: {ta_result.get('rsi', 0):.1f} | MACD: {ta_result.get('macd', 0):.2f}\n"
                    f"  MA5: {ta_result.get('ma5', 0):,.0f} | MA20: {ta_result.get('ma20', 0):,.0f} | MA60: {ta_result.get('ma60', 0):,.0f}"
                )
                summaries.append(summary_text)

            candle_text = "\n".join(summaries)
        except Exception as e:
            self._log("WARN", f"매도예측 캔들수집 실패 ({symbol}): {str(e)[:40]}")

        # ── AI 프롬프트 (자율 판단 + 리밸런싱) ──
        prompt = f"""역할: 20년 경력의 퀀트 트레이더. 보유종목의 매도 전략을 수립하세요.

=== 보유 정보 ===
종목: {name} ({symbol})
유형: {trade_type}
보유수량: {qty}주
매수평균가: ${avg_price:.2f}
현재가: ${current_price:.2f}
현재 수익률: {profit_rate:.2f}%
수수료 포함 순이익: ${net_profit:.4f}
왕복 수수료: ${total_fees:.4f}
손익분기가: ${break_even:.4f}

{rebalance_msg}

=== 멀티 타임프레임 차트 분석 ===
{candle_text}

=== 매도 전략 수립 지침 ===
아래 항목에 따라 자율적으로 매도 전략을 수립하세요:

1. **거래 유형 분류**: 차트 패턴/변동성/보유기간을 분석하여 단타(day trade) vs 스윙(swing trade) 결정
2. **목표 매도가**: 저항선, 이동평균, 피보나치 되돌림 등 기술적 분석으로 현실적 목표가 설정
3. **손절가**: 지지선, 이전 저점, ATR 기반으로 손절 라인 설정
4. **매도 시급성**: 차트 패턴(이중천정, 헤드앤숄더, 하락돌파 등) 감지 시 긴급 매도
5. **수수료 고려**: 순이익이 수수료 이하면 보유가 유리할 수 있음
6. **리밸런싱**: 리밸런싱 경고가 있다면, 평소보다 매도 기준을 완화하여(약수익/본전 매도 등) 현금화를 우선하세요.

고정된 %기준 없이, 시장 상황과 차트에 따라 판단하세요.

JSON 형식 응답:
{{
  "action": "SELL" 또는 "HOLD",
  "trade_type": "단타" 또는 "스윙",
  "sell_price": 목표매도가,
  "stop_loss": 손절가,
  "target_profit_rate": 목표수익률(%),
  "reason": "판단 근거 (차트패턴/지표/리밸런싱 기반, 한국어 2~3문장)",
  "urgency": "high" 또는 "medium" 또는 "low",
  "hold_duration": "예상 보유기간 (예: 1~2일, 1~2주)"
}}"""

        loop = asyncio.get_event_loop()
        try:
            result = await loop.run_in_executor(
                self._executor,
                lambda: self.antigravity._call_ai(
                    prompt,
                    system_prompt="주식 매도 전략 전문가. 기술적 분석(캔들패턴, RSI, MA, 지지/저항)과 시장 맥락을 종합하여 매도 시점을 판단. 수수료를 반드시 고려.",
                    json_mode=True
                )
            )
            if result.get("success"):
                parsed = self.antigravity._extract_json(result.get("content", ""))
                if parsed and parsed.get("action"):
                    return parsed
        except Exception as e:
            self._log("WARN", f"매도 예측 실패 ({symbol}): {str(e)[:40]}")
        return None

    async def _execute_sell(self, holding: Dict):
        """KIS API로 자동 매도 실행"""
        symbol = holding.get("symbol", "")
        name = holding.get("name", symbol)
        market = holding.get("market", "US")
        exchange = holding.get("exchange") or (
            self._detect_us_exchange(symbol) if market == "US" else "NASD"
        )
        qty = holding.get("quantity", 0)
        price = holding.get("current_price", 0)
        is_domestic = (market == "KR")

        # ── 자동 매도 설정 체크 ──
        if self._db.get_setting("ENABLE_AUTO_SELL", "0") != "1":
            self._log("INFO",
                f"🔒 [시뮬레이션] {name} 매도 신호 감지 — 자동 매도 비활성화 (설정에서 변경 가능)")
            holding["sell_status"] = "watching"
            return

        # ── 장운영시간 체크 ──
        if market not in self.get_active_markets():
            self._log("WARN", f"⚠️ {name} ({market}): 현재 장운영시간이 아닙니다 — 매도 취소 (익절/손절)")
            holding["sell_status"] = "watching"
            return

        currency = "₩" if is_domestic else "$"

        # ── 거래소별 주문 단위(Lot Size) 적용 ──
        DEFAULT_LOT_SIZES = {
            "TKSE": 100, "SHAA": 100, "SZAA": 100, "SEHK": 100,
        }
        lot_size = holding.get("lot_size", 0)
        if not lot_size and not is_domestic and exchange in DEFAULT_LOT_SIZES:
            try:
                _loop = asyncio.get_event_loop()
                market_excd_map = {"NASD": "NAS", "NYSE": "NYS", "AMEX": "AMS",
                                   "SEHK": "HKS", "TKSE": "TSE", "SHAA": "SHS", "SZAA": "SZS"}
                excd = market_excd_map.get(exchange, exchange)
                price_info = await _loop.run_in_executor(
                    self._executor,
                    lambda: self.collector.kis.inquire_overseas_price(symbol, excd)
                )
                lot_size = price_info.get("lot_size", 0)
                if lot_size > 0:
                    holding["lot_size"] = lot_size
            except Exception:
                pass
        if not lot_size:
            lot_size = DEFAULT_LOT_SIZES.get(exchange, 1)
        if lot_size > 1:
            # 내림으로 lot_size 배수 조정
            qty = (qty // lot_size) * lot_size
            if qty <= 0:
                self._log("WARN",
                    f"⚠️ {name} 보유수량이 최소 주문단위 {lot_size}주 미만 — 매도 불가")
                holding["sell_status"] = "watching"
                return

        self._log("ALERT", f"🏷️ 매도 주문 실행: {name} ({symbol}) {qty}주 @{currency}{price:,.0f}")

        loop = asyncio.get_event_loop()
        try:
            if is_domestic:
                # 국내주식: place_domestic_order 호출
                result = await loop.run_in_executor(
                    self._executor,
                    lambda: self.collector.kis.place_domestic_order(
                        symbol=symbol, qty=qty,
                        price=int(price), side="sell"
                    )
                )
            else:
                # 해외주식: place_overseas_order 호출
                result = await loop.run_in_executor(
                    self._executor,
                    lambda: self.collector.kis.place_overseas_order(
                        symbol=symbol, exchange=exchange,
                        qty=qty, price=price, side="sell"
                    )
                )

            if result.get("success"):
                holding["sell_status"] = "sold"
                holding["sell_order_id"] = result.get("order_no", "")
                holding["sold_price"] = price
                holding["sold_at"] = datetime.now().strftime("%H:%M:%S")

                # 순이익 계산
                profit = self.fee_calc.calculate_net_profit(
                    buy_price=holding.get("avg_price", 0),
                    sell_price=price,
                    quantity=qty,
                    exchange=exchange
                )

                self._log("BULL",
                    f"✅ 매도 체결: {name} {qty}주 @{currency}{price:,.0f} "
                    f"순이익: {currency}{profit['net_profit']:,.0f} "
                    f"({profit['net_profit_rate']:.2f}%) "
                    f"수수료: {currency}{profit['total_fees']:,.0f} "
                    f"주문번호: {result.get('order_no', '')}")

                # ── Discord 매도 알림 ──
                try:
                    self.notifier.send_trade_alert(
                        action="SELL", symbol=symbol, name=name,
                        price=price, quantity=qty,
                        reason=holding.get('sell_reason', ''),
                        market=market,
                        profit_pct=profit.get('net_profit_rate', 0)
                    )
                except Exception as e:
                    self._log("WARN", f"Discord 매도 알림 실패: {str(e)[:40]}")

                await asyncio.get_event_loop().run_in_executor(self._executor, self._refresh_cash)

                # 거래 기록
                # 전략 성과 업데이트 (학습용)
                associated_strat_id = holding.get("strategy_id")
                if associated_strat_id:
                    is_win = profit["net_profit"] > 0
                    self._log("INFO", f"📈 전략 성과 기록: ID {associated_strat_id} ({'성공' if is_win else '실패'})")
                    self._db.update_strategy_stats(associated_strat_id, is_win)

                trade_record = {
                    "symbol": symbol, "name": name,
                    "market": market, "side": "sell",
                    "qty": qty, "price": price,
                    "avg_buy_price": holding.get("avg_price", 0),
                    "net_profit": profit["net_profit"],
                    "net_profit_rate": profit["net_profit_rate"],
                    "total_fees": profit["total_fees"],
                    "order_no": result.get("order_no", ""),
                    "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }
                self.trade_log.append(trade_record)
                # DB 저장
                try:
                    self._db.save_trade(trade_record)
                except Exception:
                    pass

                # 매수 패턴 결과 업데이트
                pnl_pct = profit.get("net_profit_rate", 0)
                self.strategy_store.update_pattern_result(symbol, pnl_pct)
                self._log("INFO", f"📊 패턴 결과 업데이트: {name} {'+' if pnl_pct > 0 else ''}{pnl_pct:.1f}%")

            else:
                holding["sell_status"] = "watching"
                self._log("ERROR", f"❌ 매도 실패: {name} - {result.get('message', '')}")

        except Exception as e:
            holding["sell_status"] = "watching"
            self._log("ERROR", f"매도 주문 오류: {str(e)[:60]}")

    # ──────────────────────────────────────
    # 메인 루프
    # ──────────────────────────────────────
    async def run(self):
        """메인 스캐너 루프 (장 운영시간 자동 감지)"""
        await asyncio.sleep(3)  # 서버 시작 대기
        self._log("SYSTEM", "🚀 AI Trading Scanner 시작")
        self.state["started_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.state["status"] = "idle"

        # Buy Candidate 추적 태스크 병렬 실행
        asyncio.create_task(self._track_candidates())
        # 보유종목 매도 추적 태스크 병렬 실행
        asyncio.create_task(self._track_holdings())
        # 미체결 주문 자동 취소 태스크 병렬 실행
        asyncio.create_task(self._auto_cancel_pending())

        was_market_open = False

        while True:
            if self.state["status"] == "stopped":
                await asyncio.sleep(5)
                continue

            if self.state["status"] == "paused":
                await asyncio.sleep(10)
                continue

            active_markets = self.get_active_markets()

            if active_markets:
                was_market_open = True
                self.state["status"] = "scanning"

                await self.run_scan_cycle(active_markets)

                # 사이클 완료 후 대기
                self.state["status"] = "idle"
                self._log("INFO",
                    f"💤 다음 스캔까지 {CYCLE_INTERVAL}초 대기..."
                )
                await asyncio.sleep(CYCLE_INTERVAL)

            else:
                # 장이 닫혀있는 경우
                if was_market_open:
                    # 장이 방금 마감됨 → 마감 분석 실행
                    was_market_open = False
                    self._offmarket_done = False
                    await self.closing_analysis()

                self.state["status"] = "idle"
                self.state["phase"] = "waiting"

                # Off-Market 활동 실행 (장 마감 후 1회)
                if not self._offmarket_done:
                    self._offmarket_done = True
                    await self._run_offmarket_tasks()

                # 60초마다 체크
                await asyncio.sleep(60)

    # ──────────────────────────────────────
    # Off-Market 활동 시스템
    # ──────────────────────────────────────

    async def _run_offmarket_tasks(self):
        """장 마감 후 Off-Market 활동 순차 실행"""
        # ── 장외 분석 설정 체크 ──
        if self._db.get_setting("ENABLE_OFFMARKET", "1") != "1":
            self._log("INFO", "⏸️ 장외 분석 활동이 비활성화 상태입니다 (설정에서 변경 가능)")
            return

        self._log("SYSTEM", "🌙 Off-Market 활동 시작")
        self.offmarket_state["status"] = "running"
        self.offmarket_state["progress"] = 0
        self.state["phase"] = "offmarket"

        tasks = [
            ("📊 일봉 데이터 사전 수집", self._prefetch_candle_data),
            ("📰 뉴스/공시 수집", self._collect_market_news),
            ("🎯 AI 판단 정확도 추적", self._track_ai_accuracy),
            ("🔬 기술적 분석 프리로드", self._preload_technical_analysis),
            ("🌐 글로벌 시장 연동 분석", self._analyze_global_correlation),
            ("⭐ 프리마켓 후보 선별", self._preselect_candidates),
        ]

        for i, (name, func) in enumerate(tasks, 1):
            self.offmarket_state["current_task"] = name
            self.offmarket_state["progress"] = i
            self._log("SYSTEM", f"[{i}/6] {name}")
            try:
                await func()
                self.offmarket_state["tasks"][name] = {
                    "status": "done",
                    "completed_at": datetime.now().strftime("%H:%M:%S")
                }
            except Exception as e:
                self._log("ERROR", f"Off-Market 작업 실패 ({name}): {str(e)[:60]}")
                self.offmarket_state["tasks"][name] = {
                    "status": "error",
                    "error": str(e)[:60]
                }
            await asyncio.sleep(2)  # 작업 간 간격

        self.offmarket_state["status"] = "done"
        self.offmarket_state["current_task"] = ""
        self.offmarket_state["last_run"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.state["phase"] = "waiting"
        self._log("SYSTEM", "✅ Off-Market 활동 완료")

    # ── 1. 일봉 데이터 사전 수집 ──
    async def _prefetch_candle_data(self):
        """모든 관심종목의 일봉 데이터를 미리 수집하여 캐싱"""
        all_symbols = []
        for market, stocks in COUNTRY_STOCKS.items():
            for sym, name, *_ in stocks:
                all_symbols.append((sym, name, market))

        self._candle_cache.clear()
        fetched = 0
        errors = 0
        loop = asyncio.get_event_loop()

        # 배치 처리 (5개씩)
        for i in range(0, len(all_symbols), 5):
            batch = all_symbols[i:i+5]
            futures = []
            for sym, name, market in batch:
                futures.append(
                    loop.run_in_executor(
                        self._executor,
                        self._fetch_yahoo_candles,
                        sym, market, "1d", "6mo"
                    )
                )
            results = await asyncio.gather(*futures, return_exceptions=True)

            for j, result in enumerate(results):
                sym, name, market = batch[j]
                if isinstance(result, Exception) or not result:
                    errors += 1
                else:
                    self._candle_cache[sym] = {
                        "name": name,
                        "market": market,
                        "candles_1d": result,
                        "cached_at": datetime.now().strftime("%H:%M:%S")
                    }
                    fetched += 1

            await asyncio.sleep(1)  # API 제한 방지

        self._log("INFO",
            f"📊 캔들 사전 수집 완료: {fetched}개 종목 "
            f"({errors}개 실패, 총 {sum(len(v.get('candles_1d', [])) for v in self._candle_cache.values())} 캔들)")

    # ── 2. 뉴스/공시 수집 ──
    async def _collect_market_news(self):
        """Yahoo Finance RSS로 시장별 뉴스 수집 및 AI 감성분석"""
        # ── 뉴스 수집 설정 체크 ──
        if self._db.get_setting("ENABLE_NEWS_COLLECT", "1") != "1":
            self._log("INFO", "⏸️ 뉴스/공시 수집이 비활성화 상태입니다 (설정에서 변경 가능)")
            return

        self._news_cache.clear()
        loop = asyncio.get_event_loop()

        # 주요 종목의 뉴스 수집 (시장별 상위 5개)
        targets = []
        for market, stocks in COUNTRY_STOCKS.items():
            for sym, name, *_ in stocks[:5]:
                targets.append((sym, name, market))

        for sym, name, market in targets:
            try:
                suffix_fn = YAHOO_SUFFIX.get(market, lambda c: "")
                yahoo_sym = sym + suffix_fn(sym)
                url = f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_sym}?interval=1d&range=5d"

                resp = await loop.run_in_executor(
                    self._executor,
                    lambda u=url: requests.get(u, timeout=10,
                        headers={"User-Agent": "Mozilla/5.0"})
                )

                if resp.status_code == 200:
                    data = resp.json()
                    chart_result = data.get("chart", {}).get("result", [])
                    if chart_result:
                        meta = chart_result[0].get("meta", {})
                        price = meta.get("regularMarketPrice", 0)
                        prev_close = meta.get("chartPreviousClose", 0)
                        change_pct = ((price - prev_close) / prev_close * 100) if prev_close else 0

                        self._news_cache.append({
                            "symbol": sym,
                            "name": name,
                            "market": market,
                            "price": price,
                            "change_pct": round(change_pct, 2),
                            "collected_at": datetime.now().strftime("%H:%M:%S")
                        })
            except Exception:
                pass
            await asyncio.sleep(0.5)

        # AI 감성 분석 (수집된 데이터 기반)
        if self._news_cache:
            movers = sorted(self._news_cache, key=lambda x: abs(x.get("change_pct", 0)), reverse=True)
            top_movers = movers[:10]

            if top_movers:
                summary = "\n".join([
                    f"- {n['name']}({n['symbol']}/{n['market']}): {n['change_pct']:+.2f}%"
                    for n in top_movers
                ])

                try:
                    result = await loop.run_in_executor(
                        self._executor,
                        lambda: self.antigravity._call_ai(
                            f"오늘 주요 종목 등락률:\n{summary}\n\n"
                            f"위 종목들의 등락 원인을 추정하고, 내일 시장 전망을 "
                            f"JSON 형식으로 답하세요:\n"
                            f'{{"market_sentiment": "bullish/bearish/neutral", '
                            f'"key_factors": ["요인1", "요인2"], '
                            f'"tomorrow_outlook": "전망 요약"}}',
                            system_prompt="금융 시장 분석 전문가. 간결하게 답변.",
                            json_mode=True
                        )
                    )
                    if result.get("success"):
                        parsed = self.antigravity._extract_json(result.get("content", ""))
                        if parsed:
                            self._news_cache.append({
                                "type": "ai_analysis",
                                "analysis": parsed,
                                "analyzed_at": datetime.now().strftime("%H:%M:%S")
                            })
                except Exception as e:
                    self._log("WARN", f"뉴스 AI 분석 실패: {str(e)[:40]}")

        self._log("INFO",
            f"📰 뉴스 수집 완료: {len(self._news_cache)}개 항목, "
            f"급등락 종목 {len([n for n in self._news_cache if abs(n.get('change_pct', 0)) >= 3])}개")

    # ── 3. AI 판단 정확도 추적 ──
    async def _track_ai_accuracy(self):
        """trade_log의 AI 예측 vs 실제 결과 비교"""
        if not self.trade_log:
            self._log("INFO", "🎯 AI 정확도: 거래 기록 없음")
            return

        total = 0
        correct = 0
        details = []

        for trade in self.trade_log:
            if trade.get("side") != "buy":
                continue

            total += 1
            buy_price = trade.get("price", 0)
            target = trade.get("target_price", 0)
            stop_loss = trade.get("stop_loss", 0)
            symbol = trade.get("symbol", "")

            # 현재가 확인 (캐시에서)
            cached = self._candle_cache.get(symbol, {})
            candles_1d = cached.get("candles_1d", [])
            if candles_1d:
                current_price = candles_1d[-1].get("close", 0)
            else:
                current_price = trade.get("live_price", buy_price)

            # 정확도 판단
            pnl_pct = ((current_price - buy_price) / buy_price * 100) if buy_price else 0
            hit_target = target > 0 and current_price >= target
            hit_stoploss = stop_loss > 0 and current_price <= stop_loss

            if pnl_pct > 0 or hit_target:
                correct += 1
                verdict = "✅ 수익"
            elif hit_stoploss:
                verdict = "❌ 손절"
            elif pnl_pct < -3:
                verdict = "❌ 손실"
            else:
                correct += 1  # 소폭 손실은 정상
                verdict = "⚪ 보합"

            details.append({
                "symbol": symbol,
                "name": trade.get("name", ""),
                "buy_price": buy_price,
                "current_price": current_price,
                "pnl_pct": round(pnl_pct, 2),
                "verdict": verdict
            })

        accuracy = round((correct / total * 100), 1) if total > 0 else 0
        self._ai_stats = {
            "total": total,
            "correct": correct,
            "accuracy": accuracy,
            "details": details[-20:],  # 최근 20건
            "updated_at": datetime.now().strftime("%H:%M:%S")
        }

        self._log("INFO",
            f"🎯 AI 정확도: {accuracy}% ({correct}/{total}) "
            + (f"— 최근: {', '.join(d['verdict'] + d['symbol'] for d in details[-5:])}" if details else ""))

    # ── 4. 기술적 분석 프리로드 ──
    async def _preload_technical_analysis(self):
        """캐시된 캔들로 지지/저항, 피보나치, 볼린저밴드 등 미리 계산"""
        self._ta_cache.clear()
        analyzed = 0

        for sym, data in self._candle_cache.items():
            candles = data.get("candles_1d", [])
            if len(candles) < 20:
                continue

            closes = [c["close"] for c in candles]
            highs = [c["high"] for c in candles]
            lows = [c["low"] for c in candles]
            volumes = [c["volume"] for c in candles]

            latest = closes[-1]

            # 이동평균
            ma5 = sum(closes[-5:]) / 5
            ma20 = sum(closes[-20:]) / 20
            ma60 = sum(closes[-60:]) / min(60, len(closes)) if len(closes) >= 10 else 0

            # RSI (14)
            rsi = 50
            if len(closes) >= 15:
                gains, losses = [], []
                for k in range(1, min(15, len(closes))):
                    diff = closes[-k] - closes[-k-1]
                    if diff > 0:
                        gains.append(diff)
                    else:
                        losses.append(abs(diff))
                avg_gain = sum(gains) / 14 if gains else 0.001
                avg_loss = sum(losses) / 14 if losses else 0.001
                rsi = 100 - (100 / (1 + avg_gain / avg_loss))

            # 볼린저 밴드 (20일)
            if len(closes) >= 20:
                sma20 = sum(closes[-20:]) / 20
                variance = sum((c - sma20) ** 2 for c in closes[-20:]) / 20
                std20 = variance ** 0.5
                bb_upper = sma20 + 2 * std20
                bb_lower = sma20 - 2 * std20
            else:
                bb_upper = bb_lower = latest

            # 지지/저항선 (최근 60일 고가/저가 기반)
            recent_highs = highs[-60:] if len(highs) >= 60 else highs
            recent_lows = lows[-60:] if len(lows) >= 60 else lows
            resistance = max(recent_highs)
            support = min(recent_lows)

            # 피보나치 되돌림
            high_price = max(recent_highs)
            low_price = min(recent_lows)
            diff = high_price - low_price
            fib_levels = {
                "0.0": high_price,
                "0.236": high_price - diff * 0.236,
                "0.382": high_price - diff * 0.382,
                "0.5": high_price - diff * 0.5,
                "0.618": high_price - diff * 0.618,
                "1.0": low_price,
            }

            # 거래량 트렌드
            avg_vol = sum(volumes[-20:]) / min(20, len(volumes)) if volumes else 0
            recent_vol = volumes[-1] if volumes else 0
            vol_ratio = round(recent_vol / avg_vol, 2) if avg_vol > 0 else 0

            # 추세 판단
            trend = "neutral"
            if ma5 > ma20 > ma60 and ma60 > 0:
                trend = "strong_up"
            elif ma5 > ma20:
                trend = "up"
            elif ma5 < ma20 < ma60 and ma60 > 0:
                trend = "strong_down"
            elif ma5 < ma20:
                trend = "down"

            self._ta_cache[sym] = {
                "name": data.get("name", ""),
                "market": data.get("market", ""),
                "price": latest,
                "ma5": round(ma5, 2),
                "ma20": round(ma20, 2),
                "ma60": round(ma60, 2),
                "rsi": round(rsi, 1),
                "bb_upper": round(bb_upper, 2),
                "bb_lower": round(bb_lower, 2),
                "support": round(support, 2),
                "resistance": round(resistance, 2),
                "fibonacci": {k: round(v, 2) for k, v in fib_levels.items()},
                "vol_ratio": vol_ratio,
                "trend": trend,
            }
            analyzed += 1

        self._log("INFO",
            f"🔬 기술적 분석 완료: {analyzed}개 종목 "
            f"(상승추세 {len([v for v in self._ta_cache.values() if 'up' in v.get('trend', '')])}개, "
            f"과매도 RSI<30 {len([v for v in self._ta_cache.values() if v.get('rsi', 50) < 30])}개)")

    # ── 5. 글로벌 시장 연동 분석 ──
    async def _analyze_global_correlation(self):
        """주요 지수 성과 수집 + AI 크로스마켓 예측"""
        loop = asyncio.get_event_loop()

        # 주요 지수 수집
        indices = {
            "^GSPC": "S&P 500",
            "^DJI": "Dow Jones",
            "^IXIC": "NASDAQ",
            "^N225": "Nikkei 225",
            "^KS11": "KOSPI",
            "^HSI": "Hang Seng",
            "000001.SS": "Shanghai",
        }

        index_data = {}
        for symbol, name in indices.items():
            try:
                url = (
                    f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
                    f"?interval=1d&range=5d"
                )
                resp = await loop.run_in_executor(
                    self._executor,
                    lambda u=url: requests.get(u, timeout=10,
                        headers={"User-Agent": "Mozilla/5.0"})
                )
                if resp.status_code == 200:
                    data = resp.json()
                    result = data.get("chart", {}).get("result", [])
                    if result:
                        meta = result[0].get("meta", {})
                        price = meta.get("regularMarketPrice", 0)
                        prev = meta.get("chartPreviousClose", 0)
                        chg = ((price - prev) / prev * 100) if prev else 0
                        index_data[name] = {
                            "price": price,
                            "change_pct": round(chg, 2)
                        }
            except Exception:
                pass
            await asyncio.sleep(0.5)

        if not index_data:
            self._log("WARN", "🌐 글로벌 지수 데이터 수집 실패")
            return

        # AI 연동 분석
        summary = "\n".join([
            f"- {name}: {d['change_pct']:+.2f}%"
            for name, d in index_data.items()
        ])

        try:
            result = await loop.run_in_executor(
                self._executor,
                lambda: self.antigravity._call_ai(
                    f"오늘 글로벌 주요 지수 등락률:\n{summary}\n\n"
                    f"다음 항목을 JSON으로 분석하세요:\n"
                    f"1. 미국 시장이 아시아에 미칠 영향\n"
                    f"2. 내일 유망 시장 (KR/JP/CN/HK/US)\n"
                    f"3. 섹터별 전망\n\n"
                    f'{{"us_to_asia_impact": "설명", '
                    f'"recommended_markets": ["시장코드"], '
                    f'"sector_outlook": {{"tech": "bullish/bearish", "finance": "...", "auto": "..."}}, '
                    f'"risk_level": "low/medium/high", '
                    f'"summary": "종합 전망 1~2문장"}}',
                    system_prompt="글로벌 매크로 분석 전문가. 크로스마켓 상관관계에 집중.",
                    json_mode=True
                )
            )
            if result.get("success"):
                parsed = self.antigravity._extract_json(result.get("content", ""))
                if parsed:
                    self._global_analysis = {
                        "indices": index_data,
                        "ai_analysis": parsed,
                        "analyzed_at": datetime.now().strftime("%H:%M:%S")
                    }
        except Exception as e:
            self._log("WARN", f"글로벌 분석 AI 실패: {str(e)[:40]}")

        self._log("INFO",
            f"🌐 글로벌 분석 완료: {len(index_data)}개 지수 수집 — "
            + ", ".join(f"{n} {d['change_pct']:+.1f}%" for n, d in list(index_data.items())[:4]))

    # ── 6. 프리마켓 후보 선별 ──
    async def _preselect_candidates(self):
        """캐시된 캔들+뉴스+TA로 다음 장 유망 종목 AI 선별"""
        self._premarket_picks.clear()
        loop = asyncio.get_event_loop()

        # TA 캐시에서 유망 종목 필터 (기술적 신호 기반)
        prospects = []
        for sym, ta in self._ta_cache.items():
            score = 0
            reasons = []

            # RSI 과매도 → 반등 기대
            if ta.get("rsi", 50) < 35:
                score += 30
                reasons.append(f"RSI 과매도({ta['rsi']:.0f})")
            elif ta.get("rsi", 50) < 45:
                score += 15
                reasons.append(f"RSI 저위({ta['rsi']:.0f})")

            # 볼린저 하단 근접
            price = ta.get("price", 0)
            bb_lower = ta.get("bb_lower", 0)
            if bb_lower > 0 and price > 0:
                bb_dist = (price - bb_lower) / price * 100
                if bb_dist < 2:
                    score += 25
                    reasons.append("볼린저 하단 근접")

            # 상승 추세
            if "up" in ta.get("trend", ""):
                score += 20
                reasons.append(f"추세: {ta['trend']}")

            # 지지선 근접
            support = ta.get("support", 0)
            if support > 0 and price > 0:
                sup_dist = (price - support) / price * 100
                if sup_dist < 3:
                    score += 20
                    reasons.append("지지선 근접")

            # 거래량 증가
            if ta.get("vol_ratio", 0) > 1.5:
                score += 10
                reasons.append(f"거래량 {ta['vol_ratio']}배")

            if score >= 30:
                prospects.append({
                    "symbol": sym,
                    "name": ta.get("name", ""),
                    "market": ta.get("market", ""),
                    "price": price,
                    "ta_score": score,
                    "reasons": reasons,
                    **{k: ta[k] for k in ["rsi", "trend", "support", "resistance", "ma5", "ma20"]}
                })

        # 상위 15개를 AI에 전달
        prospects.sort(key=lambda x: x["ta_score"], reverse=True)
        top_prospects = prospects[:15]

        if top_prospects:
            prospect_text = "\n".join([
                f"{i+1}. {p['name']}({p['symbol']}/{p['market']}) "
                f"가격:{p['price']:.2f} RSI:{p['rsi']:.0f} 추세:{p['trend']} "
                f"TA점수:{p['ta_score']} 이유:{', '.join(p['reasons'])}"
                for i, p in enumerate(top_prospects)
            ])

            # 글로벌 분석 컨텍스트 추가
            global_ctx = ""
            if self._global_analysis.get("ai_analysis"):
                ga = self._global_analysis["ai_analysis"]
                global_ctx = f"\n\n글로벌 시장 전망: {ga.get('summary', '')}\n추천 시장: {ga.get('recommended_markets', [])}"

            try:
                result = await loop.run_in_executor(
                    self._executor,
                    lambda: self.antigravity._call_ai(
                        f"다음 장 매수 후보를 기술적 분석 기반으로 선별했습니다:\n\n"
                        f"{prospect_text}"
                        f"{global_ctx}\n\n"
                        f"상위 5개를 선정하고 각각의 진입 전략을 수립하세요.\n"
                        f'JSON 형식: [{{"symbol": "코드", "name": "종목명", "market": "시장", '
                        f'"priority": 1~5, "strategy": "진입 전략", "entry_price": 가격, '
                        f'"target_price": 목표가, "stop_loss": 손절가}}]',
                        system_prompt="프리마켓 분석 전문가. 기술적 분석과 글로벌 매크로를 종합.",
                        json_mode=True
                    )
                )
                if result.get("success"):
                    parsed = self.antigravity._extract_json(result.get("content", ""))
                    if parsed:
                        if isinstance(parsed, list):
                            self._premarket_picks = parsed
                        elif isinstance(parsed, dict) and "picks" in parsed:
                            self._premarket_picks = parsed["picks"]
            except Exception as e:
                self._log("WARN", f"프리마켓 AI 선별 실패: {str(e)[:40]}")

        self._log("INFO",
            f"⭐ 프리마켓 후보: {len(self._premarket_picks)}개 선별 "
            f"(TA 유망 {len(prospects)}개 중) "
            + (", ".join(p.get("name", p.get("symbol", ""))
                for p in self._premarket_picks[:5]) if self._premarket_picks else "없음"))


    # ──────────────────────────────────────
    # 제어
    # ──────────────────────────────────────
    def pause(self):
        self.state["status"] = "paused"
        self._log("SYSTEM", "⏸️ 스캐너 일시정지")

    def resume(self):
        self.state["status"] = "idle"
        self._log("SYSTEM", "▶️ 스캐너 재개")

    def stop(self):
        self.state["status"] = "stopped"
        self._log("SYSTEM", "⏹️ 스캐너 중지")

    def reset_results(self):
        """결과 초기화 (새 장 시작 시)"""
        self.scan_results.clear()
        self.candidates.clear()
        self._symbol_blacklist.clear()
        self.state["analyzed_count"] = 0
        self.state["skipped_by_budget"] = 0
        self.state["cheapest_skipped"] = ""
        self.state["progress"] = 0
        self._log("SYSTEM", "🗑️ 스캔 결과 초기화")

    def get_state_snapshot(self) -> Dict:
        """현재 상태 스냅샷"""
        return {
            **self.state,
            "results_count": len(self.scan_results),
            "candidates_count": len(self.candidates),
            "active_markets": self.get_active_markets(),
            "market_status": self.get_all_market_status(),
            "offmarket": self.offmarket_state,
        }
