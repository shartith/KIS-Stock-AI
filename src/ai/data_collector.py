"""
Data Collector - 글로벌 시장 데이터 수집 (KR, US, HK, JP, CN)
KIS REST API 직접 호출
"""
import json
from datetime import datetime
from typing import Dict, List, Optional
from database import DatabaseManager
from kis_api import KISApi


class StockDataCollector:
    """주식 데이터 수집기 (글로벌)"""
    
    def __init__(self):
        self.db = DatabaseManager()
        self.kis = KISApi(self.db)

    def get_market_indicators(self, symbol: str) -> Dict:
        """시장 지표 조회 (현재가, 등락률, PER, RSI 등)"""
        market = "US" if symbol.isalpha() else "KR"
        
        if market == "KR":
            data = self.kis.inquire_price(symbol)
            if not data:
                print(f"Failed to get indicators for {symbol}")
                return {}
            
            return {
                "symbol": symbol,
                "current_price": data.get("price", 0),
                "change_rate": data.get("change_rate", 0),
                "volume": data.get("volume", 0),
                "volume_ratio": data.get("volume_ratio", 0),
                "per": data.get("per", 0),
                "pbr": data.get("pbr", 0),
                "rsi": 50.0  # 기본값 (별도 계산 필요)
            }
        else:
            # US 주식은 현재가 조회
            data = self.get_current_price(symbol, "US")
            return {
                "symbol": symbol,
                "current_price": data.get("price", 0),
                "change_rate": data.get("change_rate", 0),
                "volume": data.get("volume", 0),
                "volume_ratio": 0,
                "per": 0,
                "rsi": 50.0
            }

    def get_market_rankings(self, market: str, top_n: int = 50, max_price: int = 0) -> List[Dict]:
        """국가별 거래대금/등락률 상위 종목 조회"""
        if market == "KR":
            rankings = self.kis.get_fluctuation_ranking(top_n=top_n, max_price=max_price)
            # 가격 필터링
            if max_price > 0:
                rankings = [r for r in rankings if r["price"] <= max_price]
            return rankings[:top_n]
        elif market == "US":
            # Yahoo Finance Screener 활용 (Day Gainers)
            try:
                import requests
                url = "https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved?formatted=false&lang=en-US&region=US&scrIds=day_gainers&count=50"
                headers = {"User-Agent": "Mozilla/5.0"}
                resp = requests.get(url, headers=headers, timeout=10)
                if resp.status_code == 200:
                    quotes = resp.json().get("finance", {}).get("result", [{}])[0].get("quotes", [])
                    rankings = []
                    for q in quotes:
                        price = q.get("regularMarketPrice", 0)
                        if max_price > 0 and price > max_price:
                            continue
                        rankings.append({
                            "symbol": q.get("symbol"),
                            "name": q.get("shortName") or q.get("longName"),
                            "price": price,
                            "change_rate": q.get("regularMarketChangePercent", 0),
                            "volume": q.get("regularMarketVolume", 0),
                            "market": "US"
                        })
                    return rankings[:top_n]
            except Exception as e:
                print(f"[Collector] US Ranking fetch failed: {e}")
                return []
        
        return []

    def get_current_price(self, symbol: str, market: str) -> Dict:
        """통합 현재가 조회"""
        if market == "KR":
            data = self.kis.inquire_price(symbol)
            if data:
                return {
                    "price": data.get("price", 0),
                    "change_rate": data.get("change_rate", 0),
                    "volume": data.get("volume", 0),
                    "open": data.get("open", 0),
                    "high": data.get("high", 0),
                    "low": data.get("low", 0),
                    "market": "KR"
                }
            return {"price": 0, "change_rate": 0, "volume": 0, "market": "KR"}
        else:
            # 해외주식 (미국/홍콩/중국/일본)
            exchange_map = {"US": "NAS", "HK": "HKS", "CN": "SHS", "JP": "TSE"}
            exchange = exchange_map.get(market, "NAS")
            
            # 중국/홍콩 종목코드가 5자리 미만인 경우 앞자리 0 채움 등의 처리 필요시 추가
            
            data = self.kis.inquire_overseas_price(symbol, exchange)
            if data:
                return data
            return {"price": 0, "change_rate": 0, "volume": 0, "market": market}

    def get_balance_total(self) -> int:
        """통합 추정 예수금 (KRW 환산)"""
        balance = self.kis.inquire_balance()
        return balance.get("cash", 0)

    def get_holdings(self) -> List[Dict]:
        """보유종목 조회"""
        balance = self.kis.inquire_balance()
        return balance.get("holdings", [])

    def get_news(self, symbol: str, market: str) -> List[Dict]:
        """종목 관련 최신 뉴스 수집 (Yahoo Finance RSS)"""
        import requests
        import xml.etree.ElementTree as ET
        
        news_list = []
        try:
            # Yahoo Finance RSS URL
            if market == "KR":
                ticker = f"{symbol}.KS"  # 코스피 기준 (코스닥은 .KQ 체크 필요하지만 일단 KS 시도)
            else:
                ticker = symbol

            url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US"
            
            resp = requests.get(url, timeout=5)
            if resp.status_code == 200:
                root = ET.fromstring(resp.content)
                for item in root.findall(".//item")[:3]: # 최신 3개만
                    title = item.find("title").text
                    link = item.find("link").text
                    pubDate = item.find("pubDate").text
                    news_list.append({
                        "title": title,
                        "link": link,
                        "published_at": pubDate
                    })
        except Exception as e:
            print(f"[Collector] News fetch failed for {symbol}: {e}")
            
        return news_list
