"""
Antigravity Ultra Authentication
Google OAuth 2.0 → Antigravity Cloud Code API 직접 인증

앱 등록 불필요. Antigravity IDE의 기존 OAuth 설정 활용.
"""
import json
import os
import time
import threading
import webbrowser
import platform
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlencode, urlparse, parse_qs
from pathlib import Path
from typing import Dict, Optional, Tuple
import requests
import sys

# 상위 경로 추가 (database.py 접근용)
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from database import DatabaseManager

# ==========================
# OAuth Configuration (Default)
# ==========================
DEFAULT_CLIENT_ID = "1071006060591-tmhssin2h21lcre235vtolojh4g403ep.apps.googleusercontent.com"
DEFAULT_CLIENT_SECRET = "" # Secret removed

def get_oauth_config():
    """DB 또는 환경변수에서 OAuth 설정 로드"""
    db = DatabaseManager()
    
    client_id = db.get_setting("GOOGLE_OAUTH_CLIENT_ID") or os.getenv("GOOGLE_OAUTH_CLIENT_ID") or DEFAULT_CLIENT_ID
    client_secret = db.get_setting("GOOGLE_OAUTH_CLIENT_SECRET") or os.getenv("GOOGLE_OAUTH_CLIENT_SECRET") or DEFAULT_CLIENT_SECRET
    
    return {
        "client_id": client_id,
        "client_secret": client_secret,
        "auth_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "userinfo_url": "https://www.googleapis.com/oauth2/v1/userinfo?alt=json",
        "project_url": "https://cloudcode-pa.googleapis.com/v1internal:loadCodeAssist",
        "scopes": [
            "https://www.googleapis.com/auth/cloud-platform",
            "https://www.googleapis.com/auth/userinfo.email",
            "https://www.googleapis.com/auth/userinfo.profile",
            "https://www.googleapis.com/auth/cclog",
            "https://www.googleapis.com/auth/experimentsandconfigs",
        ],
    }

# Antigravity API endpoints (daily 우선)
ANTIGRAVITY_API_URLS = [
    "https://daily-cloudcode-pa.googleapis.com",
    "https://daily-cloudcode-pa.sandbox.googleapis.com",
    "https://cloudcode-pa.googleapis.com",
]
STREAM_ENDPOINT = "/v1internal:streamGenerateContent"

# User-Agent (Antigravity IDE 모방)
ANTIGRAVITY_IDE_VERSION = "1.15.8"
_platform = "macos" if platform.system() == "Darwin" else platform.system().lower()
_arch = platform.machine()
ANTIGRAVITY_USER_AGENT = f"antigravity/{ANTIGRAVITY_IDE_VERSION} {_platform}/{_arch}"

# 사용 가능한 모델
AVAILABLE_MODELS = {
    "claude-opus-4-5-thinking": "Claude Opus 4.5 (Thinking)",
    "claude-opus-4-6-thinking": "Claude Opus 4.6 (최신, Thinking)",
    "claude-sonnet-4-5": "Claude Sonnet 4.5",
    "claude-sonnet-4-5-thinking": "Claude Sonnet 4.5 (Thinking)",
    "gemini-3-pro-high": "Gemini 3 Pro (High Quality)",
    "gemini-3-pro-low": "Gemini 3 Pro (Fast)",
    "gemini-3-flash": "Gemini 3 Flash",
}

# 토큰 저장 경로
AUTH_DIR = Path.home() / ".kis-stock-ai"
AUTH_FILE = AUTH_DIR / "antigravity_auth.json"

# Callback port
CALLBACK_PORT = 51121


# ==========================
# Token Persistence
# ==========================

def _load_saved_auth() -> Dict:
    """저장된 인증 정보 로드"""
    try:
        if AUTH_FILE.exists():
            with open(AUTH_FILE) as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_auth(data: Dict):
    """인증 정보 저장"""
    AUTH_DIR.mkdir(parents=True, exist_ok=True)
    with open(AUTH_FILE, "w") as f:
        json.dump(data, f, indent=2)
    # 파일 권한 제한 (소유자만)
    os.chmod(AUTH_FILE, 0o600)


