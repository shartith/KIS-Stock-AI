"""
Notification Service - Discord Webhook 알림
"""
import requests
import json
import os
from typing import Dict, Any, Optional
from datetime import datetime


class NotificationService:
    def __init__(self, db=None):
        """
        db: DatabaseManager 인스턴스.
        webhook URL은 DB에서 매번 읽어옴 (설정 변경 즉시 반영).
        """
        self._db = db

    def _get_webhook_url(self) -> Optional[str]:
        """DB에서 Discord Webhook URL을 조회"""
        if self._db:
            url = self._db.get_setting("DISCORD_WEBHOOK_URL", "")
            if url and url.startswith("https://"):
                return url
        # DB 없으면 환경변수 폴백
        return os.getenv("DISCORD_WEBHOOK_URL")

    def _is_trade_alert_enabled(self) -> bool:
        """매매 알림 활성화 여부"""
        if self._db:
            return self._db.get_setting("NOTI_TRADE_ALERTS", "1") == "1"
        return True

    def send_message(self, content: str = None, embeds: list = None):
        """Discord 메시지 전송"""
        webhook_url = self._get_webhook_url()
        if not webhook_url:
            print(f"⚠️ Discord Webhook URL이 설정되지 않았습니다. (메시지: {content})")
            return False

        payload = {}
        if content:
            payload["content"] = content
        if embeds:
            payload["embeds"] = embeds

        try:
            response = requests.post(webhook_url, json=payload, timeout=10)
            response.raise_for_status()
            print(f"✅ Discord 알림 전송 성공")
            return True
        except Exception as e:
            print(f"❌ Discord 알림 전송 실패: {e}")
            return False

    def send_trade_alert(self, action: str, symbol: str, name: str,
                         price: float, quantity: int, reason: str = "",
                         market: str = "KR", profit_pct: float = None):
        """매매 체결 알림"""
        if not self._is_trade_alert_enabled():
            print(f"ℹ️ 매매 알림 비활성화 — {action} {name}")
            return False

        is_buy = action.upper() in ("BUY", "매수")
        color = 0x00FF00 if is_buy else 0xFF0000
        emoji = "📈" if is_buy else "📉"
        action_kr = "매수" if is_buy else "매도"

        # 가격 포맷
        if market == "KR":
            price_str = f"{int(price):,}원"
            total_str = f"{int(price * quantity):,}원"
        else:
            price_str = f"${price:,.2f}"
            total_str = f"${price * quantity:,.2f}"

        fields = [
            {"name": "종목", "value": f"{name} ({symbol})", "inline": False},
            {"name": "가격", "value": price_str, "inline": True},
            {"name": "수량", "value": f"{quantity:,}주", "inline": True},
            {"name": "총액", "value": total_str, "inline": True},
        ]

        if profit_pct is not None:
            profit_emoji = "🟢" if profit_pct >= 0 else "🔴"
            fields.append({"name": "수익률", "value": f"{profit_emoji} {profit_pct:+.2f}%", "inline": True})

        if reason:
            fields.append({"name": "사유", "value": reason[:200], "inline": False})

        embed = {
            "title": f"{emoji} {action_kr} 체결 알림",
            "color": color,
            "fields": fields,
            "footer": {"text": f"KIS-Stock-AI • {market}"},
            "timestamp": datetime.utcnow().isoformat()
        }

        return self.send_message(embeds=[embed])

    def send_error_alert(self, error_msg: str):
        """에러 알림"""
        embed = {
            "title": "🚨 시스템 오류 발생",
            "description": error_msg[:500],
            "color": 0xFF0000,
            "footer": {"text": "KIS-Stock-AI System Alert"},
            "timestamp": datetime.utcnow().isoformat()
        }
        return self.send_message(embeds=[embed])

    def send_system_alert(self, title: str, message: str, color: int = 0x3498DB):
        """시스템 알림 (스캐너 시작/종료 등)"""
        embed = {
            "title": title,
            "description": message[:500],
            "color": color,
            "footer": {"text": "KIS-Stock-AI"},
            "timestamp": datetime.utcnow().isoformat()
        }
        return self.send_message(embeds=[embed])

