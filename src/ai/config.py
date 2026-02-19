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
LOCAL_LLM_URL = os.getenv("LOCAL_LLM_URL", "http://127.0.0.1:8002")
LOCAL_LLM_MODEL = os.getenv("LOCAL_LLM_MODEL", "bitnet-3b")
LOCAL_LLM_TIMEOUT = int(os.getenv("LOCAL_LLM_TIMEOUT", "120"))


# Antigravity (Google AI)
ANTIGRAVITY_API_KEY = os.getenv("ANTIGRAVITY_API_KEY", "")
ANTIGRAVITY_MODEL = os.getenv("ANTIGRAVITY_MODEL", "gemini-2.0-flash")

# OpenAI (Optional)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")


# =========================================================================
# 종목 설정 — (코드, 종목명, 시가총액(조원 또는 $B 기준))
# 시총은 히트맵 블록 크기 결정용 (대략치)
# =========================================================================

# ── 🇰🇷 한국 (KRX) ──────────────────────────────────────
# Yahoo suffix: .KS (코스피), .KQ (코스닥)
# ⚠️ 잘못 등록하면 Yahoo에서 다른 종목 가격을 가져옴
KOSDAQ_CODES = {"247540", "086520", "028300", "196170", "277810",
                "058470", "214450", "214150", "180400"}  # 실제 코스닥 종목만 (DXVX 추가)

TOP_STOCKS_KR = [
    ("005930", "삼성전자", 400),
    ("000660", "SK하이닉스", 130),
    ("373220", "LG에너지솔루션", 90),
    ("207940", "삼성바이오로직스", 55),
    ("005380", "현대차", 55),
    ("005490", "POSCO홀딩스", 50),
    ("000270", "기아", 40),
    ("068270", "셀트리온", 35),
    ("105560", "KB금융", 32),
    ("055550", "신한지주", 30),
    ("035420", "네이버", 38),
    ("006400", "삼성SDI", 28),
    ("003670", "포스코퓨처엠", 22),
    ("051910", "LG화학", 25),
    ("012330", "현대모비스", 24),
    ("035720", "카카오", 18),
    ("028260", "삼성물산", 25),
    ("003550", "LG", 16),
    ("032830", "삼성생명", 20),
    ("086790", "하나금융지주", 18),
    ("066570", "LG전자", 17),
    ("316140", "우리금융지주", 15),
    ("009150", "삼성전기", 14),
    ("034730", "SK", 13),
    ("000810", "삼성화재", 14),
    ("030200", "KT", 10),
    ("010130", "고려아연", 12),
    ("259960", "크래프톤", 15),
    ("033780", "KT&G", 11),
    ("018260", "삼성에스디에스", 12),
    ("034020", "두산에너빌리티", 9),
    ("003490", "대한항공", 8),
    ("011200", "HMM", 8),
    ("096770", "SK이노베이션", 9),
    ("047050", "포스코인터내셔널", 7),
    ("010950", "S-Oil", 6),
    ("326030", "SK바이오팜", 8),
    ("267250", "HD현대", 7),
    ("009540", "HD한국조선해양", 8),
    ("329180", "HD현대중공업", 10),
    ("017670", "SK텔레콤", 5),
    ("090430", "아모레퍼시픽", 5),
    ("036570", "엔씨소프트", 4),
    ("323410", "카카오뱅크", 5),
    ("352820", "하이브", 8),
    ("377300", "카카오페이", 4),
    ("000100", "유한양행", 5),
    ("011170", "롯데케미칼", 3),
    ("271560", "오리온", 4),
    ("138040", "메리츠금융지주", 15),
]

# ── 🇯🇵 일본 (TSE) ──────────────────────────────────────
# Yahoo suffix: .T
TOP_STOCKS_JP = [
    ("7203", "Toyota", 45),
    ("6758", "Sony", 25),
    ("6861", "Keyence", 18),
    ("8306", "MUFG", 17),
    ("6501", "Hitachi", 16),
    ("6902", "Denso", 10),
    ("9432", "NTT", 14),
    ("9984", "SoftBank Group", 13),
    ("6367", "Daikin", 10),
    ("7741", "HOYA", 9),
    ("4063", "Shin-Etsu Chemical", 11),
    ("6098", "Recruit", 9),
    ("8035", "Tokyo Electron", 15),
    ("6594", "Nidec", 7),
    ("9433", "KDDI", 10),
    ("4502", "Takeda", 8),
    ("7267", "Honda", 9),
    ("7974", "Nintendo", 10),
    ("6981", "Murata", 7),
    ("8316", "SMB Financial", 8),
    ("4568", "Daiichi Sankyo", 12),
    ("6971", "Kyocera", 4),
    ("8058", "Mitsubishi Corp", 10),
    ("8031", "Mitsui & Co", 9),
    ("7751", "Canon", 5),
    ("8001", "ITOCHU", 9),
    ("2914", "Japan Tobacco", 7),
    ("6857", "Advantest", 8),
    ("4519", "Chugai Pharma", 8),
    ("6273", "SMC", 6),
    ("9434", "SoftBank Corp", 8),
    ("3382", "Seven & i", 4),
    ("6702", "Fujitsu", 5),
    ("4661", "Oriental Land", 7),
    ("7733", "Olympus", 4),
    ("8411", "Mizuho FG", 7),
    ("6301", "Komatsu", 5),
    ("4901", "Fujifilm", 4),
    ("8766", "Tokio Marine", 6),
    ("6503", "Mitsubishi Electric", 5),
    ("2802", "Ajinomoto", 4),
    ("3659", "Nexon", 3),
    ("7832", "Bandai Namco", 3),
    ("6762", "TDK", 3),
    ("9983", "Fast Retailing", 12),
    ("4543", "Terumo", 4),
    ("6723", "Renesas", 5),
    ("5108", "Bridgestone", 4),
    ("7011", "Mitsubishi HI", 5),
    ("8802", "Mitsubishi Estate", 3),
]

