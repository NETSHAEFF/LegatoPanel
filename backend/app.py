import os
import shutil
import asyncio
from pathlib import Path
from typing import Optional, Dict, Any, List
from fastapi import FastAPI, Request, UploadFile, File, Form, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from .config import (
    BASE_DIR,
    UPLOADS_DIR,
    VIDEOS_DIR,
    DEFAULT_FINGERPRINT,
    SUPPORTED_FINGERPRINTS
)
from .fingerprint import parse_cookies_input
from .client import WvidsClient
from .uploader import ContentUploader
from .batch_worker import batch_worker
from .bulk_redactor import bulk_redactor
from .tor import (
    check_tor_connection,
    auto_detect_tor_port,
    TorRoutingError,
    get_tor_proxy_url
)
from .storage import (
    get_all_accounts,
    get_account_by_id,
    get_active_account,
    set_active_account,
    save_account,
    update_account_stats,
    update_account_username,
    delete_account,
    add_upload_record,
    get_recent_uploads,
    get_recent_logs,
    clear_logs,
    add_log,
    get_tor_config,
    save_tor_config,
    get_setting,
    set_setting
)

app = FastAPI(title="Wvids Manager Panel", version="1.0.0")

@app.exception_handler(TorRoutingError)
async def tor_error_handler(request: Request, exc: TorRoutingError):
    return JSONResponse(
        status_code=400,
        content={"success": False, "error": str(exc), "tor_blocked": True}
    )

# Setup templates and static files
TEMPLATES_DIR = BASE_DIR / "frontend" / "templates"
STATIC_DIR = BASE_DIR / "frontend" / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# In-memory progress tracker for live uploads
active_uploads: Dict[str, Dict[str, Any]] = {}

def get_client_for_account(account: Optional[Dict[str, Any]] = None) -> WvidsClient:
    if not account:
        account = get_active_account()
    if not account:
        return WvidsClient(use_tor=True)
    return WvidsClient(
        cookies=account.get("cookies", {}),
        fingerprint=account.get("fingerprint") or DEFAULT_FINGERPRINT,
        use_tor=True
    )

# --- Routes ---

@app.get("/", response_class=HTMLResponse)
async def serve_index(request: Request):
    return templates.TemplateResponse(request, "index.html")

# Accounts & Auth
class VerifyAccountRequest(BaseModel):
    name: Optional[str] = "Wvids Account"
    cookies: str
    fingerprint: Optional[str] = DEFAULT_FINGERPRINT

@app.get("/api/accounts")
async def list_accounts():
    accounts = get_all_accounts()
    active = get_active_account()
    return {
        "success": True,
        "accounts": accounts,
        "active_account_id": active["id"] if active else None
    }

@app.post("/api/accounts/verify")
async def verify_and_save_account(req: VerifyAccountRequest):
    cookies_dict = parse_cookies_input(req.cookies)
    if not cookies_dict:
        raise HTTPException(status_code=400, detail="Не удалось распознать cookies. Вставьте строку cookie или JSON.")

    client = WvidsClient(
        cookies=cookies_dict,
        fingerprint=req.fingerprint or DEFAULT_FINGERPRINT,
        use_tor=True
    )

    verify_result = await client.verify_session()
    if not verify_result.get("valid"):
        detail_msg = verify_result.get("error") or "Сессия не авторизована на wvids.com. Проверьте cookies auth_user_id / auth_sess / auth_p."
        raise HTTPException(status_code=400, detail=detail_msg)

    uid = verify_result.get("uid")
    username = verify_result.get("username")
    acc_name = req.name or f"@{username}"

    # Fetch initial stats
    stats = await client.get_statistics(uid, username)
    subscribers = stats.get("subscribers", 0) if stats.get("success") else 0
    views_today = stats.get("views_today", 0) if stats.get("success") else 0
    total_views = stats.get("total_views", 0) if stats.get("success") else 0

    acc_id = save_account(
        name=acc_name,
        username=username,
        uid=uid,
        cookies_raw=req.cookies,
        cookies_dict=cookies_dict,
        proxy="",
        fingerprint=req.fingerprint or DEFAULT_FINGERPRINT
    )
    update_account_stats(acc_id, subscribers, views_today, total_views)

    return {
        "success": True,
        "account_id": acc_id,
        "username": username,
        "uid": uid,
        "subscribers": subscribers,
        "views_today": views_today,
        "total_views": total_views,
        "message": f"Аккаунт @{username} успешно подключен!"
    }

