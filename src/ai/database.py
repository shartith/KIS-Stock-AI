"""
Database Manager - SQLite 기반 데이터 관리 (SQLAlchemy)
"""
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Text, Index, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime
import json as _json
import os

# DB 경로 설정
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(BASE_DIR, "data", "kis_stock.db")
DATABASE_URL = f"sqlite:///{DB_PATH}"

Base = declarative_base()

class CacheData(Base):
    """일반 캐시 데이터 (환율 등)"""
    __tablename__ = 'cache_data'

    key = Column(String(50), primary_key=True)
    value = Column(Text)  # JSON 직렬화된 값
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

class MarketData(Base):
    """시세 데이터 (OHLCV)"""
    __tablename__ = 'market_data'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), index=True) # 종목코드
    market = Column(String(10))             # KR, US
    timestamp = Column(DateTime, index=True) # 시간
    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)
    volume = Column(Integer)
    
    # 복합 인덱스 (조회 속도 향상)
    __table_args__ = (Index('idx_symbol_timestamp', 'symbol', 'timestamp'),)

class TradeHistory(Base):
    """매매 기록"""
    __tablename__ = 'trade_history'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    order_no = Column(String(50))
    symbol = Column(String(20), index=True)
    name = Column(String(100))
    market = Column(String(10), default="US")
    side = Column(String(10))               # buy / sell
    type = Column(String(10))               # legacy (BUY/SELL)
    price = Column(Float)
    quantity = Column(Integer)
    amount = Column(Float)
    fee = Column(Float)
    risk_level = Column(Integer)
    trade_type = Column(String(20))         # 스윙 / 단기 / 데이트레이딩
    net_profit = Column(Float)              # 매도 시 순이익
    net_profit_rate = Column(Float)         # 매도 시 수익률 %
    reason = Column(Text)
    strategy_id = Column(Integer, index=True) # 어떤 AI 전략에 의해 체결되었는지
    timestamp = Column(DateTime, default=datetime.now)

class AIAnalysis(Base):
    """AI 분석 로그"""
    __tablename__ = 'ai_analysis'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20))
    score = Column(Integer)
    action = Column(String(10))
    confidence = Column(Integer)
    summary = Column(Text)
    timestamp = Column(DateTime, default=datetime.now)


class TrainingDataset(Base):
    """로컬 LLM 학습용 데이터셋
    매매 종료 시점(익절/손절)에 생성되며, 진입 시점의 상황(Input)과 결과(Label)를 쌍으로 저장
    """
    __tablename__ = 'training_dataset'

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), index=True)
    market = Column(String(10))
    trade_type = Column(String(20))          # 스윙/단타
    entry_time = Column(DateTime)            # 진입 시간
    exit_time = Column(DateTime)             # 청산 시간
    
    # --- Input Features (진입 시점) ---
    chart_data = Column(Text, default="{}")  # 캔들 데이터 (JSON)
    indicators = Column(Text, default="{}")  # 기술적 지표 (JSON)
    ai_reasoning = Column(Text)              # 당시 AI의 매수 근거
    
    # --- Labels (결과) ---
    result_type = Column(String(10))         # WIN / LOSS
    profit_rate = Column(Float)              # 수익률 %
    hold_duration = Column(Integer)          # 보유 시간 (분)
    is_trained = Column(Integer, default=0)  # 0=미학습, 1=학습완료
    
    created_at = Column(DateTime, default=datetime.now)

class Watchlist(Base):
    """관심 종목 (기존 stocks.json 대체)"""
    __tablename__ = 'watchlists'

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), index=True)
    name = Column(String(100))
    market = Column(String(10), index=True) # KR, US, ...
    exchange = Column(String(10))           # NASD, NYSE, ...
    mcap = Column(Float, default=0)         # 시가총액 (참고용)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.now)

class AppSettings(Base):
    """애플리케이션 설정 (key-value)
    
    Google AI, Discord, KIS API 등 런타임 설정을 DB에 저장/관리
    .env 파일보다 DB 값이 우선 적용됨
    """
    __tablename__ = 'app_settings'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String(100), unique=True, nullable=False, index=True)
    value = Column(Text, default="")
    description = Column(String(255), default="")
    category = Column(String(50), default="general")   # api, notification, ai, general
    is_secret = Column(Integer, default=0)              # 1=비밀값 (마스킹 표시)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class BacktestRun(Base):
    """백테스트 실행 기록"""
    __tablename__ = 'backtest_runs'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False)
    name = Column(String(50), default="")
    strategy = Column(String(30), nullable=False)
    config_json = Column(Text, default="{}")     # BacktestConfig 직렬화
    result_json = Column(Text, default="{}")     # 거래내역 + equity_curve
    # 주요 성과 지표 (빠른 조회용)
    total_return = Column(Float, default=0.0)
    win_rate = Column(Float, default=0.0)
    mdd = Column(Float, default=0.0)
    sharpe_ratio = Column(Float, default=0.0)
    total_trades = Column(Integer, default=0)
    period_start = Column(String(10), default="")
    period_end = Column(String(10), default="")
    created_at = Column(DateTime, default=datetime.now)


