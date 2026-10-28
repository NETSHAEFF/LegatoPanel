import sqlite3
import json
import time
from typing import List, Dict, Any, Optional, Tuple
from .config import DB_PATH

def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS accounts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        username TEXT,
        uid INTEGER,
        cookies_raw TEXT,
        cookies_json TEXT,
        proxy TEXT,
        fingerprint TEXT DEFAULT 'chrome124',
        subscribers INTEGER DEFAULT 0,
        views_today INTEGER DEFAULT 0,
        total_views INTEGER DEFAULT 0,
        is_active INTEGER DEFAULT 0,
        last_checked INTEGER DEFAULT 0,
        created_at INTEGER
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS uploads (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id INTEGER,
        filename TEXT,
        file_path TEXT,
        file_type TEXT,
        file_size INTEGER,
        title TEXT,
        description TEXT,
        remote_fid TEXT,
        status TEXT,
        error TEXT,
        created_at INTEGER,
        FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE SET NULL
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        level TEXT,
        message TEXT,
        details TEXT,
        created_at INTEGER
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )
    """)

    # Check and migrate accounts table columns for suspension support
    cursor.execute("PRAGMA table_info(accounts)")
    account_cols = [col[1] for col in cursor.fetchall()]
    if "is_suspended" not in account_cols:
        cursor.execute("ALTER TABLE accounts ADD COLUMN is_suspended INTEGER DEFAULT 0")
    if "suspension_reason" not in account_cols:
        cursor.execute("ALTER TABLE accounts ADD COLUMN suspension_reason TEXT DEFAULT ''")
    if "suspended_at" not in account_cols:
        cursor.execute("ALTER TABLE accounts ADD COLUMN suspended_at INTEGER DEFAULT 0")
    if "title_template" not in account_cols:
        cursor.execute("ALTER TABLE accounts ADD COLUMN title_template TEXT DEFAULT ''")
    if "desc_template" not in account_cols:
        cursor.execute("ALTER TABLE accounts ADD COLUMN desc_template TEXT DEFAULT ''")
    if "watermark_settings" not in account_cols:
        cursor.execute("ALTER TABLE accounts ADD COLUMN watermark_settings TEXT DEFAULT ''")
    if "is_batch_enabled" not in account_cols:
        cursor.execute("ALTER TABLE accounts ADD COLUMN is_batch_enabled INTEGER DEFAULT 1")

    # Seed default Tor settings (enabled by default)
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('tor_enabled', '1')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('tor_port', '9150')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('tor_host', '127.0.0.1')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('tor_strict', '1')")

    conn.commit()
    conn.close()

# Log Helper
def add_log(level: str, message: str, details: str = ""):
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        "INSERT INTO logs (level, message, details, created_at) VALUES (?, ?, ?, ?)",
        (level.upper(), message, details, int(time.time()))
    )
    conn.commit()
    conn.close()

def get_recent_logs(limit: int = 100) -> List[Dict[str, Any]]:
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM logs ORDER BY id DESC LIMIT ?", (limit,))
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def clear_logs():
    conn = get_connection()
    c = conn.cursor()
    c.execute("DELETE FROM logs")
    conn.commit()
    conn.close()

# Accounts Management
def format_account_row(r) -> Dict[str, Any]:
    if not r:
        return {}
    d = dict(r)
    d["cookies"] = json.loads(d["cookies_json"]) if d.get("cookies_json") else {}
    d["is_suspended"] = bool(d.get("is_suspended", 0))
    d["suspension_reason"] = d.get("suspension_reason") or ""
    d["suspended_at"] = d.get("suspended_at") or 0
    d["title_template"] = d.get("title_template") or ""
    d["desc_template"] = d.get("desc_template") or ""
    d["is_batch_enabled"] = bool(d.get("is_batch_enabled", 1))

    # Parse watermark_settings JSON
    wm_raw = d.get("watermark_settings")
    if wm_raw:
        try:
            d["watermark_settings"] = json.loads(wm_raw) if isinstance(wm_raw, str) else wm_raw
        except Exception:
            d["watermark_settings"] = {}
    else:
        d["watermark_settings"] = {}

    d["has_custom_watermark"] = bool(d["watermark_settings"].get("use_custom_watermark", False))
    return d

def update_account_settings(
    account_id: int,
    title_template: Optional[str] = None,
    desc_template: Optional[str] = None,
    watermark_settings: Optional[Dict[str, Any]] = None,
    is_batch_enabled: Optional[bool] = None
) -> Dict[str, Any]:
    acc = get_account_by_id(account_id)
    if not acc:
        raise ValueError(f"Account {account_id} not found")

    conn = get_connection()
    c = conn.cursor()

    updates = []
    params = []

    if title_template is not None:
        updates.append("title_template = ?")
        params.append(title_template.strip())

    if desc_template is not None:
        updates.append("desc_template = ?")
        params.append(desc_template.strip())

    if is_batch_enabled is not None:
        updates.append("is_batch_enabled = ?")
        params.append(1 if is_batch_enabled else 0)

    if watermark_settings is not None:
        existing_wm = acc.get("watermark_settings") or {}
        existing_wm.update(watermark_settings)
        updates.append("watermark_settings = ?")
        params.append(json.dumps(existing_wm, ensure_ascii=False))

    if updates:
        params.append(account_id)
        c.execute(f"UPDATE accounts SET {', '.join(updates)} WHERE id = ?", params)
        conn.commit()

    conn.close()
    return get_account_by_id(account_id)

def get_all_accounts() -> List[Dict[str, Any]]:
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM accounts ORDER BY id DESC")
    rows = c.fetchall()
    conn.close()
    return [format_account_row(r) for r in rows]

def get_account_by_id(account_id: int) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM accounts WHERE id = ?", (account_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        return None
    return format_account_row(row)

def get_active_account() -> Optional[Dict[str, Any]]:
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM accounts WHERE is_active = 1 LIMIT 1")
    row = c.fetchone()
    if not row:
        c.execute("SELECT * FROM accounts ORDER BY id DESC LIMIT 1")
        row = c.fetchone()
    conn.close()
    if not row:
        return None
    return format_account_row(row)

def mark_account_suspended(account_id: int, reason: str = "Account suspended"):
    conn = get_connection()
    c = conn.cursor()
    now = int(time.time())
    c.execute(
        "UPDATE accounts SET is_suspended = 1, suspension_reason = ?, suspended_at = ? WHERE id = ?",
        (reason, now, account_id)
    )
    conn.commit()
    conn.close()
    add_log("ERROR", f"🚨 [ВНИМАНИЕ] Аккаунт #{account_id} заблокирован на wvids.com: {reason}")

def mark_account_active(account_id: int):
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        "UPDATE accounts SET is_suspended = 0, suspension_reason = '', suspended_at = 0 WHERE id = ?",
        (account_id,)
    )
    conn.commit()
    conn.close()
    add_log("INFO", f"✅ Аккаунт #{account_id} проверен: Активен (блокировка отсутствует)")

def set_active_account(account_id: int):
    conn = get_connection()
    c = conn.cursor()
    c.execute("UPDATE accounts SET is_active = 0")
    c.execute("UPDATE accounts SET is_active = 1 WHERE id = ?", (account_id,))
    conn.commit()
    conn.close()

def save_account(
    name: str,
    username: str,
    uid: int,
    cookies_raw: str,
    cookies_dict: Dict[str, str],
    proxy: str = "",
    fingerprint: str = "chrome124",
    account_id: Optional[int] = None
) -> int:
    conn = get_connection()
    c = conn.cursor()
    now = int(time.time())
    cookies_json = json.dumps(cookies_dict)

    if account_id:
        c.execute("""
        UPDATE accounts SET
            name = ?, username = ?, uid = ?, cookies_raw = ?, cookies_json = ?,
            proxy = ?, fingerprint = ?
        WHERE id = ?
        """, (name, username, uid, cookies_raw, cookies_json, proxy, fingerprint, account_id))
        saved_id = account_id
    else:
        # Check if already exists by uid or username
        c.execute("SELECT id FROM accounts WHERE uid = ? OR username = ?", (uid, username))
        existing = c.fetchone()
        if existing and uid:
            saved_id = existing["id"]
            c.execute("""
            UPDATE accounts SET
                name = ?, username = ?, uid = ?, cookies_raw = ?, cookies_json = ?,
                proxy = ?, fingerprint = ?
            WHERE id = ?
            """, (name, username, uid, cookies_raw, cookies_json, proxy, fingerprint, saved_id))
        else:
            c.execute("""
            INSERT INTO accounts (name, username, uid, cookies_raw, cookies_json, proxy, fingerprint, is_active, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
            """, (name, username, uid, cookies_raw, cookies_json, proxy, fingerprint, now))
            saved_id = c.lastrowid
            # Set active
            c.execute("UPDATE accounts SET is_active = 0 WHERE id != ?", (saved_id,))

    conn.commit()
    conn.close()
    return saved_id

def update_account_stats(account_id: int, subscribers: int, views_today: int, total_views: int):
    conn = get_connection()
    c = conn.cursor()
    now = int(time.time())
    c.execute("""
    UPDATE accounts SET
        subscribers = ?, views_today = ?, total_views = ?, last_checked = ?
    WHERE id = ?
    """, (subscribers, views_today, total_views, now, account_id))
    conn.commit()
    conn.close()

def update_account_username(account_id: int, new_username: str):
    conn = get_connection()
    c = conn.cursor()
    c.execute("UPDATE accounts SET username = ? WHERE id = ?", (new_username, account_id))
    conn.commit()
    conn.close()

def delete_account(account_id: int):
    conn = get_connection()
    c = conn.cursor()
    c.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
    conn.commit()
    conn.close()

# Uploads History
def add_upload_record(
    account_id: Optional[int],
    filename: str,
    file_path: str,
    file_type: str,
    file_size: int,
    title: str,
    description: str,
    status: str = "pending",
    remote_fid: str = "",
    error: str = ""
) -> int:
    conn = get_connection()
    c = conn.cursor()
    now = int(time.time())
    c.execute("""
    INSERT INTO uploads (account_id, filename, file_path, file_type, file_size, title, description, remote_fid, status, error, created_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (account_id, filename, file_path, file_type, file_size, title, description, remote_fid, status, error, now))
    rec_id = c.lastrowid
    conn.commit()
    conn.close()
    return rec_id

def update_upload_record(record_id: int, status: str, remote_fid: str = "", error: str = ""):
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
    UPDATE uploads SET status = ?, remote_fid = COALESCE(NULLIF(?, ''), remote_fid), error = ?
    WHERE id = ?
    """, (status, remote_fid, error, record_id))
    conn.commit()
    conn.close()

def get_recent_uploads(limit: int = 50) -> List[Dict[str, Any]]:
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
    SELECT u.*, a.username as account_username
    FROM uploads u
    LEFT JOIN accounts a ON u.account_id = a.id
    ORDER BY u.id DESC LIMIT ?
    """, (limit,))
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_uploaded_filenames(account_id: Optional[int] = None) -> set:
    conn = get_connection()
    c = conn.cursor()
    if account_id:
        c.execute("SELECT DISTINCT filename FROM uploads WHERE status = 'success' AND account_id = ?", (account_id,))
    else:
        c.execute("SELECT DISTINCT filename FROM uploads WHERE status = 'success'")
    rows = c.fetchall()
    conn.close()
    return {r["filename"] for r in rows if r["filename"]}

