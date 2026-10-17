import re
import json
import time
import base64
import os
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
from .storage import add_log, get_tor_config

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

