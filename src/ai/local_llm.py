"""
Local LLM Client - 로컬 AI 모델 연동 (BitNet, Ollama, Transformers 등)
OpenAI 호환 API (/v1/chat/completions) 사용
"""
import requests
import json
import re
from typing import Dict, List, Optional


class LocalLLMClient:
    """로컬 LLM 클라이언트 (OpenAI 호환 API)

    DB에서 LOCAL_LLM_URL / LOCAL_LLM_MODEL을 읽으며,
    설정 변경 시 즉시 반영됩니다 (서버 재시작 불필요).
    """

    def __init__(self, db=None):
        self._db = db
        self.max_tokens = 500
        self.temperature = 0.3
        self.timeout = 120

    def _get_url(self) -> str:
        """DB에서 LLM 서버 URL 조회 (매 호출마다 최신값)"""
        if self._db:
            try:
                url = self._db.get_setting("LOCAL_LLM_URL", "")
                if url:
                    return url.rstrip("/")
            except Exception:
                pass
        return "http://host.docker.internal:11434"

    def _get_model(self) -> str:
        """DB에서 모델명 조회"""
        if self._db:
            try:
                model = self._db.get_setting("LOCAL_LLM_MODEL", "")
                if model:
                    return model
            except Exception:
                pass
        return "bitnet-3b"

    def is_available(self) -> bool:
        """서버 가용성 체크"""
        try:
            response = requests.get(
                f"{self._get_url()}/v1/models",
                timeout=5
            )
            return response.status_code == 200
        except Exception:
            return False

    def get_models(self) -> List[str]:
        """사용 가능한 모델 목록"""
        try:
            response = requests.get(
                f"{self._get_url()}/v1/models",
                timeout=10
            )
            if response.status_code == 200:
                data = response.json()
                return [m.get("id", "") for m in data.get("data", [])]
        except Exception:
            pass
        return []

    def chat(
        self,
        messages: List[Dict],
        max_tokens: int = None,
        temperature: float = None,
        json_mode: bool = False
    ) -> Dict:
        """채팅 완성 요청 (OpenAI 호환)"""
        payload = {
            "model": self._get_model(),
            "messages": messages,
            "max_tokens": max_tokens or self.max_tokens,
            "temperature": temperature or self.temperature
        }

        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        try:
            response = requests.post(
                f"{self._get_url()}/v1/chat/completions",
                headers={"Content-Type": "application/json"},
                json=payload,
                timeout=self.timeout
            )

            if response.status_code == 200:
                data = response.json()
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                return {
                    "success": True,
                    "content": content,
                    "usage": data.get("usage", {}),
                    "model": data.get("model", self._get_model())
                }
            else:
                return {
                    "success": False,
                    "error": f"HTTP {response.status_code}: {response.text[:200]}"
                }

        except requests.Timeout:
            return {"success": False, "error": "Timeout"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def analyze_stock(self, stock_data: Dict) -> Dict:
        """주식 분석 (로컬 LLM)"""
        prompt = f"""주식 투자 전문가로서 다음 종목을 분석해주세요.
종목: {stock_data.get('name', 'N/A')} ({stock_data.get('symbol', 'N/A')})
현재가: {stock_data.get('current_price', 0):,}원
등락률: {stock_data.get('change_rate', 0):.2f}%

JSON으로 답변: {{"score": 점수, "outlook": "전망", "action": "추천", "summary": "한줄요약"}}"""

        result = self.chat([
            {"role": "system", "content": "주식 분석 전문가. JSON으로만 답변."},
            {"role": "user", "content": prompt}
        ], max_tokens=300)

        if result.get("success"):
            content = result.get("content", "")
            try:
                json_match = re.search(r'\{[^{}]*\}', content)
                if json_match:
                    return json.loads(json_match.group())
            except Exception:
                pass
            return {"raw_response": content}

        return result

    def analyze_sentiment(self, text: str) -> Dict:
        """감성 분석 (로컬 LLM)"""
        prompt = f"""다음 텍스트의 투자 관점 감성을 분석하세요.
텍스트: {text[:500]}
JSON으로 답변: {{"sentiment": "positive/negative/neutral", "score": -100~100}}"""

        result = self.chat([
            {"role": "user", "content": prompt}
        ], max_tokens=100)

        if result.get("success"):
            content = result.get("content", "")
            try:
                json_match = re.search(r'\{[^{}]*\}', content)
                if json_match:
                    return json.loads(json_match.group())
            except Exception:
                pass

        return {"sentiment": "neutral", "score": 0}