def get_duplicate_filenames(account_id: Optional[int] = None) -> set:
    conn = get_connection()
    c = conn.cursor()
    if account_id:
        c.execute("SELECT DISTINCT filename FROM uploads WHERE status = 'duplicate' AND account_id = ?", (account_id,))
    else:
        c.execute("SELECT DISTINCT filename FROM uploads WHERE status = 'duplicate'")
    rows = c.fetchall()
    conn.close()
    return {r["filename"] for r in rows if r["filename"]}

def get_skipped_filenames(account_id: Optional[int] = None) -> set:
    conn = get_connection()
    c = conn.cursor()
    if account_id:
        c.execute("SELECT DISTINCT filename FROM uploads WHERE status = 'skipped' AND account_id = ?", (account_id,))
    else:
        c.execute("SELECT DISTINCT filename FROM uploads WHERE status = 'skipped'")
    rows = c.fetchall()
    conn.close()
    return {r["filename"] for r in rows if r["filename"]}

# Settings API
def get_setting(key: str, default: str = "") -> str:
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = c.fetchone()
    conn.close()
    if row and row["value"] is not None:
        return row["value"]
    return default

def set_setting(key: str, value: str):
    conn = get_connection()
    c = conn.cursor()
    c.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = ?", (key, value, value))
    conn.commit()
    conn.close()

