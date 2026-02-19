"""
Backtest Engine - 과거 시세 데이터 기반 매매 전략 시뮬레이션

역할:
- 과거 OHLCV 데이터 위에서 전략별 매매 시그널 생성
- 가상 포트폴리오로 매매 실행 (수수료/세금 반영)
- 성과 지표 계산 (수익률, MDD, 샤프, 승률 등)
"""
import json
import math
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from dataclasses import dataclass, field, asdict


@dataclass
class BacktestConfig:
    """백테스트 설정"""
    symbol: str = "005930"
    name: str = ""
    start_date: str = ""            # YYYY-MM-DD (빈 값이면 6개월 전)
    end_date: str = ""              # YYYY-MM-DD (빈 값이면 오늘)
    initial_capital: int = 10_000_000
    strategy: str = "ai_combined"   # ai_combined / technical / momentum / volume / value
    confidence_threshold: int = 80
    stop_loss_pct: float = 0.05
    take_profit_pct: float = 0.10
    fee_rate: float = 0.00015       # 매매 수수료 (0.015%)
    tax_rate: float = 0.0023        # 매도세 (0.23%)


@dataclass
class Trade:
    """개별 거래"""
    date: str
    type: str           # BUY / SELL
    price: float
    quantity: int
    amount: float
    fee: float
    reason: str
    pnl: float = 0.0    # 실현 손익 (매도 시)
    pnl_pct: float = 0.0


@dataclass
class BacktestResult:
    """백테스트 결과"""
    config: dict = field(default_factory=dict)
    trades: list = field(default_factory=list)
    equity_curve: list = field(default_factory=list)
    daily_returns: list = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    error: str = ""


class VirtualPortfolio:
    """가상 포트폴리오"""
    
    def __init__(self, initial_capital: int, fee_rate: float = 0.00015, tax_rate: float = 0.0023):
        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.fee_rate = fee_rate
        self.tax_rate = tax_rate
        
        # 보유 종목
        self.holding_qty = 0
        self.holding_avg_price = 0.0
        
        # 거래 기록
        self.trades: List[Trade] = []
    
    def buy(self, date: str, price: float, reason: str = "") -> Optional[Trade]:
        """매수 (가용 현금의 90%까지)"""
        if price <= 0:
            return None
        
        max_amount = self.cash * 0.9  # 현금의 90%
        quantity = int(max_amount / price)
        
        if quantity <= 0:
            return None
        
        amount = price * quantity
        fee = amount * self.fee_rate
        total_cost = amount + fee
        
        if total_cost > self.cash:
            quantity -= 1
            if quantity <= 0:
                return None
            amount = price * quantity
            fee = amount * self.fee_rate
            total_cost = amount + fee
        
        self.cash -= total_cost
        
        # 평균 단가 갱신
        total_holding_value = self.holding_avg_price * self.holding_qty + amount
        self.holding_qty += quantity
        self.holding_avg_price = total_holding_value / self.holding_qty if self.holding_qty > 0 else 0
        
        trade = Trade(
            date=date, type="BUY", price=price, quantity=quantity,
            amount=amount, fee=fee, reason=reason
        )
        self.trades.append(trade)
        return trade
    
    def sell(self, date: str, price: float, reason: str = "") -> Optional[Trade]:
        """전량 매도"""
        if self.holding_qty <= 0 or price <= 0:
            return None
        
        quantity = self.holding_qty
        amount = price * quantity
        fee = amount * self.fee_rate
        tax = amount * self.tax_rate
        net_amount = amount - fee - tax
        
        # 실현 손익
        cost_basis = self.holding_avg_price * quantity
        pnl = net_amount - cost_basis
        pnl_pct = (pnl / cost_basis * 100) if cost_basis > 0 else 0
        
        self.cash += net_amount
        self.holding_qty = 0
        self.holding_avg_price = 0.0
        
        trade = Trade(
            date=date, type="SELL", price=price, quantity=quantity,
            amount=amount, fee=fee + tax, reason=reason,
            pnl=pnl, pnl_pct=pnl_pct
        )
        self.trades.append(trade)
        return trade
    
    def get_total_value(self, current_price: float) -> float:
        """총 자산 평가"""
        return self.cash + (self.holding_qty * current_price)


