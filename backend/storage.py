import sqlite3
import json
import time
from typing import List, Dict, Any, Optional
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
def get_all_accounts() -> List[Dict[str, Any]]:
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM accounts ORDER BY id DESC")
    rows = c.fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        d["cookies"] = json.loads(d["cookies_json"]) if d["cookies_json"] else {}
        result.append(d)
    return result

def get_account_by_id(account_id: int) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM accounts WHERE id = ?", (account_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    d["cookies"] = json.loads(d["cookies_json"]) if d["cookies_json"] else {}
    return d

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
    d = dict(row)
    d["cookies"] = json.loads(d["cookies_json"]) if d["cookies_json"] else {}
    return d

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

def get_uploaded_filenames() -> set:
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT DISTINCT filename FROM uploads WHERE status = 'success'")
    rows = c.fetchall()
    conn.close()
    return {r["filename"] for r in rows if r["filename"]}

def get_duplicate_filenames() -> set:
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT DISTINCT filename FROM uploads WHERE status = 'duplicate'")
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

# Initialize tables
init_db()
