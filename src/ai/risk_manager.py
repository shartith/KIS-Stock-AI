"""
Risk Manager - 리스크 관리 및 투자 비율 산정
100% 투자 금지, 위험도 기반 포지션 사이징
"""
import json
from datetime import datetime
from typing import Dict, List, Optional
from dataclasses import dataclass


@dataclass
class PortfolioConfig:
    """포트폴리오 설정"""
    max_single_stock_ratio: float = 0.20  # 단일 종목 최대 20%
    max_sector_ratio: float = 0.40  # 단일 섹터 최대 40%
    min_cash_ratio: float = 0.10  # 최소 현금 비중 10%
    max_loss_per_trade: float = 0.02  # 거래당 최대 손실 2%
    stop_loss_pct: float = 0.05  # 손절 기준 5%
    take_profit_pct: float = 0.10  # 익절 기준 10%


class RiskManager:
    """리스크 관리 및 포지션 사이징"""
    
    def __init__(self, config: PortfolioConfig = None):
        self.config = config or PortfolioConfig()
        self.risk_scores = {}
    
    def calculate_risk_score(self, stock_data: Dict) -> float:
        """종목 리스크 점수 계산 (0-100, 높을수록 위험)"""
        risk = 50  # 기본 점수
        
        # 변동성 기반 리스크
        volatility = stock_data.get("volatility", 0)
        if volatility > 5:
            risk += 20
        elif volatility > 3:
            risk += 10
        elif volatility < 1:
            risk -= 10
        
        # 등락률 기반 리스크
        change_rate = abs(stock_data.get("change_rate", 0))
        if change_rate > 5:
            risk += 15
        elif change_rate > 3:
            risk += 5
        
        # 거래량 이상 감지
        volume_ratio = stock_data.get("volume_ratio", 1)
        if volume_ratio > 5:
            risk += 15  # 과도한 거래량은 리스크
        elif volume_ratio < 0.5:
            risk += 10  # 거래량 급감도 리스크
        
        # PER 기반 (고PER = 고위험)
        per = stock_data.get("per", 0)
        if per > 50:
            risk += 15
        elif per > 30:
            risk += 5
        elif per < 10 and per > 0:
            risk -= 10
        
        return max(0, min(100, risk))
    
    def calculate_position_size(
        self,
        total_capital: int,
        current_holdings: List[Dict],
        stock_data: Dict,
        risk_score: float = None
    ) -> Dict:
        """투자 비율 및 수량 산정"""
        
        if risk_score is None:
            risk_score = self.calculate_risk_score(stock_data)
        
        # 현재 투자 비중 계산
        total_invested = sum(h.get("eval_amount", 0) for h in current_holdings)
        cash_balance = total_capital - total_invested
        cash_ratio = cash_balance / total_capital if total_capital > 0 else 0
        
        # 현금 비중 체크
        if cash_ratio < self.config.min_cash_ratio:
            return {
                "can_buy": False,
                "reason": f"현금 비중 부족 ({cash_ratio:.1%} < {self.config.min_cash_ratio:.1%})",
                "recommended_qty": 0
            }
        
        # 리스크 기반 투자 비율 산정
        # 리스크 높을수록 낮은 비율
        base_ratio = self.config.max_single_stock_ratio
        risk_adjustment = (100 - risk_score) / 100
        adjusted_ratio = base_ratio * risk_adjustment
        
        # 최대 투자 가능 금액
        max_invest = total_capital * adjusted_ratio
        
        # 거래당 최대 손실 기준
        max_loss_amount = total_capital * self.config.max_loss_per_trade
        price = stock_data.get("current_price", stock_data.get("price", 0))
        
        if price <= 0:
            return {"can_buy": False, "reason": "가격 정보 없음", "recommended_qty": 0}
        
        # 손절 기준으로 최대 수량 계산
        loss_per_share = price * self.config.stop_loss_pct
        max_qty_by_risk = int(max_loss_amount / loss_per_share) if loss_per_share > 0 else 0
        
        # 금액 기준 최대 수량
        max_qty_by_amount = int(max_invest / price)
        
        # 현금 기준 최대 수량
        available_cash = cash_balance - (total_capital * self.config.min_cash_ratio)
        max_qty_by_cash = int(available_cash / price) if available_cash > 0 else 0
        
        # 최종 추천 수량 (가장 보수적인 값)
        recommended_qty = min(max_qty_by_risk, max_qty_by_amount, max_qty_by_cash)
        
        return {
            "can_buy": recommended_qty > 0,
            "recommended_qty": max(0, recommended_qty),
            "recommended_amount": recommended_qty * price,
            "invest_ratio": (recommended_qty * price) / total_capital if total_capital > 0 else 0,
            "risk_score": risk_score,
            "risk_level": self._get_risk_level(risk_score),
            "stop_loss_price": int(price * (1 - self.config.stop_loss_pct)),
            "take_profit_price": int(price * (1 + self.config.take_profit_pct)),
            "max_by_risk": max_qty_by_risk,
            "max_by_amount": max_qty_by_amount,
            "max_by_cash": max_qty_by_cash
        }
    
    def _get_risk_level(self, score: float) -> str:
        if score >= 70:
            return "HIGH"
        elif score >= 40:
            return "MEDIUM"
        else:
            return "LOW"
    
    def check_stop_loss(self, holding: Dict) -> Dict:
        """손절 조건 체크"""
        entry_price = holding.get("avg_price", 0)
        current_price = holding.get("current_price", 0)
        
        if entry_price <= 0:
            return {"should_sell": False}
        
        loss_rate = (current_price - entry_price) / entry_price
        
        if loss_rate <= -self.config.stop_loss_pct:
            return {
                "should_sell": True,
                "reason": "STOP_LOSS",
                "loss_rate": loss_rate,
                "message": f"손절선 도달 ({loss_rate:.1%})"
            }
        
        return {"should_sell": False, "current_pnl": loss_rate}
    
    def check_take_profit(self, holding: Dict) -> Dict:
        """익절 조건 체크"""
        entry_price = holding.get("avg_price", 0)
        current_price = holding.get("current_price", 0)
        
        if entry_price <= 0:
            return {"should_sell": False}
        
        profit_rate = (current_price - entry_price) / entry_price
        
        if profit_rate >= self.config.take_profit_pct:
            return {
                "should_sell": True,
                "reason": "TAKE_PROFIT",
                "profit_rate": profit_rate,
                "message": f"익절선 도달 ({profit_rate:.1%})"
            }
        
        return {"should_sell": False, "current_pnl": profit_rate}
    
    def evaluate_portfolio(self, total_capital: int, holdings: List[Dict]) -> Dict:
        """포트폴리오 전체 평가"""
        total_invested = sum(h.get("eval_amount", 0) for h in holdings)
        total_pnl = sum(h.get("pnl_amount", 0) for h in holdings)
        
        # 포지션별 비중
        positions = []
        for h in holdings:
            ratio = h.get("eval_amount", 0) / total_capital if total_capital > 0 else 0
            positions.append({
                "symbol": h.get("symbol"),
                "name": h.get("name"),
                "ratio": ratio,
                "pnl_rate": h.get("pnl_rate", 0),
                "is_overweight": ratio > self.config.max_single_stock_ratio
            })
        
        overweight = [p for p in positions if p.get("is_overweight")]
        
        return {
            "total_capital": total_capital,
            "total_invested": total_invested,
            "cash_balance": total_capital - total_invested,
            "cash_ratio": (total_capital - total_invested) / total_capital if total_capital > 0 else 1,
            "invest_ratio": total_invested / total_capital if total_capital > 0 else 0,
            "total_pnl": total_pnl,
            "total_pnl_rate": total_pnl / total_invested if total_invested > 0 else 0,
            "position_count": len(holdings),
            "overweight_positions": overweight,
            "health": "GOOD" if not overweight and (total_capital - total_invested) / total_capital >= self.config.min_cash_ratio else "WARNING"
        }


if __name__ == "__main__":
    rm = RiskManager()
    
    # 테스트
    test_stock = {
        "symbol": "005930",
        "name": "삼성전자",
        "current_price": 70000,
        "change_rate": -2.5,
        "volatility": 2.0,
        "volume_ratio": 1.5,
        "per": 12
    }
    
    risk_score = rm.calculate_risk_score(test_stock)
    print(f"리스크 점수: {risk_score}")
    
    position = rm.calculate_position_size(
        total_capital=10000000,
        current_holdings=[],
        stock_data=test_stock
    )
    print(json.dumps(position, indent=2, ensure_ascii=False))
