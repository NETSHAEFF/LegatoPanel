import time
import random
import string
import base64
import json
import asyncio
import re
from typing import Dict, Any, Optional

def generate_actionfingerprint() -> Dict[str, Any]:
    """
    Generates synthetic anti-detect behavioral fingerprint that matches
    wvids.com's client-side tracker from footer.min.js:
    get_actionfingerprint()
    """
    start_time = int(time.time() * 1000)

    def rand_id() -> str:
        chars = string.ascii_lowercase + string.digits
        return ''.join(random.choice(chars) for _ in range(5))

    def encode_point(val: Any, offset_ms: int) -> str:
        raw = f"{val}|{offset_ms}"
        return base64.b64encode(raw.encode('utf-8')).decode('utf-8')

    # Simulate realistic user scroll, touch, mouse actions
    offset1 = random.randint(180, 520)
    offset2 = offset1 + random.randint(120, 350)
    offset3 = offset2 + random.randint(90, 240)

    scroll_pos = random.randint(120, 680)
    mouse_x = random.randint(140, 850)
    mouse_y = random.randint(120, 750)

    action_fp = {
        "s": f"{rand_id()}|{encode_point(scroll_pos, offset1)}",
        "m": f"{rand_id()}|{encode_point(f'{mouse_x},{mouse_y}', offset2)}",
        "c": f"{rand_id()}|{encode_point(1, offset3)}",
        "_last": start_time + offset3 + random.randint(50, 150)
    }

    return action_fp

def get_browser_headers(preset: str = "chrome124") -> Dict[str, str]:
    """
    Returns realistic headers matching the specified anti-detect impersonate preset.
    """
    if "safari" in preset.lower():
        ua = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15"
        headers = {
            "User-Agent": ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept-Encoding": "gzip, deflate, br",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Dest": "document",
            "Priority": "u=0, i"
        }
    elif "edge" in preset.lower():
        ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0"
        headers = {
            "User-Agent": ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "sec-ch-ua": '"Chromium";v="124", "Microsoft Edge";v="124", "Not-A.Brand";v="99"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-User": "?1",
            "Sec-Fetch-Dest": "document",
            "Upgrade-Insecure-Requests": "1"
        }
    else:  # Default chrome
        ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        headers = {
            "User-Agent": ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-User": "?1",
            "Sec-Fetch-Dest": "document",
            "Upgrade-Insecure-Requests": "1"
        }
    return headers

def parse_cookies_input(cookie_input: Any) -> Dict[str, str]:
    """
    Parses cookies from any format:
    1. Header string: "auth_user_id=123; auth_sess=xyz; auth_p=abc"
    2. Header with Cookie: prefix: "Cookie: auth_user_id=123; ..."
    3. JSON array / object from extensions (Cookie-Editor, EditThisCookie)
    4. Dict: {"auth_user_id": "123", ...}
    5. Netscape / cookies.txt format (tab-separated)
    6. Multiline key=value pairs or & separated
    """
    cookies: Dict[str, str] = {}
    if not cookie_input:
        return cookies

    if isinstance(cookie_input, dict):
        return {
            str(k).strip().strip("\"' "): str(v).strip().strip("\"' ")
            for k, v in cookie_input.items()
            if k
        }

    if isinstance(cookie_input, list):
        for item in cookie_input:
            if isinstance(item, dict) and "name" in item and "value" in item:
                k = str(item["name"]).strip().strip("\"' ")
                v = str(item["value"]).strip().strip("\"' ")
                if k:
                    cookies[k] = v
        return cookies

    text = str(cookie_input).strip()

    # Strip global 'Cookie:' / 'cookies:' prefix if present
    if re.match(r'^(?:cookie|cookies)\s*:\s*', text, re.IGNORECASE):
        text = re.sub(r'^(?:cookie|cookies)\s*:\s*', '', text, flags=re.IGNORECASE).strip()

    # Try parsing as JSON first (handling both [] and {})
    if text.startswith("[") or text.startswith("{"):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return {
                    str(k).strip().strip("\"' "): str(v).strip().strip("\"' ")
                    for k, v in parsed.items()
                    if k
                }
            if isinstance(parsed, list):
                for item in parsed:
                    if isinstance(item, dict) and "name" in item and "value" in item:
                        k = str(item["name"]).strip().strip("\"' ")
                        v = str(item["value"]).strip().strip("\"' ")
                        if k:
                            cookies[k] = v
                if cookies:
                    return cookies
        except Exception:
            # Fallback for single-quoted pseudo-JSON
            try:
                import ast
                parsed = ast.literal_eval(text)
                if isinstance(parsed, dict):
                    return {
                        str(k).strip().strip("\"' "): str(v).strip().strip("\"' ")
                        for k, v in parsed.items()
                        if k
                    }
                if isinstance(parsed, list):
                    for item in parsed:
                        if isinstance(item, dict) and "name" in item and "value" in item:
                            k = str(item["name"]).strip().strip("\"' ")
                            v = str(item["value"]).strip().strip("\"' ")
                            if k:
                                cookies[k] = v
                    if cookies:
                        return cookies
            except Exception:
                pass

    # Line-by-line / Semicolon / Ampersand / Netscape parsing
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        # Strip line-level 'Cookie:' prefix
        if re.match(r'^(?:cookie|cookies)\s*:\s*', line, re.IGNORECASE):
            line = re.sub(r'^(?:cookie|cookies)\s*:\s*', '', line, flags=re.IGNORECASE).strip()

        # Netscape format (tab separated: domain, flag, path, secure, expiration, name, value)
        if "\t" in line:
            parts = line.split("\t")
            if len(parts) >= 7:
                name, val = parts[5].strip(), parts[6].strip()
                if name:
                    cookies[name.strip("\"' ")] = val.strip("\"' ")
                continue

        # Split on ; or & (supports both cookie header and query-style strings)
        parts = re.split(r'[;&]', line)
        for part in parts:
            part = part.strip()
            if "=" in part:
                k, v = part.split("=", 1)
                k = k.strip()
                v = v.strip()
                if re.match(r'^(?:cookie|cookies)\s*:\s*', k, re.IGNORECASE):
                    k = re.sub(r'^(?:cookie|cookies)\s*:\s*', '', k, flags=re.IGNORECASE).strip()
                k = k.strip("\"' ")
                v = v.strip("\"' ")
                if k:
                    cookies[k] = v

    return cookies

async def human_jitter(min_seconds: float = 0.3, max_seconds: float = 1.0):
    """Async random delay to emulate real human interactions"""
    delay = random.uniform(min_seconds, max_seconds)
    await asyncio.sleep(delay)