def _clear_auth():
    """인증 정보 삭제"""
    if AUTH_FILE.exists():
        AUTH_FILE.unlink()


# ==========================
# Antigravity Auth Manager
# ==========================

class AntigravityAuth:
    """Antigravity Ultra 인증 매니저"""

    def __init__(self):
        self.access_token: str = ""
        self.refresh_token: str = ""
        self.token_expires_at: float = 0
        self.email: str = ""
        self.project_id: str = ""
        self.model: str = "claude-sonnet-4-5"
        self._lock = threading.Lock()
        self._oauth_result: Optional[Dict] = None

        # 저장된 인증 로드
        self._load()

    def _load(self):
        """저장된 토큰 복원"""
        data = _load_saved_auth()
        if data:
            self.access_token = data.get("access_token", "")
            self.refresh_token = data.get("refresh_token", "")
            self.token_expires_at = data.get("token_expires_at", 0)
            self.email = data.get("email", "")
            self.project_id = data.get("project_id", "")
            self.model = data.get("model", "claude-sonnet-4-5")

    def _save(self):
        """현재 상태 저장"""
        _save_auth({
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "token_expires_at": self.token_expires_at,
            "email": self.email,
            "project_id": self.project_id,
            "model": self.model,
        })

    @property
    def is_authenticated(self) -> bool:
        """인증 여부"""
        return bool(self.access_token and self.refresh_token)

    def get_status(self) -> Dict:
        """현재 인증 상태"""
        return {
            "authenticated": self.is_authenticated,
            "email": self.email,
            "model": self.model,
            "model_name": AVAILABLE_MODELS.get(self.model, self.model),
            "project_id": self.project_id,
            "available_models": {k: v for k, v in AVAILABLE_MODELS.items()},
        }

    def set_model(self, model: str) -> bool:
        """모델 변경"""
        if model in AVAILABLE_MODELS or model:  # 커스텀 모델명도 허용
            self.model = model
            self._save()
            return True
        return False

    # ==========================
    # OAuth Login Flow
    # ==========================

    def start_login(self) -> Tuple[str, int]:
        """OAuth 로그인 시작 — 인증 URL과 콜백 포트 반환"""
        self._oauth_result = None
        
        config = get_oauth_config()
        if not config["client_secret"]:
             raise ValueError("Google OAuth Client Secret이 설정되지 않았습니다. 설정 페이지에서 입력해주세요.")

        # 콜백 포트 선택
        port = CALLBACK_PORT
        for offset in range(10):
            try:
                test_server = HTTPServer(("localhost", port + offset), BaseHTTPRequestHandler)
                test_server.server_close()
                port = port + offset
                break
            except OSError:
                continue

        redirect_uri = f"http://localhost:{port}/oauth-callback"

        # 인증 URL 생성
        params = {
            "client_id": config["client_id"],
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(config["scopes"]),
            "access_type": "offline",
            "prompt": "consent",
            "state": os.urandom(16).hex(),
        }
        auth_url = f"{config['auth_url']}?{urlencode(params)}"

        # 콜백 서버 시작 (백그라운드)
        self._start_callback_server(port, redirect_uri, params["state"])

        return auth_url, port

    def _start_callback_server(self, port: int, redirect_uri: str, expected_state: str):
        """OAuth 콜백 서버 (백그라운드 스레드)"""
        auth_manager = self

        class CallbackHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                parsed = urlparse(self.path)
                if parsed.path != "/oauth-callback":
                    self.send_response(404)
                    self.end_headers()
                    return

                qs = parse_qs(parsed.query)
                code = qs.get("code", [None])[0]
                state = qs.get("state", [None])[0]
                error = qs.get("error", [None])[0]

                if error:
                    auth_manager._oauth_result = {"error": error}
                    self._respond_html("❌ 인증 실패", f"Error: {error}")
                elif state != expected_state:
                    auth_manager._oauth_result = {"error": "state_mismatch"}
                    self._respond_html("❌ 인증 실패", "State mismatch")
                elif code:
                    # 토큰 교환
                    try:
                        auth_manager._complete_login(code, redirect_uri)
                        auth_manager._oauth_result = {"success": True}
                        self._respond_html("✅ 인증 성공", f"환영합니다, {auth_manager.email}!<br>이 창을 닫으셔도 됩니다.")
                    except Exception as e:
                        auth_manager._oauth_result = {"error": str(e)}
                        self._respond_html("❌ 인증 실패", str(e))

            def _respond_html(self, title: str, body: str):
                html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{title}</title>
