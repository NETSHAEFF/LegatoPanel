import re
import json
import time
import base64
import os
import random
import asyncio
from pathlib import Path
from urllib.parse import quote
from typing import Dict, Any, Optional, Tuple, List
from curl_cffi import requests
from curl_cffi.curl import CurlMime
from bs4 import BeautifulSoup

from .config import (
    URL_MAIN,
    URL_SHARE,
    URL_SETTINGS,
    URL_UPLOAD_TOKEN,
    URL_LOGIN_API,
    URL_CHART_UID,
    URL_REDACT_CONTENT,
    URL_FETCH_CONTENT,
    DEFAULT_HOST,
    DEFAULT_FINGERPRINT,
    UPLOAD_HOST_FALLBACK
)
from .fingerprint import (
    generate_actionfingerprint,
    get_browser_headers,
    parse_cookies_input,
    human_jitter
)
from .tor import (
    get_tor_proxy_url,
    auto_detect_tor_port,
    TorRoutingError,
    is_port_open,
    tor_manager
)
from .storage import add_log, get_tor_config

def check_response_for_suspension(text: str, status_code: int = 200) -> Tuple[bool, str]:
    """
    Detects if wvids.com response indicates account suspension:
    - Text phrases: 'account suspended', 'account is suspended', 'your account has been suspended', 'аккаунт заблокирован', 'account blocked'
    - Redirection to /suspended
    - 403 status with suspension notice
    NOTE: On wvids.com, <div class="status-message" style="display: none;"> contains "Account suspended"
    as part of the base template for all normal accounts. It must be ignored if hidden.
    """
    if not text:
        return False, ""

    t_lower = text.lower()

    # Direct JSON/API error fields
    if '"account suspended"' in t_lower or '"account_suspended"' in t_lower:
        return True, "Account suspended"

    # Strip hidden template elements if HTML
    clean_text = re.sub(
        r'<div[^>]*class=["\'][^"\']*status-message[^"\']*["\'][^>]*style=["\'][^"\']*display:\s*none[\s\S]*?</div>\s*</div>\s*</div>',
        '',
        text,
        flags=re.IGNORECASE
    )
    clean_lower = clean_text.lower()

    suspension_markers = [
        "your account has been suspended",
        "this account is suspended",
        "account is suspended",
        "аккаунт заблокирован",
        "аккаунт приостановлен",
        "account blocked",
        "/suspended",
        "account suspended"
    ]
    for marker in suspension_markers:
        if marker in clean_lower:
            return True, "Account suspended"

    if status_code == 403 and any(k in t_lower for k in ["suspend", "block", "bann"]):
        return True, "Account suspended"

    return False, ""