@app.post("/api/accounts/activate/{account_id}")
async def activate_account(account_id: int):
    acc = get_account_by_id(account_id)
    if not acc:
        raise HTTPException(status_code=404, detail="Аккаунт не найден")
    set_active_account(account_id)
    add_log("INFO", f"Активирован аккаунт @{acc.get('username')}")
    return {"success": True, "message": f"Аккаунт @{acc.get('username')} выбран активным"}

@app.delete("/api/accounts/{account_id}")
async def remove_account(account_id: int):
    delete_account(account_id)
    add_log("INFO", f"Удален аккаунт ID {account_id}")
    return {"success": True, "message": "Аккаунт удален"}

# Statistics
@app.get("/api/statistics")
async def get_active_statistics():
    active = get_active_account()
    if not active:
        return {
            "success": False,
            "error": "Нет активного аккаунта. Добавьте cookies во вкладке авторизации."
        }

    client = get_client_for_account(active)
    stats = await client.get_statistics(active["uid"], active["username"])

    if stats.get("success"):
        update_account_stats(
            active["id"],
            stats.get("subscribers", 0),
            stats.get("views_today", 0),
            stats.get("total_views", 0)
        )

    return {
        "success": stats.get("success", False),
        "account": {
            "id": active["id"],
            "name": active["name"],
            "username": active["username"],
            "uid": active["uid"],
            "proxy": active["proxy"],
            "fingerprint": active["fingerprint"]
        },
        "stats": stats
    }

# Nickname Changing
class ChangeNicknameRequest(BaseModel):
    new_nickname: str

@app.post("/api/account/nickname")
async def change_nickname_endpoint(req: ChangeNicknameRequest):
    active = get_active_account()
    if not active:
        raise HTTPException(status_code=400, detail="Нет активного аккаунта")

    new_nick = req.new_nickname.strip().lstrip("@")
    if not new_nick:
        raise HTTPException(status_code=400, detail="Никнейм не может быть пустым")

    client = get_client_for_account(active)
    result = await client.change_nickname(new_nick)

    if result.get("success"):
        update_account_username(active["id"], new_nick)
        return {
            "success": True,
            "new_nickname": new_nick,
            "message": f"Никнейм успешно изменен на @{new_nick}!"
        }
    else:
        raise HTTPException(status_code=400, detail=result.get("error", "Не удалось изменить никнейм"))