# ── 🇨🇳 중국 (SSE/SZSE) ──────────────────────────────────
# Yahoo suffix: .SS (상해), .SZ (심천)
SZSE_CODES = {"000858", "000333", "002594", "000651", "000725", "002475",
              "000001", "002714", "002415", "300750", "300059", "002371",
              "002352", "300760", "300015", "002304", "002049", "300124",
              "002460", "300122"}

TOP_STOCKS_CN = [
    ("600519", "Kweichow Moutai", 220),
    ("300750", "CATL", 120),
    ("601318", "Ping An Insurance", 80),
    ("600036", "China Merchants Bank", 70),
    ("000858", "Wuliangye Yibin", 55),
    ("601398", "ICBC", 60),
    ("600900", "Yangtze Power", 50),
    ("000333", "Midea Group", 50),
    ("601288", "Agricultural Bank", 40),
    ("600276", "Hengrui Medicine", 35),
    ("601939", "CCB", 45),
    ("000651", "Gree Electric", 25),
    ("600030", "CITIC Securities", 30),
    ("601012", "LONGi Green", 20),
    ("600887", "Inner Mongolia Yili", 25),
    ("002594", "BYD", 90),
    ("601166", "Industrial Bank", 22),
    ("600690", "Haier Smart Home", 25),
    ("601888", "China Tourism Group", 20),
    ("000725", "BOE Technology", 18),
    ("002475", "Luxshare Precision", 30),
    ("600309", "Wanhua Chemical", 20),
    ("601899", "Zijin Mining", 25),
    ("002415", "Hikvision", 30),
    ("600585", "Conch Cement", 15),
    ("000001", "Ping An Bank", 15),
    ("601668", "CSSC", 25),
    ("603259", "WuXi AppTec", 20),
    ("601857", "PetroChina", 35),
    ("600028", "Sinopec", 25),
    ("002714", "Muyuan Foods", 15),
    ("300059", "East Money Info", 20),
    ("601988", "Bank of China", 35),
    ("600050", "China Unicom", 12),
    ("600104", "SAIC Motor", 15),
    ("601628", "China Life", 15),
    ("002352", "S.F. Holding", 15),
    ("601088", "China Shenhua", 30),
    ("600809", "Shanxi Fenjiu", 15),
    ("002304", "Yanghe Brewery", 10),
    ("601006", "Daqin Railway", 10),
    ("002049", "Unigroup Guoxin", 15),
    ("300124", "Inovance Tech", 18),
    ("002460", "Ganfeng Lithium", 12),
    ("600000", "SPD Bank", 10),
    ("601919", "COSCO Shipping", 15),
    ("600031", "Sany Heavy", 12),
    ("300122", "Zhifei Biological", 10),
    ("300760", "Mindray", 30),
    ("300015", "Aier Eye Hospital", 15),
]

