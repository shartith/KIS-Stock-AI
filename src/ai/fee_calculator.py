"""
Fee Calculator - 매매 수수료 계산
한국투자증권 수수료 구조 기반
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Optional
import json


@dataclass
class FeeStructure:
    """수수료 구조"""
    # 매매 수수료 (온라인)
    trading_fee_rate: float = 0.00015  # 0.015%
    
    # 증권거래세 (매도시만)
    # 코스피: 0.18% (2024년 기준, 농특세 포함)
    # 코스닥: 0.18%
    kospi_tax_rate: float = 0.0018
    kosdaq_tax_rate: float = 0.0018
    
    # 최소 수수료
    min_fee: int = 0  # 한투는 최소 수수료 없음
    
    # 기타 수수료
    etc_fee_rate: float = 0.0  # 기타 수수료 (유관기관 수수료 등)


@dataclass
class FeeRecord:
    """수수료 기록"""
    symbol: str
    name: str
    order_type: str  # buy / sell
    quantity: int
    price: int
    amount: int  # 거래금액
    trading_fee: int  # 매매수수료
    tax: int  # 증권거래세 (매도만)
    total_fee: int  # 총 수수료
    net_amount: int  # 실제 결제/수령 금액
    fee_rate: float  # 총 수수료율
    timestamp: str
    order_no: str = ""
    
    def to_dict(self) -> Dict:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "order_type": self.order_type,
            "quantity": self.quantity,
            "price": self.price,
            "amount": self.amount,
            "trading_fee": self.trading_fee,
            "tax": self.tax,
            "total_fee": self.total_fee,
            "net_amount": self.net_amount,
            "fee_rate": self.fee_rate,
            "timestamp": self.timestamp,
            "order_no": self.order_no
        }
    
    def __str__(self) -> str:
        return f"""
