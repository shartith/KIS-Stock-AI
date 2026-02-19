# -*- coding: utf-8 -*-
"""
AI 전략 & 캔들 패턴 저장소
- SQLite DB 기반 전략/패턴 영구 저장
- ChromaDB 벡터 기반 유사 패턴 검색
- 활성 전략 컨텍스트 생성 (AI 프롬프트 주입용)
"""

import json
import os
from datetime import datetime
from typing import Dict, List, Optional


class StrategyStore:
    """전략 + 캔들 패턴 저장소 (DB 기반)"""

    def __init__(self, db=None, vector_store=None):
        """
        Args:
            db: DatabaseManager 인스턴스
            vector_store: StockVectorStore 인스턴스 (벡터 검색용)
        """
        self._db = db
        self._vector_store = vector_store

    def set_db(self, db):
        """DB 매니저 지연 초기화"""
        self._db = db

    def set_vector_store(self, vs):
        """벡터 스토어 지연 초기화"""
        self._vector_store = vs

    # ──────────────────────────────────────
    # 전략 CRUD
    # ──────────────────────────────────────
    def add_strategy(self, strategy: Dict) -> int:
        """전략 추가. DB ID 반환"""
        if not self._db:
            return -1
        return self._db.save_strategy(strategy)

    def toggle_strategy(self, sid: int, active: bool) -> bool:
        """전략 활성화/비활성화"""
        if not self._db:
            return False
        self._db.toggle_strategy(sid, active)
        return True

    def delete_strategy(self, sid: int) -> bool:
        """전략 삭제"""
        if not self._db:
            return False
        self._db.delete_strategy(sid)
        return True

    def get_all_strategies(self) -> List[Dict]:
        """전체 전략 목록"""
        if not self._db:
            return []
        return self._db.get_strategies()

    def get_active_strategies(self, market: Optional[str] = None) -> List[Dict]:
        """활성 전략만 (시장 필터 가능)"""
        all_strats = self._db.get_strategies(active_only=True) if self._db else []
        if market:
            all_strats = [s for s in all_strats if s.get("market") in (None, "ALL", market)]
        return all_strats

    # ──────────────────────────────────────
    # 패턴 저장 & 조회
    # ──────────────────────────────────────
    def save_pattern(self, pattern: Dict) -> int:
        """매매 패턴 저장 (DB + 벡터 DB). DB ID 반환"""
        db_id = -1
        if self._db:
            db_id = self._db.save_candle_pattern(pattern)

        # 벡터 DB에도 저장 (유사도 검색용)
        if self._vector_store:
            try:
                snap = pattern.get("candle_snapshot", {})
                indicators = snap.get("indicators", {}) if isinstance(snap, dict) else {}
                self._vector_store.add_trade_pattern({
                    "symbol": pattern.get("symbol", ""),
                    "name": pattern.get("name", ""),
                    "market": pattern.get("market", "US"),
                    "side": pattern.get("type", "buy"),
                    "candle_snapshot": snap,
                    "indicators": indicators,
                    "pattern_label": pattern.get("pattern_label", ""),
                    "result": pattern.get("result", "pending"),
                })
            except Exception:
                pass  # 벡터 저장 실패해도 DB 저장은 유지

        return db_id

    def get_patterns(self, market: Optional[str] = None,
                     ptype: Optional[str] = None,
                     result: Optional[str] = None,
                     limit: int = 50) -> List[Dict]:
        """패턴 조회 (필터 가능)"""
        if not self._db:
            return []
        return self._db.get_candle_patterns(
            limit=limit, market=market, result=result
        )

    def update_pattern_result(self, symbol: str, pnl_pct: float):
        """매도 완료 시 → 해당 종목의 pending 패턴 결과 업데이트"""
        if self._db:
            self._db.update_pattern_result(symbol, pnl_pct)

    def get_similar_patterns(self, indicators: Dict, market: Optional[str] = None,
                             limit: int = 5) -> List[Dict]:
        """현재 지표와 유사한 과거 패턴 검색 (벡터 DB 우선, 없으면 DB fallback)"""
        # 벡터 DB 검색
        if self._vector_store:
            try:
                query = (
                    f"RSI:{indicators.get('rsi', 50)} "
                    f"추세:{indicators.get('trend', 'neutral')} "
                    f"MA:{indicators.get('ma5_vs_ma20', 'neutral')} "
                    f"BB:{indicators.get('bb_position', 'middle')}"
                )
                results = self._vector_store.search_similar_trade_patterns(
                    query, n_results=limit, side="buy"
                )
                if results:
                    return [
                        {
                            "symbol": r["metadata"].get("symbol", ""),
                            "name": r["metadata"].get("name", ""),
                            "pattern_label": r["metadata"].get("pattern_label", ""),
                            "result": r["metadata"].get("result", ""),
                            "pnl_pct": r["metadata"].get("pnl_pct", 0),
                            "distance": r.get("distance", 0),
                            "document": r.get("document", ""),
                        }
                        for r in results
                    ]
            except Exception:
                pass

        # DB fallback (RSI 기반 단순 유사도)
        if self._db:
            patterns = self._db.get_candle_patterns(limit=100, market=market)
            rsi = indicators.get("rsi", 50)
            trend = indicators.get("trend", "neutral")
            scored = []
            for p in patterns:
                if p.get("result") == "pending":
                    continue
                ind = p.get("indicators", {})
                if not ind:
                    continue
                score = 0
                p_rsi = ind.get("rsi", 50)
                if abs(rsi - p_rsi) < 10:
                    score += 30
                elif abs(rsi - p_rsi) < 20:
                    score += 15
                if ind.get("trend") == trend:
                    score += 25
                if ind.get("ma5_vs_ma20") == indicators.get("ma5_vs_ma20"):
                    score += 20
                if ind.get("bb_position") == indicators.get("bb_position"):
                    score += 15
                if score >= 30:
                    scored.append((score, p))
            scored.sort(key=lambda x: -x[0])
            return [p for _, p in scored[:limit]]

        return []

    # ──────────────────────────────────────
    # AI 프롬프트 컨텍스트 생성
    # ──────────────────────────────────────
    def build_strategy_context(self, market: str) -> str:
        """활성 전략 → AI 프롬프트 텍스트"""
        active = self.get_active_strategies(market)
        if not active:
            return "현재 활성화된 전략 없음"

        lines = []
        for s in active[:5]:
            win_rate = ""
            total = s.get("success_count", 0) + s.get("fail_count", 0)
            if total > 0:
                wr = s["success_count"] / total * 100
                win_rate = f" (승률 {wr:.0f}%, {total}건)"
            lines.append(
                f"- [{s.get('type','market')}] {s.get('name','')}: "
                f"{s.get('description','')}{win_rate}"
            )
        return "\n".join(lines)

    def build_pattern_context(self, symbol: str, indicators: Dict,
                               market: Optional[str] = None) -> str:
        """유사 패턴 → AI 프롬프트 텍스트"""
        similar = self.get_similar_patterns(indicators, market)
        if not similar:
            return "유사한 과거 패턴 없음"

        lines = []
        for p in similar[:3]:
            icon = "✅" if p.get("result") == "success" else "❌"
            pnl = p.get("pnl_pct", 0) or 0
            sign = "+" if pnl > 0 else ""
            label = p.get("pattern_label", "-")
            name = p.get("name", p.get("symbol", "?"))
            lines.append(
                f"- {icon} {name} {sign}{pnl:.1f}% | 패턴:{label}"
            )
        return "\n".join(lines)

    # ──────────────────────────────────────
    # 캔들 스냅샷 빌더 (외부 호출용)
    # ──────────────────────────────────────
    @staticmethod
    def build_candle_snapshot(candle_data: Dict, current_indicators: Dict) -> Dict:
        """캔들 데이터 → 스냅샷 (저장용)"""
        candles_1d = candle_data.get("candles", {}).get("1d", [])

        # 최근 10봉만 저장 (공간 절약)
        recent = candles_1d[-10:] if candles_1d else []
        simplified = []
        for c in recent:
            simplified.append({
                "date": c.get("date", ""),
                "open": round(c.get("open", 0), 2),
                "high": round(c.get("high", 0), 2),
                "low": round(c.get("low", 0), 2),
                "close": round(c.get("close", 0), 2),
                "volume": c.get("volume", 0),
            })

        return {
            "before_5d": simplified[:-1] if len(simplified) > 1 else [],
            "at_entry": simplified[-1] if simplified else {},
            "after_5d": [],  # 매도 시 채워짐
            "indicators": current_indicators,
            "trend_5d": round(
                (simplified[-1].get("close", 0) / simplified[-6].get("close", 1) - 1) * 100, 2
            ) if len(simplified) >= 6 else 0,
            "trend_20d": 0,
        }

    @staticmethod
    def extract_indicators(candle_data: Dict) -> Dict:
        """캔들 데이터에서 주요 지표 추출"""
        candles_1d = candle_data.get("candles", {}).get("1d", [])
        if not candles_1d or len(candles_1d) < 5:
            return {"rsi": 50, "trend": "neutral", "ma5_vs_ma20": "neutral", "bb_position": "middle"}

        closes = [c["close"] for c in candles_1d]

        # MA
        ma5 = sum(closes[-5:]) / min(5, len(closes))
        ma20 = sum(closes[-20:]) / min(20, len(closes)) if len(closes) >= 20 else ma5

        # RSI (14)
        rsi = 50
        if len(closes) >= 15:
            gains, losses = [], []
            for i in range(1, min(15, len(closes))):
                diff = closes[-i] - closes[-i - 1]
                if diff > 0:
                    gains.append(diff)
                else:
                    losses.append(abs(diff))
            avg_gain = sum(gains) / 14 if gains else 0.001
            avg_loss = sum(losses) / 14 if losses else 0.001
            rsi = round(100 - (100 / (1 + avg_gain / avg_loss)), 1)

        # 추세
        ma60 = sum(closes[-60:]) / min(60, len(closes)) if len(closes) >= 20 else ma20
        if ma5 > ma20 > ma60:
            trend = "strong_up"
        elif ma5 > ma20:
            trend = "up"
        elif ma5 < ma20 < ma60:
            trend = "strong_down"
        elif ma5 < ma20:
            trend = "down"
        else:
            trend = "neutral"

        # MA 크로스
        ma_cross = "neutral"
        if len(closes) >= 21:
            prev_ma5 = sum(closes[-6:-1]) / 5
            prev_ma20 = sum(closes[-21:-1]) / 20
            if prev_ma5 <= prev_ma20 and ma5 > ma20:
                ma_cross = "cross_up"
            elif prev_ma5 >= prev_ma20 and ma5 < ma20:
                ma_cross = "cross_down"
            elif ma5 > ma20:
                ma_cross = "above"
            else:
                ma_cross = "below"

        # 볼린저 밴드 위치
        bb_position = "middle"
        if len(closes) >= 20:
            sma20 = sum(closes[-20:]) / 20
            std = (sum((c - sma20) ** 2 for c in closes[-20:]) / 20) ** 0.5
            upper = sma20 + 2 * std
            lower = sma20 - 2 * std
            price = closes[-1]
            if price <= lower * 1.02:
                bb_position = "lower"
            elif price >= upper * 0.98:
                bb_position = "upper"

        # 거래량 비율
        volumes = [c.get("volume", 0) for c in candles_1d]
        vol_ratio = volumes[-1] / (sum(volumes[-6:-1]) / 5) if len(volumes) >= 6 and sum(volumes[-6:-1]) > 0 else 1.0

        # MACD 간이 계산
        macd_hist = 0
        if len(closes) >= 26:
            ema12 = sum(closes[-12:]) / 12
            ema26 = sum(closes[-26:]) / 26
            macd_hist = round(ema12 - ema26, 4)

        return {
            "rsi": rsi,
            "rsi14": rsi,
            "trend": trend,
            "ma5_vs_ma20": ma_cross,
            "bb_position": bb_position,
            "vol_ratio": round(vol_ratio, 2),
            "macd_hist": macd_hist,
        }

    def auto_label_pattern(self, indicators: Dict) -> str:
        """지표 조합 → 패턴 라벨 자동 생성"""
        parts = []
        rsi = indicators.get("rsi", 50)
        if rsi < 30:
            parts.append("RSI 과매도")
        elif rsi > 70:
            parts.append("RSI 과매수")

        bb = indicators.get("bb_position", "middle")
        if bb == "lower":
            parts.append("볼린저 하단")
        elif bb == "upper":
            parts.append("볼린저 상단")

        ma = indicators.get("ma5_vs_ma20", "neutral")
        if ma == "cross_up":
            parts.append("골든크로스")
        elif ma == "cross_down":
            parts.append("데드크로스")

        trend = indicators.get("trend", "neutral")
        if trend in ("strong_up", "up"):
            parts.append("상승추세")
        elif trend in ("strong_down", "down"):
            parts.append("하락추세")

        return " + ".join(parts) if parts else "일반 패턴"
