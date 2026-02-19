"""
Strategy Extractor - YouTube 영상 URL을 Gemini에 전달하여 매매 전략을 자동 생성
방식: YouTube URL → Gemini (영상 직접 분석) → 정형화된 전략 JSON
"""
import re
import json
from antigravity_client import AntigravityClient


def _extract_nested_json(text: str) -> dict:
    """중첩된 JSON을 올바르게 추출 (가장 바깥 {} 블록)"""
    # ```json ... ``` 코드블록 먼저 시도
    code_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
    if code_match:
        try:
            return json.loads(code_match.group(1))
        except json.JSONDecodeError:
            pass

    # 가장 바깥 { } 매칭 (중첩 브레이스 카운팅)
    start = text.find('{')
    if start == -1:
        return None

    depth = 0
    for i in range(start, len(text)):
        if text[i] == '{':
            depth += 1
        elif text[i] == '}':
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i+1])
                except json.JSONDecodeError:
                    break
    return None


def _validate_strategy(parsed: dict) -> dict:
    """전략 JSON이 필요한 필드를 갖추고 있는지 검증, 누락 시 기본값 추가"""
    if not parsed or not isinstance(parsed, dict):
        return None

    # 필수 필드가 없으면 conditions 안의 sub-object를 잘못 잡은 것
    if "name" not in parsed and "conditions" not in parsed:
        return None

    # 기본값 보정
    parsed.setdefault("name", "추출된 전략")
    parsed.setdefault("description", "")
    parsed.setdefault("type", "daytrading")
    parsed.setdefault("market", "KR")
    parsed.setdefault("conditions", {})
    parsed.setdefault("source", "youtube")
    return parsed

STRATEGY_SYSTEM_PROMPT = """
당신은 20년 경력의 퀀트 트레이더이자 전략 수립 전문가입니다.
YouTube 영상에서 설명하는 주식 매매 기법을 분석하여, 실제 자동매매 시스템에서
실행 가능한 수준의 구체적인 전략 규칙으로 변환하는 것이 당신의 전문 능력입니다.

핵심 원칙:
1. 모호한 표현은 구체적 수치로 변환하세요 (예: "거래량 많은" → "거래량 전일 대비 200% 이상")
2. 시간대별 전략이 있으면 반드시 포함하세요
3. 리스크 관리(손절/익절 기준)를 반드시 추출하세요
4. 영상에서 언급한 기술적 지표는 모두 포함하세요
5. 전략의 핵심 철학과 심리적 요소도 기록하세요
"""

STRATEGY_JSON_FORMAT = """\
반드시 아래 JSON 형식으로만 응답하세요. JSON 외에 다른 텍스트를 포함하지 마세요.

{{
    "name": "전략 이름 (영상 내용을 잘 반영하는 전문적인 이름)",
    "description": "전략에 대한 핵심 요약 (3~5문장, 어떤 상황에서 어떻게 매매하는지)",
    "type": "momentum | swing | trend | breakout | scalping | daytrading 중 하나",
    "market": "KR | US | ALL 중 하나",
    "timeframe": "장 시작 전, 장 초반, 장 중반, 장 후반 등 주요 시간대별 행동 요약",
    "conditions": {{
        "buy": {{
            "rules": [
                "매수 조건 1 (구체적 수치 포함)",
                "매수 조건 2",
                "..."
            ],
            "indicators": ["이동평균선", "거래량", "RSI", "MACD 등 사용하는 기술적 지표"],
            "timing": "매수 타이밍 (예: 장 시작 후 30분 이내, 눌림목 발생 시 등)"
        }},
        "sell": {{
            "profit_target": "익절 기준 (예: +3% 도달 시)",
            "stop_loss": "손절 기준 (예: -2% 하락 시)",
            "rules": ["매도 조건 1", "매도 조건 2"]
        }}
    }},
    "risk_management": {{
        "max_position_pct": "1회 매수 시 총자산 대비 최대 비중 (%)",
        "max_loss_daily": "일일 최대 허용 손실",
        "notes": "리스크 관리 관련 추가 조언"
    }},
    "key_principles": ["전략의 핵심 원칙 1", "원칙 2", "..."],
    "source": "youtube"
}}
"""