class BacktestEngine:
    """백테스팅 엔진"""
    
    def __init__(self):
        from database import DatabaseManager
        self.db = DatabaseManager()
    
    def run(self, config: BacktestConfig) -> BacktestResult:
        """백테스트 실행"""
        result = BacktestResult(config=asdict(config))
        
        # 1. 기간 설정
        if not config.end_date:
            config.end_date = datetime.now().strftime("%Y-%m-%d")
        if not config.start_date:
            end_dt = datetime.strptime(config.end_date, "%Y-%m-%d")
            config.start_date = (end_dt - timedelta(days=180)).strftime("%Y-%m-%d")
        
        print(f"\n📊 백테스트 시작: {config.name or config.symbol}")
        print(f"   기간: {config.start_date} ~ {config.end_date}")
        print(f"   전략: {config.strategy}")
        print(f"   초기 자본: {config.initial_capital:,}원")
        
        # 2. 과거 데이터 로드
        candles = self._load_historical_data(config.symbol, config.start_date, config.end_date)
        if len(candles) < 5:
            result.error = f"데이터 부족: {len(candles)}개 (최소 5개 필요)"
            return result
        
        print(f"   데이터: {len(candles)}일치")
        
        # 3. 시뮬레이션 실행
        portfolio = VirtualPortfolio(
            initial_capital=config.initial_capital,
            fee_rate=config.fee_rate,
            tax_rate=config.tax_rate
        )
        
        equity_curve = []
        daily_returns = []
        prev_value = config.initial_capital
        
        for i in range(len(candles)):
            day = candles[i]
            date = day["date"]
            close = day["close"]
            
            # 컨텍스트 (과거 N일 데이터)
            context = candles[max(0, i-20):i+1]
            
            # 손절/익절 체크
            if portfolio.holding_qty > 0:
                pnl_pct = (close - portfolio.holding_avg_price) / portfolio.holding_avg_price
                if pnl_pct <= -config.stop_loss_pct:
                    portfolio.sell(date, close, reason=f"손절 ({pnl_pct:.1%})")
                elif pnl_pct >= config.take_profit_pct:
                    portfolio.sell(date, close, reason=f"익절 ({pnl_pct:.1%})")
            
            # 전략 시그널 생성
            signal = self._generate_signal(config.strategy, day, context, config)
            
            # 시그널에 따른 매매
            if signal == "BUY" and portfolio.holding_qty == 0:
                portfolio.buy(date, close, reason=f"{config.strategy} BUY 시그널")
            elif signal == "SELL" and portfolio.holding_qty > 0:
                portfolio.sell(date, close, reason=f"{config.strategy} SELL 시그널")
            
            # 일별 자산 기록
            total_value = portfolio.get_total_value(close)
            equity_curve.append({
                "date": date,
                "value": round(total_value),
                "cash": round(portfolio.cash),
                "holding_value": round(portfolio.holding_qty * close),
                "price": close
            })
            
            # 일별 수익률
            daily_return = (total_value - prev_value) / prev_value if prev_value > 0 else 0
            daily_returns.append({"date": date, "return": round(daily_return, 6)})
            prev_value = total_value
        
        # 4. 마지막 보유 종목 정리 (강제 청산)
        if portfolio.holding_qty > 0 and candles:
            last_price = candles[-1]["close"]
            portfolio.sell(candles[-1]["date"], last_price, reason="백테스트 종료 (강제 청산)")
        
        # 5. 성과 지표 계산
        metrics = self._calculate_metrics(
            portfolio, equity_curve, daily_returns, config.initial_capital
        )
        
        result.trades = [asdict(t) for t in portfolio.trades]
        result.equity_curve = equity_curve
        result.daily_returns = daily_returns
        result.metrics = metrics
        
        print(f"\n📈 백테스트 결과:")
        print(f"   총 수익률: {metrics['total_return']:.1f}%")
        print(f"   승률: {metrics['win_rate']:.0f}%")
        print(f"   MDD: {metrics['mdd']:.1f}%")
        print(f"   거래 횟수: {metrics['total_trades']}회")
        
        return result
    
    def _load_historical_data(self, symbol: str, start_date: str, end_date: str) -> list:
        """과거 OHLCV 데이터 로드 (DB → API fallback)"""
        from database import DatabaseManager, MarketData
        
        db = DatabaseManager()
        session = db.get_session()
        
        try:
            start_dt = datetime.strptime(start_date, "%Y-%m-%d")
            end_dt = datetime.strptime(end_date, "%Y-%m-%d")
            
            results = session.query(MarketData).filter(
                MarketData.symbol == symbol,
                MarketData.timestamp >= start_dt,
                MarketData.timestamp <= end_dt
            ).order_by(MarketData.timestamp.asc()).all()
            
            candles = []
            for r in results:
                candles.append({
                    "date": r.timestamp.strftime("%Y-%m-%d"),
                    "open": r.open or 0,
                    "high": r.high or 0,
                    "low": r.low or 0,
                    "close": r.close or 0,
                    "volume": r.volume or 0
                })
            
            # DB에 없으면 KIS API로 수집 시도
            if len(candles) < 5:
                candles = self._fetch_from_api(symbol, start_date, end_date)
            
            return candles
        finally:
            session.close()
    
    def _fetch_from_api(self, symbol: str, start_date: str, end_date: str) -> list:
        """KIS REST API에서 일봉 데이터 직접 조회"""
        from kis_api import KISApi
        
        kis = KISApi()
        if not kis.is_configured():
            print("[Backtest] KIS API 미설정 - 데이터 수집 불가")
            return []
        
        # 일봉 조회 API 직접 호출
        start_fmt = start_date.replace("-", "")
        end_fmt = end_date.replace("-", "")
        
        data = kis._get(
            "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
            "FHKST03010100",
            {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": symbol,
                "FID_INPUT_DATE_1": start_fmt,
                "FID_INPUT_DATE_2": end_fmt,
                "FID_PERIOD_DIV_CODE": "D",
                "FID_ORG_ADJ_PRC": "0"
            }
        )
        
        candles = []
        output = data.get("output2", data.get("output", []))
        
        if isinstance(output, list):
            for item in output:
                try:
                    date_str = item.get("stck_bsop_date", "")
                    if len(date_str) == 8:
                        date_str = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
                    
                    candles.append({
                        "date": date_str,
                        "open": float(item.get("stck_oprc", 0)),
                        "high": float(item.get("stck_hgpr", 0)),
                        "low": float(item.get("stck_lwpr", 0)),
                        "close": float(item.get("stck_clpr", 0)),
                        "volume": int(item.get("acml_vol", 0))
                    })
                except Exception:
                    continue
        
        # 날짜순 정렬 (과거→최근)
        candles.sort(key=lambda x: x["date"])
        
        # DB에 캐싱
        if candles:
            from database import DatabaseManager
            db = DatabaseManager()
            db_data = []
            for c in candles:
                try:
                    db_data.append({
                        "symbol": symbol,
                        "market": "KR",
                        "timestamp": datetime.strptime(c["date"], "%Y-%m-%d"),
                        "open": c["open"],
                        "high": c["high"],
                        "low": c["low"],
                        "close": c["close"],
                        "volume": c["volume"]
                    })
                except Exception:
                    continue
            if db_data:
                db.save_market_data(db_data)
        
        return candles
    
    # ==========================
    # 전략별 시그널 생성
    # ==========================
    
    def _generate_signal(self, strategy: str, day: dict, context: list, config: BacktestConfig) -> str:
        """전략별 매매 시그널 생성"""
        if strategy == "momentum":
            return self._signal_momentum(day, context)
        elif strategy == "volume":
            return self._signal_volume(day, context)
        elif strategy == "value":
            return self._signal_value(day, context)
        elif strategy == "technical":
            return self._signal_technical(day, context)
        elif strategy == "ai_combined":
            return self._signal_ai_combined(day, context, config)
        else:
            return "HOLD"
    
    def _signal_momentum(self, day: dict, context: list) -> str:
        """모멘텀 전략: N일 연속 상승이면 매수, N일 하락이면 매도"""
        if len(context) < 6:
            return "HOLD"
        
        recent = context[-5:]
        up_days = sum(1 for i in range(1, len(recent)) if recent[i]["close"] > recent[i-1]["close"])
        
        if up_days >= 4:  # 5일 중 4일 상승
            return "BUY"
        elif up_days <= 1:  # 5일 중 4일 하락
            return "SELL"
        return "HOLD"
    
    def _signal_volume(self, day: dict, context: list) -> str:
        """거래량 급증 전략: 평균 대비 2배 이상 + 상승이면 매수"""
        if len(context) < 11:
            return "HOLD"
        
        avg_vol = sum(c["volume"] for c in context[-11:-1]) / 10
        if avg_vol <= 0:
            return "HOLD"
        
        vol_ratio = day["volume"] / avg_vol
        price_change = (day["close"] - context[-2]["close"]) / context[-2]["close"] if context[-2]["close"] > 0 else 0
        
        if vol_ratio >= 2.0 and price_change > 0.01:
            return "BUY"
        elif vol_ratio >= 3.0 and price_change < -0.02:
            return "SELL"
        return "HOLD"
    
    def _signal_value(self, day: dict, context: list) -> str:
        """가치투자 전략: 이동평균 아래에서 매수, 위에서 매도"""
        if len(context) < 21:
            return "HOLD"
        
        ma20 = sum(c["close"] for c in context[-20:]) / 20
        close = day["close"]
        
        if close < ma20 * 0.95:  # 20일 이평선보다 5% 이상 아래
            return "BUY"
        elif close > ma20 * 1.05:  # 20일 이평선보다 5% 이상 위
            return "SELL"
        return "HOLD"
    
    def _signal_technical(self, day: dict, context: list) -> str:
        """기술적 분석: RSI + 이동평균 교차"""
        if len(context) < 15:
            return "HOLD"
        
        # 간이 RSI (14일)
        gains = []
        losses = []
        for i in range(1, min(15, len(context))):
            change = context[-i]["close"] - context[-i-1]["close"]
            if change > 0:
                gains.append(change)
            else:
                losses.append(abs(change))
        
        avg_gain = sum(gains) / 14 if gains else 0
        avg_loss = sum(losses) / 14 if losses else 0.001
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        
        # 5일 이평선
        ma5 = sum(c["close"] for c in context[-5:]) / 5
        # 10일 이평선
        ma10 = sum(c["close"] for c in context[-10:]) / 10 if len(context) >= 10 else ma5
        
        if rsi < 30 and ma5 > ma10:
            return "BUY"
        elif rsi > 70 and ma5 < ma10:
            return "SELL"
        return "HOLD"
    
    def _signal_ai_combined(self, day: dict, context: list, config: BacktestConfig) -> str:
        """AI 종합 전략: 모든 전략의 시그널을 종합"""
        signals = {
            "momentum": self._signal_momentum(day, context),
            "volume": self._signal_volume(day, context),
            "value": self._signal_value(day, context),
            "technical": self._signal_technical(day, context),
        }
        
        buy_count = sum(1 for s in signals.values() if s == "BUY")
        sell_count = sum(1 for s in signals.values() if s == "SELL")
        
        if buy_count >= 2:   # 2개 이상 전략이 매수 시그널
            return "BUY"
        elif sell_count >= 2:  # 2개 이상 전략이 매도 시그널
            return "SELL"
        return "HOLD"
    
    # ==========================
    # 성과 지표 계산
    # ==========================
    
    def _calculate_metrics(self, portfolio: VirtualPortfolio, equity_curve: list,
                           daily_returns: list, initial_capital: int) -> dict:
        """성과 지표 계산"""
        if not equity_curve:
            return {}
        
        final_value = equity_curve[-1]["value"]
        total_return = ((final_value - initial_capital) / initial_capital) * 100
        
        # 승률
        sell_trades = [t for t in portfolio.trades if t.type == "SELL"]
        winning_trades = [t for t in sell_trades if t.pnl > 0]
        win_rate = (len(winning_trades) / len(sell_trades) * 100) if sell_trades else 0
        
        # MDD (Maximum Drawdown)
        peak = initial_capital
        mdd = 0
        for point in equity_curve:
            if point["value"] > peak:
                peak = point["value"]
            drawdown = (peak - point["value"]) / peak * 100
            if drawdown > mdd:
                mdd = drawdown
        
        # 샤프 비율 (연율화, 무위험이자율 3%)
        returns = [r["return"] for r in daily_returns]
        if returns and len(returns) > 1:
            avg_return = sum(returns) / len(returns)
            std_return = math.sqrt(sum((r - avg_return) ** 2 for r in returns) / len(returns))
            risk_free_daily = 0.03 / 252
            sharpe = ((avg_return - risk_free_daily) / std_return * math.sqrt(252)) if std_return > 0 else 0
        else:
            sharpe = 0
        
        # 평균 손익비
        avg_win = sum(t.pnl for t in winning_trades) / len(winning_trades) if winning_trades else 0
        losing_trades = [t for t in sell_trades if t.pnl <= 0]
        avg_loss = abs(sum(t.pnl for t in losing_trades) / len(losing_trades)) if losing_trades else 1
        profit_loss_ratio = avg_win / avg_loss if avg_loss > 0 else 0
        
        return {
            "total_return": round(total_return, 2),
            "final_value": final_value,
            "total_profit": final_value - initial_capital,
            "win_rate": round(win_rate, 1),
            "mdd": round(mdd, 2),
            "sharpe_ratio": round(sharpe, 2),
            "profit_loss_ratio": round(profit_loss_ratio, 2),
            "total_trades": len(portfolio.trades),
            "buy_trades": len([t for t in portfolio.trades if t.type == "BUY"]),
            "sell_trades": len(sell_trades),
            "winning_trades": len(winning_trades),
            "losing_trades": len(losing_trades),
            "avg_win": round(avg_win),
            "avg_loss": round(avg_loss),
            "trading_days": len(equity_curve),
            "period": f"{equity_curve[0]['date']} ~ {equity_curve[-1]['date']}" if equity_curve else ""
        }


