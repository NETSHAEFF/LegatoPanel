import os
import sys
import time
import socket
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional, List
from curl_cffi import requests

from .config import BASE_DIR, DATA_DIR, DEFAULT_FINGERPRINT
from .storage import get_setting, set_setting, add_log

VPN_TXT_PATH = BASE_DIR / "vpn.txt"
PID_FILE = DATA_DIR / "vless.pid"
VLESS_DEFAULT_PORT = 10808

def is_pid_running(pid: int) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        if os.name == "nt":
            import ctypes
            PROCESS_QUERY_INFORMATION = 0x0400
            handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_INFORMATION, False, pid)
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                return True
            return False
        else:
            os.kill(pid, 0)
            return True
    except Exception:
        return False

def kill_process_tree(pid: int) -> bool:
    if not is_pid_running(pid):
        return True
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            os.kill(pid, 15)
        return True
    except Exception:
        return False

def is_port_open(host: str, port: int, timeout: float = 0.8) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            return s.connect_ex((host, port)) == 0
    except Exception:
        return False

def find_xray_binary() -> Optional[Path]:
    candidates: List[Path] = [
        Path("C:/Program Files/FlyFrogLLC/Happ/core/xray.exe"),
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Xray" / "xray.exe",
        BASE_DIR / "xray.exe",
        DATA_DIR / "xray" / "xray.exe",
    ]
    for c in candidates:
        if c.exists() and c.is_file():
            return c

    # System PATH
    bin_name = "xray.exe" if os.name == "nt" else "xray"
    path_env = os.environ.get("PATH", "")
    for p in path_env.split(os.pathsep):
        candidate = Path(p.strip()) / bin_name
        if candidate.exists() and candidate.is_file():
            return candidate

    return None

class VlessManager:
    """
    Manages background Xray-core VLESS proxy process.
    Connects to external VLESS Reality server using vpn.txt.
    """
    def __init__(self):
        self._process: Optional[subprocess.Popen] = None
        self._port: int = VLESS_DEFAULT_PORT

    def get_status(self) -> Dict[str, Any]:
        running = False
        pid = None
        managed = False

        if PID_FILE.exists():
            try:
                saved_pid = int(PID_FILE.read_text(encoding="utf-8").strip())
                if is_pid_running(saved_pid):
                    pid = saved_pid
                    running = True
                    managed = True
                else:
                    PID_FILE.unlink(missing_ok=True)
            except Exception:
                pass

        if not running and self._process and self._process.poll() is None:
            running = True
            pid = self._process.pid
            managed = True

        # Check if port 10808 is open (whether managed by us or by Happ/v2rayN)
        port_open = is_port_open("127.0.0.1", self._port)
        if port_open:
            running = True

        binary = find_xray_binary()
        has_config = VPN_TXT_PATH.exists()

        return {
            "running": running,
            "port_open": port_open,
            "managed_by_panel": managed,
            "pid": pid,
            "port": self._port,
            "has_binary": bool(binary),
            "binary_path": str(binary) if binary else None,
            "has_config": has_config,
            "config_path": str(VPN_TXT_PATH) if has_config else None
        }

    def start(self, port: int = VLESS_DEFAULT_PORT, wait_seconds: int = 15) -> Dict[str, Any]:
        self._port = port

        # 1. Already listening on port 10808 (e.g. from Happ/v2rayN or previous run)
        if is_port_open("127.0.0.1", port, timeout=0.5):
            return {
                "success": True,
                "already_running": True,
                "port": port,
                "message": f"VLESS SOCKS5 прокси уже активен на порту {port}"
            }

        # 2. Check binary
        binary = find_xray_binary()
        if not binary:
            err = "Не найден исполняемый файл xray.exe для запуска VLESS"
            add_log("ERROR", err)
            return {"success": False, "error": err}

        # 3. Check config
        if not VPN_TXT_PATH.exists():
            err = f"Файл конфигурации {VPN_TXT_PATH} не найден"
            add_log("ERROR", err)
            return {"success": False, "error": err}

        # 4. Launch xray.exe run -c vpn.txt
        cmd = [str(binary), "run", "-c", str(VPN_TXT_PATH)]
        creationflags = 0
        if os.name == "nt":
            creationflags = subprocess.CREATE_NO_WINDOW | getattr(subprocess, "DETACHED_PROCESS", 0x00000008)

        try:
            add_log("INFO", f"⚡ Запуск службы VLESS: {cmd[0]} run -c {VPN_TXT_PATH.name}...")
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                creationflags=creationflags
            )
            self._process = proc
            PID_FILE.write_text(str(proc.pid), encoding="utf-8")

            # Wait for port to open
            start_t = time.time()
            opened = False
            while time.time() - start_t < wait_seconds:
                if is_port_open("127.0.0.1", port, timeout=0.4):
                    opened = True
                    break
                if proc.poll() is not None:
                    return {
                        "success": False,
                        "error": f"xray.exe аварийно завершился с кодом {proc.returncode}"
                    }
                time.sleep(0.5)

            if opened:
                add_log("SUCCESS", f"⚡ VLESS SOCKS5 запущен (PID {proc.pid}) на 127.0.0.1:{port}")
                return {
                    "success": True,
                    "pid": proc.pid,
                    "port": port,
                    "message": f"Служба VLESS успешно запущена на порту {port}"
                }
            else:
                return {
                    "success": False,
                    "error": f"xray.exe запущен (PID {proc.pid}), но порт {port} не открылся за {wait_seconds}с."
                }
        except Exception as e:
            err = f"Ошибка запуска VLESS: {str(e)}"
            add_log("ERROR", err)
            return {"success": False, "error": err}

    def stop(self) -> Dict[str, Any]:
        stopped = False
        pid = None

        if PID_FILE.exists():
            try:
                pid = int(PID_FILE.read_text(encoding="utf-8").strip())
                kill_process_tree(pid)
                PID_FILE.unlink(missing_ok=True)
                stopped = True
            except Exception:
                pass

        if self._process:
            pid = self._process.pid
            kill_process_tree(pid)
            self._process = None
            stopped = True

        time.sleep(0.5)
        if stopped or pid:
            add_log("INFO", f"⚡ VLESS процесс (PID: {pid}) остановлен")
            return {"success": True, "message": f"Процесс VLESS (PID: {pid}) остановлен"}
        return {"success": True, "message": "Процесс VLESS не был запущен панелью"}

    def ensure_running(self, port: int = VLESS_DEFAULT_PORT) -> bool:
        if is_port_open("127.0.0.1", port, timeout=0.5):
            return True
        res = self.start(port=port)
        return bool(res.get("success"))