class Strategy(Base):
    """AI 전략"""
    __tablename__ = 'strategies'

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200))
    type = Column(String(30))               # momentum / reversal / ...
    market = Column(String(10))             # US / KR / ALL
    source = Column(String(50))             # offmarket / manual / ai
    conditions = Column(Text, default="{}") # JSON 조건
    active = Column(Boolean, default=True)
    success_count = Column(Integer, default=0)
    fail_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class CandlePattern(Base):
    """학습된 캔들 매매 패턴"""
    __tablename__ = 'candle_patterns'

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), index=True)
    name = Column(String(100))
    market = Column(String(10))
    pattern_type = Column(String(10))       # buy / sell
    result = Column(String(10))             # pending / success / fail
    pnl_pct = Column(Float)                 # 수익률 %
    pattern_label = Column(String(100))     # RSI과매도+골든크로스 등
    candle_snapshot = Column(Text, default="{}")  # JSON 캔들 스냅샷
    indicators = Column(Text, default="{}")       # JSON 지표
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class ScanResult(Base):
    """AI 스캔 결과 (사이클별 영속화)"""
    __tablename__ = 'scan_results'

    id = Column(Integer, primary_key=True, autoincrement=True)
    cycle_id = Column(Integer, index=True)           # 스캔 사이클 번호
    symbol = Column(String(20), index=True)
    name = Column(String(100))
    market = Column(String(10), index=True)
    price = Column(Float)
    price_krw = Column(Integer)
    ai_action = Column(String(10), index=True)       # BUY / HOLD / SELL / ERROR
    ai_score = Column(Integer, default=0)
    ai_confidence = Column(Integer, default=0)
    ai_reason = Column(Text, default="")
    target_price = Column(Float, default=0)
    stop_loss = Column(Float, default=0)
    is_candidate = Column(Integer, default=0)         # 1=매수 후보
    tracking_status = Column(String(20), default="")  # watching/ordering/filled
    data_json = Column(Text, default="{}")            # 전체 데이터 JSON
    scanned_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        Index('idx_scan_cycle_action', 'cycle_id', 'ai_action'),
        Index('idx_scan_symbol_date', 'symbol', 'scanned_at'),
    )


# 설정 기본값 정의
DEFAULT_SETTINGS = {
    # KIS API
    "KIS_APP_KEY": {"category": "api", "description": "한국투자증권 App Key", "is_secret": 1},
    "KIS_SECRET_KEY": {"category": "api", "description": "한국투자증권 Secret Key", "is_secret": 1},
    "KIS_ACCT_STOCK": {"category": "api", "description": "한국투자증권 계좌번호", "is_secret": 1},
    # Antigravity (Google AI)
    "ANTIGRAVITY_API_KEY": {"category": "ai", "description": "Google AI API Key", "is_secret": 1},
    "ANTIGRAVITY_MODEL": {"category": "ai", "description": "Antigravity 모델명"},
    "GOOGLE_OAUTH_CLIENT_ID": {"category": "ai", "description": "Google OAuth Client ID", "is_secret": 0},
    "GOOGLE_OAUTH_CLIENT_SECRET": {"category": "ai", "description": "Google OAuth Client Secret", "is_secret": 1},
    # Discord
    "DISCORD_WEBHOOK_URL": {"category": "notification", "description": "Discord Webhook URL", "is_secret": 0},
    "NOTI_TRADE_ALERTS": {"category": "notification", "description": "매매 알림 활성화", "is_secret": 0},
    "NOTI_HOURLY_REPORT": {"category": "notification", "description": "시간별 리포트 활성화", "is_secret": 0},
    # AI 설정
    "AI_MODE": {"category": "ai", "description": "AI 모드 (local/antigravity)"},
    "LOCAL_LLM_URL": {"category": "ai", "description": "로컬 LLM 서버 URL"},
    "LOCAL_LLM_MODEL": {"category": "ai", "description": "로컬 LLM 모델명"},
    # 거래 설정
    "ALLOW_LEVERAGE": {"category": "trading", "description": "레버리지/인버스 거래 허용"},
    "ENABLE_AUTO_SCAN": {"category": "trading", "description": "자동 종목 스캔"},
    "ENABLE_AUTO_BUY": {"category": "trading", "description": "자동 매수 실행"},
    "ENABLE_AUTO_SELL": {"category": "trading", "description": "자동 매도 실행"},
    "ENABLE_OFFMARKET": {"category": "trading", "description": "장외 분석 활동"},
    "ENABLE_NEWS_COLLECT": {"category": "trading", "description": "뉴스/공시 수집"},
}


