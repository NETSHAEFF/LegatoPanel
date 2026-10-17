import time
import socket
from typing import Dict, Any, Optional, Tuple
from curl_cffi import requests

from .config import TOR_HOST, TOR_DEFAULT_PORT, TOR_FALLBACK_PORT, DEFAULT_FINGERPRINT
from .storage import get_tor_config, add_log

class TorRoutingError(Exception):
    """Raised when Tor is required but unreachable, preventing IP/DNS leaks."""
    pass

def is_port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    """Checks if a TCP port is open locally."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            return s.connect_ex((host, port)) == 0
    except Exception:
        return False

def auto_detect_tor_port() -> Optional[int]:
    """Tries 9150 (Tor Browser), then 9050 (Tor service), then returns None."""
    for p in [TOR_DEFAULT_PORT, TOR_FALLBACK_PORT]:
        if is_port_open(TOR_HOST, p, timeout=0.8):
            return p
    return None

def get_tor_proxy_url(port: Optional[int] = None) -> str:
    """Builds socks5h:// URL ensuring DNS is resolved remotely on Tor exit node."""
    if not port:
        cfg = get_tor_config()
        port = cfg.get("port") or TOR_DEFAULT_PORT
    return f"socks5h://{TOR_HOST}:{port}"

def check_tor_connection(port: Optional[int] = None, timeout: int = 15) -> Dict[str, Any]:
    """
    Checks Tor connectivity via check.torproject.org and api.ipify.org.
    Verifies that the IP is indeed a Tor exit node.
    """
    if port is None:
        cfg = get_tor_config()
        port = cfg.get("port") or TOR_DEFAULT_PORT

    proxy_url = f"socks5h://{TOR_HOST}:{port}"
    proxies = {"http": proxy_url, "https": proxy_url}

    start_t = time.time()
    try:
        s = requests.Session(impersonate=DEFAULT_FINGERPRINT, proxies=proxies)
        
        # 1. Primary check: official Tor Project API
        is_tor = False
        ip = "Unknown"
        try:
            r = s.get("https://check.torproject.org/api/ip", timeout=timeout)
            if r.status_code == 200:
                data = r.json()
                is_tor = bool(data.get("IsTor", False))
                ip = data.get("IP", "Unknown")
        except Exception:
            # Fallback to ipify if check.torproject is slow
            r_ip = s.get("https://api.ipify.org?format=json", timeout=timeout)
            if r_ip.status_code == 200:
                ip = r_ip.json().get("ip", "Unknown")
                is_tor = True

        elapsed_ms = int((time.time() - start_t) * 1000)

        if not ip or ip == "Unknown":
            return {
                "connected": False,
                "is_tor": False,
                "ip": None,
                "port": port,
                "latency_ms": elapsed_ms,
                "error": "Не удалось определить внешний IP через Tor",
                "proxy_url": proxy_url
            }

        return {
            "connected": True,
            "is_tor": is_tor,
            "ip": ip,
            "port": port,
            "latency_ms": elapsed_ms,
            "proxy_url": proxy_url,
            "error": None
        }

    except Exception as e:
        elapsed_ms = int((time.time() - start_t) * 1000)
        return {
            "connected": False,
            "is_tor": False,
            "ip": None,
            "port": port,
            "latency_ms": elapsed_ms,
            "proxy_url": proxy_url,
            "error": f"Ошибка соединения с Tor ({TOR_HOST}:{port}): {str(e)}"
        }
