import os
from pathlib import Path

# Paths
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
UPLOADS_DIR = BASE_DIR / "uploads"
VIDEOS_DIR = BASE_DIR / "videos"
DB_PATH = DATA_DIR / "wvids_panel.db"

DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
VIDEOS_DIR.mkdir(parents=True, exist_ok=True)

# Default Host & Endpoints
DEFAULT_HOST = "https://wvids.com"
UPLOAD_HOST_FALLBACK = "https://add4.oakroot.top"

URL_MAIN = f"{DEFAULT_HOST}/"
URL_SHARE = f"{DEFAULT_HOST}/share"
URL_SETTINGS = f"{DEFAULT_HOST}/settings"
URL_UPLOAD_TOKEN = f"{DEFAULT_HOST}/api/upload_token.php"
URL_LOGIN_API = f"{DEFAULT_HOST}/api/login.php"
URL_CHART_UID = f"{DEFAULT_HOST}/api/get_chart_uid.php"
URL_CHART_FID = f"{DEFAULT_HOST}/api/get_chart_fid.php"
URL_USERS_API = f"{DEFAULT_HOST}/api/api_users.php"
URL_REDACT_CONTENT = f"{DEFAULT_HOST}/api/redact_content.php"
URL_MANAGEFORMS = f"{DEFAULT_HOST}/api/manageforms.php"

# Upload limits
MAX_FILE_SIZE_BYTES = 300 * 1024 * 1024  # 300 MB

# Anti-detect impersonation presets supported by curl_cffi
SUPPORTED_FINGERPRINTS = [
    "chrome124",
    "chrome120",
    "chrome119",
    "safari17_0",
    "safari15_5",
    "edge101"
]
DEFAULT_FINGERPRINT = "chrome124"

# Tor Routing Configuration (socks5h for 0 DNS leak)
TOR_ENABLED = True
TOR_HOST = "127.0.0.1"
TOR_DEFAULT_PORT = 9050   # Standalone Headless Tor service port
TOR_FALLBACK_PORT = 9150  # Tor Browser port
TOR_STRICT_MODE = True    # Blocks non-Tor traffic to prevent real IP leaks

# Standard Web Panel Server Settings
HOST = "127.0.0.1"
PORT = 8088
