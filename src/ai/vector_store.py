"""
Vector Store - ChromaDB를 활용한 주식 데이터 벡터화 및 검색
"""
import chromadb
from chromadb.config import Settings
from openai import OpenAI
from datetime import datetime
import json
from typing import Optional
import hashlib

from config import CHROMA_DIR, OPENAI_API_KEY, EMBEDDING_MODEL


class StockVectorStore:
    """주식 데이터를 벡터로 저장하고 유사 패턴 검색"""
    
    def __init__(self):
        # ChromaDB 클라이언트 초기화 (로컬 영구 저장)
        self.client = chromadb.PersistentClient(path=str(CHROMA_DIR))
        
        # OpenAI 임베딩 클라이언트
        self.openai = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
        
        # 컬렉션 초기화
        self.stock_collection = self.client.get_or_create_collection(
            name="stock_patterns",
            metadata={"description": "주식 시세 패턴 및 투자 분석 데이터"}
        )
        
        self.news_collection = self.client.get_or_create_collection(
            name="stock_news",
            metadata={"description": "주식 관련 뉴스 및 공시"}
        )

        self.trade_collection = self.client.get_or_create_collection(
            name="trade_patterns",
            metadata={"description": "매매 시점 캔들/지표 패턴 (학습용)"}
        )
    
    def _get_embedding(self, text: str) -> list:
        """텍스트를 벡터로 변환"""
        if not self.openai:
            # OpenAI 키가 없으면 간단한 해시 기반 임베딩 (테스트용)
            hash_val = hashlib.md5(text.encode()).hexdigest()
            return [int(hash_val[i:i+2], 16) / 255.0 for i in range(0, 32, 2)]
        
        try:
            response = self.openai.embeddings.create(
                model=EMBEDDING_MODEL,
                input=text
            )
            return response.data[0].embedding
        except Exception as e:
            print(f"OpenAI embedding failed: {e}. Falling back to hash embedding.")
            self.openai = None # Disable for future calls
            hash_val = hashlib.md5(text.encode()).hexdigest()
            return [int(hash_val[i:i+2], 16) / 255.0 for i in range(0, 32, 2)]
    
    def create_stock_document(self, stock_data: dict) -> str:
        """주식 데이터를 분석 가능한 텍스트 문서로 변환"""
        doc = f"""
종목: {stock_data.get('name', '')} ({stock_data.get('symbol', '')})
현재가: {stock_data.get('current_price', 0):,}원
등락률: {stock_data.get('change_rate', 0):.2f}%
거래량비율: {stock_data.get('volume_ratio', 0):.2f}x (5일 평균 대비)
모멘텀: {stock_data.get('price_momentum', 0):.2f}% (5일)
변동성: {stock_data.get('volatility', 0):.2f}%
분석시간: {datetime.now().strftime('%Y-%m-%d %H:%M')}

투자 신호 분석:
- 거래량 {'급증' if stock_data.get('volume_ratio', 0) > 2 else '보통' if stock_data.get('volume_ratio', 0) > 1 else '감소'}
- 가격 {'상승세' if stock_data.get('price_momentum', 0) > 0 else '하락세'}
- 변동성 {'높음' if stock_data.get('volatility', 0) > 3 else '보통' if stock_data.get('volatility', 0) > 1.5 else '낮음'}
""".strip()
        return doc
    
    def add_stock_pattern(self, stock_data: dict, analysis: str = "") -> str:
        """주식 패턴 데이터 저장"""
        doc = self.create_stock_document(stock_data)
        if analysis:
            doc += f"\n\nAI 분석:\n{analysis}"
        
        doc_id = f"{stock_data['symbol']}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        embedding = self._get_embedding(doc)
        
        self.stock_collection.add(
            ids=[doc_id],
            embeddings=[embedding],
            documents=[doc],
            metadatas=[{
                "symbol": stock_data.get("symbol", ""),
                "name": stock_data.get("name", ""),
                "price": float(stock_data.get("current_price", 0)),
                "change_rate": float(stock_data.get("change_rate", 0)),
                "volume_ratio": float(stock_data.get("volume_ratio", 0)),
                "momentum": float(stock_data.get("price_momentum", 0)),
                "volatility": float(stock_data.get("volatility", 0)),
                "timestamp": datetime.now().isoformat()
            }]
        )
        
        return doc_id
    
    def search_similar_patterns(self, query: str, n_results: int = 5) -> list:
        """쿼리와 유사한 패턴 검색"""
        embedding = self._get_embedding(query)
        
        results = self.stock_collection.query(
            query_embeddings=[embedding],
            n_results=n_results
        )
        
        return results
    
    def find_similar_stocks(self, symbol: str, n_results: int = 5) -> list:
        """특정 종목과 유사한 패턴의 종목 찾기"""
        # 해당 종목의 최신 데이터 가져오기
        results = self.stock_collection.get(
            where={"symbol": symbol},
            limit=1
        )
        
        if not results["documents"]:
            return []
        
        # 유사 패턴 검색
        return self.search_similar_patterns(results["documents"][0], n_results)
    
    def get_high_potential_stocks(self, min_momentum: float = 2.0, min_volume_ratio: float = 1.5) -> list:
        """투자 가치가 높은 종목 필터링"""
        # 모멘텀과 거래량이 높은 종목 검색
        query = f"상승세 종목, 거래량 급증, 모멘텀 {min_momentum}% 이상, 투자 가치 높음"
        
        results = self.search_similar_patterns(query, n_results=10)
        
        # 메타데이터 기반 필터링
        filtered = []
        if results["metadatas"]:
            for i, meta in enumerate(results["metadatas"][0]):
                if (meta.get("momentum", 0) >= min_momentum and 
                    meta.get("volume_ratio", 0) >= min_volume_ratio):
                    filtered.append({
                        "metadata": meta,
                        "document": results["documents"][0][i] if results["documents"] else "",
                        "distance": results["distances"][0][i] if results["distances"] else 0
                    })
        
        return sorted(filtered, key=lambda x: x["metadata"].get("momentum", 0), reverse=True)
    
    def get_collection_stats(self) -> dict:
        """컬렉션 통계"""
        return {
            "stock_patterns": self.stock_collection.count(),
            "news": self.news_collection.count(),
            "trade_patterns": self.trade_collection.count(),
        }

    # ============================
    # 매매 패턴 벡터 저장/검색
    # ============================

    def add_trade_pattern(self, trade_data: dict) -> str:
        """매매 시점의 캔들/지표 데이터를 벡터로 임베딩하여 저장

        trade_data keys:
            symbol, name, market, side (buy/sell),
            candle_snapshot (dict), indicators (dict),
            pattern_label (str), result (pending/success/fail),
            pnl_pct (float, optional)
        """
        snap = trade_data.get("candle_snapshot", {})
        ind = trade_data.get("indicators", {})

        # 임베딩용 텍스트 문서 생성
        doc = (
            f"종목: {trade_data.get('name','')} ({trade_data.get('symbol','')}) "
            f"시장: {trade_data.get('market','US')}\n"
            f"매매: {trade_data.get('side','buy')}\n"
            f"패턴: {trade_data.get('pattern_label','')}\n"
            f"RSI: {ind.get('rsi14','?')} MACD: {ind.get('macd_hist','?')}\n"
            f"BB위치: {ind.get('bb_position','?')} 거래량비: {ind.get('vol_ratio','?')}\n"
            f"추세: 5d={snap.get('trend_5d','?')}% 20d={snap.get('trend_20d','?')}%\n"
            f"결과: {trade_data.get('result','pending')}"
        )
        if trade_data.get("pnl_pct") is not None:
            doc += f" 수익률: {trade_data['pnl_pct']:.1f}%"

        doc_id = f"trade_{trade_data.get('symbol','X')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        embedding = self._get_embedding(doc)

        self.trade_collection.add(
            ids=[doc_id],
            embeddings=[embedding],
            documents=[doc],
            metadatas=[{
                "symbol": trade_data.get("symbol", ""),
                "name": trade_data.get("name", ""),
                "market": trade_data.get("market", "US"),
                "side": trade_data.get("side", "buy"),
                "pattern_label": trade_data.get("pattern_label", ""),
                "result": trade_data.get("result", "pending"),
                "pnl_pct": float(trade_data.get("pnl_pct", 0) or 0),
                "timestamp": datetime.now().isoformat(),
            }]
        )
        return doc_id

    def search_similar_trade_patterns(self, query_text: str,
                                       n_results: int = 5,
                                       side: str = None) -> list:
        """현재 지표와 유사한 과거 매매 패턴 검색

        Returns list of dicts with keys: document, metadata, distance
        """
        if self.trade_collection.count() == 0:
            return []

        embedding = self._get_embedding(query_text)
        where_filter = {"side": side} if side else None

        try:
            results = self.trade_collection.query(
                query_embeddings=[embedding],
                n_results=min(n_results, self.trade_collection.count()),
                where=where_filter,
            )
        except Exception:
            # where filter 실패 시 fallback
            results = self.trade_collection.query(
                query_embeddings=[embedding],
                n_results=min(n_results, self.trade_collection.count()),
            )

        items = []
        if results and results.get("documents"):
            for i, doc in enumerate(results["documents"][0]):
                items.append({
                    "document": doc,
                    "metadata": results["metadatas"][0][i] if results.get("metadatas") else {},
                    "distance": results["distances"][0][i] if results.get("distances") else 0,
                })
        return items


if __name__ == "__main__":
    store = StockVectorStore()
    print(f"Vector Store 초기화 완료")
    print(f"컬렉션 통계: {store.get_collection_stats()}")
    
    # 테스트 데이터 추가
    test_data = {
        "symbol": "005930",
        "name": "삼성전자",
        "current_price": 71000,
        "change_rate": 2.5,
        "volume_ratio": 1.8,
        "price_momentum": 3.2,
        "volatility": 1.5
    }
    
    doc_id = store.add_stock_pattern(test_data, "AI 분석: 반도체 수요 증가로 상승세 지속 예상")
    print(f"패턴 저장됨: {doc_id}")
    
    # 유사 패턴 검색
    results = store.search_similar_patterns("상승세 반도체 종목")
    print(f"검색 결과: {len(results['documents'][0])}건")