def get_tor_config() -> Dict[str, Any]:
    enabled = get_setting("tor_enabled", "1") == "1"
    port_str = get_setting("tor_port", "9150")
    try:
        port = int(port_str)
    except ValueError:
        port = 9150
    strict = get_setting("tor_strict", "1") == "1"
    host = get_setting("tor_host", "127.0.0.1")
    return {
        "enabled": enabled,
        "port": port,
        "host": host,
        "strict": strict,
        "proxy_url": f"socks5h://{host}:{port}"
    }

def save_tor_config(enabled: bool, port: int, strict: bool = True, host: str = "127.0.0.1"):
    set_setting("tor_enabled", "1" if enabled else "0")
    set_setting("tor_port", str(port))
    set_setting("tor_host", host)
    set_setting("tor_strict", "1" if strict else "0")

DEFAULT_PROCESSING_SETTINGS: Dict[str, Any] = {
    # Watermark General Mode
    "watermark_enabled": True,
    "watermark_mode": "text",              # "both", "text", "media", "none"

    # Text Watermark Settings
    "watermark_text": "@wvids",
    "watermark_font": "arial.ttf",
    "watermark_size": 36,
    "watermark_color": "#ffffff",
    "watermark_opacity": 0.9,
    "watermark_position": "bottom_right",  # bottom_right, bottom_left, top_right, top_left, center, bottom_center, top_center
    "watermark_margin_x": 30,
    "watermark_margin_y": 30,
    "watermark_box_enabled": True,
    "watermark_box_color": "#000000",
    "watermark_box_opacity": 0.55,
    "watermark_box_border": 10,
    "watermark_start_time": 0.0,           # seconds after start (0 = сразу)
    "watermark_duration": 0.0,             # seconds duration (0 = до конца)

    # Media Watermark Settings (Photo or Video via Drag & Drop)
    "watermark_media_path": "",            # absolute path to uploaded image/video
    "watermark_media_name": "",            # original filename
    "watermark_media_type": "image",       # "image" or "video"
    "watermark_media_scale": 18,           # % of video width (10-50%)
    "watermark_media_opacity": 0.85,       # 0.1 to 1.0
    "watermark_media_position": "top_right", # bottom_right, bottom_left, top_right, top_left, center...
    "watermark_media_margin_x": 30,
    "watermark_media_margin_y": 30,
    "watermark_media_start_time": 2.0,     # e.g. appears 2 seconds after video starts
    "watermark_media_duration": 0.0,       # 0 = till end of video

    # Uniqueizer Settings
    "unique_enabled": True,
    "unique_metadata": True,
    "unique_device_profile": "random",   # random, iphone15, s24, pixel8, sony_a7
    "unique_duration_mode": "random",    # random, add, trim, speed, none
    "unique_duration_delta": 1.0,        # seconds to add or trim
    "unique_visual_hash": True,          # micro-crop spatial jitter
    "unique_noise": False,               # dynamic sensor noise (off by default for speed)
    "unique_audio_hash": False,          # pitch shift, eq, echo (off by default for audio copy)
    "unique_preset": "ultrafast",        # ultrafast for high rendering throughput
    "unique_crf": 24,                    # 20-28 (24 is optimal for web & Tor speed)
    "max_processing_time_sec": 300,      # 5 minutes processing limit
    "skip_if_exceeds_time": True         # Auto-skip video if uniqueization will take > 5 minutes
}

