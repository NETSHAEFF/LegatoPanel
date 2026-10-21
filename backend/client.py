import re
import json
import time
import base64
import os
import urllib.parse
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
from .storage import (
    add_log,
    get_tor_config,
    get_network_mode,
    get_vless_config
)
from .vless import vless_manager

def check_response_for_suspension(text: str, status_code: int = 200) -> Tuple[bool, str]:
    """
    Detects if wvids.com response indicates account suspension:
    - Text phrases: 'account suspended', 'account is suspended', 'your account has been suspended', 'аккаунт заблокирован', 'account blocked'
    - Redirection to /suspended
    - 403 status with suspension notice
    """
    if not text:
        return False, ""

    t_lower = text.lower()
    suspension_markers = [
        "account suspended",
        "account is suspended",
        "your account has been suspended",
        "this account is suspended",
        "account suspended.",
        "аккаунт заблокирован",
        "аккаунт приостановлен",
        "account blocked",
        "/suspended"
    ]
    for marker in suspension_markers:
        if marker in t_lower:
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
        self.cached_config: Optional[Dict[str, Any]] = None

        # Check Tor mode: defaults to database setting (enabled by default)
        tor_cfg = get_tor_config()
        self.use_tor = tor_cfg.get("enabled", True) if use_tor is None else use_tor
        self.tor_port = tor_cfg.get("port", 9050)
        self.tor_strict = tor_cfg.get("strict", True)

    def _get_proxies_dict(self) -> Optional[Dict[str, str]]:
        network_mode = get_network_mode()

        # 1. VLESS VPN Mode (Recommended - German Exit Node via Xray)
        if network_mode == "vless":
            vless_cfg = get_vless_config()
            v_port = vless_cfg.get("port", 10808)
            v_host = vless_cfg.get("host", "127.0.0.1")
            if not is_port_open(v_host, v_port):
                # Auto-start standalone xray with vpn.txt if port not listening
                vless_manager.ensure_running(port=v_port)

            vless_url = f"socks5h://{v_host}:{v_port}"
            return {
                "http": vless_url,
                "https": vless_url
            }

        # 2. Tor Network Mode
        if network_mode == "tor" or (network_mode not in ("direct", "proxy") and self.use_tor):
            active_port = self.tor_port
            if not is_port_open("127.0.0.1", active_port):
                detected = auto_detect_tor_port()
                if detected:
                    active_port = detected
                else:
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

        # 3. Custom Proxy per Account
        if network_mode == "proxy" or (network_mode != "direct" and self.proxy):
            if not self.proxy:
                return None
            p = self.proxy
            if not (p.startswith("http://") or p.startswith("https://") or p.startswith("socks5://") or p.startswith("socks5h://")):
                p = f"http://{p}"
            return {
                "http": p,
                "https": p
            }

        # 4. Direct mode (no proxy)
        return None

    def get_session(self) -> requests.Session:
        if self._session is None:
            proxies = self._get_proxies_dict()
            network_mode = get_network_mode()
            if proxies:
                label = "VLESS Reality (Германия)" if network_mode == "vless" else ("Tor" if network_mode == "tor" else "Proxy")
                add_log("INFO", f"🌐 Сессия инициализирована через {label}: {proxies['http']} (Zero DNS leak)")
            else:
                add_log("INFO", "🌐 Сессия инициализирована напрямую (Direct / без прокси)")

            self._session = requests.Session(
                impersonate=self.fingerprint,
                proxies=proxies
            )
            # Set default headers
            headers = get_browser_headers(self.fingerprint)
            self._session.headers.update(headers)
            # Add cookies
            for k, v in self.cookies.items():
                self._session.cookies.set(k, v, domain="wvids.com")

        return self._session

    def reset_session(self):
        if self._session:
            try:
                self._session.close()
            except Exception:
                pass
            self._session = None

    def update_cookies(self, new_cookies: Dict[str, str]):
        self.cookies.update(new_cookies)
        self.reset_session()

    async def verify_session(self) -> Dict[str, Any]:
        """
        Verifies whether current cookies are logged in,
        and extracts userpageid (uid) and username.
        """
        session = self.get_session()
        await human_jitter(0.2, 0.5)

        try:
            # 1. First probe wvids.com/settings
            res = session.get(URL_SETTINGS, timeout=15)
            html = res.text

            is_susp, susp_reason = check_response_for_suspension(html, res.status_code)
            if is_susp:
                add_log("ERROR", f"🚨 Session verification: Аккаунт заблокирован на wvids.com ({susp_reason})")
                return {
                    "valid": False,
                    "is_suspended": True,
                    "error": susp_reason,
                    "status_code": res.status_code
                }

            # Look for uid in script: let userpageid = 3195151; or uid = 3195151;
            uid_match = re.search(r'(?:userpageid|uid)\s*=\s*(\d+)', html)
            uid = int(uid_match.group(1)) if uid_match else None

            # Look for username in <title>@username</title> or .headerUser__login or FooterTop-Login
            username = None
            title_match = re.search(r'<title>@?([^<]+)</title>', html)
            if title_match and title_match.group(1).strip() and title_match.group(1).strip() != "@":
                username = title_match.group(1).strip()

            login_match = re.search(r'class=["\']headerUser__login["\'][^>]*>\s*([^<\s]+)\s*<', html)
            if login_match:
                username = login_match.group(1).strip()

            footer_login = re.search(r'class=["\'][^"\']*FooterTop-Login[^"\']*["\'][^>]*>\s*([^<\s]+)\s*<', html)
            if footer_login:
                username = footer_login.group(1).strip()

            # If not found on /settings, try main page
            if not uid or not username:
                res_main = session.get(URL_MAIN, timeout=15)
                html_main = res_main.text
                is_susp, susp_reason = check_response_for_suspension(html_main, res_main.status_code)
                if is_susp:
                    add_log("ERROR", f"🚨 Session verification: Аккаунт заблокирован на wvids.com ({susp_reason})")
                    return {
                        "valid": False,
                        "is_suspended": True,
                        "error": susp_reason,
                        "status_code": res_main.status_code
                    }
                if not uid:
                    m = re.search(r'(?:userpageid|uid)\s*=\s*(\d+)', html_main)
                    if m: uid = int(m.group(1))
                if not username:
                    m = re.search(r'class=["\']headerUser__login["\'][^>]*>\s*([^<\s]+)\s*<', html_main)
                    if m: username = m.group(1).strip()
                    m2 = re.search(r'href=["\'][^"\']*/@([a-zA-Z0-9_\-]+)["\']', html_main)
                    if m2 and not username:
                        username = m2.group(1).strip()

            # Extract APP_CONFIG token if available
            app_token = None
            cfg_match = re.search(r'window\.APP_CONFIG\s*=\s*({.*?});', html)
            if cfg_match:
                try:
                    cfg_json = json.loads(cfg_match.group(1))
                    app_token = cfg_json.get("token")
                except Exception:
                    pass

            is_valid = bool(uid and username)
            add_log("INFO" if is_valid else "WARNING", 
                    f"Session verification: valid={is_valid}, username={username}, uid={uid}")

            return {
                "valid": is_valid,
                "uid": uid,
                "username": username,
                "app_token": app_token,
                "status_code": res.status_code
            }

        except Exception as e:
            add_log("ERROR", f"Session verification error: {str(e)}")
            return {
                "valid": False,
                "uid": None,
                "username": None,
                "error": str(e)
            }

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

            # Check if authenticated
            if r.status_code == 200 and ("userpageid" in r.text or "uid" in r.text):
                return {"status": "active", "is_suspended": False, "reason": ""}

            return {"status": "unauthorized", "is_suspended": False, "reason": "Сессия не авторизована"}
        except Exception as e:
            return {"status": "error", "is_suspended": False, "error": str(e)}

    async def check_post_in_feed(self, fid: str, slug: Optional[str] = None) -> Dict[str, Any]:
        """
        Scans https://wvids.com/feed to check whether the post is visible to public users
        or filtered into a shadowban (0 distribution).
        """
        session = self.get_session()
        await human_jitter(0.2, 0.5)
        try:
            feed_url = f"{DEFAULT_HOST}/feed"
            res = session.get(feed_url, timeout=15)
            if res.status_code != 200:
                return {
                    "success": False,
                    "in_feed": False,
                    "fid": str(fid),
                    "error": f"HTTP {res.status_code} при запросе ленты /feed"
                }

            html = res.text
            clean_fid = str(fid).strip()
            # 1. Direct regex check for post ID in feed
            id_pattern = rf'id=[\'"](?:progress)?{re.escape(clean_fid)}[\'"]'
            found_by_id = bool(re.search(id_pattern, html))

            # 2. Check by slug if provided
            found_by_slug = False
            if slug:
                clean_slug = str(slug).strip()
                slug_pattern = rf'(?:data-post|href)=[\'"][^\'"]*{re.escape(clean_slug)}[\'"]'
                found_by_slug = bool(re.search(slug_pattern, html))

            # 3. Fallback: string presence of clean fid
            found_by_str = clean_fid in html

            in_feed = found_by_id or found_by_slug or found_by_str

            # Count total posts visible in feed
            visible_count = len(re.findall(r'class=["\']boxpost tube["\']', html))

            return {
                "success": True,
                "in_feed": in_feed,
                "fid": clean_fid,
                "visible_count": visible_count,
                "status_code": res.status_code
            }
        except Exception as e:
            return {
                "success": False,
                "in_feed": False,
                "fid": str(fid),
                "error": str(e)
            }

    async def get_popular_videos(self, max_items: int = 50) -> List[Dict[str, Any]]:
        """
        Scans https://wvids.com/ to find the dynamic Popular section link (/p-...)
        and parses popular videos across initial HTML and infinite scroll cursor API (/api/fetch_content.php).
        """
        session = self.get_session()
        await human_jitter(0.2, 0.4)
        items: List[Dict[str, Any]] = []
        seen_fids = set()

        try:
            # Step 1: Request home page to find popular link
            res_home = session.get(DEFAULT_HOST, timeout=15)
            if res_home.status_code != 200:
                add_log("ERROR", f"Не удалось загрузить главную страницу для раздела Популярное: HTTP {res_home.status_code}")
                return []

            home_html = res_home.text
            match = re.search(r'href=["\'](https://wvids\.com/p-[^"\']+|/p-[^"\']+)["\']', home_html)
            if not match:
                match = re.search(r'/(p-[a-zA-Z0-9=_-]+)', home_html)

            if not match:
                add_log("WARNING", "Не удалось найти ссылку на раздел Популярное на главной странице wvids.com")
                return []

            pop_href = match.group(1)
            pop_url = pop_href if pop_href.startswith("http") else f"{DEFAULT_HOST}/{pop_href.lstrip('/')}"

            # Step 2: Fetch popular page HTML to get first batch of videos
            res_page = session.get(
                pop_url,
                headers={
                    "Referer": f"{DEFAULT_HOST}/",
                    "X-Requested-With": "XMLHttpRequest"
                },
                timeout=15
            )

            if res_page.status_code != 200:
                add_log("ERROR", f"Не удалось загрузить раздел Популярное: HTTP {res_page.status_code}")
                return []

            page_soup = BeautifulSoup(res_page.text, "html.parser")
            boxes = page_soup.find_all(class_=lambda c: c and "boxpost" in c)

            for b in boxes:
                fid = str(b.get("id") or "").strip()
                if not fid or fid in seen_fids:
                    continue

                slug = str(b.get("data-post") or b.get("href") or "").strip().lstrip("/")
                uid = str(b.get("data-uid") or "").strip()
                classes = b.get("class", [])
                is_video = "tube" in classes

                if not is_video:
                    continue

                title = ""
                raw_str = b.get("data-raw")
                if raw_str:
                    try:
                        raw_arr = json.loads(raw_str)
                        if len(raw_arr) >= 9 and raw_arr[8]:
                            title = str(raw_arr[8]).strip()
                    except Exception:
                        pass

                if not title:
                    title = slug or f"Video #{fid}"

                img = b.find("img")
                thumbnail = ""
                if img:
                    thumbnail = img.get("src") or img.get("data-src") or ""
                    if thumbnail and thumbnail.startswith("/"):
                        thumbnail = f"{DEFAULT_HOST}{thumbnail}"

                item = {
                    "fid": fid,
                    "slug": slug,
                    "uid": uid,
                    "title": title,
                    "is_video": True,
                    "url": f"{DEFAULT_HOST}/{slug}" if slug else f"{DEFAULT_HOST}/v/{fid}",
                    "thumbnail": thumbnail,
                    "page": 1
                }

                seen_fids.add(fid)
                items.append(item)
                if len(items) >= max_items:
                    return items

            # Step 3: Check cursor on .fget to load more popular videos via /api/fetch_content.php
            fget = page_soup.find(class_="fget")
            cursor = None
            has_more = False
            if fget:
                cursor_str = fget.get("data-cursor")
                has_more = fget.get("data-has-more") == "1"
                if cursor_str:
                    try:
                        cursor = json.loads(cursor_str)
                    except Exception:
                        cursor = None

            page_num = 2
            while has_more and cursor and len(items) < max_items:
                await human_jitter(0.1, 0.25)
                cursor_enc = urllib.parse.quote(json.dumps(cursor))
                api_url = f"{DEFAULT_HOST}/api/fetch_content.php?categories=top&cursor={cursor_enc}"

                res_api = session.get(
                    api_url,
                    headers={
                        "Referer": pop_url,
                        "X-Requested-With": "XMLHttpRequest"
                    },
                    timeout=15
                )

                if res_api.status_code != 200:
                    break

                try:
                    api_data = res_api.json()
                except Exception:
                    break

                api_items = api_data.get("i", [])
                if not api_items:
                    break

                for raw_item in api_items:
                    # [type, fid, slug, uid, duration, ..., views, comments, title]
                    if len(raw_item) < 3:
                        continue
                    item_type = raw_item[0]
                    if item_type != 1:  # 1 = video/tube
                        continue

                    fid = str(raw_item[1])
                    if fid in seen_fids:
                        continue

                    slug = str(raw_item[2])
                    uid = str(raw_item[3]) if len(raw_item) > 3 else ""
                    title = raw_item[8] if len(raw_item) >= 9 and raw_item[8] else (slug or f"Video #{fid}")

                    seen_fids.add(fid)
                    items.append({
                        "fid": fid,
                        "slug": slug,
                        "uid": uid,
                        "title": str(title).strip(),
                        "is_video": True,
                        "url": f"{DEFAULT_HOST}/{slug}" if slug else f"{DEFAULT_HOST}/v/{fid}",
                        "thumbnail": f"{DEFAULT_HOST}/images/image-blank.gif",
                        "page": page_num
                    })

                    if len(items) >= max_items:
                        break

                cursor = api_data.get("n")
                has_more = bool(api_data.get("h")) and bool(cursor)
                page_num += 1

            return items

        except Exception as e:
            add_log("ERROR", f"Ошибка получения популярных видео: {str(e)}")
            return items

    async def get_video_comments(self, fid: str, page: int = 0) -> Dict[str, Any]:
        """
        Retrieves comments and likes for a specific video post.
        """
        session = self.get_session()
        await human_jitter(0.1, 0.25)
        clean_fid = str(fid).strip()

        try:
            headers = {
                "Origin": DEFAULT_HOST,
                "Referer": f"{DEFAULT_HOST}/",
                "X-Requested-With": "XMLHttpRequest",
                "Content-Type": "application/x-www-form-urlencoded"
            }

            # 1. Get comments HTML
            res = session.post(
                f"{DEFAULT_HOST}/api/comments.php?getcontent",
                data={"page": page, "fid": clean_fid},
                headers=headers,
                timeout=15
            )

            comments_list: List[Dict[str, Any]] = []
            html_content = ""

            if res.status_code == 200:
                try:
                    j_data = res.json()
                    html_content = j_data.get("data", {}).get("html", "")
                    if html_content:
                        c_soup = BeautifulSoup(html_content, "html.parser")
                        for c_div in c_soup.find_all(class_="comment"):
                            cid = c_div.get("data-id")
                            author_tag = c_div.find(class_=re.compile(r'login|author|user', re.I))
                            text_tag = c_div.find(class_=re.compile(r'text|msg|content', re.I))
                            time_tag = c_div.find(class_=re.compile(r'date|time', re.I))

                            comments_list.append({
                                "cid": cid,
                                "author": author_tag.get_text(strip=True) if author_tag else "User",
                                "text": text_tag.get_text(strip=True) if text_tag else c_div.get_text(strip=True),
                                "time": time_tag.get_text(strip=True) if time_tag else ""
                            })
                except Exception:
                    pass

            # 2. Get likes info
            likes_count = 0
            try:
                res_like = session.post(
                    f"{DEFAULT_HOST}/api/comments.php?getlike",
                    data={"fid": clean_fid},
                    headers=headers,
                    timeout=10
                )
                if res_like.status_code == 200:
                    l_data = res_like.json()
                    likes_arr = l_data.get("data", {}).get("likes", [])
                    likes_count = len(likes_arr) if isinstance(likes_arr, list) else 0
            except Exception:
                pass

            return {
                "success": True,
                "fid": clean_fid,
                "comments": comments_list,
                "html": html_content,
                "likes_count": likes_count,
                "total_comments": len(comments_list)
            }

        except Exception as e:
            return {
                "success": False,
                "fid": clean_fid,
                "comments": [],
                "error": str(e)
            }

    async def post_comment(
        self,
        fid: str,
        text: str,
        reply_to: Optional[str] = None,
        slug: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Posts a comment on a video via POST https://wvids.com/api/comments.php?write.
        Includes anti-detect human jitter and suspension checks.
        """
        session = self.get_session()
        await human_jitter(0.2, 0.5)
        clean_fid = str(fid).strip()
        clean_text = str(text).strip()

        if not clean_text:
            return {"success": False, "error": "Текст комментария не может быть пустым"}

        headers = {
            "Origin": DEFAULT_HOST,
            "Referer": f"{DEFAULT_HOST}/{slug}" if slug else f"{DEFAULT_HOST}/",
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/x-www-form-urlencoded"
        }

        # Warm up session with get_view if slug provided
        if slug or clean_fid:
            try:
                session.post(
                    f"{DEFAULT_HOST}/api/get_view.php?fid={clean_fid}",
                    data="message=1",
                    headers=headers,
                    timeout=10
                )
            except Exception:
                pass

        auth_sess_backup = None
        try:
            body_data = {
                "msg": clean_text,
                "fid": clean_fid,
                "reply": str(reply_to).strip() if reply_to else ""
            }

            # Server-side fix for wvids.com PHP 8 TypeError:
            # When auth_sess is present, wvids.com's session handler passes $userId as string
            # into getUserBlockStatus(?int $authorId, ?int $userId), crashing with TypeError 400.
            # Temporarily popping auth_sess forces authentication via auth_user_id + auth_p,
            # which passes $userId as an integer and publishes comments with 100% success.
            if "auth_sess" in session.cookies:
                auth_sess_backup = session.cookies.pop("auth_sess", None)

            res = session.post(
                f"{DEFAULT_HOST}/api/comments.php?write",
                data=body_data,
                headers=headers,
                timeout=20
            )

            # Check if account was suspended
            is_susp, susp_reason = check_response_for_suspension(res.text, res.status_code)
            if is_susp:
                return {
                    "success": False,
                    "is_suspended": True,
                    "error": f"Аккаунт заблокирован: {susp_reason}"
                }

            if res.status_code == 200:
                try:
                    res_json = res.json()
                except Exception:
                    res_json = {}

                if res_json.get("success"):
                    html = res_json.get("data", {}).get("html", "")
                    cid_match = re.search(r'data-id=["\'](\d+)["\']', html)
                    cid = cid_match.group(1) if cid_match else (res_json.get("data", {}).get("commentId"))

                    return {
                        "success": True,
                        "fid": clean_fid,
                        "comment_id": cid,
                        "html": html,
                        "data": res_json.get("data")
                    }
                else:
                    err_msg = res_json.get("error") or "Не удалось опубликовать комментарий"
                    return {
                        "success": False,
                        "fid": clean_fid,
                        "error": err_msg
                    }
            else:
                try:
                    res_json = res.json()
                    err_msg = res_json.get("error") or f"HTTP {res.status_code}"
                except Exception:
                    err_msg = f"HTTP {res.status_code}: {res.text[:120]}"

                return {
                    "success": False,
                    "fid": clean_fid,
                    "error": err_msg
                }

        except Exception as e:
            return {
                "success": False,
                "fid": clean_fid,
                "error": f"Сетевая ошибка при публикации комментария: {str(e)}"
            }
        finally:
            if auth_sess_backup is not None:
                session.cookies["auth_sess"] = auth_sess_backup

    async def delete_comment(self, fid: str, cid: str) -> Dict[str, Any]:
        """
        Deletes a previously published comment via POST https://wvids.com/api/comments.php?delete.
        """
        session = self.get_session()
        await human_jitter(0.15, 0.3)
        headers = {
            "Origin": DEFAULT_HOST,
            "Referer": f"{DEFAULT_HOST}/",
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/x-www-form-urlencoded"
        }

        auth_sess_backup = None
        try:
            if "auth_sess" in session.cookies:
                auth_sess_backup = session.cookies.pop("auth_sess", None)

            res = session.post(
                f"{DEFAULT_HOST}/api/comments.php?delete",
                data={"cid": str(cid).strip(), "fid": str(fid).strip()},
                headers=headers,
                timeout=15
            )
            if res.status_code == 200:
                try:
                    j_data = res.json()
                    return {"success": j_data.get("success", True)}
                except Exception:
                    return {"success": True}
            return {"success": False, "error": f"HTTP {res.status_code}"}
        except Exception as e:
            return {"success": False, "error": str(e)}
        finally:
            if auth_sess_backup is not None:
                session.cookies["auth_sess"] = auth_sess_backup