[수수료 상세]
종목: {self.name} ({self.symbol})
유형: {'매수' if self.order_type == 'buy' else '매도'}
수량: {self.quantity:,}주
단가: {self.price:,}원
거래금액: {self.amount:,}원
---
매매수수료: {self.trading_fee:,}원
증권거래세: {self.tax:,}원 {'(매도시만 부과)' if self.order_type == 'sell' else '(매수시 없음)'}
---
총 수수료: {self.total_fee:,}원 ({self.fee_rate:.4%})
{'실제 지불: ' if self.order_type == 'buy' else '실제 수령: '}{self.net_amount:,}원
"""


class FeeCalculator:
    """수수료 계산기"""
    
    def __init__(self, fee_structure: FeeStructure = None):
        self.fee_structure = fee_structure or FeeStructure()
        self.fee_history: list = []
    
    def calculate_buy_fee(self, price: float, quantity: int, symbol: str = "", name: str = "", market: str = "KR", exchange: str = "") -> FeeRecord:
        """매수 수수료 계산 (국내/해외 통합)"""
        amount = price * quantity
        
        if market == "KR":
            # 국내주식
            trading_fee = int(amount * self.fee_structure.trading_fee_rate)
            trading_fee = max(trading_fee, self.fee_structure.min_fee)
            tax = 0
            fee_rate = trading_fee / amount if amount > 0 else 0
        else:
            # 해외주식
            ex = exchange or market
            default = {"fee_rate": 0.0025, "min_fee": 0, "buy_tax": 0}
            rates = self.OVERSEAS_FEE_RATES.get(ex, default)
            trading_fee = max(amount * rates["fee_rate"], rates["min_fee"])
            tax = amount * rates.get("buy_tax", 0)
            fee_rate = (trading_fee + tax) / amount if amount > 0 else 0
            
        total_fee = trading_fee + tax
        net_amount = amount + total_fee
        
        record = FeeRecord(
            symbol=symbol, name=name, order_type="buy",
            quantity=quantity, price=price, amount=amount,
            trading_fee=trading_fee, tax=tax, total_fee=total_fee,
            net_amount=net_amount, fee_rate=fee_rate,
            timestamp=datetime.now().isoformat()
        )
        return record
    
    def calculate_sell_fee(self, price: float, quantity: int, symbol: str = "", name: str = "", market: str = "KR", exchange: str = "") -> FeeRecord:
        """매도 수수료 계산 (국내/해외 통합)"""
        amount = price * quantity
        
        if market == "KR":
            # 국내주식
            trading_fee = int(amount * self.fee_structure.trading_fee_rate)
            trading_fee = max(trading_fee, self.fee_structure.min_fee)
            tax_rate = self.fee_structure.kospi_tax_rate if "kospi" in symbol.lower() else self.fee_structure.kosdaq_tax_rate
            tax = int(amount * tax_rate)
            fee_rate = (trading_fee + tax) / amount if amount > 0 else 0
        else:
            # 해외주식
            ex = exchange or market
            default = {"fee_rate": 0.0025, "min_fee": 0, "sell_tax": 0}
            rates = self.OVERSEAS_FEE_RATES.get(ex, default)
            trading_fee = max(amount * rates["fee_rate"], rates["min_fee"])
            tax = amount * rates.get("sell_tax", 0)
            fee_rate = (trading_fee + tax) / amount if amount > 0 else 0

        total_fee = trading_fee + tax
        net_amount = amount - total_fee
        
        record = FeeRecord(
            symbol=symbol, name=name, order_type="sell",
            quantity=quantity, price=price, amount=amount,
            trading_fee=trading_fee, tax=tax, total_fee=total_fee,
            net_amount=net_amount, fee_rate=fee_rate,
            timestamp=datetime.now().isoformat()
        )
        return record
    
    def record_fee(self, fee_record: FeeRecord, order_no: str = ""):
        """수수료 기록 저장"""
        fee_record.order_no = order_no
        self.fee_history.append(fee_record)
    
    def get_total_fees(self) -> Dict:
        """총 수수료 통계"""
        total_trading_fee = sum(r.trading_fee for r in self.fee_history)
        total_tax = sum(r.tax for r in self.fee_history)
        total_amount = sum(r.amount for r in self.fee_history)
        
        buy_fees = [r for r in self.fee_history if r.order_type == "buy"]
        sell_fees = [r for r in self.fee_history if r.order_type == "sell"]
        
        return {
            "total_trades": len(self.fee_history),
            "buy_trades": len(buy_fees),
            "sell_trades": len(sell_fees),
            "total_amount": total_amount,
            "total_trading_fee": total_trading_fee,
            "total_tax": total_tax,
            "total_fee": total_trading_fee + total_tax,
            "average_fee_rate": (total_trading_fee + total_tax) / total_amount if total_amount > 0 else 0
        }
    
    def estimate_round_trip_fee(self, price: float, quantity: int, market: str = "KR", exchange: str = "") -> Dict:
        """왕복 거래 수수료 예상 (매수 + 매도)"""
        buy_fee = self.calculate_buy_fee(price, quantity, market=market, exchange=exchange)
        sell_fee = self.calculate_sell_fee(price, quantity, market=market, exchange=exchange)
        
        total_fee = buy_fee.total_fee + sell_fee.total_fee
        amount = price * quantity
        rate = total_fee / amount if amount > 0 else 0
        
        return {
            "buy_fee": buy_fee.total_fee,
            "sell_fee": sell_fee.total_fee,
            "total_round_trip_fee": total_fee,
            "round_trip_rate": rate,
            "break_even_rate": rate,
            "message": f"최소 {rate * 100:.3f}% 이상 수익 필요 (왕복)"
        }

    # ──────────────────────────────────────
    # 해외주식 수수료 계산
    # ──────────────────────────────────────

    # 한국투자증권 해외주식 온라인 수수료 (공식 수수료표 기준)
    # fee_rate: 온라인 매매 수수료
    # buy_tax: 매수 시 제세금
    # sell_tax: 매도 시 제세금
    # 최소수수료 폐지됨 (2024~)
    OVERSEAS_FEE_RATES = {
        # ── 미국 0.25%, 제세금 없음 (SEC Fee 0%) ──
        "US":   {"fee_rate": 0.0025, "min_fee": 0, "buy_tax": 0, "sell_tax": 0},
        "NASD": {"fee_rate": 0.0025, "min_fee": 0, "buy_tax": 0, "sell_tax": 0},
        "NYSE": {"fee_rate": 0.0025, "min_fee": 0, "buy_tax": 0, "sell_tax": 0},
        "AMEX": {"fee_rate": 0.0025, "min_fee": 0, "buy_tax": 0, "sell_tax": 0},
        # ── 일본 0.23%, 제세금 없음 ──
        "JP":   {"fee_rate": 0.0023, "min_fee": 0, "buy_tax": 0, "sell_tax": 0},
        "TKSE": {"fee_rate": 0.0023, "min_fee": 0, "buy_tax": 0, "sell_tax": 0},
        # ── 홍콩 0.30%, 제세금: 매수/매도 각 0.1085% ──
        "HK":   {"fee_rate": 0.003, "min_fee": 0, "buy_tax": 0.001085, "sell_tax": 0.001085},
        "SEHK": {"fee_rate": 0.003, "min_fee": 0, "buy_tax": 0.001085, "sell_tax": 0.001085},
        # ── 중국 0.30%, 제세금: 매수 0.00841% / 매도 0.05841% ──
        "CN":   {"fee_rate": 0.003, "min_fee": 0, "buy_tax": 0.0000841, "sell_tax": 0.0005841},
        "SHAA": {"fee_rate": 0.003, "min_fee": 0, "buy_tax": 0.0000841, "sell_tax": 0.0005841},
        "SZAA": {"fee_rate": 0.003, "min_fee": 0, "buy_tax": 0.0000841, "sell_tax": 0.0005841},
        # ── 영국 0.30%, 제세금: 매수 0.5% (Stamp Duty) ──
        "UK":   {"fee_rate": 0.003, "min_fee": 0, "buy_tax": 0.005, "sell_tax": 0},
        "XLON": {"fee_rate": 0.003, "min_fee": 0, "buy_tax": 0.005, "sell_tax": 0},
        # ── 베트남 0.40%, 제세금: 매도 0.1% ──
        "VN":   {"fee_rate": 0.004, "min_fee": 0, "buy_tax": 0, "sell_tax": 0.001},
        "HOSE": {"fee_rate": 0.004, "min_fee": 0, "buy_tax": 0, "sell_tax": 0.001},
        "HNX":  {"fee_rate": 0.004, "min_fee": 0, "buy_tax": 0, "sell_tax": 0.001},
    }

    def calculate_overseas_sell_fee(
        self, price: float, quantity: int, exchange: str = "NASD"
    ) -> Dict:
        """해외주식 매도 수수료 + 제세금 계산

        Returns:
            {amount, fee, tax, total_cost, net_proceeds, fee_rate}
        """
        default = {"fee_rate": 0.0025, "min_fee": 0, "buy_tax": 0, "sell_tax": 0}
        rates = self.OVERSEAS_FEE_RATES.get(exchange, default)
        amount = price * quantity
        fee = amount * rates["fee_rate"]
        tax = amount * rates.get("sell_tax", 0)

        # 최소 수수료 적용
        min_fee_applied = False
        if fee < rates["min_fee"]:
            fee = rates["min_fee"]
            min_fee_applied = True

        total_cost = fee + tax
        net_proceeds = amount - total_cost

        return {
            "amount": round(amount, 2),
            "fee": round(fee, 2),
            "tax": round(tax, 4),
            "total_cost": round(total_cost, 2),
            "min_fee_applied": min_fee_applied,
            "net_proceeds": round(net_proceeds, 2),
            "fee_rate": round(total_cost / amount * 100, 4) if amount > 0 else 0,
        }

    def calculate_net_profit(
        self, buy_price: float, sell_price: float, quantity: int,
        exchange: str = "NASD"
    ) -> Dict:
        """순이익 계산 (수수료 + 제세금 포함)

        Returns:
            {buy_cost, sell_proceeds, buy_fee, buy_tax, sell_fee, sell_tax,
             total_fees, gross_profit, net_profit, net_profit_rate, profitable}
        """
        default = {"fee_rate": 0.0025, "min_fee": 0, "buy_tax": 0, "sell_tax": 0}
        rates = self.OVERSEAS_FEE_RATES.get(exchange, default)

        # 매수 비용 (수수료 + 매수 제세금)
        buy_amount = buy_price * quantity
        buy_fee = max(buy_amount * rates["fee_rate"], rates["min_fee"])
        buy_tax = buy_amount * rates.get("buy_tax", 0)

        # 매도 금액 (수수료 + 매도 제세금)
        sell_amount = sell_price * quantity
        sell_fee = max(sell_amount * rates["fee_rate"], rates["min_fee"])
        sell_tax = sell_amount * rates.get("sell_tax", 0)

        total_fees = buy_fee + buy_tax + sell_fee + sell_tax
        gross_profit = sell_amount - buy_amount
        net_profit = gross_profit - total_fees
        net_profit_rate = (net_profit / buy_amount * 100) if buy_amount > 0 else 0

        return {
            "buy_cost": round(buy_amount, 2),
            "sell_proceeds": round(sell_amount, 2),
            "buy_fee": round(buy_fee, 4),
            "buy_tax": round(buy_tax, 4),
            "sell_fee": round(sell_fee, 4),
            "sell_tax": round(sell_tax, 4),
            "total_fees": round(total_fees, 4),
            "gross_profit": round(gross_profit, 2),
            "net_profit": round(net_profit, 4),
            "net_profit_rate": round(net_profit_rate, 3),
            "break_even_price": round(buy_price * (1 + total_fees / buy_amount), 4) if buy_amount > 0 else 0,
            "profitable": net_profit > 0,
        }


if __name__ == "__main__":
    calc = FeeCalculator()
    
    # 삼성전자 10주 @ 70,000원 테스트
    print("=== 매수 수수료 ===")
    buy = calc.calculate_buy_fee(70000, 10, "005930", "삼성전자")
    print(buy)
    
    print("\n=== 매도 수수료 ===")
    sell = calc.calculate_sell_fee(75000, 10, "005930", "삼성전자")
    print(sell)
    
    print("\n=== 왕복 거래 수수료 ===")
    round_trip = calc.estimate_round_trip_fee(70000, 10)
    print(json.dumps(round_trip, indent=2, ensure_ascii=False))