# ── 🇭🇰 홍콩 (HKEX) ──────────────────────────────────────
# Yahoo suffix: .HK
TOP_STOCKS_HK = [
    ("0700", "Tencent", 380),
    ("9988", "Alibaba", 180),
    ("0941", "China Mobile", 120),
    ("1299", "AIA Group", 90),
    ("0005", "HSBC", 130),
    ("3690", "Meituan", 80),
    ("9618", "JD.com", 50),
    ("2318", "Ping An", 55),
    ("0939", "CCB", 45),
    ("1398", "ICBC", 40),
    ("0388", "HKEX", 40),
    ("9999", "NetEase", 45),
    ("3968", "CM Bank", 35),
    ("0883", "CNOOC", 35),
    ("0027", "Galaxy Ent", 15),
    ("1810", "Xiaomi", 60),
    ("0386", "Sinopec", 20),
    ("2020", "ANTA Sports", 25),
    ("0857", "PetroChina", 25),
    ("1211", "BYD Company", 70),
    ("0016", "SHK Properties", 15),
    ("0688", "China Overseas", 15),
    ("0002", "CLP Holdings", 15),
    ("0003", "HK & China Gas", 10),
    ("0011", "Hang Seng Bank", 15),
    ("0001", "CKH Holdings", 15),
    ("0066", "MTR Corp", 12),
    ("0006", "Power Assets", 8),
    ("2388", "BOC Hong Kong", 15),
    ("0960", "Longfor Group", 8),
    ("0267", "CITIC Ltd", 12),
    ("1113", "CK Asset", 10),
    ("1038", "CK Infra", 8),
    ("0012", "Henderson Land", 8),
    ("0017", "New World Dev", 5),
    ("0823", "Link REIT", 12),
    ("0669", "Techtronic", 15),
    ("2269", "WuXi Biologics", 20),
    ("1109", "China Resources", 8),
    ("0288", "WH Group", 8),
    ("6862", "Haidilao", 8),
    ("2328", "PICC P&C", 8),
    ("0241", "Ali Health", 10),
    ("1177", "Sino Biopharm", 8),
    ("0968", "Xinyi Solar", 8),
    ("1024", "Kuaishou", 15),
    ("0175", "Geely Auto", 12),
    ("0981", "SMIC", 15),
    ("2382", "Sunny Optical", 10),
    ("9961", "Trip.com", 20),
]

# ── 🇺🇸 미국 (NYSE/NASDAQ) ──────────────────────────────
# Yahoo suffix: 없음
# 형식: (심볼, 이름, 시가총액(B$), KIS 거래소코드)
TOP_STOCKS_US = [
    ("AAPL", "Apple", 280, "NASD"),
    ("MSFT", "Microsoft", 270, "NASD"),
    ("NVDA", "NVIDIA", 250, "NASD"),
    ("AMZN", "Amazon", 190, "NASD"),
    ("GOOGL", "Alphabet", 180, "NASD"),
    ("META", "Meta", 130, "NASD"),
    ("BRK-B", "Berkshire", 85, "NYSE"),
    ("TSLA", "Tesla", 70, "NASD"),
    ("LLY", "Eli Lilly", 60, "NYSE"),
    ("UNH", "UnitedHealth", 50, "NYSE"),
    ("AVGO", "Broadcom", 70, "NASD"),
    ("V", "Visa", 55, "NYSE"),
    ("JPM", "JP Morgan", 60, "NYSE"),
    ("MA", "Mastercard", 45, "NYSE"),
    ("XOM", "Exxon Mobil", 45, "NYSE"),
    ("JNJ", "J&J", 40, "NYSE"),
    ("COST", "Costco", 40, "NASD"),
    ("PG", "P&G", 40, "NYSE"),
    ("HD", "Home Depot", 38, "NYSE"),
    ("ABBV", "AbbVie", 35, "NYSE"),
    ("CRM", "Salesforce", 30, "NYSE"),
    ("ORCL", "Oracle", 35, "NYSE"),
    ("AMD", "AMD", 25, "NASD"),
    ("WMT", "Walmart", 45, "NYSE"),
    ("NFLX", "Netflix", 30, "NASD"),
    ("BAC", "BofA", 30, "NYSE"),
    ("MRK", "Merck", 28, "NYSE"),
    ("KO", "Coca-Cola", 26, "NYSE"),
    ("PEP", "PepsiCo", 24, "NASD"),
    ("ADBE", "Adobe", 22, "NASD"),
    ("TMO", "ThermoFisher", 20, "NYSE"),
    ("CSCO", "Cisco", 22, "NASD"),
    ("ACN", "Accenture", 20, "NYSE"),
    ("DIS", "Disney", 18, "NYSE"),
    ("INTC", "Intel", 10, "NASD"),
    ("QCOM", "Qualcomm", 18, "NASD"),
    ("NKE", "Nike", 12, "NYSE"),
    ("TXN", "Texas Instruments", 17, "NASD"),
    ("PM", "Philip Morris", 18, "NYSE"),
    ("IBM", "IBM", 17, "NYSE"),
    ("UBER", "Uber", 15, "NYSE"),
    ("GE", "GE Aerospace", 18, "NYSE"),
    ("T", "AT&T", 15, "NYSE"),
    ("CAT", "Caterpillar", 17, "NYSE"),
    ("BA", "Boeing", 12, "NYSE"),
    ("AMGN", "Amgen", 15, "NASD"),
    ("GS", "Goldman Sachs", 15, "NYSE"),
    ("RTX", "RTX Corp", 14, "NYSE"),
    ("PLTR", "Palantir", 15, "NASD"),
    ("COIN", "Coinbase", 8, "NASD"),
]

# 통합 리스트 (하위 호환성)
TOP_STOCKS = TOP_STOCKS_KR

# 국가별 시장 정보
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

# 각 국가 Yahoo Finance suffix 매핑
YAHOO_SUFFIX = {
    "KR": lambda code: ".KQ" if code in KOSDAQ_CODES else ".KS",
    "JP": lambda code: ".T",
    "CN": lambda code: ".SZ" if code in SZSE_CODES else ".SS",
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