class DatabaseManager:
    def __init__(self):
        self.engine = create_engine(DATABASE_URL, echo=False)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self._migrate()

    def _migrate(self):
        """기존 테이블에 새 컬럼 추가 (ALTER TABLE)"""
        import sqlite3
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # trade_history 확장 컬럼
        migrations = [
            ("trade_history", "market", "TEXT DEFAULT 'US'"),
            ("trade_history", "side", "TEXT"),
            ("trade_history", "risk_level", "INTEGER"),
            ("trade_history", "trade_type", "TEXT"),
            ("trade_history", "net_profit_rate", "REAL"),
            ("trade_history", "strategy_id", "INTEGER"),
        ]

        for table, column, col_type in migrations:
            try:
                cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
            except sqlite3.OperationalError:
                pass  # 이미 존재

        conn.commit()
        conn.close()
        
    def get_session(self):
        return self.Session()
    
    def save_market_data(self, data_list: list):
        """시세 데이터 일괄 저장"""
        session = self.get_session()
        try:
            objects = [MarketData(**data) for data in data_list]
            session.add_all(objects)
            session.commit()
        except Exception as e:
            session.rollback()
            print(f"DB Error: {e}")
        finally:
            session.close()

    def get_candles(self, symbol: str, limit: int = 100) -> list:
        """캔들 데이터 조회 (최신순)"""
        session = self.get_session()
        try:
            results = session.query(MarketData).filter_by(symbol=symbol)\
                .order_by(MarketData.timestamp.desc()).limit(limit).all()
            return [
                {
                    "time": r.timestamp.isoformat(),
                    "open": r.open,
                    "high": r.high,
                    "low": r.low,
                    "close": r.close,
                    "volume": r.volume
                }
                for r in reversed(results)
            ]
        finally:
            session.close()
    
    # ==========================
    # 설정 관리 (AppSettings)
    # ==========================
    
    def get_setting(self, key: str, default: str = "") -> str:
        """설정값 조회 (DB 우선, .env fallback)"""
        session = self.get_session()
        try:
            setting = session.query(AppSettings).filter_by(key=key).first()
            if setting and setting.value:
                return setting.value
            # DB에 없으면 .env에서 조회
            return os.getenv(key, default)
        finally:
            session.close()
    
    def set_setting(self, key: str, value: str, category: str = None, description: str = None):
        """설정값 저장/업데이트 및 .env 동기화"""
        session = self.get_session()
        try:
            setting = session.query(AppSettings).filter_by(key=key).first()
            if setting:
                setting.value = value
                setting.updated_at = datetime.now()
                if category:
                    setting.category = category
                if description:
                    setting.description = description
            else:
                defaults = DEFAULT_SETTINGS.get(key, {})
                setting = AppSettings(
                    key=key,
                    value=value,
                    category=category or defaults.get("category", "general"),
                    description=description or defaults.get("description", ""),
                    is_secret=defaults.get("is_secret", 0)
                )
                session.add(setting)
            session.commit()
            
            # .env 파일 업데이트 (동기화)
            self._update_env_file(key, value)
            
        except Exception as e:
            session.rollback()
            print(f"Settings DB Error: {e}")
        finally:
            session.close()

    def _update_env_file(self, key: str, value: str):
        """단일 키값으로 .env 파일 갱신"""
        env_path = os.path.join(BASE_DIR, ".env")
        try:
            lines = []
            key_found = False
            if os.path.exists(env_path):
                with open(env_path, "r", encoding="utf-8") as f:
                    for line in f:
                        if line.strip().startswith(f"{key}="):
                            lines.append(f'{key}="{value}"\n')
                            key_found = True
                        else:
                            lines.append(line)
            
            if not key_found:
                lines.append(f'{key}="{value}"\n')
                
            with open(env_path, "w", encoding="utf-8") as f:
                f.writelines(lines)
        except Exception as e:
            print(f"⚠️ Failed to update .env for {key}: {e}")
    
    def get_all_settings(self, category: str = None) -> list:
        """전체 설정 조회 (카테고리별 필터 가능)"""
        session = self.get_session()
        try:
            query = session.query(AppSettings)
            if category:
                query = query.filter_by(category=category)
            results = query.order_by(AppSettings.category, AppSettings.key).all()
            
            settings = []
            for r in results:
                settings.append({
                    "key": r.key,
                    "value": r.value if not r.is_secret else self._mask_value(r.value),
                    "raw_value": r.value,  # 내부 사용용
                    "description": r.description,
                    "category": r.category,
                    "is_secret": bool(r.is_secret),
                    "updated_at": r.updated_at.isoformat() if r.updated_at else None
                })
            return settings
        finally:
            session.close()
    
    def get_settings_for_display(self) -> dict:
        """웹 UI 표시용 설정 (비밀값 마스킹)"""
        all_settings = {}
        
        # DB에 저장된 값 로드
        session = self.get_session()
        try:
            results = session.query(AppSettings).all()
            for r in results:
                all_settings[r.key] = {
                    "value": r.value if not r.is_secret else self._mask_value(r.value),
                    "has_value": bool(r.value),
                    "category": r.category,
                    "description": r.description,
                    "is_secret": bool(r.is_secret)
                }
        finally:
            session.close()
        
        # DEFAULT_SETTINGS에 있지만 DB에 없는 항목은 .env에서 체크
        for key, meta in DEFAULT_SETTINGS.items():
            if key not in all_settings:
                env_val = os.getenv(key, "")
                all_settings[key] = {
                    "value": self._mask_value(env_val) if meta.get("is_secret") else env_val,
                    "has_value": bool(env_val),
                    "category": meta.get("category", "general"),
                    "description": meta.get("description", ""),
                    "is_secret": bool(meta.get("is_secret", 0)),
                    "source": "env" if env_val else "none"
                }
        
        return all_settings
    
    def save_settings_bulk(self, settings_dict: dict):
        """여러 설정을 한번에 저장"""
        for key, value in settings_dict.items():
            if value is not None and value != "":
                self.set_setting(key, value)
    
    def _mask_value(self, value: str) -> str:
        """비밀값 마스킹 (앞 4자만 표시)"""
        if not value:
            return ""
        if len(value) <= 4:
            return "****"
        return value[:4] + "*" * (len(value) - 4)
    
    def init_default_settings(self):
        """기본 설정 초기화 & 양방향 동기화 (.env <-> DB) & 자격증명 파일 마이그레이션"""
        session = self.get_session()
        env_updated = False
        
        # .env 파일 경로
        env_path = os.path.join(BASE_DIR, ".env")
        current_env = {}
        
        # 1. 현재 .env 로드
        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    if "=" in line and not line.strip().startswith("#"):
                        key, val = line.strip().split("=", 1)
                        current_env[key] = val.strip('"').strip("'")
        
        try:
            # 2. 양방향 동기화
            for key, meta in DEFAULT_SETTINGS.items():
                db_setting = session.query(AppSettings).filter_by(key=key).first()
                env_value = os.getenv(key, "")
                
                # Case A: DB에는 없고 .env에는 있음 -> DB에 저장
                if not db_setting and env_value:
                    print(f"📥 Syncing {key} from .env to DB")
                    self.set_setting(key, env_value, meta.get("category"), meta.get("description"))
                
                # Case B: DB에는 있고 .env에는 없거나 다름 -> .env 업데이트 예약
                elif db_setting and db_setting.value and db_setting.value != current_env.get(key):
                    print(f"📤 Syncing {key} from DB to .env")
                    current_env[key] = db_setting.value
                    env_updated = True
                
                # Case C: 둘 다 없음 -> 기본값으로 DB 생성 (빈 값)
                elif not db_setting:
                    setting = AppSettings(
                        key=key,
                        value="",
                        category=meta.get("category", "general"),
                        description=meta.get("description", ""),
                        is_secret=meta.get("is_secret", 0)
                    )
                    session.add(setting)

            # 3. .env 파일 업데이트 (변경된 경우만)
            if env_updated:
                try:
                    with open(env_path, "w", encoding="utf-8") as f:
                        for key, val in current_env.items():
                            f.write(f'{key}="{val}"\n')
                    print("💾 Updated .env file from DB")
                except Exception as e:
                    print(f"⚠️ Failed to update .env: {e}")

            # 4. kis_credentials.txt 마이그레이션 (기존 유지)
            cred_path = os.path.join(BASE_DIR, "kis_credentials.txt")
            if os.path.exists(cred_path):
                print("📦 Migrating kis_credentials.txt to DB...")
                try:
                    with open(cred_path, "r", encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if line.startswith("App Key:"):
                                val = line.split(":", 1)[1].strip()
                                self.set_setting("KIS_APP_KEY", val)
                            elif line.startswith("Secret Key:"):
                                val = line.split(":", 1)[1].strip()
                                self.set_setting("KIS_SECRET_KEY", val)
                    
                    os.remove(cred_path)
                    print("✅ Credentials migrated and file deleted.")
                    # 마이그레이션 후 .env 동기화 재실행 필요할 수 있으나 다음 실행 시 처리됨
                except Exception as e:
                    print(f"⚠️ Failed to migrate credentials: {e}")

            session.commit()
            print("✅ 설정 동기화 및 초기화 완료")
            
        except Exception as e:
            session.rollback()
            print(f"Settings init error: {e}")
        finally:
            session.close()
    
    # ==========================
    # 백테스트 기록 관리
    # ==========================
    
    def save_backtest(self, config, result) -> int:
        """백테스트 결과 저장"""
        import json as _json
        session = self.get_session()
        try:
            config_dict = config if isinstance(config, dict) else {
                "symbol": getattr(config, 'symbol', ''),
                "name": getattr(config, 'name', ''),
                "start_date": getattr(config, 'start_date', ''),
                "end_date": getattr(config, 'end_date', ''),
                "initial_capital": getattr(config, 'initial_capital', 0),
                "strategy": getattr(config, 'strategy', ''),
                "confidence_threshold": getattr(config, 'confidence_threshold', 80),
                "stop_loss_pct": getattr(config, 'stop_loss_pct', 0.05),
                "take_profit_pct": getattr(config, 'take_profit_pct', 0.10),
            }
            
            result_dict = result if isinstance(result, dict) else {
                "trades": getattr(result, 'trades', []),
                "equity_curve": getattr(result, 'equity_curve', []),
                "metrics": getattr(result, 'metrics', {}),
            }
            metrics = result_dict.get("metrics", {})
            
            run = BacktestRun(
                symbol=config_dict.get("symbol", ""),
                name=config_dict.get("name", ""),
                strategy=config_dict.get("strategy", ""),
                config_json=_json.dumps(config_dict, ensure_ascii=False),
                result_json=_json.dumps(result_dict, ensure_ascii=False),
                total_return=metrics.get("total_return", 0),
                win_rate=metrics.get("win_rate", 0),
                mdd=metrics.get("mdd", 0),
                sharpe_ratio=metrics.get("sharpe_ratio", 0),
                total_trades=metrics.get("total_trades", 0),
                period_start=config_dict.get("start_date", ""),
                period_end=config_dict.get("end_date", ""),
            )
            session.add(run)
            session.commit()
            return run.id
        except Exception as e:
            session.rollback()
            print(f"Backtest save error: {e}")
            return -1
        finally:
            session.close()
    
    def get_backtest_history(self, limit: int = 20, strategy: str = None, symbol: str = None) -> list:
        """백테스트 실행 이력 조회"""
        session = self.get_session()
        try:
            query = session.query(BacktestRun)
            if strategy:
                query = query.filter_by(strategy=strategy)
            if symbol:
                query = query.filter_by(symbol=symbol)
            results = query.order_by(BacktestRun.created_at.desc()).limit(limit).all()
            return [
                {
                    "id": r.id,
                    "symbol": r.symbol,
                    "name": r.name,
                    "strategy": r.strategy,
                    "total_return": r.total_return,
                    "win_rate": r.win_rate,
                    "mdd": r.mdd,
                    "sharpe_ratio": r.sharpe_ratio,
                    "total_trades": r.total_trades,
                    "period": f"{r.period_start} ~ {r.period_end}",
                    "created_at": r.created_at.isoformat() if r.created_at else None
                }
                for r in results
            ]
        finally:
            session.close()
    
    def get_backtest_detail(self, backtest_id: int) -> dict:
        """백테스트 상세 결과 조회"""
        import json as _json
        session = self.get_session()
        try:
            run = session.query(BacktestRun).filter_by(id=backtest_id).first()
            if not run:
                return {}
            return {
                "id": run.id,
                "symbol": run.symbol,
                "name": run.name,
                "strategy": run.strategy,
                "config": _json.loads(run.config_json) if run.config_json else {},
                "result": _json.loads(run.result_json) if run.result_json else {},
                "total_return": run.total_return,
                "win_rate": run.win_rate,
                "mdd": run.mdd,
                "sharpe_ratio": run.sharpe_ratio,
                "total_trades": run.total_trades,
                "period": f"{run.period_start} ~ {run.period_end}",
                "created_at": run.created_at.isoformat() if run.created_at else None
            }
        finally:
            session.close()

    # ==========================
    # 거래 기록 (TradeHistory)
    # ==========================

    def save_trade(self, trade: dict) -> int:
        """거래 기록 DB 저장"""
        session = self.get_session()
        try:
            record = TradeHistory(
                order_no=trade.get("order_no", ""),
                symbol=trade.get("symbol", ""),
                name=trade.get("name", ""),
                market=trade.get("market", "US"),
                side=trade.get("side", ""),
                type=trade.get("side", "").upper(),
                price=trade.get("price", 0),
                quantity=trade.get("qty", trade.get("quantity", 0)),
                amount=trade.get("price", 0) * trade.get("qty", trade.get("quantity", 0)),
                fee=trade.get("total_fees", 0),
                risk_level=trade.get("risk_level"),
                trade_type=trade.get("trade_type", ""),
                net_profit=trade.get("net_profit"),
                net_profit_rate=trade.get("net_profit_rate"),
                reason=trade.get("reason", ""),
                strategy_id=trade.get("strategy_id"),
            )
            session.add(record)
            session.commit()
            return record.id
        except Exception as e:
            session.rollback()
            print(f"Trade save error: {e}")
            return -1
        finally:
            session.close()

    def get_trades(self, limit: int = 100, side: str = None, symbol: str = None) -> list:
        """거래 기록 조회"""
        session = self.get_session()
        try:
            query = session.query(TradeHistory)
            if side:
                query = query.filter_by(side=side)
            if symbol:
                query = query.filter_by(symbol=symbol)
            results = query.order_by(TradeHistory.timestamp.desc()).limit(limit).all()
            return [
                {
                    "id": r.id,
                    "order_no": r.order_no,
                    "symbol": r.symbol,
                    "name": r.name,
                    "market": r.market or "US",
                    "side": r.side or (r.type.lower() if r.type else ""),
                    "price": r.price,
                    "qty": r.quantity,
                    "amount": r.amount,
                    "fee": r.fee,
                    "risk_level": r.risk_level,
                    "trade_type": r.trade_type,
                    "net_profit": r.net_profit,
                    "net_profit_rate": r.net_profit_rate,
                    "time": r.timestamp.strftime("%Y-%m-%d %H:%M:%S") if r.timestamp else "",
                }
                for r in results
            ]
        finally:
            session.close()

    # ==========================
    # 스캔 결과 영속화
    # ==========================

    def save_scan_results(self, cycle_id: int, results: list, candidates: list):
        """스캔 결과 + 후보를 DB에 일괄 저장 (사이클 단위)"""
        session = self.get_session()
        candidate_symbols = {c.get("symbol") for c in candidates}
        try:
            records = []
            for r in results:
                records.append(ScanResult(
                    cycle_id=cycle_id,
                    symbol=r.get("symbol", ""),
                    name=r.get("name", ""),
                    market=r.get("market", ""),
                    price=r.get("price", 0),
                    price_krw=r.get("price_krw", 0),
                    ai_action=r.get("ai_action", ""),
                    ai_score=r.get("ai_score", 0),
                    ai_confidence=r.get("ai_confidence", 0),
                    ai_reason=r.get("ai_reason", "")[:500],
                    target_price=r.get("target_price", 0),
                    stop_loss=r.get("stop_loss", 0),
                    is_candidate=1 if r.get("symbol") in candidate_symbols else 0,
                    tracking_status=r.get("tracking_status", ""),
                    data_json=_json.dumps(r, ensure_ascii=False, default=str),
                ))
            session.add_all(records)
            session.commit()
            return len(records)
        except Exception as e:
            session.rollback()
            print(f"ScanResult save error: {e}")
            return 0
        finally:
            session.close()

    def load_latest_scan_results(self, limit: int = 200) -> tuple:
        """최근 스캔 결과 + 후보 로드 (서버 재시작 시 복원용)

        Returns:
            (scan_results: list[dict], candidates: list[dict], cycle_id: int)
        """
        session = self.get_session()
        try:
            # 가장 최근 cycle_id 조회
            latest = session.query(ScanResult.cycle_id)\
                .order_by(ScanResult.scanned_at.desc()).first()
            if not latest:
                return [], [], 0

            latest_cycle = latest[0]

            # 해당 사이클의 전체 결과 로드
            rows = session.query(ScanResult)\
                .filter(ScanResult.cycle_id == latest_cycle)\
                .order_by(ScanResult.ai_score.desc())\
                .limit(limit).all()

            scan_results = []
            candidates = []
            for r in rows:
                try:
                    data = _json.loads(r.data_json)
                except Exception:
                    data = {
                        "symbol": r.symbol, "name": r.name,
                        "market": r.market, "price": r.price,
                        "ai_action": r.ai_action, "ai_score": r.ai_score,
                    }
                scan_results.append(data)
                if r.is_candidate:
                    candidates.append(data)

            return scan_results, candidates, latest_cycle
        except Exception as e:
            print(f"ScanResult load error: {e}")
            return [], [], 0
        finally:
            session.close()

    def update_candidate_status(self, symbol: str, tracking_status: str,
                                 order_id: str = "", order_price: float = 0):
        """후보 종목의 추적 상태 업데이트"""
        session = self.get_session()
        try:
            row = session.query(ScanResult)\
                .filter(ScanResult.symbol == symbol, ScanResult.is_candidate == 1)\
                .order_by(ScanResult.scanned_at.desc()).first()
            if row:
                row.tracking_status = tracking_status
                # data_json 내 상태도 업데이트
                try:
                    data = _json.loads(row.data_json)
                    data["tracking_status"] = tracking_status
                    if order_id:
                        data["order_id"] = order_id
                    if order_price:
                        data["order_price"] = order_price
                    row.data_json = _json.dumps(data, ensure_ascii=False, default=str)
                except Exception:
                    pass
                session.commit()
        except Exception as e:
            session.rollback()
            print(f"Candidate status update error: {e}")
        finally:
            session.close()

    def cleanup_old_scans(self, keep_cycles: int = 10):
        """오래된 스캔 결과 정리 (최근 N개 사이클만 유지)"""
        session = self.get_session()
        try:
            from sqlalchemy import distinct
            cycles = session.query(distinct(ScanResult.cycle_id))\
                .order_by(ScanResult.cycle_id.desc()).all()
            if len(cycles) > keep_cycles:
                cutoff = cycles[keep_cycles - 1][0]
                session.query(ScanResult)\
                    .filter(ScanResult.cycle_id < cutoff).delete()
                session.commit()
        except Exception as e:
            session.rollback()
            print(f"Scan cleanup error: {e}")
        finally:
            session.close()

    # ==========================
    # 전략 관리 (Strategy)
    # ==========================

    def save_strategy(self, data: dict) -> int:
        """AI 전략 저장"""
        session = self.get_session()
        try:
            strat = Strategy(
                name=data.get("name", "Unnamed Strategy"),
                type=data.get("type", "momentum"),
                market=data.get("market", "ALL"),
                source=data.get("source", "ai"),
                conditions=_json.dumps(data.get("conditions", {}), ensure_ascii=False),
                active=data.get("active", True)
            )
            session.add(strat)
            session.commit()
            return strat.id
        except Exception as e:
            session.rollback()
            print(f"Strategy save error: {e}")
            return -1
        finally:
            session.close()

    def get_strategies(self, active_only: bool = False) -> list:
        """전략 목록 조회"""
        session = self.get_session()
        try:
            query = session.query(Strategy)
            if active_only:
                query = query.filter_by(active=True)
            results = query.order_by(Strategy.created_at.desc()).all()
            return [
                {
                    "id": r.id,
                    "name": r.name,
                    "type": r.type,
                    "market": r.market,
                    "source": r.source,
                    "conditions": _json.loads(r.conditions) if r.conditions else {},
                    "active": r.active,
                    "success_count": r.success_count or 0,
                    "fail_count": r.fail_count or 0,
                    "created_at": r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else "",
                }
                for r in results
            ]
        finally:
            session.close()

    def toggle_strategy(self, strategy_id: int, active: bool):
        """전략 활성/비활성 토글"""
        session = self.get_session()
        try:
            strat = session.query(Strategy).filter_by(id=strategy_id).first()
            if strat:
                strat.active = active
                session.commit()
        except Exception as e:
            session.rollback()
            print(f"Strategy toggle error: {e}")
        finally:
            session.close()

    def update_strategy_stats(self, strategy_id: int, is_success: bool):
        """전략 성과 업데이트 (학습용)"""
        session = self.get_session()
        try:
            strat = session.query(Strategy).filter_by(id=strategy_id).first()
            if strat:
                if is_success:
                    strat.success_count += 1
                else:
                    strat.fail_count += 1
                session.commit()
        except Exception as e:
            session.rollback()
            print(f"Strategy stats update error: {e}")
        finally:
            session.close()

    def delete_strategy(self, strategy_id: int):
        """전략 삭제"""
        session = self.get_session()
        try:
            strat = session.query(Strategy).filter_by(id=strategy_id).first()
            if strat:
                session.delete(strat)
                session.commit()
        except Exception as e:
            session.rollback()
            print(f"Strategy delete error: {e}")
        finally:
            session.close()

    # ==========================
    # 캔들 패턴 (CandlePattern)
    # ==========================

    def save_candle_pattern(self, data: dict) -> int:
        """캔들 매매 패턴 저장"""
        session = self.get_session()
        try:
            pattern = CandlePattern(
                symbol=data.get("symbol", ""),
                name=data.get("name", ""),
                market=data.get("market", "US"),
                pattern_type=data.get("type", "buy"),
                result=data.get("result", "pending"),
                pattern_label=data.get("pattern_label", ""),
                candle_snapshot=_json.dumps(data.get("candle_snapshot", {}), ensure_ascii=False),
                indicators=_json.dumps(data.get("indicators", {}), ensure_ascii=False),
            )
            session.add(pattern)
            session.commit()
            return pattern.id
        except Exception as e:
            session.rollback()
            print(f"Pattern save error: {e}")
            return -1
        finally:
            session.close()

    def get_candle_patterns(self, limit: int = 50, market: str = None,
                            result: str = None, symbol: str = None) -> list:
        """캔들 패턴 조회"""
        session = self.get_session()
        try:
            query = session.query(CandlePattern)
            if market:
                query = query.filter_by(market=market)
            if result:
                query = query.filter_by(result=result)
            if symbol:
                query = query.filter_by(symbol=symbol)
            results = query.order_by(CandlePattern.created_at.desc()).limit(limit).all()
            return [
                {
                    "id": r.id,
                    "symbol": r.symbol,
                    "name": r.name,
                    "market": r.market,
                    "type": r.pattern_type,
                    "result": r.result,
                    "pnl_pct": r.pnl_pct,
                    "pattern_label": r.pattern_label,
                    "candle_snapshot": _json.loads(r.candle_snapshot) if r.candle_snapshot else {},
                    "indicators": _json.loads(r.indicators) if r.indicators else {},
                    "created_at": r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else "",
                }
                for r in results
            ]
        finally:
            session.close()

    def update_pattern_result(self, symbol: str, pnl_pct: float):
        """가장 최근 pending 패턴 결과 업데이트"""
        session = self.get_session()
        try:
            pattern = session.query(CandlePattern).filter_by(
                symbol=symbol, result="pending"
            ).order_by(CandlePattern.created_at.desc()).first()
            if pattern:
                pattern.result = "success" if pnl_pct > 0 else "fail"
                pattern.pnl_pct = round(pnl_pct, 2)
                pattern.updated_at = datetime.now()
                session.commit()
        except Exception as e:
            session.rollback()
            print(f"Pattern update error: {e}")
        finally:
            session.close()

    # ==========================
    # 관심 종목 관리 (Watchlist)
    # ==========================
    
    def init_default_watchlist(self):
        """stocks.json 파일이 있다면 DB로 마이그레이션 후 파일 삭제"""
        json_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stocks.json")
        if not os.path.exists(json_path):
            return

        print("📦 Migrating stocks.json to DB...")
        session = self.get_session()
        try:
            # 기존 데이터 확인 (이미 있으면 스킵할지, 덮어쓸지 결정. 여기선 비어있을 때만)
            if session.query(Watchlist).count() > 0:
                print("⚠️ Watchlist table not empty. Skipping migration.")
                return

            with open(json_path, "r", encoding="utf-8") as f:
                data = _json.load(f)
            
            count = 0
            for market, stocks in data.items():
                for s in stocks:
                    w = Watchlist(
                        symbol=s.get("code", ""),
                        name=s.get("name", ""),
                        market=market,
                        exchange=s.get("exchange", ""),
                        mcap=s.get("mcap", 0),
                        is_active=True
                    )
                    session.add(w)
                    count += 1
            session.commit()
            print(f"✅ Migrated {count} stocks to DB.")
            
            # 파일 삭제는 안전을 위해 수동으로 하거나, 여기서 수행
            # os.remove(json_path) 
            
        except Exception as e:
            session.rollback()
            print(f"Migration error: {e}")
        finally:
            session.close()

    def get_watchlist(self, market: str = None, active_only: bool = True) -> list:
        """관심 종목 조회"""
        session = self.get_session()
        try:
            query = session.query(Watchlist)
            if market:
                query = query.filter_by(market=market)
            if active_only:
                query = query.filter_by(is_active=True)
            results = query.all()
            
            # ScannerEngine에서 사용하는 포맷 (tuple)으로 변환하지 않고 dict 리스트 반환
            # (ScannerEngine 쪽에서 처리)
            return [
                {
                    "symbol": r.symbol,
                    "name": r.name,
                    "market": r.market,
                    "exchange": r.exchange,
                    "mcap": r.mcap
                }
                for r in results
            ]
        finally:
            session.close()
            
    def add_watchlist_item(self, item: dict):
        """관심 종목 추가"""
        session = self.get_session()
        try:
            # 중복 체크
            existing = session.query(Watchlist).filter_by(
                symbol=item.get("symbol"), market=item.get("market")
            ).first()
            if existing:
                return # 이미 존재
                
            w = Watchlist(
                symbol=item.get("symbol"),
                name=item.get("name"),
                market=item.get("market"),
                exchange=item.get("exchange"),
                mcap=item.get("mcap", 0),
                is_active=True
            )
            session.add(w)
            session.commit()
        except Exception:
            session.rollback()
        finally:
            session.close()

    # ==========================
    # 학습 데이터 (TrainingDataset)
    # ==========================

    def save_training_data(self, data: dict):
        """학습 데이터 저장 (기존 메서드)"""
        session = self.get_session()
        try:
            record = TrainingDataset(
                symbol=data.get("symbol", ""),
                market=data.get("market", ""),
                trade_type=data.get("trade_type", ""),
                entry_time=data.get("entry_time"),
                exit_time=datetime.now(),
                chart_data=_json.dumps(data.get("chart_data", {}), ensure_ascii=False),
                indicators=_json.dumps(data.get("indicators", {}), ensure_ascii=False),
                ai_reasoning=data.get("ai_reasoning", ""),
                result_type=data.get("result_type", "HOLD"),
                profit_rate=data.get("profit_rate", 0),
                hold_duration=data.get("hold_duration", 0),
                is_trained=0  # 기본값 미학습
            )
            session.add(record)
            session.commit()
            return record.id
        except Exception as e:
            session.rollback()
            print(f"Training data save error: {e}")
            return -1
        finally:
            session.close()

    def add_training_data(self, trade_log: dict, input_data: str, ai_output: str, score: int):
        """
        놓친 급등주(False Negative) 등을 학습 데이터로 추가 (ScannerEngine 연동용)
        Args:
            trade_log: {code, name, profit_rate, trade_type, reason, ...}
            input_data: JSON string (분석 당시의 전체 데이터)
            ai_output: "BUY" (정답 라벨)
            score: 당시 점수 (참고용)
        """
        import json
        session = self.get_session()
        try:
            # input_data 파싱하여 필요한 정보 추출
            try:
                raw_data = json.loads(input_data)
            except:
                raw_data = {}

            market = raw_data.get("market", "KR")
            
            # 차트 데이터와 지표 분리 (가능하다면)
            # 현재 ScannerEngine은 전체 result를 json으로 넘기므로, 이를 chart_data 컬럼에 통째로 저장하거나
            # 구조에 맞게 분리해야 함. 여기서는 통째로 chart_data에 저장하고 indicators는 빈값 처리.
            
            record = TrainingDataset(
                symbol=trade_log.get("code", ""),
                market=market,
                trade_type=trade_log.get("trade_type", "FALSE_NEGATIVE"),
                entry_time=datetime.now(), # 대략적인 시간
                exit_time=datetime.now(),
                chart_data=input_data, # 전체 컨텍스트 저장
                indicators="{}", 
                ai_reasoning=f"[Correction] {trade_log.get('reason', '')} (Original Score: {score})",
                result_type="WIN", # 급등했으므로 긍정 사례
                profit_rate=trade_log.get("profit_rate", 0),
                hold_duration=0, # 장중 전체
                is_trained=0
            )
            session.add(record)
            session.commit()
            return record.id
        except Exception as e:
            session.rollback()
            print(f"Add training data error: {e}")
            return -1
        finally:
            session.close()

    def mark_data_as_trained(self, ids: list):
        """데이터 학습 완료 처리"""
        if not ids: return
        session = self.get_session()
        try:
            session.query(TrainingDataset)\
                .filter(TrainingDataset.id.in_(ids))\
                .update({TrainingDataset.is_trained: 1}, synchronize_session=False)
            session.commit()
        except Exception as e:
            session.rollback()
            print(f"Mark trained error: {e}")
        finally:
            session.close()

    # ==========================
    # 캐시 관리 (CacheData)
    # ==========================

    def get_cache(self, key: str) -> dict:
        """캐시 조회 (없으면 None)"""
        session = self.get_session()
        try:
            cache = session.query(CacheData).filter_by(key=key).first()
            if cache:
                return _json.loads(cache.value)
            return None
        except Exception:
            return None
        finally:
            session.close()

    def set_cache(self, key: str, data: dict):
        """캐시 저장"""
        session = self.get_session()
        try:
            cache = session.query(CacheData).filter_by(key=key).first()
            if cache:
                cache.value = _json.dumps(data, ensure_ascii=False)
                cache.updated_at = datetime.now()
            else:
                cache = CacheData(
                    key=key,
                    value=_json.dumps(data, ensure_ascii=False)
                )
                session.add(cache)
            session.commit()
        except Exception as e:
            session.rollback()
            print(f"Cache save error: {e}")
        finally:
            session.close()

if __name__ == "__main__":
    db = DatabaseManager()
    db.init_default_settings()
    db.init_default_watchlist() # 마이그레이션 실행
    print(f"✅ Database initialized at {DB_PATH}")
    
    # 설정 확인
    settings = db.get_settings_for_display()
    for key, info in settings.items():
        status = "✅" if info["has_value"] else "❌"
        print(f"  {status} {key}: {info['value'] or '(미설정)'}")