class StrategyExtractor:
    def __init__(self):
        self.ai = AntigravityClient()

    def _extract_video_id(self, url: str) -> str:
        """유튜브 URL에서 비디오 ID 추출"""
        patterns = [
            r'(?:v=|\/)([0-9A-Za-z_-]{11}).*',
            r'(?:be\/)([0-9A-Za-z_-]{11}).*'
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return ""

    def analyze_with_url(self, url: str) -> dict:
        """Gemini에 YouTube URL을 직접 전달하여 전략 추출 (영상 내용 분석)"""
        prompt = f"""
다음 YouTube 영상의 전체 내용을 꼼꼼히 분석하여, 실전에서 바로 사용 가능한
주식 매매 전략을 추출해주세요.

🎬 영상 URL: {url}

분석 시 반드시 다음 항목을 확인하세요:
1. **매수 진입 조건**: 어떤 종목을, 어떤 조건에서, 언제 매수하는가?
   - 종목 선정 기준 (거래량, 시가총액, 테마, 뉴스 등)
   - 기술적 분석 지표 (이동평균선, 캔들패턴, 호가창, RSI, MACD 등)
   - 매수 타이밍 (장 시작 전/후, 특정 패턴 발생 시 등)

2. **매도 조건**: 언제 팔 것인가?
   - 익절 기준 (목표 수익률, 저항선 도달 등)
   - 손절 기준 (최대 허용 손실, 지지선 이탈 등)
   - 분할 매도 여부

3. **시간대별 전략**: 장 전/장 초반/장 중반/장 마감 등 시간에 따른 행동

4. **리스크 관리**: 자금 관리, 포지션 크기, 일일 손실 한도

5. **핵심 원칙**: 영상에서 강조하는 매매 철학이나 멘탈 관리

{STRATEGY_JSON_FORMAT}
"""
        result = self.ai._call_ai(prompt, system_prompt=STRATEGY_SYSTEM_PROMPT, json_mode=True)
        if result.get("success"):
            content = result.get("content", "")
            parsed = _extract_nested_json(content)
            parsed = _validate_strategy(parsed)
            if parsed:
                parsed["source_url"] = url
                return parsed
            return {"error": "JSON 파싱 실패 — AI 응답에서 전략 구조를 찾을 수 없음"}
        return {"error": result.get("error", "AI 호출 실패")}

    def analyze_with_transcript(self, transcript: str, url: str = "") -> dict:
        """자막 텍스트를 AI에 전달하여 전략 추출 (폴백용)"""
        if not transcript:
            return {"error": "자막을 읽을 수 없습니다."}

        prompt = f"""
다음은 주식 투자 기법을 설명하는 영상의 자막입니다:
---
{transcript[:8000]}
---

위 내용에서 핵심적인 매매 전략을 추출하세요.

{STRATEGY_JSON_FORMAT}
"""
        result = self.ai._call_ai(prompt, system_prompt=STRATEGY_SYSTEM_PROMPT, json_mode=True)
        if result.get("success"):
            content = result.get("content", "")
            parsed = _extract_nested_json(content)
            parsed = _validate_strategy(parsed)
            if parsed:
                if url:
                    parsed["source_url"] = url
                return parsed
            return {"error": "JSON 파싱 실패 — AI 응답에서 전략 구조를 찾을 수 없음"}
        return {"error": result.get("error", "AI 호출 실패")}

    def get_transcript(self, video_id: str) -> str:
        """자막 추출 (한국어 우선, 차선으로 영어) - 폴백용"""
        try:
            from youtube_transcript_api import YouTubeTranscriptApi
            transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)

            # 한국어 자막 시도
            try:
                transcript = transcript_list.find_transcript(['ko'])
            except Exception:
                # 한국어 없으면 영어 또는 기타
                transcript = transcript_list.find_transcript(['en', 'ja', 'zh-Hans'])

            data = transcript.fetch()
            text = " ".join([d['text'] for d in data])
            return text
        except Exception as e:
            print(f"Transcript extraction error: {e}")
            return ""


def extract_from_youtube(url: str) -> dict:
    """통합 호출 함수: YouTube URL → Gemini 직접 분석 (자막 폴백)"""
    extractor = StrategyExtractor()
    video_id = extractor._extract_video_id(url)
    if not video_id:
        return {"error": "올바른 유튜브 URL이 아닙니다."}

    # 1차: Gemini에 URL 직접 전달 (영상 분석)
    print(f"[Strategy] Gemini에 YouTube URL 직접 분석 요청: {url}")
    result = extractor.analyze_with_url(url)
    if "error" not in result:
        print(f"[Strategy] ✅ Gemini 영상 직접 분석 성공: {result.get('name', '?')}")
        return result

    # 2차: 자막 추출 후 분석 (폴백)
    print(f"[Strategy] ⚠️ 직접 분석 실패 ({result.get('error', '?')}), 자막 추출 폴백...")
    transcript = extractor.get_transcript(video_id)
    if transcript:
        print(f"[Strategy] 자막 {len(transcript)}자 추출 완료, AI 분석 시작...")
        return extractor.analyze_with_transcript(transcript, url)

    return {"error": f"영상 분석 실패: Gemini 직접 분석과 자막 추출 모두 실패"}
