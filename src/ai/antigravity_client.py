"""
Antigravity AI Client - Google AI 기반 NLP 분석 클라이언트

역할: 뉴스 감성 분석, 시장 리포트, 매매 판단 등 로컬 AI가 수행 불가능한 NLP 작업
인증 우선순위: Antigravity Ultra (직접 OAuth) > API Key (Google AI 직접)
"""
import json
import os
import re
from typing import Dict, List, Optional
from dataclasses import dataclass


@dataclass
class AntigravityConfig:
    """Antigravity 설정"""
    api_key: str = ""
    model: str = "gemini-2.0-flash"
    timeout: int = 120


class AntigravityClient:
    """Antigravity AI 클라이언트 (Antigravity Ultra + Google AI 직접)"""
    
    def __init__(self, config: AntigravityConfig = None):
        self.config = config or AntigravityConfig()
        self._antigravity_auth = None  # Antigravity Ultra 인증
        
        # DB → env 순서로 설정 로드 (Settings 페이지에서 저장한 값 우선)
        from database import DatabaseManager
        _db = DatabaseManager()
        
        if not self.config.api_key:
            self.config.api_key = _db.get_setting("ANTIGRAVITY_API_KEY")
        if self.config.model == "gemini-2.0-flash":
            self.config.model = _db.get_setting("ANTIGRAVITY_MODEL") or "gemini-2.0-flash"
        
        # 인증 모드 결정 (우선순위: antigravity > google_direct)
        try:
            from antigravity_auth import get_antigravity_auth
            auth = get_antigravity_auth()
            if auth.is_authenticated:
                self.mode = "antigravity"  # Antigravity Ultra 직접 호출
                self._antigravity_auth = auth
                self.config.model = auth.model  # 모델 동기화
            elif self.config.api_key:
                self.mode = "google_direct"
            else:
                self.mode = "none"
        except ImportError:
            if self.config.api_key:
                self.mode = "google_direct"
            else:
                self.mode = "none"
        
        print(f"🌐 Antigravity 클라이언트 초기화 (모드: {self.mode}, 모델: {self.config.model})")
    
    def is_available(self) -> bool:
        """Antigravity 서비스 사용 가능 여부"""
        if self.mode == "antigravity":
            return self._antigravity_auth is not None and self._antigravity_auth.is_authenticated
        elif self.mode == "google_direct":
            return bool(self.config.api_key)
        return False
    
    def refresh_mode(self):
        """인증 모드 재확인 (로그인/로그아웃 후 호출)"""
        try:
            from antigravity_auth import get_antigravity_auth
            auth = get_antigravity_auth()
            if auth.is_authenticated:
                self.mode = "antigravity"
                self._antigravity_auth = auth
                self.config.model = auth.model
            elif self.config.api_key:
                self.mode = "google_direct"
                self._antigravity_auth = None
            else:
                self.mode = "none"
                self._antigravity_auth = None
        except ImportError:
            pass
        print(f"🔄 Antigravity 모드 변경: {self.mode}, 모델: {self.config.model}")
    

    def _call_google_ai(self, prompt: str, system_prompt: str = "", json_mode: bool = False) -> Dict:
        """Google AI API 직접 호출 (google-generativeai SDK)"""
        try:
            import google.generativeai as genai
            
            genai.configure(api_key=self.config.api_key)
            model = genai.GenerativeModel(self.config.model)
            
            full_prompt = f"{system_prompt}\n\n{prompt}" if system_prompt else prompt
            
            response = model.generate_content(
                full_prompt,
                generation_config=genai.types.GenerationConfig(
                    temperature=0.3,
                    max_output_tokens=4096,
                )
            )
            
            content = response.text
            return {"success": True, "content": content}
            
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def _call_antigravity(self, prompt: str, system_prompt: str = "", json_mode: bool = False) -> Dict:
        """Antigravity Ultra Cloud Code API 직접 호출"""
        if not self._antigravity_auth:
            return {"success": False, "error": "Antigravity not authenticated"}
        
        try:
            if json_mode:
                prompt += "\n\n반드시 JSON 형식으로만 응답하세요. 마크다운이나 설명 없이 순수 JSON만 반환하세요."
            
            result = self._antigravity_auth.call_api(
                prompt=prompt,
                system_prompt=system_prompt,
            )
            return result
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def _call_ai(self, prompt: str, system_prompt: str = "", json_mode: bool = False) -> Dict:
        """AI 호출 (모드에 따라 분기)"""
        # Antigravity Ultra 우선
        if self.mode == "antigravity":
            result = self._call_antigravity(prompt, system_prompt, json_mode)
            if result.get("success"):
                return result
            # Antigravity 실패 시 Google AI fallback
            print(f"  ⚠️ Antigravity Ultra 실패, fallback: {result.get('error')}")
            if self.config.api_key:
                return self._call_google_ai(prompt, system_prompt, json_mode)
            return result
        elif self.mode == "google_direct":
            return self._call_google_ai(prompt, system_prompt, json_mode)
        else:
            return {"success": False, "error": "No AI service configured. Login to Antigravity Ultra or set API key."}
    
    def _extract_json(self, text: str) -> Optional[dict]:
        """텍스트에서 JSON 추출"""
        try:
            json_match = re.search(r'\{[^{}]*\}', text, re.DOTALL)
            if json_match:
                return json.loads(json_match.group())
        except Exception:
            pass
        return None
    

    
    def judge_stock(self, symbol: str, name: str, indicators: Dict, market_condition: str = "") -> Dict:
        """매매 판단 (BUY/SELL/HOLD)"""
        prompt = f"""
역할: 당신은 월스트리트 출신의 전설적인 트레이더입니다.
상황: {market_condition or '시장 혼조세'}
종목: {name} ({symbol})
데이터:
- 현재가: {indicators.get('current_price', 0):,}원
- 등락률: {indicators.get('change_rate', 0):.2f}%
- 거래량회전율: {indicators.get('volume_ratio', 0):.2f}%
- PER: {indicators.get('per', 0)}
- RSI: {indicators.get('rsi', 0)}

위 데이터를 바탕으로 매매 판단을 내려주세요.
JSON 형식으로 답하세요:
{{"action": "BUY" | "SELL" | "HOLD", "confidence": 0~100, "reason": "판단 근거 상세히", "target_price": 목표가, "stop_loss": 손절가}}
"""
        result = self._call_ai(prompt, json_mode=True)
        
        if result.get("success"):
            parsed = self._extract_json(result.get("content", ""))
            return parsed or {"action": "HOLD", "confidence": 0, "reason": "JSON 파싱 실패", "raw": result.get("content")}
        else:
            return {"action": "ERROR", "reason": result.get("error", "Unknown")}
    

    def analyze_sentiment(self, news_items: List[Dict]) -> Dict:
        """뉴스 감성 분석 (긍정/부정/중립)"""
        if not news_items:
            return {"sentiment": "neutral", "score": 0, "confidence": 0}
        
        news_text = "\n".join([
            f"- {item.get('title', '')}: {item.get('snippet', '')}"
            for item in news_items[:10]
        ])
        
        if not news_text.strip():
            return {"sentiment": "neutral", "score": 0, "confidence": 0}
        
        prompt = f"""
다음 뉴스들의 주식 투자 관점에서 감성을 분석하세요.

뉴스:
{news_text}

JSON 형식으로 응답:
{{"sentiment": "positive|negative|neutral", "score": -100~100, "confidence": 0~100, "key_factors": ["요인1", "요인2"], "summary": "한줄요약"}}
"""
        result = self._call_ai(prompt, system_prompt="주식 뉴스 감성 분석 전문가", json_mode=True)
        
        if result.get("success"):
            parsed = self._extract_json(result.get("content", ""))
            return parsed or {"sentiment": "neutral", "score": 0, "error": "파싱 실패"}
        else:
            return {"sentiment": "neutral", "score": 0, "error": result.get("error")}
    

    def generate_market_report(self, stocks_data: list, additional_context: str = "") -> str:
        """시장 종합 리포트 생성"""
        if not stocks_data:
            return "분석할 데이터가 없습니다."
        
        avg_change = sum(s.get("change_rate", 0) for s in stocks_data) / len(stocks_data)
        rising = len([s for s in stocks_data if s.get("change_rate", 0) > 0])
        falling = len(stocks_data) - rising
        
        prompt = f"""
오늘의 주식시장 분석 리포트를 간결하게 작성하세요.

## 시장 현황
- 분석 종목: {len(stocks_data)}개
- 평균 등락률: {avg_change:.2f}%
- 상승 {rising}개 / 하락 {falling}개

## 주요 종목
{chr(10).join([f"- {s.get('name', 'N/A')}: {s.get('change_rate', 0):+.1f}%" for s in stocks_data[:5]])}

{f'## 추가 인사이트{chr(10)}{additional_context}' if additional_context else ''}

간결하고 전문적인 시장 분석 리포트를 작성하세요.
"""
        result = self._call_ai(prompt, system_prompt="증권사 수석 애널리스트")
        
        if result.get("success"):
            return result.get("content", "리포트 생성 실패")
        else:
            return f"리포트 생성 실패: {result.get('error')}"
    

    
    def analyze_stock(self, stock_data: Dict) -> Dict:
        """주식 분석 (Antigravity 모델 사용)"""
        prompt = f"""
주식 투자 전문가로서 다음 종목을 분석해주세요.

## 종목 정보
- 종목명: {stock_data.get('name', 'N/A')} ({stock_data.get('symbol', 'N/A')})
- 현재가: {stock_data.get('current_price', 0):,}원
- 등락률: {stock_data.get('change_rate', 0):.2f}%
- 시가: {stock_data.get('open', 0):,}원
- 고가: {stock_data.get('high', 0):,}원
- 저가: {stock_data.get('low', 0):,}원
- 거래량: {stock_data.get('volume', 0):,}주

## 분석 요청
1. 투자 매력도 점수 (1-100)
2. 단기 전망 (1주일)
3. 추천 액션 (매수/관망/매도)
4. 주요 이유

JSON으로 응답:
{{"score": 점수, "outlook": "전망", "action": "추천", "reason": "이유", "summary": "한줄요약"}}
"""
        result = self._call_ai(prompt, system_prompt="한국 주식시장 전문 애널리스트", json_mode=True)
        
        if result.get("success"):
            parsed = self._extract_json(result.get("content", ""))
            return parsed or {"raw_response": result.get("content")}
        else:
            return {"error": result.get("error")}
    