vless_manager = VlessManager()

def check_vless_connection(port: int = VLESS_DEFAULT_PORT, timeout: int = 10) -> Dict[str, Any]:
    """
    Checks VLESS connectivity through SOCKS5 proxy (127.0.0.1:10808).
    Verifies IP, Country, ISP, Tor status (must be False) and wvids.com availability.
    """
    if not is_port_open("127.0.0.1", port, timeout=0.5):
        # Auto start if available
        vless_manager.ensure_running(port=port)

    proxy_url = f"socks5h://127.0.0.1:{port}"
    proxies = {"http": proxy_url, "https": proxy_url}

    start_t = time.time()
    try:
        s = requests.Session(impersonate=DEFAULT_FINGERPRINT, proxies=proxies)

        # 1. Geo info via ip-api
        r_geo = s.get("http://ip-api.com/json", timeout=timeout)
        elapsed_ms = int((time.time() - start_t) * 1000)

        if r_geo.status_code != 200:
            return {
                "connected": False,
                "ip": None,
                "port": port,
                "proxy_url": proxy_url,
                "latency_ms": elapsed_ms,
                "error": f"ip-api вернул HTTP {r_geo.status_code}"
            }

        geo = r_geo.json()
        ip = geo.get("query")
        country = geo.get("country", "Unknown")
        country_code = (geo.get("countryCode") or "").upper()
        city = geo.get("city", "")
        isp = geo.get("isp", "")
        org = geo.get("org", "")

        # 2. Check Tor status (should be False for VLESS)
        is_tor = False
        try:
            r_tor = s.get("https://check.torproject.org/api/ip", timeout=5)
            if r_tor.status_code == 200:
                is_tor = bool(r_tor.json().get("IsTor", False))
        except Exception:
            pass

        # 3. Check wvids.com access
        wvids_accessible = False
        wvids_status = 0
        try:
            r_w = s.get("https://wvids.com/feed", timeout=8)
            wvids_status = r_w.status_code
            wvids_accessible = (r_w.status_code == 200)
        except Exception:
            pass

        return {
            "connected": True,
            "ip": ip,
            "country": country,
            "country_code": country_code,
            "city": city,
            "isp": isp,
            "org": org,
            "is_tor": is_tor,
            "wvids_accessible": wvids_accessible,
            "wvids_status": wvids_status,
            "port": port,
            "latency_ms": elapsed_ms,
            "proxy_url": proxy_url,
            "error": None
        }

    except Exception as e:
        elapsed_ms = int((time.time() - start_t) * 1000)
        return {
            "connected": False,
            "ip": None,
            "port": port,
            "proxy_url": proxy_url,
            "latency_ms": elapsed_ms,
            "error": f"Ошибка соединения через VLESS ({proxy_url}): {str(e)}"
        }