# Content Uploading
@app.post("/api/upload")
async def upload_file_form(
    file: UploadFile = File(...),
    title: str = Form(""),
    description: str = Form("")
):
    active = get_active_account()
    if not active:
        raise HTTPException(status_code=400, detail="Сначала добавьте и активируйте аккаунт")

    # Save incoming file to local uploads directory
    temp_path = UPLOADS_DIR / file.filename
    with open(temp_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    file_size = temp_path.stat().st_size
    record_id = add_upload_record(
        account_id=active["id"],
        filename=file.filename,
        file_path=str(temp_path),
        file_type=file.content_type or "media",
        file_size=file_size,
        title=title,
        description=description,
        status="uploading"
    )

    client = get_client_for_account(active)
    uploader = ContentUploader(client)

    upload_result = await uploader.upload(
        file_path=str(temp_path),
        title=title,
        description=description,
        record_id=record_id
    )

    if upload_result.get("success"):
        return {
            "success": True,
            "result": upload_result
        }
    else:
        raise HTTPException(status_code=400, detail=upload_result.get("error", "Ошибка загрузки"))

class LocalPathUploadRequest(BaseModel):
    file_path: str
    title: Optional[str] = ""
    description: Optional[str] = ""

@app.post("/api/upload-local-path")
async def upload_local_path(req: LocalPathUploadRequest):
    active = get_active_account()
    if not active:
        raise HTTPException(status_code=400, detail="Сначала добавьте и активируйте аккаунт")

    p = Path(req.file_path.strip().strip('"').strip("'"))
    if not p.exists() or not p.is_file():
        raise HTTPException(status_code=400, detail=f"Файл не найден на ПК: {req.file_path}")

    file_size = p.stat().st_size
    record_id = add_upload_record(
        account_id=active["id"],
        filename=p.name,
        file_path=str(p.resolve()),
        file_type="media",
        file_size=file_size,
        title=req.title or "",
        description=req.description or "",
        status="uploading"
    )

    client = get_client_for_account(active)
    uploader = ContentUploader(client)

    upload_result = await uploader.upload(
        file_path=str(p.resolve()),
        title=req.title or "",
        description=req.description or "",
        record_id=record_id
    )

    if upload_result.get("success"):
        return {
            "success": True,
            "result": upload_result
        }
    else:
        raise HTTPException(status_code=400, detail=upload_result.get("error", "Ошибка загрузки"))

@app.get("/api/uploads")
async def list_recent_uploads():
    uploads = get_recent_uploads(50)
    return {"success": True, "uploads": uploads}

# Batch Upload Queue API
class StartBatchRequest(BaseModel):
    folder_path: Optional[str] = "videos"
    delay_minutes: Optional[float] = 5.0
    title_template: Optional[str] = ""
    desc_template: Optional[str] = ""

@app.get("/api/batch/files")
async def get_batch_files(folder: Optional[str] = "videos"):
    target = folder or "videos"
    p = VIDEOS_DIR if target.strip() in ["videos", "/videos", "\\videos", ""] else Path(target.strip())
    files = batch_worker.scan_folder(str(p))
    return {
        "success": True,
        "folder": str(p),
        "total": len(files),
        "files": files
    }

@app.get("/api/batch/status")
async def get_batch_status():
    status = batch_worker.get_status()
    return {
        "success": True,
        "status": status
    }

@app.post("/api/batch/start")
async def start_batch_endpoint(req: StartBatchRequest):
    active = get_active_account()
    if not active:
        raise HTTPException(status_code=400, detail="Сначала добавьте и активируйте аккаунт для выгрузки")
    
    target = req.folder_path or "videos"
    p = VIDEOS_DIR if target.strip() in ["videos", "/videos", "\\videos", ""] else Path(target.strip())
    
    res = await batch_worker.start(
        folder_path=str(p),
        delay_minutes=req.delay_minutes or 5.0,
        title_template=req.title_template or "",
        desc_template=req.desc_template or "",
        account_id=active["id"]
    )
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("error", "Не удалось запустить пакетную выгрузку"))
    return res

@app.post("/api/batch/stop")
async def stop_batch_endpoint():
    await batch_worker.stop()
    return {"success": True, "message": "Пакетная публикация остановлена"}

@app.get("/api/batch/settings")
async def get_batch_settings():
    return {
        "success": True,
        "folder": get_setting("batch_folder", "videos"),
        "interval_min": float(get_setting("batch_interval_min", "5")),
        "title": get_setting("batch_title", ""),
        "desc": get_setting("batch_desc", "")
    }

# Bulk Post Redaction (All pages pagination: page=1, 2, 3, 4...)
class BulkRedactRequest(BaseModel):
    title_template: Optional[str] = ""
    desc_template: Optional[str] = ""
    file_ids: Optional[List[str]] = None

class SingleRedactRequest(BaseModel):
    file_id: str
    title: Optional[str] = None
    description: Optional[str] = None

@app.get("/api/posts/scan")
async def scan_user_posts(max_pages: Optional[int] = 50):
    active = get_active_account()
    if not active:
        raise HTTPException(status_code=400, detail="Сначала добавьте и активируйте аккаунт")
    
    client = get_client_for_account(active)
    username = active.get("username")
    posts = await bulk_redactor.scan_posts(client=client, username=username, max_pages=max_pages or 50)
    return {
        "success": True,
        "username": username,
        "total": len(posts),
        "posts": posts
    }

@app.get("/api/posts/status")
async def get_bulk_redact_status():
    status = bulk_redactor.get_status()
    return {
        "success": True,
        "status": status
    }

@app.post("/api/posts/bulk-redact")
async def start_bulk_redact(req: BulkRedactRequest):
    active = get_active_account()
    if not active:
        raise HTTPException(status_code=400, detail="Сначала добавьте и активируйте аккаунт")
    
    client = get_client_for_account(active)
    username = active.get("username")
    res = await bulk_redactor.start(
        client=client,
        title_template=req.title_template,
        desc_template=req.desc_template,
        file_ids=req.file_ids,
        username=username
    )
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("error", "Не удалось запустить редактирование"))
    return res

@app.post("/api/posts/bulk-redact/stop")
async def stop_bulk_redact():
    await bulk_redactor.stop()
    return {"success": True, "message": "Процесс редактирования остановлен"}

