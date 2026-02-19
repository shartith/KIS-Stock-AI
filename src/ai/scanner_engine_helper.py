"""
Scanner Engine Helper - 스캐너 엔진의 로직 분리 모듈
"""
import asyncio
from datetime import datetime
from typing import Dict, List, Optional
from antigravity_client import AntigravityClient
from config import HARD_STOP_LOSS_PERCENT, TRAILING_STOP_CONFIG, TIME_BASED_ROI

class ScannerHelper:
    """ScannerEngine의 보조 메서드 집합"""
    
    def __init__(self, scanner_engine):
        self.engine = scanner_engine

    async def _trigger_sell(self, candidate: Dict, market: str, live_price: float, 
                           reason_code: str, reason_detail: str):
        """매도 실행 헬퍼"""
        self.engine._log("ALERT", f"📉 [{reason_code}] {candidate.get('symbol')} {reason_detail} — 매도 실행")
        holding_data = {
            "symbol": candidate.get("symbol"),
            "name": candidate.get("name", ""),
            "market": market,
            "exchange": candidate.get("exchange", "NASD"),
            "quantity": candidate.get("qty", 0),
            "current_price": live_price,
            "avg_price": candidate.get("order_price", 0),
            "lot_size": candidate.get("lot_size", 1),
            "sell_status": "selling",
            
            # --- 학습 데이터용 메타데이터 ---
            "trade_type": candidate.get("buy_trade_type", "스윙"),
            "entry_time": candidate.get("filled_at_dt"), # datetime 객체 필요
            "chart_data": candidate.get("chart_data_snapshot"), # 매수 시점 캔들
            "indicators": candidate.get("indicators_snapshot"), # 매수 시점 지표
            "ai_reasoning": candidate.get("buy_reason", ""),
            "result_type": "WIN" if live_price > candidate.get("order_price", 0) else "LOSS",
            "profit_rate": candidate.get("live_change", 0),
            "hold_duration": 0 # 계산 필요
        }
        
        # 보유 시간 계산 보정
        if holding_data["entry_time"]:
            holding_data["hold_duration"] = int((datetime.now() - holding_data["entry_time"]).total_seconds() / 60)

        # 1. 실제 매도 실행
        await self.engine._execute_sell(holding_data)
        
        # 2. 학습 데이터 DB 저장 (Data Logger)
        self.engine._db.save_training_data(holding_data)
        self.engine._log("INFO", f"💾 학습 데이터 저장 완료 ({reason_code})")
        
        candidate["tracking_status"] = "sold"

    def select_balanced_portfolio(self, affordable_candidates: List[Dict], cash: int) -> List[Dict]:
        """
        예산과 밸런싱 비율에 맞춰 매수 후보 선정
        Args:
            affordable_candidates: 예산 내 매수 가능한 후보 목록
            cash: 가용 예산
        Returns:
            List[Dict]: 최종 선정된 매수 후보
        """
        # 현재 보유/추적 중인 수량 파악
        current_swing = len([h for h in self.engine.holdings if h.get("trade_type") == "스윙"])
        current_day = len([h for h in self.engine.holdings if h.get("trade_type") == "단타"])
        
        existing_tracked = [
            c for c in self.engine.candidates
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
            and x.get("symbol") not in self.engine._symbol_blacklist
        ]
        pool_swing = [
            x for x in affordable_candidates 
            if x.get("buy_trade_type") == "스윙" 
            and x.get("symbol") not in seen_symbols 
            and x.get("symbol") not in self.engine._symbol_blacklist
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
                    # 예산 초과 시 다음 루프로 (더 싼 종목이 있을 수 있으므로 continue)
                    continue
        
        return selected

    async def process_individual_candidate(self, candidate: Dict, market: str, active_markets: List[str]) -> bool:
        """
        개별 매수 후보의 실시간 처리 (가격 갱신, 손절 체크, 매수 판단)
        Returns:
            bool: 처리 완료 여부 (True면 상위 루프에서 continue 가능)
        """
        symbol = candidate.get("symbol", "")
        is_filled = candidate.get("tracking_status") == "filled"

        # 1. 실시간 가격 조회
        ref = candidate.get("price", 0)
        live_price = await self.engine._fetch_live_price(symbol, market, ref_price=ref)
        
        if live_price and live_price > 0:
            candidate["live_price"] = live_price
            if is_filled and candidate.get("order_price", 0) > 0:
                base = candidate["order_price"]
            else:
                base = candidate.get("price", live_price)
            candidate["live_change"] = round(((live_price - base) / base) * 100, 2) if base > 0 else 0
            candidate["last_updated"] = datetime.now().strftime("%H:%M:%S")

        # 2. 체결된 종목: 매도 조건 체크 (Hard Stop + Trailing Stop)
        if is_filled:
            live_change = candidate.get("live_change", 0)
            
            # (1) 하드 손절 체크
            if live_change <= HARD_STOP_LOSS_PERCENT:
                await self._trigger_sell(candidate, market, live_price, "HARD_STOP", 
                                       f"수익률 {live_change}% 도달 (손절선 {HARD_STOP_LOSS_PERCENT}%)")
                return True

            # (2) Trailing Stop 체크
            # 최고가 갱신
            current_high = candidate.get("highest_price", 0)
            if live_price > current_high:
                candidate["highest_price"] = live_price
                current_high = live_price
                
            # 트레일링 스탑 조건 계산
            activation = TRAILING_STOP_CONFIG["activation_offset"]
            trailing = TRAILING_STOP_CONFIG["trailing_offset"]
            
            if live_change >= activation:
                # 활성화 상태 표시
                candidate["trailing_active"] = True
                
                # 최고가 대비 하락률 계산
                drop_from_high = 0
                if current_high > 0:
                    drop_from_high = (current_high - live_price) / current_high * 100
                
                if drop_from_high >= trailing:
                     await self._trigger_sell(candidate, market, live_price, "TRAILING_STOP", 
                                            f"최고가({current_high}) 대비 {drop_from_high:.2f}% 하락 (익절)")
                     return True

            # (3) Time Based ROI (시간차 익절)
            # 보유 시간(분) 계산
            filled_at_str = candidate.get("filled_at") # 매수 체결 시간
            if filled_at_str:
                try:
                    # filled_at 형식이 HH:MM:SS 라고 가정 (오늘 날짜 기준)
                    now = datetime.now()
                    filled_at = datetime.strptime(filled_at_str, "%H:%M:%S").replace(
                        year=now.year, month=now.month, day=now.day
                    )
                    
                    # 만약 체결 시간이 현재 시간보다 미래라면(자정 넘어감 등) 하루 뺌
                    if filled_at > now:
                        filled_at -= timedelta(days=1)
                        
                    elapsed_min = (now - filled_at).total_seconds() / 60
                    
                    # 설정된 ROI 기준 확인
                    # TIME_BASED_ROI = {30: 5.0, 60: 3.0, ...} (시간: 목표%)
                    # 시간이 적게 지난 순서대로 정렬하여 체크
                    for time_limit, target_roi in sorted(TIME_BASED_ROI.items()):
                        if elapsed_min <= time_limit:
                            if live_change >= target_roi:
                                await self._trigger_sell(candidate, market, live_price, "TIME_ROI", 
                                                    f"보유 {int(elapsed_min)}분: 목표 {target_roi}% 달성 ({live_change}%)")
                                return True
                            break # 해당 시간 구간에 해당하므로 더 긴 시간 기준은 체크 불필요
                        
                    # 설정된 최대 시간(마지막 키)을 넘긴 경우, 마지막 기준 적용
                    max_time = max(TIME_BASED_ROI.keys())
                    min_roi = TIME_BASED_ROI[max_time]
                    if elapsed_min > max_time and live_change >= min_roi:
                         await self._trigger_sell(candidate, market, live_price, "TIME_ROI", 
                                            f"보유 {int(elapsed_min)}분(장기): 최소목표 {min_roi}% 달성 ({live_change}%)")
                         return True
                         
                except Exception as e:
                    self.engine._log("WARN", f"ROI 시간 계산 오류 ({symbol}): {e}")

            return True

        # 3. 미체결 종목: AI 매수 타이밍 예측
        if not candidate.get("predicted_buy_price") and candidate.get("ai_action") == "BUY":
            candidate["tracking_status"] = "analyzing"
            predicted = await self.engine._predict_buy_timing(candidate)
            if predicted and predicted.get("buy_price", 0) > 0:
                self._update_candidate_with_prediction(candidate, predicted)
                self._log_buy_signal(candidate, predicted)
            else:
                candidate["tracking_status"] = "watching"

        # 4. 매수 조건 확인 및 실행
        if self._check_buy_condition(candidate):
            candidate["tracking_status"] = "ordering"
            await self.engine._execute_buy(candidate)
            
        return False

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
        
        self.engine._log("BULL",
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
                    self.engine._log("ALERT", f"🚀 {candidate.get('name')} 🔥 돌파 매매! ${current:.2f} ≥ ${pred_price:.2f}")
                    return True
            else: # pullback
                if current <= pred_price:
                    self.engine._log("ALERT", f"🚀 {candidate.get('name')} 💰 눌림목 매칭! ${current:.2f} ≤ ${pred_price:.2f}")
                    return True
        return False
