"""
Technical Analysis Utilities
Pandas 기반 경량 기술적 지표 계산 (TA-Lib/pandas-ta 의존성 제거)
"""
import pandas as pd
import numpy as np

def calculate_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """RSI (Relative Strength Index) 계산"""
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def calculate_macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    """MACD (Moving Average Convergence Divergence) 계산"""
    exp1 = series.ewm(span=fast, adjust=False).mean()
    exp2 = series.ewm(span=slow, adjust=False).mean()
    macd = exp1 - exp2
    signal_line = macd.ewm(span=signal, adjust=False).mean()
    histogram = macd - signal_line
    return macd, signal_line, histogram

def calculate_bollinger_bands(series: pd.Series, period: int = 20, std_dev: int = 2):
    """볼린저 밴드 계산"""
    ma = series.rolling(window=period).mean()
    std = series.rolling(window=period).std()
    upper = ma + (std * std_dev)
    lower = ma - (std * std_dev)
    return upper, ma, lower

def calculate_ma(series: pd.Series, windows: list = [5, 10, 20, 60, 120]):
    """이동평균선 계산"""
    result = {}
    for w in windows:
        result[f"MA{w}"] = series.rolling(window=w).mean()
    return result

def analyze_candles(candles_list: list) -> dict:
    """캔들 리스트(dict)를 받아 기술적 지표 요약 반환"""
    if not candles_list or len(candles_list) < 20:
        return {"summary": "데이터 부족으로 분석 불가"}

    df = pd.DataFrame(candles_list)
    if "close" not in df.columns:
        return {"summary": "Close 가격 데이터 없음"}

    close = df["close"]
    
    # 1. RSI
    rsi = calculate_rsi(close).iloc[-1]
    
    # 2. MACD
    macd, sig, hist = calculate_macd(close)
    macd_val = macd.iloc[-1]
    sig_val = sig.iloc[-1]
    hist_val = hist.iloc[-1]
    prev_hist = hist.iloc[-2] if len(hist) > 1 else 0
    
    # 3. Bollinger Bands
    upper, mid, lower = calculate_bollinger_bands(close)
    curr_price = close.iloc[-1]
    bb_upper = upper.iloc[-1]
    bb_lower = lower.iloc[-1]
    
    # 4. MA (이동평균)
    mas = calculate_ma(close, windows=[5, 20, 60])
    ma5 = mas["MA5"].iloc[-1]
    ma20 = mas["MA20"].iloc[-1]
    ma60 = mas["MA60"].iloc[-1]
    
    # 5. 해석 (Interpretation)
    signals = []
    
    # RSI 해석
    if rsi >= 70:
        signals.append(f"🔴 RSI 과매수 ({rsi:.1f})")
    elif rsi <= 30:
        signals.append(f"🟢 RSI 과매도 ({rsi:.1f})")
    else:
        signals.append(f"⚪ RSI 중립 ({rsi:.1f})")
        
    # MACD 해석
    if macd_val > sig_val:
        signals.append("🟢 MACD 매수우위")
    else:
        signals.append("🔴 MACD 매도우위")
        
    if prev_hist < 0 and hist_val > 0:
        signals.append("✨ MACD 골든크로스")
    elif prev_hist > 0 and hist_val < 0:
        signals.append("💀 MACD 데드크로스")
        
    # BB 해석
    if curr_price >= bb_upper:
        signals.append("🔴 볼린저 상단 돌파 (단기 고점 주의)")
    elif curr_price <= bb_lower:
        signals.append("🟢 볼린저 하단 터치 (반등 가능성)")
        
    # MA 해석
    if ma5 > ma20:
        signals.append("📈 단기 상승 추세 (MA5 > MA20)")
    else:
        signals.append("📉 단기 하락 추세 (MA5 < MA20)")
        
    if ma20 > ma60:
        signals.append("📈 중기 상승 추세 (정배열)")
    elif ma20 < ma60:
        signals.append("📉 중기 하락 추세 (역배열)")

    return {
        "rsi": rsi,
        "macd": macd_val,
        "macd_signal": sig_val,
        "bb_upper": bb_upper,
        "bb_lower": bb_lower,
        "ma5": ma5,
        "ma20": ma20,
        "ma60": ma60,
        "signals": signals,
        "summary": " | ".join(signals)
    }
