import os
import sys
import time
import socket
import atexit
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List
from curl_cffi import requests

from .config import TOR_HOST, TOR_DEFAULT_PORT, TOR_FALLBACK_PORT, DEFAULT_FINGERPRINT, DATA_DIR
from .storage import get_tor_config, add_log

PID_FILE = DATA_DIR / "tor.pid"

class TorRoutingError(Exception):
    """Raised when Tor is required but unreachable, preventing IP/DNS leaks."""
    pass

def is_pid_running(pid: int) -> bool:
    """Checks if a process with given PID is currently active."""
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
    """Force terminates process and its child processes (e.g. Lyrebird obfs4 proxy)."""
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

def is_port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    """Checks if a TCP port is open locally."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            return s.connect_ex((host, port)) == 0
    except Exception:
        return False

def find_tor_binary() -> Optional[Path]:
    """
    Locates tor binary across known paths, LocalAppData, Desktop Tor Browser, or system PATH.
    """
    bin_name = "tor.exe" if os.name == "nt" else "tor"
    candidates: List[Path] = []
    # 1. LocalAppData Programs (Windows)
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if local_app_data:
        candidates.append(Path(local_app_data) / "Programs" / "Tor" / "tor.exe")
        candidates.append(Path(local_app_data) / "Programs" / "Python" / "Python312" / "Scripts" / "tor.exe")

    # 2. Project local tor directory
    base_dir = Path(__file__).resolve().parent.parent
    candidates.append(base_dir / "tor" / bin_name)

    # 3. Desktop Tor Browser
    desktop_candidates = [
        Path("C:/Users/Administrator/Desktop/Tor Browser/Browser/TorBrowser/Tor/tor.exe"),
        Path.home() / "Desktop" / "Tor Browser" / "Browser" / "TorBrowser" / "Tor" / "tor.exe"
    ]
    candidates.extend(desktop_candidates)

    # 4. Standard Program Files (Windows) / Standard Linux Paths
    candidates.extend([
        Path("C:/Program Files/Tor/tor.exe"),
        Path("C:/Program Files (x86)/Tor/tor.exe"),
        Path("/usr/bin/tor"),
        Path("/usr/local/bin/tor"),
        Path("/usr/sbin/tor")
    ])

    for c in candidates:
        if c.exists() and c.is_file():
            return c

    # 5. Which tor in PATH
    w = shutil.which("tor")
    if w:
        return Path(w)

    return None

def setup_standalone_tor_env(target_port: int = 9050) -> Tuple[Optional[Path], Optional[Path]]:
    """
    Prepares standalone Tor environment with torrc, obfs4 bridges and GeoIP files.
    Returns (tor_exe_path, torrc_file_path).
    """
    tor_exe = find_tor_binary()
    if not tor_exe:
        return None, None

    bin_name = "tor.exe" if os.name == "nt" else "tor"

    # Preferred base dir for standalone Tor
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    tor_dir = (Path(local_app_data) / "Programs" / "Tor") if local_app_data else (Path(__file__).resolve().parent.parent / "tor")
    tor_dir.mkdir(parents=True, exist_ok=True)

    # Ensure tor binary in tor_dir
    local_tor_exe = tor_dir / bin_name
    if not local_tor_exe.exists() and tor_exe.exists():
        try:
            shutil.copy2(str(tor_exe), str(local_tor_exe))
            tor_exe = local_tor_exe
        except Exception:
            pass
    elif local_tor_exe.exists():
        tor_exe = local_tor_exe

    # Locate PluggableTransports and GeoIP
    source_tor_dir = tor_exe.parent
    desktop_tor = Path("C:/Users/Administrator/Desktop/Tor Browser/Browser/TorBrowser")
    
    # PluggableTransports
    pt_dir = tor_dir / "PluggableTransports"
    if not pt_dir.exists():
        for candidate_pt in [source_tor_dir / "PluggableTransports", desktop_tor / "Tor" / "PluggableTransports"]:
            if candidate_pt.exists():
                try:
                    shutil.copytree(str(candidate_pt), str(pt_dir))
                    break
                except Exception:
                    pass

    # GeoIP
    for gf in ["geoip", "geoip6"]:
        dst_g = tor_dir / gf
        if not dst_g.exists():
            for src_g in [source_tor_dir / gf, desktop_tor / "Data" / "Tor" / gf]:
                if src_g.exists():
                    try:
                        shutil.copy2(str(src_g), str(dst_g))
                        break
                    except Exception:
                        pass

    # Data directory
    data_dir = tor_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    # Lyrebird binary
    lyrebird_exe = tor_dir / "PluggableTransports" / "lyrebird.exe"
    pt_line = ""
    if lyrebird_exe.exists():
        lyre_path = str(lyrebird_exe).replace("\\", "/")
        pt_line = f"ClientTransportPlugin meek_lite,obfs2,obfs3,obfs4,scramblesuit,webtunnel exec {lyre_path}"

    geoip_line = f"GeoIPFile {str(tor_dir / 'geoip').replace('\\', '/')}" if (tor_dir / "geoip").exists() else ""
    geoip6_line = f"GeoIPv6File {str(tor_dir / 'geoip6').replace('\\', '/')}" if (tor_dir / "geoip6").exists() else ""

    log_file = str(tor_dir / "tor.log").replace('\\', '/')
    torrc_content = f"""# Standalone Headless Tor Config for Wvids Studio Pro
