import os
import sys
import time
import socket
import atexit
import signal
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List
from curl_cffi import requests

from .config import TOR_HOST, TOR_DEFAULT_PORT, TOR_FALLBACK_PORT, DEFAULT_FINGERPRINT, DATA_DIR, BASE_DIR
from .storage import get_tor_config, save_tor_config, add_log

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
    """Force terminates process and its child processes (e.g. obfs4proxy)."""
    if not is_pid_running(pid):
        return True
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            try:
                pgid = os.getpgid(pid)
                os.killpg(pgid, signal.SIGTERM)
                time.sleep(0.3)
                if is_pid_running(pid):
                    os.killpg(pgid, signal.SIGKILL)
            except Exception:
                try:
                    os.kill(pid, signal.SIGTERM)
                    time.sleep(0.3)
                    if is_pid_running(pid):
                        os.kill(pid, signal.SIGKILL)
                except Exception:
                    pass
        return True
    except Exception:
        return False

def is_port_open(host: str, port: int, timeout: float = 0.8) -> bool:
    """Checks if a TCP port is open locally."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            return s.connect_ex((host, port)) == 0
    except Exception:
        return False

def find_tor_binary() -> Optional[Path]:
    """
    Locates tor binary across known paths, system PATH, LocalAppData, or Tor Browser.
    """
    bin_name = "tor.exe" if os.name == "nt" else "tor"

    # 1. On Linux, prefer native system tor (/usr/bin/tor) which has full filesystem access
    if os.name != "nt":
        for p in [Path("/usr/bin/tor"), Path("/usr/local/bin/tor"), Path("/usr/sbin/tor")]:
            if p.is_file():
                return p
        w = shutil.which("tor")
        if w and Path(w).is_file():
            return Path(w)

    candidates: List[Path] = []

    # 2. LocalAppData Programs (Windows)
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if local_app_data:
        candidates.append(Path(local_app_data) / "Programs" / "Tor" / "tor.exe")
        candidates.append(Path(local_app_data) / "Programs" / "Python" / "Python312" / "Scripts" / "tor.exe")

    # 3. Project local tor directory
    candidates.append(BASE_DIR / "tor" / bin_name)

    # 4. Desktop Tor Browser
    desktop_candidates = [
        Path("C:/Users/Administrator/Desktop/Tor Browser/Browser/TorBrowser/Tor/tor.exe"),
        Path.home() / "Desktop" / "Tor Browser" / "Browser" / "TorBrowser" / "Tor" / "tor.exe",
        Path.home() / ".local" / "share" / "torbrowser" / "tbb" / "x86_64" / "tor-browser" / "Browser" / "TorBrowser" / "Tor" / "tor",
    ]
    candidates.extend(desktop_candidates)

    # 5. Standard Program Files (Windows)
    candidates.extend([
        Path("C:/Program Files/Tor/tor.exe"),
        Path("C:/Program Files (x86)/Tor/tor.exe"),
    ])

    for c in candidates:
        if c.exists() and c.is_file():
            return c

    # 5. Which tor in PATH
    w = shutil.which("tor")
    if w:
        return Path(w)

    return None

def find_pluggable_transport(tor_dir: Path) -> Tuple[Optional[str], Optional[Path]]:
    """
    Finds pluggable transport binary (obfs4proxy, lyrebird, snowflake-client).
    Returns (transport_name, binary_path).
    """
    pt_dir = tor_dir / "PluggableTransports"

    # Search candidates in priority order
    if os.name == "nt":
        candidates = [
            ("obfs4", pt_dir / "lyrebird.exe"),
            ("obfs4", pt_dir / "obfs4proxy.exe"),
            ("snowflake", pt_dir / "snowflake-client.exe"),
        ]
    else:
        candidates = [
            ("obfs4", pt_dir / "obfs4proxy"),
            ("obfs4", pt_dir / "lyrebird"),
            ("snowflake", pt_dir / "snowflake-client"),
            ("obfs4", Path("/usr/bin/obfs4proxy")),
            ("snowflake", Path("/usr/bin/snowflake-client")),
        ]

    for t_type, p in candidates:
        if p.exists() and p.is_file():
            if os.name != "nt":
                try:
                    os.chmod(p, 0o755)
                except Exception:
                    pass
            return t_type, p

    # Check system PATH
    for name, t_type in [("obfs4proxy", "obfs4"), ("lyrebird", "obfs4"), ("snowflake-client", "snowflake")]:
        w = shutil.which(name)
        if w:
            return t_type, Path(w)

    return None, None

def setup_standalone_tor_env(target_port: int = 9150) -> Tuple[Optional[Path], Optional[Path]]:
    """
    Prepares standalone Tor environment with torrc, obfs4 bridges and GeoIP files.
    Returns (tor_exe_path, torrc_file_path).
    """
    tor_exe = find_tor_binary()
    if not tor_exe:
        return None, None

    local_app_data = os.environ.get("LOCALAPPDATA", "")
    tor_dir = (Path(local_app_data) / "Programs" / "Tor") if (os.name == "nt" and local_app_data) else (BASE_DIR / "tor")
    tor_dir.mkdir(parents=True, exist_ok=True)

    # Data directory
    data_dir = tor_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    # Pluggable Transports
    pt_type, pt_path = find_pluggable_transport(tor_dir)
    pt_line = ""
    if pt_path:
        exec_path = str(pt_path.resolve()).replace("\\", "/")
        if "lyrebird" in pt_path.name.lower():
            pt_line = f"ClientTransportPlugin meek_lite,obfs2,obfs3,obfs4,scramblesuit,webtunnel exec {exec_path}"
        elif "snowflake" in pt_path.name.lower():
            pt_line = f"ClientTransportPlugin snowflake exec {exec_path}"
        else:
            pt_line = f"ClientTransportPlugin obfs4 exec {exec_path}"

    # GeoIP resolution
    geoip_line = ""
    geoip6_line = ""
    geoip_candidates = [
        tor_dir / "geoip",
        Path("/usr/share/tor/geoip"),
        Path("/usr/local/share/tor/geoip"),
        tor_exe.parent / "geoip",
    ]
    geoip6_candidates = [
        tor_dir / "geoip6",
        Path("/usr/share/tor/geoip6"),
        Path("/usr/local/share/tor/geoip6"),
        tor_exe.parent / "geoip6",
    ]

    for g in geoip_candidates:
        if g.exists() and g.is_file():
            geoip_line = f"GeoIPFile {str(g.resolve()).replace('\\', '/')}"
            break

    for g6 in geoip6_candidates:
        if g6.exists() and g6.is_file():
            geoip6_line = f"GeoIPv6File {str(g6.resolve()).replace('\\', '/')}"
            break

    log_file = str((tor_dir / "tor.log").resolve()).replace('\\', '/')

    # Working high-bandwidth obfs4 bridges for DPI bypass
    torrc_content = f"""# Standalone Headless Tor Config for Wvids Studio Pro