@app.post("/api/posts/single-redact")
async def single_redact_post(req: SingleRedactRequest):
    active = get_active_account()
    if not active:
        raise HTTPException(status_code=400, detail="Сначала добавьте и активируйте аккаунт")
    
    client = get_client_for_account(active)
    res = await client.redact_post(
        file_id=req.file_id,
        title=req.title,
        description=req.description
    )
    if not res.get("success"):
        err = "; ".join(res.get("errors", ["Ошибка обновления поста"]))
        raise HTTPException(status_code=400, detail=err)
    return {"success": True, "result": res}

# Tor Network Management
class TorConfigRequest(BaseModel):
    enabled: bool
    port: int
    strict: Optional[bool] = True

class TorTestRequest(BaseModel):
    port: Optional[int] = None

@app.get("/api/tor/config")
async def get_tor_configuration():
    cfg = get_tor_config()
    detected_port = auto_detect_tor_port()
    return {
        "success": True,
        "config": cfg,
        "detected_port": detected_port
    }

@app.post("/api/tor/config")
async def update_tor_configuration(req: TorConfigRequest):
    save_tor_config(enabled=req.enabled, port=req.port, strict=bool(req.strict))
    add_log("INFO", f"Настройки Tor обновлены: enabled={req.enabled}, port={req.port}, strict={req.strict}")
    return {
        "success": True,
        "config": get_tor_config(),
        "message": "Настройки маршрутизации Tor сохранены"
    }

@app.get("/api/tor/status")
async def get_tor_status():
    cfg = get_tor_config()
    port = cfg.get("port", 9150)
    status = check_tor_connection(port=port)
    return {
        "success": True,
        "tor_enabled": cfg.get("enabled", True),
        "tor_strict": cfg.get("strict", True),
        "status": status
    }

@app.post("/api/tor/test")
async def test_tor_connection_endpoint(req: TorTestRequest):
    port = req.port
    if not port:
        cfg = get_tor_config()
        port = cfg.get("port", 9150)
    status = check_tor_connection(port=port)
    return {
        "success": status.get("connected", False),
        "result": status
    }

@app.post("/api/tor/detect")
async def detect_tor_port_endpoint():
    detected = auto_detect_tor_port()
    if detected:
        return {
            "success": True,
            "port": detected,
            "message": f"Обнаружен активный порт Tor: {detected}"
        }
    return {
        "success": False,
        "error": "Активный процесс Tor не обнаружен на стандартных портах 9150 / 9050"
    }

# Anti-detect & Proxies
@app.get("/api/fingerprints")
async def list_fingerprints():
    return {
        "success": True,
        "presets": SUPPORTED_FINGERPRINTS,
        "default": DEFAULT_FINGERPRINT
    }

class ProxyTestRequest(BaseModel):
    proxy: str
    fingerprint: Optional[str] = DEFAULT_FINGERPRINT

@app.post("/api/proxy/test")
async def test_proxy_endpoint(req: ProxyTestRequest):
    import time
    from curl_cffi import requests
    proxy = req.proxy.strip()
    if not proxy:
        raise HTTPException(status_code=400, detail="Укажите прокси адрес")

    if not (proxy.startswith("http://") or proxy.startswith("https://") or proxy.startswith("socks5://") or proxy.startswith("socks5h://")):
        proxy = f"http://{proxy}"

    proxies = {"http": proxy, "https": proxy}
    fp = req.fingerprint or DEFAULT_FINGERPRINT

    try:
        start_t = time.time()
        s = requests.Session(impersonate=fp, proxies=proxies)
        # Check IP via ipify / httpbin
        res = s.get("https://api.ipify.org?format=json", timeout=12)
        elapsed_ms = int((time.time() - start_t) * 1000)
        ip = res.json().get("ip", "Unknown")
        return {
            "success": True,
            "ip": ip,
            "latency_ms": elapsed_ms,
            "message": f"Прокси работает! Внешний IP: {ip} ({elapsed_ms}ms)"
        }
    except Exception as e:
        return {
            "success": False,
            "error": f"Ошибка подключения через прокси: {str(e)}"
        }

# Logs
@app.get("/api/logs")
async def list_logs():
    logs = get_recent_logs(100)
    return {"success": True, "logs": logs}

@app.post("/api/logs/clear")
async def clear_logs_endpoint():
    clear_logs()
    return {"success": True, "message": "Журнал очищен"}