class WvidsClient:
    def __init__(
        self,
        cookies: Optional[Dict[str, str]] = None,
        proxy: Optional[str] = None,
        fingerprint: str = DEFAULT_FINGERPRINT,
        use_tor: Optional[bool] = None
    ):
        self.cookies = cookies or {}
        self.proxy = proxy.strip() if proxy else None
        self.fingerprint = fingerprint or DEFAULT_FINGERPRINT
        self._session: Optional[requests.Session] = None
        self._comment_session: Optional[requests.Session] = None
        self.cached_config: Optional[Dict[str, Any]] = None

        # Check Tor mode: defaults to database setting (enabled by default)
        tor_cfg = get_tor_config()
        self.use_tor = tor_cfg.get("enabled", True) if use_tor is None else use_tor
        self.tor_port = tor_cfg.get("port", 9050)
        self.tor_strict = tor_cfg.get("strict", True)

    def _get_proxies_dict(self) -> Optional[Dict[str, str]]:
        if self.use_tor:
            # Enforce Tor via socks5h for 0 DNS leak
            active_port = self.tor_port
            if not is_port_open("127.0.0.1", active_port):
                detected = auto_detect_tor_port()
                if detected:
                    active_port = detected
                else:
                    # Auto-start headless Tor in background!
                    add_log("INFO", "🧅 Попытка автоматического запуска автономного фонового Tor...")
                    if tor_manager.ensure_running(port=active_port or 9050):
                        active_port = tor_manager.get_status().get("port", 9050)
                    elif self.tor_strict:
                        msg = (
                            f"🧅 [TOR BLOCKED] Не удалось запустить Tor на порту {active_port or 9050}. "
                            "Включен строгий режим защиты от утечки IP."
                        )
                        add_log("ERROR", msg)
                        raise TorRoutingError(msg)

            tor_url = f"socks5h://127.0.0.1:{active_port}"
            return {
                "http": tor_url,
                "https": tor_url
            }

        if not self.proxy:
            return None
        # curl_cffi supports http, https, socks5, socks5h
        p = self.proxy
        if not (p.startswith("http://") or p.startswith("https://") or p.startswith("socks5://") or p.startswith("socks5h://")):
            p = f"http://{p}"
        return {
            "http": p,
            "https": p
        }

    def get_session(self) -> requests.Session:
        if self._session is None:
            proxies = self._get_proxies_dict()
            if self.use_tor and proxies:
                add_log("INFO", f"🧅 Инициализирована сессия через Tor: {proxies['http']} (DNS удаленный)")
            self._session = requests.Session(
                impersonate=self.fingerprint,
                proxies=proxies
            )
            # Set default headers
            headers = get_browser_headers(self.fingerprint)
            self._session.headers.update(headers)
            # Add cookies to both wvids.com and default jar
            for k, v in self.cookies.items():
                self._session.cookies.set(k, v, domain="wvids.com")
                self._session.cookies.set(k, v, domain=".wvids.com")
                self._session.cookies.set(k, v)

        return self._session

    def get_comment_session(self) -> requests.Session:
        """
        Returns a session configured specifically for posting comments to wvids.com.
        NOTE: 'auth_sess' must be excluded because wvids.com comments.php has a PHP 8
        type crash (TypeError in getUserBlockStatus) when looking up auth_sess, whereas
        authenticating solely via auth_user_id + auth_p avoids this bug and succeeds.
        """
        if self._comment_session is None:
            proxies = self._get_proxies_dict()
            sess = requests.Session(
                impersonate=self.fingerprint,
                proxies=proxies
            )
            sess.headers.update(get_browser_headers(self.fingerprint))
            for k, v in self.cookies.items():
                if k == "auth_sess":
                    continue
                sess.cookies.set(k, v, domain="wvids.com")
                sess.cookies.set(k, v, domain=".wvids.com")
                sess.cookies.set(k, v)
            self._comment_session = sess
        return self._comment_session

    def reset_session(self):
        if self._session:
            try:
                self._session.close()
            except Exception:
                pass
            self._session = None
        if self._comment_session:
            try:
                self._comment_session.close()
            except Exception:
                pass
            self._comment_session = None

    def update_cookies(self, new_cookies: Dict[str, str]):
        self.cookies.update(new_cookies)
        self.reset_session()

    async def verify_session(self, fallback_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Verifies whether current cookies are logged in,
        and extracts userpageid (uid) and username with multi-layer fallback.
        """
        session = self.get_session()
        await human_jitter(0.2, 0.4)

        if not self.cookies:
            return {
                "valid": False,
                "uid": None,
                "username": None,
                "error": "Введенные cookies пусты. Вставьте строку cookie или JSON."
            }

        candidate_uid = None
        if "auth_user_id" in self.cookies:
            clean_str = re.sub(r"\D", "", str(self.cookies["auth_user_id"]))
            if clean_str:
                candidate_uid = int(clean_str)

        uid: Optional[int] = None
        username: Optional[str] = None
        app_token: Optional[str] = None
        jwt_uid_null = False

        # Attempt verification with automatic Tor recovery if circuit is blocked
        for attempt in range(2):
            try:
                # 1. Primary probe: URL_SHARE (https://wvids.com/share)
                # wvids.com exposes window.config with apiKey (JWT containing uid) and username on /share
                res_share = session.get(URL_SHARE, timeout=15)
                html_share = res_share.text

                is_susp, susp_reason = check_response_for_suspension(html_share, res_share.status_code)
                if is_susp:
                    add_log("ERROR", f"🚨 Session verification: Аккаунт заблокирован на wvids.com ({susp_reason})")
                    return {
                        "valid": False,
                        "is_suspended": True,
                        "error": susp_reason,
                        "status_code": res_share.status_code
                    }

                if res_share.status_code == 200:
                    cfg_match = re.search(r'window\.config\s*=\s*window\.config\s*\|\|\s*({[\s\S]*?});', html_share)
                    if cfg_match:
                        raw_cfg = cfg_match.group(1)
                        # Extract username
                        un_match = re.search(r'username:\s*["\']([^"\']+)["\']', raw_cfg)
                        if un_match and un_match.group(1).strip():
                            username = un_match.group(1).strip()

                        # Extract apiKey and decode JWT
                        k_match = re.search(r'apiKey:\s*["\']([^"\']+)["\']', raw_cfg)
                        if k_match:
                            jwt_payload = self._decode_jwt_payload(k_match.group(1))
                            if jwt_payload:
                                if jwt_payload.get("uid") is not None:
                                    uid = int(jwt_payload["uid"])
                                else:
                                    jwt_uid_null = True

                    # Also check HTML tags for username
                    if not username:
                        footer_login = re.search(r'class=["\'][^"\']*FooterTop-Login[^"\']*["\'][^>]*>\s*([^<\s]+)\s*<', html_share)
                        if footer_login:
                            cand = footer_login.group(1).strip()
                            if cand and cand.lower() not in ["login", "вход", "войти", "share"]:
                                username = cand

                    if not username:
                        login_match = re.search(r'class=["\']headerUser__login["\'][^>]*>\s*([^<\s]+)\s*<', html_share)
                        if login_match:
                            username = login_match.group(1).strip()

                # 2. Secondary probe: URL_SETTINGS (https://wvids.com/settings)
                res_settings = session.get(URL_SETTINGS, timeout=15)
                html_settings = res_settings.text

                is_susp, susp_reason = check_response_for_suspension(html_settings, res_settings.status_code)
                if is_susp:
                    add_log("ERROR", f"🚨 Session verification: Аккаунт заблокирован на wvids.com ({susp_reason})")
                    return {
                        "valid": False,
                        "is_suspended": True,
                        "error": susp_reason,
                        "status_code": res_settings.status_code
                    }

                if res_settings.status_code == 200:
                    if not uid:
                        uid_match = re.search(r'(?:userpageid|uid)\s*=\s*(\d+)', html_settings)
                        if uid_match:
                            uid = int(uid_match.group(1))

                    if not username:
                        # Must strictly require @ and exclude known page title names
                        title_match = re.search(r'<title>@([a-zA-Z0-9_\-\.]+)</title>', html_settings)
                        if title_match:
                            cand = title_match.group(1).strip()
                            if cand and cand.lower() not in ["404 not found", "wvids", "share", "settings", "just a moment..."]:
                                username = cand

                    if not username:
                        login_match = re.search(r'class=["\']headerUser__login["\'][^>]*>\s*([^<\s]+)\s*<', html_settings)
                        if login_match:
                            username = login_match.group(1).strip()

                # 3. Tertiary probe: URL_UPLOAD_TOKEN (https://wvids.com/api/upload_token.php)
                if not uid:
                    try:
                        res_token = session.post(
                            URL_UPLOAD_TOKEN,
                            headers={"Accept": "application/json", "Referer": URL_SHARE},
                            timeout=10
                        )
                        if res_token.status_code == 200:
                            t_data = res_token.json()
                            if t_data.get("key"):
                                payload = self._decode_jwt_payload(t_data["key"])
                                if payload and payload.get("uid") is not None:
                                    uid = int(payload["uid"])
                                elif payload and payload.get("uid") is None:
                                    jwt_uid_null = True
                    except Exception:
                        pass

                # 4. If username still missing, check URL_MAIN (https://wvids.com/)
                if not username and uid:
                    try:
                        res_main = session.get(URL_MAIN, timeout=12)
                        html_main = res_main.text
                        m_login = re.search(r'class=["\']headerUser__login["\'][^>]*>\s*([^<\s]+)\s*<', html_main)
                        if m_login:
                            username = m_login.group(1).strip()
                        m_href = re.search(r'href=["\'][^"\']*/@([a-zA-Z0-9_\-]+)["\']', html_main)
                        if m_href and not username:
                            username = m_href.group(1).strip()
                    except Exception:
                        pass

                # If candidate_uid is available from cookie auth_user_id and session was recognized
                if not uid and candidate_uid and (username or not jwt_uid_null):
                    uid = candidate_uid

                # Check if Tor exit node might be returning 404 or block page
                if attempt == 0 and res_share.status_code >= 400 and res_settings.status_code >= 400:
                    add_log("WARNING", f"🧅 Узел Tor вернул HTTP {res_share.status_code} на wvids.com. Перестроение цепи Tor...")
                    tor_manager.new_circuit()
                    self.reset_session()
                    session = self.get_session()
                    await human_jitter(1.0, 1.5)
                    continue

                break

            except Exception as e:
                if attempt == 0:
                    add_log("WARNING", f"🧅 Сбой связи через Tor ({e}). Попытка смены цепи...")
                    tor_manager.new_circuit()
                    self.reset_session()
                    session = self.get_session()
                    await human_jitter(1.0, 1.5)
                    continue
                else:
                    add_log("ERROR", f"Session verification error: {str(e)}")
                    return {
                        "valid": False,
                        "uid": None,
                        "username": None,
                        "error": f"Ошибка соединения через Tor: {str(e)}"
                    }

        # 5. Final analysis
        if jwt_uid_null and not uid:
            error_msg = (
                "Сессия не авторизована на wvids.com: куки не подошли или устарели. "
                "Пожалуйста, авторизуйтесь в аккаунте на wvids.com заново и скопируйте свежие cookies."
            )
            add_log("WARNING", "Session verification: rejected by wvids (jwt uid is null)")
            return {
                "valid": False,
                "uid": None,
                "username": None,
                "error": error_msg
            }

        # Fallback for username if UID is valid but username tag was not found
        if uid and not username:
            if fallback_name and fallback_name.strip():
                username = fallback_name.strip().lstrip("@")
            elif "username" in self.cookies and self.cookies["username"].strip():
                username = self.cookies["username"].strip().lstrip("@")
            else:
                username = f"user_{uid}"

        is_valid = bool(uid and username)
        add_log(
            "INFO" if is_valid else "WARNING",
            f"Session verification: valid={is_valid}, username={username}, uid={uid}"
        )

        if not is_valid:
            error_msg = (
                "Не удалось авторизовать сессию на wvids.com. "
                "Проверьте правильность и актуальность cookies (auth_user_id, auth_sess, auth_p)."
            )
            return {
                "valid": False,
                "uid": uid,
                "username": username,
                "error": error_msg
            }

        return {
            "valid": True,
            "uid": uid,
            "username": username,
            "app_token": app_token,
            "status_code": 200
        }

    def _decode_jwt_payload(self, token: str) -> Optional[Dict[str, Any]]:
        try:
            if not token or not isinstance(token, str):
                return None
            parts = token.split(".")
            if len(parts) < 2:
                return None
            b64 = parts[0].replace("-", "+").replace("_", "/")
            b64 += "=" * ((4 - len(b64) % 4) % 4)
            return json.loads(base64.b64decode(b64).decode("utf-8"))
        except Exception:
            return None

    async def get_statistics(self, uid: int, username: Optional[str] = None) -> Dict[str, Any]:
        """
        Fetches subscribers and views statistics for the given user.
        Uses POST /api/get_chart_uid.php
        """
        session = self.get_session()
        await human_jitter(0.2, 0.6)

        safe_user = quote(str(username)) if username else ""
        referrer = f"https://wvids.com/@{safe_user}" if safe_user else URL_MAIN
        payload = {
            "redact": "userstatistics",
            "id": "false",
            "uid": str(uid),
            "referrer": referrer,
            "timezone": "Europe/Moscow"
        }

        try:
            res = session.post(
                URL_CHART_UID,
                data=payload,
                headers={
                    "Referer": referrer,
                    "X-Requested-With": "XMLHttpRequest",
                    "Content-Type": "application/x-www-form-urlencoded"
                },
                timeout=15
            )

            is_susp, susp_reason = check_response_for_suspension(res.text, res.status_code)
            if is_susp:
                add_log("ERROR", f"🚨 Статистика: Аккаунт заблокирован на wvids.com ({susp_reason})")
                return {"success": False, "is_suspended": True, "error": susp_reason}

            if res.status_code != 200:
                add_log("WARNING", f"Stats request failed with status {res.status_code}")
                return {"success": False, "error": f"HTTP {res.status_code}"}

            data = res.json()
            html_data = data.get("data", "")
            js_script = data.get("js", "")

            # 1. Parse subscribers from HTML
            subscribers = 0
            # Matches: <div ... class="stat">0</div>
            sub_matches = re.findall(r'Subscribers[\s\S]*?<div[^>]*class=["\']stat["\'][^>]*>\s*([0-9,]+)\s*</div>', html_data)
            if sub_matches:
                subscribers = int(sub_matches[0].replace(",", ""))
            else:
                # Fallback to any stat class
                any_stat = re.findall(r'class=["\']stat["\'][^>]*>\s*([0-9,]+)\s*</div>', html_data)
                if any_stat:
                    subscribers = int(any_stat[0].replace(",", ""))

            # 2. Parse daily views and chart from JS
            labels = []
            views_series = []

            labels_match = re.search(r'labels:\s*(\[[^\]]*\])', js_script)
            if labels_match:
                try:
                    labels = json.loads(labels_match.group(1))
                except Exception:
                    pass

            data_match = re.search(r'data:\s*(\[[^\]]*\])', js_script)
            if data_match:
                try:
                    views_series = json.loads(data_match.group(1))
                except Exception:
                    pass

            views_today = views_series[-1] if views_series else 0
            total_views = sum(views_series) if views_series else 0

            # Build history list
            history = []
            for i in range(min(len(labels), len(views_series))):
                history.append({
                    "date": str(labels[i]),
                    "views": views_series[i]
                })

            add_log("SUCCESS", f"Stats fetched: {subscribers} subs, {views_today} today, {total_views} total")

            return {
                "success": True,
                "uid": uid,
                "username": username,
                "subscribers": subscribers,
                "views_today": views_today,
                "total_views": total_views,
                "chart_labels": labels,
                "chart_data": views_series,
                "history": history,
                "raw_html": html_data
            }

        except Exception as e:
            add_log("ERROR", f"Failed to get statistics: {str(e)}")
            return {
                "success": False,
                "error": str(e)
            }

    async def change_nickname(self, new_nickname: str) -> Dict[str, Any]:
        """
        Changes the account nickname / login.
        Sends POST /api/login.php with type=savesettings and exact form fields:
        user=<new_nickname>&mail=...&hidemail=...
        """
        session = self.get_session()
        await human_jitter(0.3, 0.8)

        new_nickname = new_nickname.strip().lstrip("@")
        if not new_nickname:
            return {"success": False, "error": "Никнейм не может быть пустым"}

        # 1. Fetch current settings to read existing mail and hidemail fields
        mail_val = ""
        hidemail_val = ""
        try:
            r_set = session.get(URL_SETTINGS, timeout=12)
            if r_set.status_code == 200:
                m_mail = re.search(r'name=["\']mail["\']\s+value=["\']([^"\']*)["\']', r_set.text)
                if m_mail: mail_val = m_mail.group(1)
                m_hide = re.search(r'name=["\']hidemail["\']\s+value=["\']([^"\']*)["\']', r_set.text)
                if m_hide: hidemail_val = m_hide.group(1)
        except Exception:
            pass

        action_fp = generate_actionfingerprint()

        # Form data matching exact #getform in settings page
        payload = {
            "user": new_nickname,
            "mail": mail_val,
            "hidemail": hidemail_val,
            "type": "savesettings",
            "fa": json.dumps(action_fp)
        }

        try:
            res = session.post(
                URL_LOGIN_API,
                data=payload,
                headers={
                    "Referer": URL_SETTINGS,
                    "Origin": "https://wvids.com",
                    "X-Requested-With": "XMLHttpRequest",
                    "Content-Type": "application/x-www-form-urlencoded"
                },
                timeout=15
            )

            response_json = None
            try:
                response_json = res.json()
            except Exception:
                pass

            if response_json:
                res_type = response_json.get("type", "")
                if "savesettings-successful" in res_type or response_json.get("success") is True:
                    self.reset_session()
                    add_log("SUCCESS", f"Никнейм успешно изменен на: @{new_nickname}")
                    return {"success": True, "new_nickname": new_nickname, "message": f"Никнейм успешно изменен на @{new_nickname}!"}
                if "savesettings-error" in res_type:
                    err_msg = response_json.get("message") or response_json.get("text") or response_json.get("error") or "Ошибка сохранения настроек"
                    add_log("WARNING", f"Ошибка смены никнейма: {err_msg}")
                    return {"success": False, "error": str(err_msg)}

            # Fallback verification
            self.reset_session()
            verify_res = await self.verify_session()
            if verify_res.get("username") == new_nickname:
                add_log("SUCCESS", f"Никнейм подтвержден как @{new_nickname}")
                return {"success": True, "new_nickname": new_nickname, "message": f"Никнейм успешно изменен на @{new_nickname}!"}

            if "input_error" in res.text or "error" in res.text.lower():
                return {"success": False, "error": "Сервер отклонил новый никнейм"}

            return {
                "success": True,
                "new_nickname": new_nickname,
                "message": f"Никнейм успешно изменен на @{new_nickname}!"
            }

        except Exception as e:
            add_log("ERROR", f"Error during nickname change: {str(e)}")
            return {"success": False, "error": str(e)}

    async def get_upload_config(self) -> Dict[str, Any]:
        """
        Parses window.config from /share page and gets fresh upload token.
        """
        session = self.get_session()
        await human_jitter(0.2, 0.5)

        config = {
            "uploadUrl": f"{UPLOAD_HOST_FALLBACK}/api/upload.php",
            "apiUrl": f"{UPLOAD_HOST_FALLBACK}/api/share.php",
            "tokenUrl": URL_UPLOAD_TOKEN,
            "apiKey": None,
            "username": None
        }

        try:
            res = session.get(URL_SHARE, timeout=15)
            if res.status_code == 200:
                html = res.text
                cfg_match = re.search(r'window\.config\s*=\s*window\.config\s*\|\|\s*({[\s\S]*?});', html)
                if cfg_match:
                    raw_cfg = cfg_match.group(1)
                    # Extract fields via regex
                    u_match = re.search(r'uploadUrl:\s*["\']([^"\']+)["\']', raw_cfg)
                    if u_match: config["uploadUrl"] = u_match.group(1)

                    a_match = re.search(r'apiUrl:\s*["\']([^"\']+)["\']', raw_cfg)
                    if a_match: config["apiUrl"] = a_match.group(1)

                    k_match = re.search(r'apiKey:\s*["\']([^"\']+)["\']', raw_cfg)
                    if k_match: config["apiKey"] = k_match.group(1)

                    t_match = re.search(r'tokenUrl:\s*["\']([^"\']+)["\']', raw_cfg)
                    if t_match: config["tokenUrl"] = t_match.group(1)

                    un_match = re.search(r'username:\s*["\']([^"\']+)["\']', raw_cfg)
                    if un_match: config["username"] = un_match.group(1)

            # If apiKey is still missing, refresh via tokenUrl
            if not config.get("apiKey"):
                token_url = config.get("tokenUrl") or URL_UPLOAD_TOKEN
                t_res = session.post(
                    token_url,
                    headers={"Accept": "application/json", "Referer": URL_SHARE},
                    timeout=10
                )
                if t_res.status_code == 200:
                    t_json = t_res.json()
                    if t_json.get("success") and t_json.get("key"):
                        config["apiKey"] = t_json["key"]

            self.cached_config = config
            return config

        except Exception as e:
            add_log("ERROR", f"Failed to get upload config: {str(e)}")
            return config

    async def get_all_user_posts(self, username: Optional[str] = None, max_pages: int = 50) -> List[Dict[str, Any]]:
        """
        Scans all published posts on the user's channel across all pagination pages
        (page=1, page=2, page=3, page=4, etc.).
        Returns list of posts with file_id, title, views, likes, type, url, page.
        """
        session = self.get_session()
        await human_jitter(0.2, 0.4)

        if not username:
            verify_res = await self.verify_session()
            username = verify_res.get("username")
            if not username:
                add_log("ERROR", "Не удалось определить имя пользователя для сканирования публикаций")
                return []

        all_posts: List[Dict[str, Any]] = []
        seen_fids = set()

        for page in range(1, max_pages + 1):
            url = f"https://wvids.com/@{username}" if page == 1 else f"https://wvids.com/@{username}?page={page}"
            try:
                res = session.get(
                    url,
                    headers={
                        "Referer": "https://wvids.com/",
                        "X-Requested-With": "XMLHttpRequest"
                    },
                    timeout=25
                )
                if res.status_code != 200:
                    add_log("WARNING", f"Страница {page} вернула статус {res.status_code}, завершение сканирования")
                    break

                soup = BeautifulSoup(res.text, "html.parser")
                post_tags = soup.find_all("a", class_="boxpost")

                if not post_tags:
                    # No more posts on this page
                    break

                page_new_count = 0
                for a in post_tags:
                    fid = str(a.get("id") or "").strip()
                    if not fid:
                        continue
                    if fid in seen_fids:
                        continue

                    seen_fids.add(fid)
                    page_new_count += 1

                    classes = a.get("class", [])
                    is_video = "tube" in classes
                    post_type = "video" if is_video else "photo"
                    post_slug = a.get("data-post") or a.get("href", "").lstrip("/")

                    title = ""
                    views = 0
                    likes = 0
                    raw_attr = a.get("data-raw")
                    if raw_attr:
                        try:
                            raw_list = json.loads(raw_attr)
                            if isinstance(raw_list, list):
                                if len(raw_list) > 8 and isinstance(raw_list[8], str):
                                    title = raw_list[8]
                                elif len(raw_list) > 0 and isinstance(raw_list[-1], str):
                                    title = raw_list[-1]
                                if len(raw_list) > 4 and isinstance(raw_list[4], (int, float)):
                                    views = int(raw_list[4])
                                if len(raw_list) > 6 and isinstance(raw_list[6], (int, float)):
                                    likes = int(raw_list[6])
                        except Exception:
                            pass

                    if not title:
                        cut_tag = a.find(class_="cut")
                        if cut_tag:
                            title = cut_tag.text.strip()
                        else:
                            title = a.text.strip()

                    all_posts.append({
                        "file_id": fid,
                        "type": post_type,
                        "title": title,
                        "slug": post_slug,
                        "url": f"https://wvids.com/{post_slug}" if post_slug else f"https://wvids.com/{fid}",
                        "views": views,
                        "likes": likes,
                        "page": page
                    })

                add_log("INFO", f"📄 Стр. {page}: найдено {len(post_tags)} постов (новых: {page_new_count})")

                if page_new_count == 0:
                    # All posts on this page were already seen, stop pagination
                    break

                # Polite jitter between pages
                await human_jitter(0.25, 0.6)

            except Exception as e:
                add_log("ERROR", f"Ошибка сканирования страницы {page}: {str(e)}")
                break

        add_log("SUCCESS", f"Сканирование завершено: найдено {len(all_posts)} публикаций на канале @{username}")
        return all_posts

    async def redact_post(
        self,
        file_id: str,
        title: Optional[str] = None,
        description: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Updates title and/or description of a specific post via POST /api/redact_content.php.
        """
        session = self.get_session()
        headers = {
            "Origin": "https://wvids.com",
            "Referer": "https://wvids.com/",
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/x-www-form-urlencoded"
        }

        fid = str(file_id).strip()
        updated_title = False
        updated_desc = False
        errors = []

        if title is not None:
            await human_jitter(0.15, 0.35)
            try:
                res_title = session.post(
                    URL_REDACT_CONTENT,
                    data={"text": title, "type": "title", "fileid": fid},
                    headers=headers,
                    timeout=15
                )
                t_json = {}
                try:
                    t_json = res_title.json()
                except Exception:
                    pass

                if res_title.status_code == 200 and t_json.get("type") == "savecontent-successful":
                    updated_title = True
                else:
                    err_msg = t_json.get("text") or res_title.text[:100]
                    errors.append(f"Ошибка названия: {err_msg}")
            except Exception as e:
                errors.append(f"Исключение при обновлении названия: {str(e)}")

        if description is not None:
            await human_jitter(0.15, 0.35)
            try:
                res_desc = session.post(
                    URL_REDACT_CONTENT,
                    data={"text": description, "type": "description", "fileid": fid},
                    headers=headers,
                    timeout=15
                )
                d_json = {}
                try:
                    d_json = res_desc.json()
                except Exception:
                    pass

                if res_desc.status_code == 200 and d_json.get("type") == "savecontent-successful":
                    updated_desc = True
                else:
                    err_msg = d_json.get("text") or res_desc.text[:100]
                    errors.append(f"Ошибка описания: {err_msg}")
            except Exception as e:
                errors.append(f"Исключение при обновлении описания: {str(e)}")

        success = len(errors) == 0 and (updated_title or updated_desc)
        return {
            "success": success,
            "file_id": fid,
            "updated_title": updated_title,
            "updated_description": updated_desc,
            "errors": errors
        }

    async def check_account_status(self, username: Optional[str] = None) -> Dict[str, Any]:
        """
        Directly queries wvids.com to check whether account is active, suspended, or invalid.
        """
        session = self.get_session()
        await human_jitter(0.2, 0.4)
        try:
            r = session.get(URL_SETTINGS, timeout=12)
            is_susp, reason = check_response_for_suspension(r.text, r.status_code)
            if is_susp:
                add_log("ERROR", f"🚨 [ПРОВЕРКА СТАТУСА] Аккаунт заблокирован на wvids.com: {reason}")
                return {"status": "suspended", "is_suspended": True, "reason": reason}

            if username:
                r_user = session.get(f"https://wvids.com/@{username}", timeout=12)
                is_susp2, reason2 = check_response_for_suspension(r_user.text, r_user.status_code)
                if is_susp2:
                    add_log("ERROR", f"🚨 [ПРОВЕРКА СТАТУСА] Аккаунт @{username} заблокирован: {reason2}")
                    return {"status": "suspended", "is_suspended": True, "reason": reason2}

            # Check if authenticated via share page (primary endpoint)
            r_share = session.get(URL_SHARE, timeout=12)
            is_susp_s, reason_s = check_response_for_suspension(r_share.text, r_share.status_code)
            if is_susp_s:
                add_log("ERROR", f"🚨 [ПРОВЕРКА СТАТУСА] Аккаунт заблокирован на wvids.com: {reason_s}")
                return {"status": "suspended", "is_suspended": True, "reason": reason_s}

            if r_share.status_code == 200:
                cfg_match = re.search(r'window\.config\s*=\s*window\.config\s*\|\|\s*({[\s\S]*?});', r_share.text)
                if cfg_match:
                    k_match = re.search(r'apiKey:\s*["\']([^"\']+)["\']', cfg_match.group(1))
                    if k_match:
                        payload = self._decode_jwt_payload(k_match.group(1))
                        if payload and payload.get("uid") is not None:
                            return {"status": "active", "is_suspended": False, "reason": ""}

            # Check if authenticated via settings
            if r.status_code == 200 and re.search(r'(?:userpageid|uid)\s*=\s*\d+', r.text):
                return {"status": "active", "is_suspended": False, "reason": ""}

            return {"status": "unauthorized", "is_suspended": False, "reason": "Сессия не авторизована"}
        except Exception as e:
            return {"status": "error", "is_suspended": False, "error": str(e)}

    async def get_popular_videos(
        self,
        min_views: int = 0,
        max_pages: int = 0,
        stop_check: Optional[Any] = None,
        progress_callback: Optional[Any] = None
    ) -> List[Dict[str, Any]]:
        """
        Scans all pages of 'Популярное' (top) on wvids.com via /api/fetch_content.php.
        Filters for videos (type == 1) with views >= min_views.
        max_pages: maximum pages to scan (0 = scan all pages until end).
        """
        session = self.get_session()
        await human_jitter(0.2, 0.4)

        matching_videos: List[Dict[str, Any]] = []
        seen_fids = set()
        cursor = None
        page = 0

        while True:
            if stop_check and stop_check():
                add_log("WARNING", "⏹️ Сканирование раздела 'Популярное' прервано по требованию")
                break

            page += 1
            if max_pages > 0 and page > max_pages:
                break

            url = f"{URL_FETCH_CONTENT}?categories=top"
            if cursor is not None:
                url += f"&cursor={quote(json.dumps(cursor))}"

            try:
                res = session.get(
                    url,
                    headers={
                        "Referer": "https://wvids.com/",
                        "X-Requested-With": "XMLHttpRequest"
                    },
                    timeout=25
                )
                if res.status_code != 200:
                    add_log("WARNING", f"Популярное: страница {page} вернула статус {res.status_code}")
                    break

                data = res.json()
                raw_items = data.get("i", [])
                has_more = bool(data.get("h", 0))
                cursor = data.get("n")

                if not raw_items:
                    break

                page_matches = 0
                for it in raw_items:
                    if not isinstance(it, list) or len(it) < 7:
                        continue

                    # it[0]: 1=video, 0=image
                    post_type_code = it[0]
                    if post_type_code != 1:
                        # Skip images / photos
                        continue

                    fid = str(it[1])
                    if fid in seen_fids:
                        continue
                    seen_fids.add(fid)

                    slug = str(it[2]) if len(it) > 2 else fid
                    user_id = it[3] if len(it) > 3 else None
                    duration = it[4] if len(it) > 4 else 0
                    views = int(it[6]) if len(it) > 6 and it[6] is not None else 0
                    comments = int(it[7]) if len(it) > 7 and it[7] is not None else 0
                    title = str(it[8]).strip() if len(it) > 8 and it[8] else ""

                    if views >= min_views:
                        video_obj = {
                            "file_id": fid,
                            "slug": slug,
                            "title": title,
                            "views": views,
                            "comments": comments,
                            "duration": duration,
                            "user_id": user_id,
                            "post_url": f"https://wvids.com/{slug}",
                            "page": page
                        }
                        matching_videos.append(video_obj)
                        page_matches += 1

                if progress_callback:
                    try:
                        progress_callback(page, len(matching_videos), len(seen_fids), has_more)
                    except Exception:
                        pass

                add_log("INFO", f"📄 Популярное [Стр. {page}]: просмотрено {len(raw_items)} элементов, подходит по просмотрам: {page_matches} (Всего: {len(matching_videos)})")

                if not has_more or not cursor:
                    # No more pages available
                    break

                await human_jitter(0.2, 0.4)

            except Exception as e:
                add_log("ERROR", f"Ошибка запроса страницы {page} раздела Популярное: {str(e)}")
                break

        add_log("SUCCESS", f"Сканирование 'Популярное' завершено: найдено {len(matching_videos)} видео с просмотрами >= {min_views} (всего просканировано стр.: {page})")
        return matching_videos

    async def get_video_download_url(self, slug_or_id: str) -> Optional[str]:
        """
        Fetches the post page on wvids.com, parses streamToken and idgh,
        and constructs the direct video stream/download URL.
        """
        session = self.get_session()
        await human_jitter(0.1, 0.3)

        post_url = f"https://wvids.com/{slug_or_id}" if not str(slug_or_id).startswith("http") else str(slug_or_id)
        try:
            r = session.get(
                post_url,
                headers={"Referer": "https://wvids.com/"},
                timeout=20
            )
            if r.status_code != 200:
                add_log("WARNING", f"Не удалось открыть страницу видео {post_url}: статус {r.status_code}")
                return None

            html = r.text

            st_match = re.search(r'streamToken\s*=\s*["\']([^"\']+)["\']', html)
            stream_token = st_match.group(1) if st_match else None

            ss_match = re.search(r'securitySigner\s*=\s*["\']([^"\']+)["\']', html)
            security_signer = ss_match.group(1) if ss_match else None

            idgh_match = re.search(r'idgh\s*=\s*["\']([^"\']+)["\']', html)
            idgh = idgh_match.group(1) if idgh_match else None

            if not idgh:
                add_log("WARNING", f"На странице {post_url} не найден параметр idgh")
                return None

            try:
                cdn_map = json.loads(base64.b64decode(idgh).decode("utf-8"))
            except Exception as e:
                add_log("ERROR", f"Ошибка декодирования idgh: {e}")
                return None

            cdn_host = cdn_map.get("d") or cdn_map.get("m") or cdn_map.get("f")
            if not cdn_host:
                add_log("ERROR", f"CDN хост не найден в idgh: {cdn_map}")
                return None

            # Primary stream URL: streamToken
            if stream_token:
                return f"{cdn_host}/s/{stream_token}"

            # Fallback: request token via securitySigner
            if security_signer:
                tok_url = f"{cdn_host}/t/{security_signer}"
                r_tok = session.get(tok_url, headers={"Referer": "https://wvids.com/"}, timeout=15)
                if r_tok.status_code == 200:
                    tok_data = r_tok.json()
                    tok = tok_data.get("token")
                    if tok:
                        return f"{cdn_host}/s/{tok}"

            return None
        except Exception as e:
            add_log("ERROR", f"Ошибка извлечения ссылки на скачивание видео {slug_or_id}: {e}")
            return None

    async def download_video_file(
        self,
        video_url: str,
        dest_path: Path,
        on_progress: Optional[Any] = None,
        stop_check: Optional[Any] = None,
        timeout: int = 300
    ) -> bool:
        """
        Streams video content directly to dest_path via a temporary .part file.
        Atomically replaces dest_path when complete.
        """
        session = self.get_session()
        part_path = dest_path.with_name(f"{dest_path.name}.part")

        dest_path.parent.mkdir(parents=True, exist_ok=True)

        for attempt in range(1, 3):
            if stop_check and stop_check():
                if part_path.exists():
                    try:
                        part_path.unlink()
                    except Exception:
                        pass
                return False

            try:
                res = session.get(
                    video_url,
                    headers={"Referer": "https://wvids.com/"},
                    timeout=timeout,
                    stream=True
                )
                if res.status_code != 200:
                    add_log("WARNING", f"Попытка {attempt}: CDN вернул статус {res.status_code} для {video_url[:60]}...")
                    continue

                total_size = int(res.headers.get("content-length") or 0)
                downloaded = 0

                with open(part_path, "wb") as f:
                    for chunk in res.iter_content(chunk_size=256 * 1024):
                        if stop_check and stop_check():
                            break
                        if chunk:
                            f.write(chunk)
                            downloaded += len(chunk)
                            if on_progress:
                                try:
                                    on_progress(downloaded, total_size)
                                except Exception:
                                    pass
                        await asyncio.sleep(0)

                if stop_check and stop_check():
                    if part_path.exists():
                        try:
                            part_path.unlink()
                        except Exception:
                            pass
                    return False

                if total_size > 0 and downloaded < total_size:
                    add_log("WARNING", f"Попытка {attempt}: получено только {downloaded}/{total_size} байт, повтор...")
                    continue

                # Finalize file atomically
                if part_path.exists():
                    if dest_path.exists():
                        dest_path.unlink()
                    part_path.rename(dest_path)
                    return True

            except Exception as e:
                add_log("WARNING", f"Попытка {attempt} загрузки видео завершилась ошибкой: {e}")
                if attempt < 2:
                    await asyncio.sleep(1.5)

        if part_path.exists():
            try:
                part_path.unlink()
            except Exception:
                pass
        return False

    async def get_new_videos(
        self,
        min_views: int = 0,
        max_views: int = 0,
        max_pages: int = 3,
        stop_check: Optional[Any] = None
    ) -> List[Dict[str, Any]]:
        """
        Scans pages of fresh videos (categories=videos) on wvids.com via /api/fetch_content.php.
        Filters for videos (type == 1) with views >= min_views and views <= max_views (if max_views > 0).
        """
        session = self.get_session()
        await human_jitter(0.2, 0.4)

        matching_videos: List[Dict[str, Any]] = []
        seen_fids = set()
        cursor = None
        page = 0

        while True:
            if stop_check and stop_check():
                break

            page += 1
            if max_pages > 0 and page > max_pages:
                break

            url = f"{URL_FETCH_CONTENT}?categories=videos"
            if cursor is not None:
                url += f"&cursor={quote(json.dumps(cursor))}"

            try:
                res = session.get(
                    url,
                    headers={
                        "Referer": "https://wvids.com/mov",
                        "X-Requested-With": "XMLHttpRequest"
                    },
                    timeout=25
                )
                if res.status_code != 200:
                    break

                data = res.json()
                raw_items = data.get("i", [])
                has_more = bool(data.get("h", 0))
                cursor = data.get("n")

                if not raw_items:
                    break

                for it in raw_items:
                    if not isinstance(it, list) or len(it) < 7:
                        continue

                    # it[0]: 1=video, 0=image
                    if it[0] != 1:
                        continue

                    fid = str(it[1])
                    if fid in seen_fids:
                        continue
                    seen_fids.add(fid)

                    slug = str(it[2]) if len(it) > 2 else fid
                    user_id = it[3] if len(it) > 3 else None
                    duration = it[4] if len(it) > 4 else 0
                    views = int(it[6]) if len(it) > 6 and it[6] is not None else 0
                    comments = int(it[7]) if len(it) > 7 and it[7] is not None else 0
                    title = str(it[8]).strip() if len(it) > 8 and it[8] else ""

                    if views >= min_views:
                        if max_views > 0 and views > max_views:
                            continue
                        video_obj = {
                            "file_id": fid,
                            "slug": slug,
                            "title": title,
                            "views": views,
                            "comments": comments,
                            "duration": duration,
                            "user_id": user_id,
                            "post_url": f"https://wvids.com/{slug}",
                            "page": page
                        }
                        matching_videos.append(video_obj)

                if not has_more or not cursor:
                    break

                await human_jitter(0.2, 0.4)
            except Exception as e:
                add_log("WARNING", f"Ошибка получения раздела свежих видео (стр. {page}): {e}")
                break

        return matching_videos

    async def post_comment(
        self,
        fid: str,
        msg: str,
        slug: Optional[str] = None,
        reply: str = ""
    ) -> Dict[str, Any]:
        """
        Posts a comment to wvids.com video via /api/comments.php?write.
        """
        session = self.get_comment_session()
        await human_jitter(0.3, 0.6)

        referer = f"https://wvids.com/{slug}" if slug else "https://wvids.com/"
        headers = {
            "Referer": referer,
            "Origin": "https://wvids.com",
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Requested-With": "XMLHttpRequest"
        }
        
        body = f"msg={quote(msg)}&fid={quote(str(fid))}&reply={quote(str(reply))}"
        
        try:
            res = session.post(
                f"{DEFAULT_HOST}/api/comments.php?write",
                data=body,
                headers=headers,
                timeout=25
            )
            
            try:
                data = res.json()
            except Exception:
                data = {"raw": res.text, "status_code": res.status_code}
                
            if res.status_code == 200 and data.get("success"):
                add_log("SUCCESS", f"💬 Оставлен комментарий на видео {fid}: '{msg[:40]}'")
                return {"success": True, "fid": fid, "msg": msg, "data": data}
            else:
                err = data.get("error") or data.get("message") or f"HTTP {res.status_code}"
                add_log("WARNING", f"⚠️ Ответ сервера при отправке комментария на видео {fid}: {err}")
                return {"success": False, "fid": fid, "error": err, "status_code": res.status_code}
        except Exception as e:
            err = f"Сетевой сбой при отправке комментария: {e}"
            add_log("ERROR", err)
            return {"success": False, "fid": fid, "error": err}

def parse_spintax(text: str) -> str:
    """Resolves nested or single {option1|option2|option3} spintax randomly."""
    pattern = re.compile(r'\{([^{}]+)\}')
    max_iter = 10
    while max_iter > 0:
        match = pattern.search(text)
        if not match:
            break
        choices = match.group(1).split('|')
        text = text[:match.start()] + random.choice(choices) + text[match.end():]
        max_iter -= 1
    return text