SocksPort {target_port}
DataDirectory {str(data_dir.resolve()).replace('\\', '/')}
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
    torrc_path.write_text(torrc_content.strip() + "\n", encoding="utf-8")

    return tor_exe, torrc_path

class HeadlessTorManager:
    """
    Manages a standalone headless Tor background process.
    Runs silently without requiring Tor Browser to be open.
    """
    def __init__(self):
        self._process: Optional[subprocess.Popen] = None
        self._port: int = TOR_DEFAULT_PORT
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
            if is_port_open(TOR_HOST, self._port, timeout=0.4):
                running = True
            elif is_port_open(TOR_HOST, 9150, timeout=0.4):
                running = True
            elif is_port_open(TOR_HOST, 9050, timeout=0.4):
                running = True

        binary = find_tor_binary()
        cfg_port = get_tor_config().get("port") or TOR_DEFAULT_PORT
        if managed and self._process and self._process.poll() is None:
            detected_port = self._port
        elif is_port_open(TOR_HOST, 9150, timeout=0.3):
            detected_port = 9150
        elif is_port_open(TOR_HOST, 9050, timeout=0.3):
            detected_port = 9050
        else:
            detected_port = cfg_port or TOR_DEFAULT_PORT

        return {
            "running": running,
            "managed_by_panel": managed,
            "pid": pid,
            "port": detected_port,
            "has_binary": bool(binary),
            "binary_path": str(binary) if binary else None
        }

    def start(self, port: int = 9150, wait_seconds: int = 25) -> Dict[str, Any]:
        self._port = port

        # 1. Check if already open on requested port
        if is_port_open(TOR_HOST, port, timeout=0.6):
            pid = None
            if PID_FILE.exists():
                try:
                    pid = int(PID_FILE.read_text(encoding="utf-8").strip())
                except Exception:
                    pass

            # If managed by our PID and responsive
            if pid and is_pid_running(pid):
                return {
                    "success": True,
                    "already_running": True,
                    "pid": pid,
                    "port": port,
                    "message": f"Tor уже запущен панелью и слушает порт {port}"
                }

            # If port is occupied by external service (e.g. Debian tor@default on 9050)
            # Quick connectivity check: does it actually work?
            probe = check_tor_connection(port=port, timeout=3)
            if probe.get("connected"):
                return {
                    "success": True,
                    "already_running": True,
                    "pid": None,
                    "port": port,
                    "message": f"Tor активен на порту {port} (Exit IP: {probe.get('ip')})"
                }
            else:
                # Port is occupied by an unresponsive or stuck process (e.g. system tor blocked by DPI)
                # Auto-fallback to 9150 if 9050 was requested
                if port == 9050:
                    add_log("WARNING", f"⚠️ Порт 9050 занят системным Tor без мостов (нет связи). Переключаемся на порт 9150...")
                    port = 9150
                    self._port = port
                    save_tor_config(enabled=True, port=9150)
                else:
                    add_log("WARNING", f"⚠️ Порт {port} занят другим процессом. Завершаем старый процесс Tor...")
                    if pid:
                        kill_process_tree(pid)
                        time.sleep(0.5)

        # 2. Setup standalone env
        tor_exe, torrc_path = setup_standalone_tor_env(target_port=port)
        if not tor_exe or not torrc_path:
            err = "Не найден исполняемый файл Tor для автономного запуска"
            add_log("ERROR", err)
            return {"success": False, "error": err}

        cmd = [str(tor_exe), "-f", str(torrc_path)]
        creationflags = 0
        start_new_session = False

        if os.name == "nt":
            DETACHED_PROCESS = 0x00000008
            CREATE_NEW_PROCESS_GROUP = 0x00000200
            CREATE_NO_WINDOW = 0x08000000
            creationflags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
        else:
            start_new_session = True

        try:
            # Clean old PID
            if PID_FILE.exists():
                try:
                    old_pid = int(PID_FILE.read_text(encoding="utf-8").strip())
                    kill_process_tree(old_pid)
                except Exception:
                    pass
                PID_FILE.unlink(missing_ok=True)

            proc = subprocess.Popen(
                cmd,
                cwd=str(tor_exe.parent),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                close_fds=True,
                creationflags=creationflags,
                start_new_session=start_new_session
            )
            self._process = proc
            self._binary_path = tor_exe

            try:
                PID_FILE.write_text(str(proc.pid), encoding="utf-8")
            except Exception:
                pass

            add_log("INFO", f"🧅 Запущен автономный headless Tor (PID: {proc.pid}, порт: {port})")

            # Wait for socket to become available and bootstrap
            start_t = time.time()
            port_ready = False
            while time.time() - start_t < wait_seconds:
                if is_port_open(TOR_HOST, port, timeout=0.8):
                    port_ready = True
                    break
                if proc.poll() is not None:
                    # Process died early
                    break
                time.sleep(0.5)

            if not port_ready:
                err_detail = "Процесс завершился с ошибкой" if proc.poll() is not None else f"порт {port} не открылся за {wait_seconds}с"
                return {
                    "success": False,
                    "error": f"Сбой запуска Tor: {err_detail}. Проверьте logs/tor.log"
                }

            # Verify connection through Tor circuit
            conn_test = check_tor_connection(port=port, timeout=12)
            if conn_test.get("connected"):
                add_log("SUCCESS", f"🧅 Автономный Tor готов к работе! (Exit IP: {conn_test.get('ip')}, порт: {port})")
                save_tor_config(enabled=True, port=port)
                return {
                    "success": True,
                    "already_running": False,
                    "pid": proc.pid,
                    "port": port,
                    "ip": conn_test.get("ip"),
                    "message": f"Автономный Tor успешно запущен и подключен (Exit IP: {conn_test.get('ip')}, порт: {port})"
                }
            else:
                add_log("WARNING", f"🧅 Порт {port} открыт, цепь Tor строится...")
                return {
                    "success": True,
                    "already_running": False,
                    "pid": proc.pid,
                    "port": port,
                    "message": f"Tor запущен на порту {port}. Построение цепи через мосты obfs4..."
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

        time.sleep(0.4)

        if stopped or pid:
            add_log("INFO", f"🧅 Автономный Tor (PID: {pid}) остановлен")
            return {"success": True, "message": f"Фоновый процесс Tor (PID: {pid}) остановлен"}
        return {"success": True, "message": "Фоновый процесс Tor не был активен"}

    def new_circuit(self) -> Dict[str, Any]:
        """
        Requests a fresh Tor circuit / new exit node (via SIGHUP on Linux or restart).
        """
        pid = None
        if PID_FILE.exists():
            try:
                pid = int(PID_FILE.read_text(encoding="utf-8").strip())
            except Exception:
                pass

        if pid and is_pid_running(pid):
            if os.name != "nt":
                try:
                    os.kill(pid, signal.SIGHUP)
                    time.sleep(1.2)
                    add_log("INFO", "🧅 Отправлен сигнал SIGHUP в Tor: смена выходного узла и цепи")
                    test_c = check_tor_connection(port=self._port or 9150, timeout=8)
                    return {
                        "success": True,
                        "message": f"Новая цепь Tor создана (Новый IP: {test_c.get('ip', 'динамический')})",
                        "status": test_c
                    }
                except Exception as e:
                    add_log("WARNING", f"Сбой отправки SIGHUP в Tor: {e}")

        # Fallback: clean restart of standalone Tor
        add_log("INFO", "🧅 Перезапуск автономного Tor для получения новой цепи...")
        self.stop()
        res = self.start(port=self._port or 9150)
        return res

    def ensure_running(self, port: int = 9150) -> bool:
        """
        Ensures Tor is active and connected. If not, auto-starts headless Tor.
        """
        # 1. Quick probe of current requested port
        if is_port_open(TOR_HOST, port, timeout=0.5):
            probe = check_tor_connection(port=port, timeout=2)
            if probe.get("connected"):
                return True

        # 2. Check 9150 if different
        if port != 9150 and is_port_open(TOR_HOST, 9150, timeout=0.5):
            probe9150 = check_tor_connection(port=9150, timeout=2)
            if probe9150.get("connected"):
                self._port = 9150
                return True

        # 3. Otherwise launch standalone
        res = self.start(port=port)
        return bool(res.get("success"))

# Global Tor manager instance
tor_manager = HeadlessTorManager()

def get_tor_service_status() -> Dict[str, Any]:
    return tor_manager.get_status()

def start_tor_service(port: int = 9150) -> Dict[str, Any]:
    return tor_manager.start(port=port)

def stop_tor_service() -> Dict[str, Any]:
    return tor_manager.stop()

def new_tor_circuit() -> Dict[str, Any]:
    return tor_manager.new_circuit()

def auto_detect_tor_port() -> Optional[int]:
    """
    Detects active Tor port:
    1. Managed standalone Tor process port
    2. Configured port from database
    3. 9150 (standalone / Tor Browser)
    4. 9050 (system daemon fallback)
    """
    status = tor_manager.get_status()
    if status.get("managed_by_panel") and status.get("port"):
        return status["port"]

    cfg = get_tor_config()
    cfg_port = cfg.get("port")
    if cfg_port and is_port_open(TOR_HOST, cfg_port, timeout=0.4):
        return cfg_port

    for p in [TOR_DEFAULT_PORT, TOR_FALLBACK_PORT]:
        if is_port_open(TOR_HOST, p, timeout=0.4):
            return p
    return None

def get_tor_proxy_url(port: Optional[int] = None) -> str:
    """Builds socks5h:// URL ensuring DNS is resolved remotely on Tor exit node."""
    if not port:
        cfg = get_tor_config()
        port = cfg.get("port") or auto_detect_tor_port() or TOR_DEFAULT_PORT
    return f"socks5h://{TOR_HOST}:{port}"

def check_tor_connection(port: Optional[int] = None, timeout: int = 10) -> Dict[str, Any]:
    """
    Checks Tor connectivity via check.torproject.org and api.ipify.org.
    Verifies that the IP is indeed a Tor exit node.
    """
    if port is None:
        cfg = get_tor_config()
        port = cfg.get("port") or auto_detect_tor_port() or TOR_DEFAULT_PORT

    if not is_port_open(TOR_HOST, port, timeout=0.4):
        detected = auto_detect_tor_port()
        if detected:
            port = detected
        else:
            return {
                "connected": False,
                "is_tor": False,
                "ip": None,
                "port": port,
                "latency_ms": 0,
                "proxy_url": f"socks5h://{TOR_HOST}:{port}",
                "error": f"Порт Tor {port} закрыт. Запустите Tor в панели или Tor Browser."
            }

    proxy_url = f"socks5h://{TOR_HOST}:{port}"
    proxies = {"http": proxy_url, "https": proxy_url}

    start_t = time.time()
    try:
        s = requests.Session(impersonate=DEFAULT_FINGERPRINT, proxies=proxies)
        
        # 1. Primary check: official Tor Project API
        is_tor = False
        ip = "Unknown"
        api_timeout = min(timeout, 8)
        try:
            r = s.get("https://check.torproject.org/api/ip", timeout=api_timeout)
            if r.status_code == 200:
                data = r.json()
                is_tor = bool(data.get("IsTor", False))
                ip = data.get("IP", "Unknown")
        except Exception:
            # Fallback to ipify if check.torproject is slow
            try:
                r_ip = s.get("https://api.ipify.org?format=json", timeout=6)
                if r_ip.status_code == 200:
                    ip = r_ip.json().get("ip", "Unknown")
                    is_tor = True
            except Exception:
                pass

        elapsed_ms = int((time.time() - start_t) * 1000)

        if not ip or ip == "Unknown":
            return {
                "connected": False,
                "is_tor": False,
                "ip": None,
                "port": port,
                "latency_ms": elapsed_ms,
                "error": f"Порт {port} слушает, но цепь Tor еще не построена или заблокирована",
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