<style>body{{font-family:-apple-system,sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;background:#0a0f1c;color:#e0e0e0}}
.card{{background:#151b2e;padding:3rem;border-radius:16px;text-align:center;max-width:400px;box-shadow:0 8px 32px rgba(0,0,0,.4)}}
h1{{font-size:2rem;margin-bottom:1rem}}p{{opacity:.8}}</style>
</head><body><div class="card"><h1>{title}</h1><p>{body}</p></div></body></html>"""
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(html.encode())

            def log_message(self, format, *args):
                pass  # suppress logs

        def run_server():
            server = HTTPServer(("0.0.0.0", port), CallbackHandler)
            server.timeout = 300  # 5분 타임아웃
            server.handle_request()  # 한 번만 처리
            server.server_close()

        thread = threading.Thread(target=run_server, daemon=True)
        thread.start()

    def _complete_login(self, code: str, redirect_uri: str):
        """인증 코드 → 토큰 교환 + 사용자 정보 획득"""
        config = get_oauth_config()
        
        # 1. 토큰 교환
        resp = requests.post(
            config["token_url"],
            data={
                "code": code,
                "client_id": config["client_id"],
                "client_secret": config["client_secret"],
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
            timeout=30,
        )
        resp.raise_for_status()
        tokens = resp.json()

        self.access_token = tokens["access_token"]
        self.refresh_token = tokens.get("refresh_token", "")
        self.token_expires_at = time.time() + tokens.get("expires_in", 3600)

        # 2. 사용자 정보
        user_resp = requests.get(
            config["userinfo_url"],
            headers={"Authorization": f"Bearer {self.access_token}"},
            timeout=15,
        )
        if user_resp.ok:
            user_info = user_resp.json()
            self.email = user_info.get("email", "")

        # 3. 프로젝트 ID 획득
        try:
            proj_resp = requests.post(
                config["project_url"],
                headers={
                    "Authorization": f"Bearer {self.access_token}",
                    "Content-Type": "application/json",
                    "User-Agent": ANTIGRAVITY_USER_AGENT,
                },
                json={"metadata": {"ideType": "ANTIGRAVITY"}},
                timeout=15,
            )
            if proj_resp.ok:
                self.project_id = proj_resp.json().get("cloudaicompanionProject", "")
        except Exception:
            pass

        # 4. 저장
        self._save()

    # ==========================
    # Token Refresh
    # ==========================

    def get_valid_token(self) -> str:
        """유효한 access_token 반환 (필요시 갱신)"""
        if not self.is_authenticated:
            raise RuntimeError("Not authenticated. Please login first.")

        # 만료 5분 전 갱신
        if time.time() > self.token_expires_at - 300:
            self._refresh()

        return self.access_token

    def _refresh(self):
        """access_token 갱신"""
        with self._lock:
            # 다른 스레드가 이미 갱신했으면 스킵
            if time.time() < self.token_expires_at - 300:
                return
            
            config = get_oauth_config()

            try:
                resp = requests.post(
                    config["token_url"],
                    data={
                        "client_id": config["client_id"],
                        "client_secret": config["client_secret"],
                        "refresh_token": self.refresh_token,
                        "grant_type": "refresh_token",
                    },
                    timeout=30,
                )
                resp.raise_for_status()
                tokens = resp.json()

                self.access_token = tokens["access_token"]
                self.token_expires_at = time.time() + tokens.get("expires_in", 3600)
                self._save()
            except Exception as e:
                raise RuntimeError(f"Token refresh failed: {e}. Please re-login.")

    # ==========================
    # API Call
    # ==========================

    def call_api(self, prompt: str, system_prompt: str = "", model: str = "") -> Dict:
        """Antigravity Cloud Code API 호출"""
        token = self.get_valid_token()
        use_model = model or self.model

        # 요청 본문 구성 (Gemini 형식)
        contents = []
        if system_prompt:
            contents.append({
                "role": "user",
                "parts": [{"text": system_prompt}],
            })
            contents.append({
                "role": "model",
                "parts": [{"text": "네, 알겠습니다. 해당 역할로 분석하겠습니다."}],
            })

        contents.append({
            "role": "user",
            "parts": [{"text": prompt}],
        })

        request_body = {
            "model": use_model,
            "userAgent": ANTIGRAVITY_USER_AGENT,
            "requestType": "agent",
            "project": self.project_id or "unknown",
            "requestId": f"kis-stock-{os.urandom(8).hex()}",
            "request": {
                "contents": contents,
                "safetySettings": [
                    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "OFF"},
                    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "OFF"},
                    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "OFF"},
                    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "OFF"},
                    {"category": "HARM_CATEGORY_CIVIC_INTEGRITY", "threshold": "OFF"},
                ],
                "generationConfig": {
                    "maxOutputTokens": 4096,
                    "temperature": 0.3,
                },
            },
        }

        # 엔드포인트 순회 (failover)
        last_error = None
        for base_url in ANTIGRAVITY_API_URLS:
            url = f"{base_url}{STREAM_ENDPOINT}"
            try:
                resp = requests.post(
                    url,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {token}",
                        "User-Agent": ANTIGRAVITY_USER_AGENT,
                    },
                    json=request_body,
                    timeout=120,
                )

                if resp.status_code == 429:
                    last_error = f"Rate limited (429) from {base_url}"
                    continue
                if resp.status_code >= 500:
                    last_error = f"Server error ({resp.status_code}) from {base_url}"
                    continue

                resp.raise_for_status()

                # 응답 파싱
                return self._parse_response(resp.text)

            except requests.exceptions.RequestException as e:
                last_error = str(e)
                continue

        return {"success": False, "error": last_error or "All endpoints failed"}

    def _parse_response(self, raw: str) -> Dict:
        """Antigravity API 응답 파싱"""
        try:
            raw = raw.strip()
            if raw.startswith("["):
                chunks = json.loads(raw)
            elif raw.startswith("{"):
                chunks = [json.loads(raw)]
            else:
                return {"success": False, "error": "Invalid response format"}

            # 텍스트 추출
            texts = []
            for chunk in chunks:
                response = chunk.get("response", chunk)
                candidates = response.get("candidates", [])
                for candidate in candidates:
                    parts = candidate.get("content", {}).get("parts", [])
                    for part in parts:
                        if "text" in part:
                            texts.append(part["text"])

            content = "".join(texts)
            if content:
                return {"success": True, "content": content}
            else:
                return {"success": False, "error": "Empty response"}

        except json.JSONDecodeError as e:
            return {"success": False, "error": f"JSON parse error: {e}"}

    def logout(self):
        """로그아웃"""
        self.access_token = ""
        self.refresh_token = ""
        self.token_expires_at = 0
        self.email = ""
        self.project_id = ""
        _clear_auth()


# 싱글턴 인스턴스
_instance: Optional[AntigravityAuth] = None

def get_antigravity_auth() -> AntigravityAuth:
    """AntigravityAuth 싱글턴"""
    global _instance
    if _instance is None:
        _instance = AntigravityAuth()
    return _instance