def get_processing_settings() -> Dict[str, Any]:
    raw = get_setting("video_processing_settings", "")
    cfg = dict(DEFAULT_PROCESSING_SETTINGS)
    if raw:
        try:
            stored = json.loads(raw)
            cfg.update(stored)
        except Exception:
            pass
    return cfg

def save_processing_settings(new_cfg: Dict[str, Any]) -> Dict[str, Any]:
    curr = get_processing_settings()
    curr.update(new_cfg)
    set_setting("video_processing_settings", json.dumps(curr, ensure_ascii=False))
    return curr

def get_account_processing_settings(account_id: Optional[int] = None) -> Dict[str, Any]:
    """
    Returns effective processing & watermark settings for a specific account.
    If account has custom watermark settings enabled (use_custom_watermark=True),
    they override the global defaults.
    """
    global_cfg = get_processing_settings()
    if not account_id:
        return global_cfg

    acc = get_account_by_id(account_id)
    if not acc:
        return global_cfg

    custom_cfg = acc.get("watermark_settings") or {}
    if not custom_cfg.get("use_custom_watermark", False):
        # Fall back to global settings, but if watermark_text is default '@wvids', replace with account username
        cfg = dict(global_cfg)
        if cfg.get("watermark_text") == "@wvids" and acc.get("username"):
            cfg["watermark_text"] = f"@{acc['username']}"
        return cfg

    merged = dict(global_cfg)
    merged.update(custom_cfg)
    # Ensure username tag matches account if empty
    if not merged.get("watermark_text") and acc.get("username"):
        merged["watermark_text"] = f"@{acc['username']}"
    return merged

def save_account_processing_settings(account_id: int, new_cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Saves custom processing / watermark settings for a specific account."""
    acc = get_account_by_id(account_id)
    if not acc:
        raise ValueError(f"Account {account_id} not found")

    existing_wm = acc.get("watermark_settings") or {}
    existing_wm.update(new_cfg)
    existing_wm["use_custom_watermark"] = True

    conn = get_connection()
    c = conn.cursor()
    c.execute(
        "UPDATE accounts SET watermark_settings = ? WHERE id = ?",
        (json.dumps(existing_wm, ensure_ascii=False), account_id)
    )
    conn.commit()
    conn.close()

    return get_account_processing_settings(account_id)

def get_account_metadata_templates(account_id: int) -> Tuple[str, str]:
    """Returns (title_template, desc_template) for an account, falling back to global batch templates."""
    acc = get_account_by_id(account_id)
    global_title = get_setting("batch_title", "")
    global_desc = get_setting("batch_desc", "")
    if not acc:
        return global_title, global_desc

    title = acc.get("title_template", "").strip() or global_title
    desc = acc.get("desc_template", "").strip() or global_desc
    return title, desc

# Initialize tables
init_db()