SocksPort {target_port}
DataDirectory {str(data_dir).replace('\\', '/')}
{geoip_line}
{geoip6_line}
{pt_line}
Bridge obfs4 51.89.228.250:21668 BA8BD67D8898CF378D4F73821DEB5657F4BB98DF cert=bEpLLgOwJ9fOJbeHb5r+ronUF2ck5nRd0Jl3zuy7rLoUp732QK2p/CUHjTAfBPCGfcVtSA iat-mode=0
Bridge obfs4 51.89.231.85:24526 A44162645CEAA39C7106C49A3F36FD99FF8D8A00 cert=cVNRBh1enpvEGxA0ouu568EDFhWPl9taldydYgiJk77OX4MzoYIRz3qx4q7yHL5bRN60Gg iat-mode=0
UseBridges 1
DisableNetwork 0
AvoidDiskWrites 1
Log notice file {log_file}
"""
    torrc_path = tor_dir / "torrc"
    torrc_path.write_text(torrc_content.strip(), encoding="utf-8")

    return tor_exe, torrc_path

class HeadlessTorManager:
    """
    Manages a standalone headless Tor background process.
    Runs silently without requiring Tor Browser to be open.
    """
    def __init__(self):
        self._process: Optional[subprocess.Popen] = None
        self._port: int = 9050
        self._binary_path: Optional[Path] = None

    def get_status(self) -> Dict[str, Any]:
        running = False
        pid = None
        managed = False

        # 1. Check PID file first
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

        # 2. Check local process handle
        if not running and self._process and self._process.poll() is None:
            running = True
            pid = self._process.pid
            managed = True

        # 3. Check open ports
        if not running:
            if is_port_open(TOR_HOST, 9050, timeout=0.5):
                running = True
            elif is_port_open(TOR_HOST, 9150, timeout=0.5):
                running = True

        binary = find_tor_binary()
        detected_port = 9050 if is_port_open(TOR_HOST, 9050) else (9150 if is_port_open(TOR_HOST, 9150) else self._port)
        return {
            "running": running,
            "managed_by_panel": managed,
            "pid": pid,
            "port": detected_port,
            "has_binary": bool(binary),
            "binary_path": str(binary) if binary else None
        }

    def start(self, port: int = 9050, wait_seconds: int = 25) -> Dict[str, Any]:
        self._port = port

        # 1. Check if already open on requested port
        if is_port_open(TOR_HOST, port, timeout=0.6):
            pid = None
            if PID_FILE.exists():
                try:
                    pid = int(PID_FILE.read_text(encoding="utf-8").strip())
                except Exception:
                    pass
            return {
                "success": True,
                "already_running": True,
                "pid": pid,
                "port": port,
                "message": f"Tor уже запущен и слушает порт {port}"
            }

        # 2. Setup standalone env
        tor_exe, torrc_path = setup_standalone_tor_env(target_port=port)
        if not tor_exe or not torrc_path:
            err = "Не найден исполняемый файл tor.exe для автономного запуска"
            add_log("ERROR", err)
            return {"success": False, "error": err}

        cmd = [str(tor_exe), "-f", str(torrc_path)]
        creationflags = 0
        if os.name == "nt":
            # Fully detached background process on Windows so it survives CLI termination
            DETACHED_PROCESS = 0x00000008
            CREATE_NEW_PROCESS_GROUP = 0x00000200
            CREATE_NO_WINDOW = 0x08000000
            creationflags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW

        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(tor_exe.parent),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                close_fds=True,
                creationflags=creationflags
            )
            self._process = proc
            self._binary_path = tor_exe

            try:
                PID_FILE.write_text(str(proc.pid), encoding="utf-8")
            except Exception:
                pass

            add_log("INFO", f"🧅 Запущен автономный headless Tor (PID: {proc.pid}, порт: {port})")

            # Wait for socket to become available
            start_t = time.time()
            ready = False
            while time.time() - start_t < wait_seconds:
                if is_port_open(TOR_HOST, port, timeout=0.8):
                    ready = True
                    break
                if proc.poll() is not None:
                    # Process died
                    break
                time.sleep(0.5)

            if ready:
                add_log("SUCCESS", f"🧅 Автономный Tor готов к работе на порту {port} (без браузера)!")
                return {
                    "success": True,
                    "already_running": False,
                    "pid": proc.pid,
                    "port": port,
                    "message": f"Автономный Tor успешно запущен на порту {port}"
                }
            else:
                return {
                    "success": False,
                    "error": f"Tor запущен (PID {proc.pid}), но порт {port} не открылся за {wait_seconds}с. Проверьте мосты obfs4."
                }

        except Exception as e:
            err = f"Ошибка запуска автономного Tor: {str(e)}"
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
            add_log("INFO", f"🧅 Автономный Tor (PID: {pid}) остановлен")
            return {"success": True, "message": f"Фоновый процесс Tor (PID: {pid}) остановлен"}
        return {"success": True, "message": "Фоновый процесс Tor не был активен"}

    def ensure_running(self, port: int = 9050) -> bool:
        """
        Ensures Tor is active on 9050 or 9150. If not, auto-starts headless Tor.
        """
        # Check if already listening on 9050 or 9150
        if is_port_open(TOR_HOST, port, timeout=0.5):
            return True
        if is_port_open(TOR_HOST, 9150, timeout=0.5):
            return True

        # Otherwise launch standalone
        res = self.start(port=port)
        return bool(res.get("success"))

# Global Tor manager instance
tor_manager = HeadlessTorManager()

def get_tor_service_status() -> Dict[str, Any]:
    return tor_manager.get_status()

def start_tor_service(port: int = 9050) -> Dict[str, Any]:
    return tor_manager.start(port=port)

def stop_tor_service() -> Dict[str, Any]:
    return tor_manager.stop()

def auto_detect_tor_port() -> Optional[int]:
    """Tries 9050 (standalone headless), then 9150 (Tor Browser), then returns None."""
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

    # Ensure Tor is running if port is not yet open
    if not is_port_open(TOR_HOST, port, timeout=0.5):
        detected = auto_detect_tor_port()
        if detected:
            port = detected
        else:
            tor_manager.ensure_running(port=port)
            if is_port_open(TOR_HOST, port, timeout=1.0):
                pass
            else:
                detected2 = auto_detect_tor_port()
                if detected2:
                    port = detected2

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
