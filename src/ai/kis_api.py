"""
KIS REST API 직접 호출 모듈
- OAuth2 토큰 관리 (매일 오전 8시 갱신)
- 국내주식 현재가, 잔고, 등락률 순위 조회
"""
import json
import os
import time
from datetime import datetime, timedelta
import pytz
import requests
from typing import Dict, List, Optional
from database import DatabaseManager

# KIS OpenAPI 기본 URL
BASE_URL = "https://openapi.koreainvestment.com:9443"

# 토큰 캐시 (모듈 레벨)
_token_cache = {"token": None, "expires_at": 0}


class KISApi:
    """한국투자증권 REST API 클라이언트"""

    def __init__(self, db: DatabaseManager = None):
        self.db = db or DatabaseManager()
        self._app_key = None
        self._app_secret = None
        self._acct_no = None

    @property
    def app_key(self) -> str:
        if not self._app_key:
            self._app_key = self.db.get_setting("KIS_APP_KEY")
        return self._app_key

    @property
    def app_secret(self) -> str:
        if not self._app_secret:
            self._app_secret = self.db.get_setting("KIS_SECRET_KEY")
        return self._app_secret

    @property
    def acct_no(self) -> str:
        """계좌번호 (앞 8자리-뒤 2자리)"""
        if not self._acct_no:
            self._acct_no = self.db.get_setting("KIS_ACCT_STOCK")
        return self._acct_no

    def is_configured(self) -> bool:
        """KIS API 키가 설정되어 있는지 확인"""
        return bool(self.app_key and self.app_secret)

    # ===================
    # OAuth 토큰 관리
    # ===================
    
    _last_error_log_time = 0

    @staticmethod
    def _next_8am_kst() -> float:
        """다음 오전 8시(KST) 시각의 Unix timestamp 반환"""
        kst = pytz.timezone("Asia/Seoul")
        now_kst = datetime.now(kst)
        target = now_kst.replace(hour=8, minute=0, second=0, microsecond=0)
        if now_kst >= target:
            target += timedelta(days=1)
        return target.timestamp()

    def get_access_token(self) -> str:
        """OAuth 접근 토큰 발급 (하루 1회, 오전 8시 KST 갱신)"""
        # 토큰 파일 경로 (현재 파일 위치 기준)
        token_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kis_token.json")
        now = time.time()

        # 1. 메모리 캐시 확인
        if _token_cache["token"] and _token_cache["expires_at"] > now:
            return _token_cache["token"]

        # 2. 파일 캐시 확인
        if os.path.exists(token_file):
            try:
                with open(token_file, "r") as f:
                    saved = json.load(f)
                    if saved.get("expires_at", 0) > now:
                        _token_cache["token"] = saved["token"]
                        _token_cache["expires_at"] = saved["expires_at"]
                        # print(f"[KIS API] 파일 캐시 토큰 사용")
                        return saved["token"]
            except Exception:
                pass

        if not self.is_configured():
            # 로그 스팸 방지 (10분에 한 번만 출력)
            now = time.time()
            if now - self._last_error_log_time > 600:
                print("⚠️ [KIS API] App Key / Secret Key 미설정. 웹 설정(http://localhost:8000/settings)에서 입력해주세요.")
                self._last_error_log_time = now
            return ""

        url = f"{BASE_URL}/oauth2/tokenP"
        body = {
            "grant_type": "client_credentials",
            "appkey": self.app_key,
            "appsecret": self.app_secret
        }

        try:
            resp = requests.post(url, json=body, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                token = data.get("access_token", "")
                
                # 다음 오전 8시까지 유효하도록 설정
                expires_at = self._next_8am_kst()
                
                # 캐시 업데이트
                _token_cache["token"] = token
                _token_cache["expires_at"] = expires_at
                
                # 파일 저장
                try:
                    with open(token_file, "w") as f:
                        json.dump({"token": token, "expires_at": expires_at}, f)
                except Exception as e:
                    print(f"[KIS API] 토큰 파일 저장 실패: {e}")

                kst = pytz.timezone("Asia/Seoul")
                next_refresh = datetime.fromtimestamp(expires_at, kst)
                print(f"[KIS API] 토큰 발급 성공 (다음 갱신: {next_refresh.strftime('%Y-%m-%d %H:%M KST')})")
                return token
            else:
                print(f"[KIS API] 토큰 발급 실패: {resp.status_code} - {resp.text}")
                return ""
        except Exception as e:
            print(f"[KIS API] 토큰 발급 오류: {e}")
            return ""

    def _headers(self, tr_id: str) -> Dict:
        """공통 API 헤더 생성"""
        token = self.get_access_token()
        if not token:
            return {}
        return {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {token}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": tr_id,
            "custtype": "P"  # 개인
        }

    def _get(self, path: str, tr_id: str, params: Dict) -> Dict:
        """GET 요청 공통 메서드"""
        headers = self._headers(tr_id)
        if not headers:
            return {}

        url = f"{BASE_URL}{path}"
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=10)
            if resp.status_code == 200:
                return resp.json()
            else:
                print(f"[KIS API] {path} 실패: {resp.status_code} - {resp.text[:200]}")
                return {}
        except Exception as e:
            print(f"[KIS API] {path} 오류: {e}")
            return {}

    def _hashkey(self, body: Dict) -> str:
        """POST 주문용 hashkey 발급"""
        url = f"{BASE_URL}/uapi/hashkey"
        headers = {
            "content-type": "application/json; charset=utf-8",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
        }
        try:
            resp = requests.post(url, headers=headers, json=body, timeout=5)
            if resp.status_code == 200:
                return resp.json().get("HASH", "")
        except Exception:
            pass
        return ""

    def _post(self, path: str, tr_id: str, body: Dict) -> Dict:
        """POST 요청 공통 메서드 (주문용)"""
        headers = self._headers(tr_id)
        if not headers:
            return {}

        # hashkey 추가
        hk = self._hashkey(body)
        if hk:
            headers["hashkey"] = hk

        url = f"{BASE_URL}{path}"
        try:
            resp = requests.post(url, headers=headers, json=body, timeout=10)
            if resp.status_code == 200:
                return resp.json()
            else:
                print(f"[KIS API] POST {path} 실패: {resp.status_code} - {resp.text[:300]}")
                return {"error": resp.text[:300]}
        except Exception as e:
            print(f"[KIS API] POST {path} 오류: {e}")
            return {"error": str(e)}

    # ===================
    # 국내주식 API
    # ===================

    def inquire_price(self, symbol: str) -> Dict:
        """국내주식 현재가 조회
        
        Returns:
            {price, change_rate, volume, open, high, low, per, pbr, ...}
        """
        data = self._get(
            "/uapi/domestic-stock/v1/quotations/inquire-price",
            "FHKST01010100",
            {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": symbol
            }
        )

        output = data.get("output", {})
        if not output:
            return {}

        try:
            return {
                "price": int(output.get("stck_prpr", 0)),
                "change_rate": float(output.get("prdy_ctrt", 0)),
                "volume": int(output.get("acml_vol", 0)),
                "open": int(output.get("stck_oprc", 0)),
                "high": int(output.get("stck_hgpr", 0)),
                "low": int(output.get("stck_lwpr", 0)),
                "per": float(output.get("per", 0)),
                "pbr": float(output.get("pbr", 0)),
                "volume_ratio": float(output.get("prdy_vrss_vol_rate", 0)),
                "market": "KR"
            }
        except (ValueError, TypeError) as e:
            print(f"[KIS API] 현재가 파싱 오류 ({symbol}): {e}")
            return {}

    # 거래소 코드 매핑 (market → EXCD)
    _MARKET_TO_EXCD = {
        "US": "NAS", "NASD": "NAS", "NYSE": "NYS", "AMEX": "AMS",
        "JP": "TSE", "TKSE": "TSE",
        "HK": "HKS", "SEHK": "HKS",
        "CN": "SHS", "SHAA": "SHS", "SZAA": "SZS",
    }

    def inquire_overseas_price(self, symbol: str, exchange: str = "NAS") -> Dict:
        """해외주식 실시간 현재가 조회 (HHDFS76200200)

        Args:
            symbol: 종목코드 (ex: AAPL)
            exchange: 거래소 코드 (NAS, NYS, AMS, TSE, HKS, SHS, SZS)
        Returns:
            {price, change_rate, volume, open, high, low, market, lot_size}
        """
        excd = self._MARKET_TO_EXCD.get(exchange, exchange)

        # 홍콩(HKS): 종목코드 5자리 zero-padding
        symb = symbol
        if excd == "HKS" and symbol.isdigit():
            symb = symbol.zfill(5)

        data = self._get(
            "/uapi/overseas-price/v1/quotations/price-detail",
            "HHDFS76200200",
            {
                "AUTH": "",
                "EXCD": excd,
                "SYMB": symb,
            }
        )

        output = data.get("output", {})
        if not output:
            return {}

        try:
            # last: 현재가, base: 전일종가
            price = float(output.get("last", 0) or 0)
            lot_size = int(float(output.get("vnit", 1) or 1))
            return {
                "price": price,
                "change_rate": float(output.get("rate", 0) or output.get("t_xrat", 0) or 0),
                "volume": int(float(output.get("tvol", 0) or 0)),
                "open": float(output.get("open", 0) or 0),
                "high": float(output.get("high", 0) or 0),
                "low": float(output.get("low", 0) or 0),
                "market": exchange,
                "lot_size": max(1, lot_size),
            }
        except (ValueError, TypeError) as e:
            print(f"[KIS API] 해외 현재가 파싱 오류 ({symbol}): {e}")
            return {}


    def inquire_balance(self) -> Dict:
        """국내주식 잔고 조회
        
        Returns:
            {cash, holdings: [{symbol, name, quantity, avg_price, current_price, profit_rate}]}
        """
        acct = self.acct_no
        if not acct or len(acct) < 10:
            print(f"[KIS API] 계좌번호 미설정 또는 형식 오류: {acct}")
            return {}

        # 계좌번호: 앞 8자리, 뒤 2자리
        cano = acct[:8]
        acnt_prdt_cd = acct[8:10] if len(acct) >= 10 else "01"

        data = self._get(
            "/uapi/domestic-stock/v1/trading/inquire-balance",
            "TTTC8434R",
            {
                "CANO": cano,
                "ACNT_PRDT_CD": acnt_prdt_cd,
                "AFHR_FLPR_YN": "N",
                "OFL_YN": "",
                "INQR_DVSN": "02",
                "UNPR_DVSN": "01",
                "FUND_STTL_ICLD_YN": "Y",  # Y: 당일 매수분 포함
                "FNCG_AMT_AUTO_RDPT_YN": "N",
                "PRCS_DVSN": "01",
                "CTX_AREA_FK100": "",
                "CTX_AREA_NK100": ""
            }
        )

        result = {"cash": 0, "order_available": 0, "total_assets": 0, "net_assets": 0, "profit_loss": 0, "holdings": []}

        # 예수금 및 총자산 (output2)
        output2 = data.get("output2", [])
        if output2 and isinstance(output2, list) and len(output2) > 0:
            o2 = output2[0]
            result["cash"] = int(o2.get("dnca_tot_amt", 0))
            # 주문가능금액: nrcvb_buy_amt > prvs_rcdl_excc_amt > dnca_tot_amt 순으로 사용
            order_avail = int(o2.get("nrcvb_buy_amt", 0))
            if not order_avail:
                order_avail = int(o2.get("prvs_rcdl_excc_amt", 0))
            if not order_avail:
                order_avail = result["cash"]
            result["order_available"] = order_avail
            result["total_assets"] = int(o2.get("tot_evlu_amt", 0))
            result["net_assets"] = int(o2.get("nass_amt", 0))
            result["profit_loss"] = int(o2.get("evlu_pfls_smtl_amt", 0))
            # 국내 주식 평가액 (예수금 제외 순수 주식 가치)
            result["domestic_evlu"] = int(o2.get("scts_evlu_amt", 0))

        # 보유종목 (output1)
        output1 = data.get("output1", [])
        if output1:
            for item in output1:
                try:
                    qty = int(item.get("hldg_qty", 0))
                    if qty > 0:
                        result["holdings"].append({
                            "symbol": item.get("pdno", ""),
                            "name": item.get("prdt_name", ""),
                            "quantity": qty,
                            "avg_price": int(float(item.get("pchs_avg_pric", 0))),
                            "current_price": int(item.get("prpr", 0)),
                            "profit_rate": float(item.get("evlu_pfls_rt", 0)),
                            "profit_amount": int(item.get("evlu_pfls_amt", 0))
                        })
                except (ValueError, TypeError):
                    continue

        return result

    def inquire_intgr_margin(self) -> Dict:
        """통합증거금 현황 조회 - 주문가능금액 확인
        
        Returns:
            {krw_order_available, usd_order_available, ...}
        """
        acct = self.acct_no
        if not acct or len(acct) < 10:
            return {}

        cano = acct[:8]
        acnt_prdt_cd = acct[8:10] if len(acct) >= 10 else "01"

        # 1) 원화 기준 조회 → KRW 주문가능금액
        data_krw = self._get(
            "/uapi/domestic-stock/v1/trading/intgr-margin",
            "TTTC0869R",
            {
                "CANO": cano,
                "ACNT_PRDT_CD": acnt_prdt_cd,
                "CMA_EVLU_AMT_ICLD_YN": "N",
                "WCRC_FRCR_DVSN_CD": "02",  # 원화기준
                "FWEX_CTRT_FRCR_DVSN_CD": "02",
            }
        )

        # 2) 외화 기준 조회 → USD 주문가능금액 (달러 원본)
        data_frc = self._get(
            "/uapi/domestic-stock/v1/trading/intgr-margin",
            "TTTC0869R",
            {
                "CANO": cano,
                "ACNT_PRDT_CD": acnt_prdt_cd,
                "CMA_EVLU_AMT_ICLD_YN": "N",
                "WCRC_FRCR_DVSN_CD": "01",  # 외화기준
                "FWEX_CTRT_FRCR_DVSN_CD": "01",
            }
        )

        output_krw = data_krw.get("output", {})
        output_frc = data_frc.get("output", {})

        result = {
            # 통합증거금 기준 KRW 주문가능 (stck_itgr_cash100_ord_psbl_amt)
            "krw_order_available": int(float(output_krw.get("stck_itgr_cash100_ord_psbl_amt", 0))),
            # USD 주문가능 (외화기준 원본 달러)
            "usd_order_available": round(float(output_frc.get("usd_gnrl_ord_psbl_amt", 0)), 2),
        }
        return result


    def get_fluctuation_ranking(self, top_n: int = 50, max_price: int = 0) -> List[Dict]:
        """등락률 순위 조회
        
        Returns:
            [{symbol, name, price, change_rate, volume, market}]
        """
        data = self._get(
            "/uapi/domestic-stock/v1/ranking/fluctuation",
            "FHPST01700000",
            {
                "fid_cond_mrkt_div_code": "J",
                "fid_cond_scr_div_code": "20170",
                "fid_input_iscd": "0000",
                "fid_rank_sort_cls_code": "0",
                "fid_input_cnt_1": str(top_n),
                "fid_prc_cls_code": "0",
                "fid_input_price_1": "0",
                "fid_input_price_2": str(max_price) if max_price > 0 else "0",
                "fid_vol_cnt": "100000",
                "fid_trgt_cls_code": "0",
                "fid_trgt_exls_cls_code": "0",
                "fid_div_cls_code": "0",
                "fid_rsfl_rate1": "0",
                "fid_rsfl_rate2": "0"
            }
        )

        rankings = []
        for item in data.get("output", []):
            try:
                rankings.append({
                    "symbol": item.get("stck_shrn_iscd", ""),
                    "name": item.get("hts_kor_isnm", ""),
                    "price": int(item.get("stck_prpr", 0)),
                    "change_rate": float(item.get("prdy_ctrt", 0)),
                    "volume": int(item.get("acml_vol", 0)),
                    "market": "KR"
                })
            except (ValueError, TypeError):
                continue

        return rankings[:top_n]

    # ===================
    # 국내주식 주문 API
    # ===================

    def place_domestic_order(
        self, symbol: str, qty: int, price: int,
        side: str = "buy", order_type: str = "00"
    ) -> Dict:
        """국내주식 매수/매도 주문

        Args:
            symbol: 종목코드 (예: 005930)
            qty: 주문수량
            price: 주문 단가 (원). 시장가 주문 시 0
            side: "buy" 또는 "sell"
            order_type: "00"=지정가, "01"=시장가

        Returns:
            {success, order_no, message}
        """
        acct = self.acct_no
        if not acct or len(acct) < 10:
            return {"success": False, "message": "계좌번호 미설정"}

        cano = acct[:8]
        acnt_prdt_cd = acct[8:10]

        # TR ID: 매수=TTTC0012U, 매도=TTTC0011U (현금주문)
        if side == "buy":
            tr_id = "TTTC0012U"
        else:
            tr_id = "TTTC0011U"

        body = {
            "CANO": cano,
            "ACNT_PRDT_CD": acnt_prdt_cd,
            "PDNO": symbol,
            "ORD_DVSN": order_type,
            "ORD_QTY": str(qty),
            "ORD_UNPR": str(int(price)),
            "EXCG_ID_DVSN_CD": "KRX",
        }

        print(f"[KIS API] 국내주식 {side} 주문: {symbol} {qty}주 ₩{price:,}")

        data = self._post(
            "/uapi/domestic-stock/v1/trading/order-cash",
            tr_id,
            body
        )

        if data.get("rt_cd") == "0":
            output = data.get("output", {})
            order_no = output.get("ODNO", "") or output.get("odno", "")
            print(f"[KIS API] ✅ 국내주문 성공: {order_no}")
            return {
                "success": True,
                "order_no": order_no,
                "message": data.get("msg1", "주문 접수"),
            }
        else:
            msg = data.get("msg1", "") or data.get("error", "주문 실패")
            print(f"[KIS API] ❌ 국내주문 실패: {msg}")
            return {
                "success": False,
                "order_no": "",
                "message": msg,
            }

    # ===================
    # 미체결 주문 조회
    # ===================

    def inquire_pending_domestic(self) -> List[Dict]:
        """국내주식 미체결 주문 조회

        Returns:
            [{symbol, name, side, qty, order_qty, order_price, order_no, order_time}]
        """
        acct = self.acct_no
        if not acct or len(acct) < 10:
            return []

        cano = acct[:8]
        acnt_prdt_cd = acct[8:10] if len(acct) >= 10 else "01"

        today = datetime.now(pytz.timezone("Asia/Seoul")).strftime("%Y%m%d")

        try:
            data = self._get(
                "/uapi/domestic-stock/v1/trading/inquire-daily-ccld",
                "TTTC8001R",
                {
                    "CANO": cano,
                    "ACNT_PRDT_CD": acnt_prdt_cd,
                    "INQR_STRT_DT": today,
                    "INQR_END_DT": today,
                    "SLL_BUY_DVSN_CD": "00",   # 전체 (매수+매도)
                    "INQR_DVSN": "00",          # 역순
                    "PDNO": "",
                    "CCLD_DVSN": "02",          # 02=미체결
                    "ORD_GNO_BRNO": "",
                    "ODNO": "",
                    "INQR_DVSN_3": "00",
                    "INQR_DVSN_1": "",
                    "CTX_AREA_FK100": "",
                    "CTX_AREA_NK100": "",
                }
            )

            results = []
            for item in data.get("output1", []):
                # rmn_qty: 미체결 잔량 (KIS API 직접 제공)
                remaining = int(item.get("rmn_qty", "0") or "0")
                if remaining <= 0:
                    continue

                ord_qty = int(item.get("ord_qty", "0") or "0")
                tot_ccld_qty = int(item.get("tot_ccld_qty", "0") or "0")
                side_code = item.get("sll_buy_dvsn_cd", "")
                results.append({
                    "symbol": item.get("pdno", ""),
                    "name": item.get("prdt_name", "") or item.get("pdno", ""),
                    "side": "sell" if side_code == "01" else "buy",
                    "order_qty": ord_qty,
                    "filled_qty": tot_ccld_qty,
                    "remaining_qty": remaining,
                    "order_price": int(float(item.get("ord_unpr", "0") or "0")),
                    "order_no": item.get("odno", ""),
                    "order_time": item.get("ord_tmd", ""),
                    "market_type": "domestic",
                    "exchange": "KRX",
                })
            return results
        except Exception as e:
            print(f"[KIS API] 국내 미체결 조회 오류: {e}")
            return []

    def inquire_fulfillment(self) -> List[Dict]:
        """당일 체결 내역 조회 (국내+해외 통합)"""
        return self.inquire_history(days=0)

    def inquire_history(self, days: int = 30) -> List[Dict]:
        """지정된 기간(최근 n일) 동안의 체결 내역 조회 (국내+해외 통합)
        
        Args:
            days: 오늘로부터 이전 몇 일을 조회할지 (0이면 오늘만)
        Returns:
            [{symbol, name, side, quantity, price, order_no, date, time, market}]
        """
        results = []
        acct = self.acct_no
        if not acct or len(acct) < 10:
            return []

        cano = acct[:8]
        acnt_prdt_cd = acct[8:10]
        
        tz = pytz.timezone("Asia/Seoul")
        now_kst = datetime.now(tz)
        end_dt = now_kst.strftime("%Y%m%d")
        start_dt = (now_kst - timedelta(days=days)).strftime("%Y%m%d")

        # 1. 국내 체결 내역 (TTTC8001R - 주식일별체결조회)
        try:
            data = self._get(
                "/uapi/domestic-stock/v1/trading/inquire-daily-ccld",
                "TTTC8001R",
                {
                    "CANO": cano,
                    "ACNT_PRDT_CD": acnt_prdt_cd,
                    "INQR_STRT_DT": start_dt,
                    "INQR_END_DT": end_dt,
                    "SLL_BUY_DVSN_CD": "00",
                    "INQR_DVSN": "00",
                    "PDNO": "",
                    "CCLD_DVSN": "01",  # 01=체결
                    "ORD_GNO_BRNO": "",
                    "ODNO": "",
                    "INQR_DVSN_3": "00",
                    "INQR_DVSN_1": "",
                    "CTX_AREA_FK100": "",
                    "CTX_AREA_NK100": "",
                }
            )
            for item in data.get("output1", []):
                ccld_qty = int(item.get("tot_ccld_qty", 0) or 0)
                if ccld_qty > 0:
                    results.append({
                        "symbol": item.get("pdno", ""),
                        "name": item.get("prdt_name", ""),
                        "side": "buy" if item.get("sll_buy_dvsn_cd") == "02" else "sell",
                        "quantity": ccld_qty,
                        "price": float(item.get("avg_prvs", item.get("avg_prc", 0)) or 0),
                        "order_no": item.get("odno", ""),
                        "date": item.get("ord_dt", ""),
                        "time": item.get("ord_tmd", ""),
                        "market": "KR"
                    })
        except Exception as e:
            print(f"[KIS API] 국내 체결 조회 오류: {e}")

        # 2. 해외 체결 내역 (TTTS3035R - 해외주식주문체결내역)
        try:
            exchanges = ["NASD", "NYSE", "AMEX"]
            for exch in exchanges:
                data = self._get(
                    "/uapi/overseas-stock/v1/trading/inquire-ccnl",
                    "TTTS3035R",
                    {
                        "CANO": cano,
                        "ACNT_PRDT_CD": acnt_prdt_cd,
                        "PDNO": "%",
                        "ORD_STRT_DT": start_dt,
                        "ORD_END_DT": end_dt,
                        "SLL_BUY_DVSN": "00",
                        "CCLD_NCCS_DVSN": "01",  # 체결
                        "OVRS_EXCG_CD": exch,
                        "SORT_SQN": "DS",
                        "ORD_DT": "",
                        "ORD_GNO_BRNO": "",
                        "ODNO": "",
                        "CTX_AREA_FK200": "",
                        "CTX_AREA_NK200": "",
                    }
                )
                for item in data.get("output", []):
                    ccld_qty = int(float(item.get("ft_ccld_qty", 0) or 0))
                    if ccld_qty > 0:
                        results.append({
                            "symbol": item.get("pdno", ""),
                            "name": item.get("prdt_name", ""),
                            "side": "buy" if item.get("sll_buy_dvsn_cd") == "02" else "sell",
                            "quantity": ccld_qty,
                            "price": float(item.get("ft_ccld_unpr3", item.get("ft_ccld_unpr", 0)) or 0),
                            "order_no": item.get("odno", ""),
                            "date": item.get("ord_dt", ""),
                            "time": item.get("ord_tmd", ""),
                            "market": "US"
                        })
        except Exception as e:
            print(f"[KIS API] 해외 체결 조회 오류: {e}")

        return results

    def inquire_pending_overseas(self, symbol: str = "", exchange: str = "NAS") -> Dict:
        acct = self.acct_no
        if not acct or len(acct) < 10:
            return []

        cano = acct[:8]
        acnt_prdt_cd = acct[8:10] if len(acct) >= 10 else "01"

        # 주요 거래소별 미체결 조회
        all_pending = []
        exchanges = ["NASD", "NYSE", "AMEX"]

        for exch in exchanges:
            try:
                data = self._get(
                    "/uapi/overseas-stock/v1/trading/inquire-nccs",
                    "TTTS3018R",
                    {
                        "CANO": cano,
                        "ACNT_PRDT_CD": acnt_prdt_cd,
                        "OVRS_EXCG_CD": exch,
                        "SORT_SQN": "DS",
                        "CTX_AREA_FK200": "",
                        "CTX_AREA_NK200": "",
                    }
                )

                for item in data.get("output", []):
                    ord_qty = int(float(item.get("ft_ord_qty", "0") or "0"))
                    ccld_qty = int(float(item.get("ft_ccld_qty", "0") or "0"))
                    remaining = ord_qty - ccld_qty
                    if remaining <= 0:
                        continue

                    side_code = item.get("sll_buy_dvsn_cd", "")
                    all_pending.append({
                        "symbol": item.get("pdno", ""),
                        "name": item.get("prdt_name", "") or item.get("pdno", ""),
                        "side": "sell" if side_code == "01" else "buy",
                        "order_qty": ord_qty,
                        "filled_qty": ccld_qty,
                        "remaining_qty": remaining,
                        "order_price": float(item.get("ft_ord_unpr3", "0") or item.get("ft_ord_unpr", "0") or "0"),
                        "order_no": item.get("odno", ""),
                        "order_time": item.get("ord_tmd", ""),
                        "market_type": "overseas",
                        "exchange": exch,
                    })
            except Exception as e:
                print(f"[KIS API] {exch} 미체결 조회 오류: {e}")

        return all_pending

    # ===================
    # 주문 취소
    # ===================

    def cancel_domestic_order(self, order_no: str, qty: int = 0) -> Dict:
        """국내주식 주문 취소

        Args:
            order_no: 원주문번호
            qty: 취소 수량 (0이면 전량)
        Returns:
            {success, message}
        """
        acct = self.acct_no
        if not acct or len(acct) < 10:
            return {"success": False, "message": "계좌번호 미설정"}

        cano = acct[:8]
        acnt_prdt_cd = acct[8:10]

        body = {
            "CANO": cano,
            "ACNT_PRDT_CD": acnt_prdt_cd,
            "KRX_FWDG_ORD_ORGNO": "",
            "ORGN_ODNO": order_no,
            "ORD_DVSN": "00",
            "RVSE_CNCL_DVSN_CD": "02",  # 02=취소
            "ORD_QTY": str(qty) if qty > 0 else "0",
            "ORD_UNPR": "0",
            "QTY_ALL_ORD_YN": "Y" if qty == 0 else "N",
        }

        print(f"[KIS API] 국내 주문취소: 주문#{order_no} {qty}주")

        data = self._post(
            "/uapi/domestic-stock/v1/trading/order-rvsecncl",
            "TTTC0013U",
            body
        )

        if data.get("rt_cd") == "0":
            print(f"[KIS API] ✅ 국내주문 취소 성공: {order_no}")
            return {"success": True, "message": "취소 완료"}
        else:
            msg = data.get("msg1", "") or data.get("error", "취소 실패")
            print(f"[KIS API] ❌ 국내주문 취소 실패: {msg}")
            return {"success": False, "message": msg}

    # 해외 거래소 → 취소 TR ID 매핑
    _OVERSEAS_CANCEL_TR = {
        "NASD": "TTTT1004U", "NYSE": "TTTT1004U", "AMEX": "TTTT1004U",
        "SEHK": "TTTS0309U", "SHAA": "TTTS0302U", "SZAA": "TTTS0306U",
        "TKSE": "TTTS0309U", "HASE": "TTTS0312U", "VNSE": "TTTS0312U",
    }

    def cancel_overseas_order(self, order_no: str, exchange: str,
                               symbol: str = "",
                               qty: int = 0, price: float = 0) -> Dict:
        """해외주식 주문 취소

        Args:
            order_no: 원주문번호
            exchange: 거래소 코드 (NASD, NYSE 등)
            symbol: 종목코드 (AAPL 등)
            qty: 취소 수량 (해외는 필수)
            price: 원주문 가격
        Returns:
            {success, message}
        """
        acct = self.acct_no
        if not acct or len(acct) < 10:
            return {"success": False, "message": "계좌번호 미설정"}

        cano = acct[:8]
        acnt_prdt_cd = acct[8:10]

        tr_id = self._OVERSEAS_CANCEL_TR.get(exchange)
        if not tr_id:
            return {"success": False, "message": f"미지원 거래소: {exchange}"}

        body = {
            "CANO": cano,
            "ACNT_PRDT_CD": acnt_prdt_cd,
            "OVRS_EXCG_CD": exchange,
            "PDNO": symbol,
            "ORGN_ODNO": order_no,
            "RVSE_CNCL_DVSN_CD": "02",  # 02=취소
            "ORD_QTY": str(qty) if qty > 0 else "0",
            "OVRS_ORD_UNPR": f"{price:.2f}" if price > 0 else "0",
            "MGCO_APTM_ODNO": "",
            "ORD_SVR_DVSN_CD": "0",
        }

        print(f"[KIS API] 해외 주문취소: {exchange} 주문#{order_no} {qty}주")

        data = self._post(
            "/uapi/overseas-stock/v1/trading/order-rvsecncl",
            tr_id,
            body
        )

        if data.get("rt_cd") == "0":
            print(f"[KIS API] ✅ 해외주문 취소 성공: {order_no}")
            return {"success": True, "message": "취소 완료"}
        else:
            msg = data.get("msg1", "") or data.get("error", "취소 실패")
            print(f"[KIS API] ❌ 해외주문 취소 실패: {msg}")
            return {"success": False, "message": msg}

    # ===================
    # 해외주식 API
    # ===================

    # 해외거래소 → 매수 TR ID 매핑
    _OVERSEAS_BUY_TR = {
        "NASD": "TTTT1002U", "NYSE": "TTTT1002U", "AMEX": "TTTT1002U",
        "SEHK": "TTTS1002U", "SHAA": "TTTS0202U", "SZAA": "TTTS0305U",
        "TKSE": "TTTS0308U", "HASE": "TTTS0311U", "VNSE": "TTTS0311U",
    }
    _OVERSEAS_SELL_TR = {
        "NASD": "TTTT1006U", "NYSE": "TTTT1006U", "AMEX": "TTTT1006U",
        "SEHK": "TTTS1001U", "SHAA": "TTTS1005U", "SZAA": "TTTS0304U",
        "TKSE": "TTTS0307U", "HASE": "TTTS0310U", "VNSE": "TTTS0310U",
    }

    def place_overseas_order(
        self, symbol: str, exchange: str, qty: int, price: float,
        side: str = "buy"
    ) -> Dict:
        """해외주식 매수/매도 주문

        Args:
            symbol: 종목코드 (예: AAPL, DDL)
            exchange: 거래소 코드 (NASD, NYSE, AMEX ...)
            qty: 주문수량
            price: 주문 단가 (USD)
            side: "buy" 또는 "sell"

        Returns:
            {success, order_no, message}
        """
        acct = self.acct_no
        if not acct or len(acct) < 10:
            return {"success": False, "message": "계좌번호 미설정"}

        cano = acct[:8]
        acnt_prdt_cd = acct[8:10]

        # TR ID 선택
        if side == "buy":
            tr_id = self._OVERSEAS_BUY_TR.get(exchange)
            sll_type = ""
        else:
            tr_id = self._OVERSEAS_SELL_TR.get(exchange)
            sll_type = "00"

        if not tr_id:
            return {"success": False, "message": f"미지원 거래소: {exchange}"}

        # 홍콩(SEHK): 종목코드 5자리 zero-padding 필요
        pdno = symbol
        if exchange == "SEHK" and symbol.isdigit():
            pdno = symbol.zfill(5)

        body = {
            "CANO": cano,
            "ACNT_PRDT_CD": acnt_prdt_cd,
            "OVRS_EXCG_CD": exchange,
            "PDNO": pdno,
            "ORD_QTY": str(qty),
            "OVRS_ORD_UNPR": f"{price:.2f}",
            "CTAC_TLNO": "",
            "MGCO_APTM_ODNO": "",
            "SLL_TYPE": sll_type,
            "ORD_SVR_DVSN_CD": "0",
            "ORD_DVSN": "00",  # 지정가
        }

        print(f"[KIS API] 해외주식 {side} 주문: {symbol}@{exchange} {qty}주 ${price:.2f}")

        data = self._post(
            "/uapi/overseas-stock/v1/trading/order",
            tr_id,
            body
        )

        if data.get("rt_cd") == "0":
            output = data.get("output", {})
            order_no = output.get("ODNO", "") or output.get("odno", "")
            print(f"[KIS API] ✅ 주문 성공: {order_no}")
            return {
                "success": True,
                "order_no": order_no,
                "message": data.get("msg1", "주문 접수"),
            }
        else:
            msg = data.get("msg1", "") or data.get("error", "주문 실패")
            print(f"[KIS API] ❌ 주문 실패: {msg}")
            return {
                "success": False,
                "order_no": "",
                "message": msg,
            }

    def inquire_overseas_balance(self) -> List[Dict]:
        """해외주식 보유종목 조회 (체결기준잔고)

        Returns:
            [{symbol, name, exchange, qty, avg_price, current_price, profit_rate, profit_amount}]
        """
        acct = self.acct_no
        if not acct or len(acct) < 10:
            return []

        cano = acct[:8]
        acnt_prdt_cd = acct[8:10]

        data = self._get(
            "/uapi/overseas-stock/v1/trading/inquire-balance",
            "TTTS3012R",
            {
                "CANO": cano,
                "ACNT_PRDT_CD": acnt_prdt_cd,
                "OVRS_EXCG_CD": "NASD",  # 전체 조회 시에도 필요
                "TR_CRCY_CD": "USD",
                "CTX_AREA_FK200": "",
                "CTX_AREA_NK200": "",
            }
        )

        holdings = []
        for item in data.get("output1", []):
            try:
                qty = int(float(item.get("ovrs_cblc_qty", 0)))
                if qty > 0:
                    holdings.append({
                        "symbol": item.get("ovrs_pdno", ""),
                        "name": item.get("ovrs_item_name", ""),
                        "exchange": item.get("ovrs_excg_cd", ""),
                        "quantity": qty,
                        "avg_price": float(item.get("pchs_avg_pric", 0)),
                        "current_price": float(item.get("now_pric2", 0) or item.get("ovrs_now_pric", 0)),
                        "profit_rate": float(item.get("evlu_pfls_rt", 0)),
                        "profit_amount": float(item.get("frcr_evlu_pfls_amt", 0)),
                        "eval_amount": float(item.get("ovrs_stck_evlu_amt", 0)),
                        "currency": item.get("tr_crcy_cd", "USD"),
                    })
            except (ValueError, TypeError):
                continue

        return holdings