# ==========================
# CLI 실행
# ==========================

def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="KIS Stock AI Backtester")
    parser.add_argument("--symbol", type=str, default="005930", help="종목코드")
    parser.add_argument("--name", type=str, default="", help="종목명")
    parser.add_argument("--start", type=str, default="", help="시작일 (YYYY-MM-DD)")
    parser.add_argument("--end", type=str, default="", help="종료일 (YYYY-MM-DD)")
    parser.add_argument("--capital", type=int, default=10_000_000, help="초기 자본")
    parser.add_argument("--strategy", type=str, default="ai_combined",
                        choices=["ai_combined", "technical", "momentum", "volume", "value"])
    parser.add_argument("--stop-loss", type=float, default=0.05, help="손절 비율")
    parser.add_argument("--take-profit", type=float, default=0.10, help="익절 비율")
    args = parser.parse_args()
    
    config = BacktestConfig(
        symbol=args.symbol,
        name=args.name or args.symbol,
        start_date=args.start,
        end_date=args.end,
        initial_capital=args.capital,
        strategy=args.strategy,
        stop_loss_pct=args.stop_loss,
        take_profit_pct=args.take_profit
    )
    
    engine = BacktestEngine()
    result = engine.run(config)
    
    if result.error:
        print(f"\n❌ 에러: {result.error}")
        return
    
    # 거래 내역 출력
    print(f"\n📋 거래 내역 ({len(result.trades)}건)")
    for t in result.trades:
        emoji = "🟢" if t["type"] == "BUY" else "🔴"
        pnl_str = f" (손익: {t['pnl']:+,.0f}원)" if t["type"] == "SELL" else ""
        print(f"  {emoji} {t['date']} {t['type']} {t['quantity']}주 @ {t['price']:,.0f}원{pnl_str}")
    
    # 성과 요약
    m = result.metrics
    print(f"\n📊 성과 요약")
    print(f"  총 수익률: {m['total_return']:+.1f}%")
    print(f"  최종 자산: {m['final_value']:,}원")
    print(f"  승률: {m['win_rate']:.0f}%")
    print(f"  MDD: -{m['mdd']:.1f}%")
    print(f"  샤프 비율: {m['sharpe_ratio']:.2f}")
    print(f"  손익비: {m['profit_loss_ratio']:.2f}")


if __name__ == "__main__":
    main()
