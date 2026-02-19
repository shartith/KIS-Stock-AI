"""
Stock AI Analyzer - Configuration (Global)
"""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Paths
BASE_DIR = Path(__file__).parent
PROJECT_ROOT = BASE_DIR.parent.parent
DATA_DIR = PROJECT_ROOT / "data"
CHROMA_DIR = DATA_DIR / "chroma_db"

DATA_DIR.mkdir(exist_ok=True)
CHROMA_DIR.mkdir(exist_ok=True)

# AI Mode
AI_MODE = os.getenv("AI_MODE", "local")
LOCAL_LLM_URL = os.getenv("LOCAL_LLM_URL", "http://host.docker.internal:11434")
LOCAL_LLM_MODEL = os.getenv("LOCAL_LLM_MODEL", "qwen2.5:latest")
LOCAL_LLM_TIMEOUT = int(os.getenv("LOCAL_LLM_TIMEOUT", "120"))


# Antigravity (Google AI)
ANTIGRAVITY_API_KEY = os.getenv("ANTIGRAVITY_API_KEY", "")
ANTIGRAVITY_MODEL = os.getenv("ANTIGRAVITY_MODEL", "gemini-2.0-flash")

# OpenAI (Optional)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")

# ======================
# 시장 정보 (Market Info)
# ======================
MARKET_INFO = {
    "KR": {"flag": "🇰🇷", "name": "한국", "hours": "09:00~15:30", "tz": "Asia/Seoul",
            "index": "^KS11", "index_name": "KOSPI", "currency": "₩"},
    "JP": {"flag": "🇯🇵", "name": "일본", "hours": "09:00~15:00", "tz": "Asia/Tokyo",
            "index": "^N225", "index_name": "Nikkei 225", "currency": "¥"},
    "CN": {"flag": "🇨🇳", "name": "중국", "hours": "10:00~16:00", "tz": "Asia/Shanghai",
            "index": "000001.SS", "index_name": "Shanghai", "currency": "¥"},
    "HK": {"flag": "🇭🇰", "name": "홍콩", "hours": "10:00~17:00", "tz": "Asia/Hong_Kong",
            "index": "^HSI", "index_name": "Hang Seng", "currency": "HK$"},
    "US": {"flag": "🇺🇸", "name": "미국", "hours": "23:30~06:00", "tz": "America/New_York",
            "index": "^GSPC", "index_name": "S&P 500", "currency": "$"},
}

# 코스닥 종목 식별용 (Yahoo Suffix 결정에 사용)
# 실제로는 더 많은 종목이 있지만, 주요 종목만 포함하거나 DB에서 관리하는 것이 좋음.
# 현재는 일부 하드코딩 유지하거나 제거 가능.
KOSDAQ_CODES = {"247540", "086520", "028300", "196170", "277810",
                "058470", "214450", "214150", "180400"}

# 각 국가 Yahoo Finance suffix 매핑
YAHOO_SUFFIX = {
    "KR": lambda code: ".KQ" if code in KOSDAQ_CODES else ".KS",
    "JP": lambda code: ".T",
    "CN": lambda code: ".SZ" if code.startswith("00") or code.startswith("30") else ".SS",
    "HK": lambda code: ".HK",
    "US": lambda code: "",
}

# ======================
# 분석 설정
# ======================

ANALYSIS_INTERVAL_SECONDS = 300
SIMILARITY_THRESHOLD = 0.75

# ======================
# 트레이딩 설정 (Trading Config)
# ======================
HARD_STOP_LOSS_PERCENT = -5.0       # 하드 손절 비율 (%)
TRAILING_STOP_CONFIG = {
    "activation_offset": 3.0,  # 3% 수익 시 활성화
    "trailing_offset": 1.5     # 최고가 대비 1.5% 하락 시 매도
}
# 시간 기반 익절 (보유시간(분): 목표수익률(%))
TIME_BASED_ROI = {
    30: 5.0,   # 30분 이내: 5% 이상 익절
    60: 3.0,   # 60분 이내: 3% 이상 익절
    120: 1.5,  # 2시간 이내: 1.5% 이상 익절
    240: 0.5   # 4시간 이내: 0.5% 이상 익절 (본전 탈출)
}
PORTFOLIO_ALLOCATION = {
    "swing": 0.50,  # 스윙 비중
    "day": 0.50     # 단타 비중
}
DEFAULT_FX_RATES = {
    "US": 1400.0,
    "JP": 9.5,
    "CN": 195.0,
    "HK": 180.0
}
MIN_TRADE_AMOUNT_KRW = 100000 # 최소 거래 금액 (원)
