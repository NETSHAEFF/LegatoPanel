import os
import shutil
import asyncio
import time
import uuid
import json
from pathlib import Path
from typing import Optional, Dict, Any, List
from fastapi import FastAPI, Request, UploadFile, File, Form, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from .config import (
    BASE_DIR,
    UPLOADS_DIR,
    VIDEOS_DIR,
    DOWNLOAD_VIDEO_DIR,
    WATERMARKS_DIR,
    DEFAULT_FINGERPRINT,
    SUPPORTED_FINGERPRINTS
)
from .fingerprint import parse_cookies_input
from .client import WvidsClient
from .uploader import ContentUploader
from .batch_worker import batch_worker
from .bulk_redactor import bulk_redactor
from .popular_downloader import popular_downloader
from .tor import (
    check_tor_connection,
    auto_detect_tor_port,
    TorRoutingError,
    get_tor_proxy_url,
    tor_manager
)
from .storage import (
    get_all_accounts,
    get_account_by_id,
    get_active_account,
    set_active_account,
    save_account,
    update_account_stats,
    update_account_username,
    update_account_settings,
    mark_account_suspended,
    mark_account_active,
    delete_account,
    add_upload_record,
    get_recent_uploads,
    get_recent_logs,
    clear_logs,
    add_log,
    get_tor_config,
    save_tor_config,
    get_setting,
    set_setting,
    get_processing_settings,
    save_processing_settings,
    get_account_processing_settings,
    save_account_processing_settings,
    get_account_metadata_templates
)
from .video_processor import (
    POPULAR_FONTS,
    DEVICE_PROFILES,
    run_test_processing
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

@app.middleware("http")
async def add_no_cache_headers(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/static/") or request.url.path == "/":
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

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
        raise HTTPException(
            status_code=400,
            detail="Не удалось распознать cookies. Вставьте строку cookies (например, auth_user_id=...; auth_sess=...) или JSON."
        )

    client = WvidsClient(
        cookies=cookies_dict,
        fingerprint=req.fingerprint or DEFAULT_FINGERPRINT,
        use_tor=True
    )

    verify_result = await client.verify_session(fallback_name=req.name)
    if not verify_result.get("valid"):
        detail_msg = verify_result.get("error") or "Сессия не авторизована на wvids.com. Проверьте cookies auth_user_id / auth_sess / auth_p."
        raise HTTPException(status_code=400, detail=detail_msg)

    uid = verify_result.get("uid")
    username = verify_result.get("username") or (req.name.strip().lstrip("@") if req.name else f"user_{uid}")
    acc_name = req.name or f"@{username}"

    # Safely fetch initial stats without failing account creation
    subscribers = 0
    views_today = 0
    total_views = 0
    try:
        stats = await client.get_statistics(uid, username)
        if stats.get("success"):
            subscribers = stats.get("subscribers", 0)
            views_today = stats.get("views_today", 0)
            total_views = stats.get("total_views", 0)
    except Exception as e:
        add_log("WARNING", f"Не удалось загрузить начальную статистику для @{username}: {e}")

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

@app.post("/api/accounts/{account_id}/check-status")
async def check_account_status_endpoint(account_id: int):
    acc = get_account_by_id(account_id)
    if not acc:
        raise HTTPException(status_code=404, detail="Аккаунт не найден")

    client = get_client_for_account(acc)
    st = await client.check_account_status(acc.get("username"))

    if st.get("status") == "suspended":
        mark_account_suspended(account_id, st.get("reason", "Account suspended"))
        return {
            "success": True,
            "status": "suspended",
            "is_suspended": True,
            "reason": st.get("reason", "Account suspended"),
            "message": f"🚨 Аккаунт @{acc.get('username')} заблокирован на wvids.com: Account suspended"
        }
    elif st.get("status") == "active":
        mark_account_active(account_id)
        return {
            "success": True,
            "status": "active",
            "is_suspended": False,
            "message": f"✅ Аккаунт @{acc.get('username')} активен и функционирует штатно"
        }
    else:
        return {
            "success": False,
            "status": st.get("status", "unknown"),
            "is_suspended": False,
            "error": st.get("error") or st.get("reason") or "Сессия не авторизована"
        }

class SimulateSuspensionRequest(BaseModel):
    simulate: bool

@app.post("/api/accounts/{account_id}/simulate-suspension")
async def simulate_suspension_endpoint(account_id: int, req: SimulateSuspensionRequest):
    acc = get_account_by_id(account_id)
    if not acc:
        raise HTTPException(status_code=404, detail="Аккаунт не найден")

    if req.simulate:
        mark_account_suspended(account_id, "Account suspended (Тест симуляции)")
        msg = f"🚨 Включена симуляция блокировки для @{acc.get('username')}: Account suspended"
    else:
        mark_account_active(account_id)
        msg = f"✅ Симуляция блокировки снята для @{acc.get('username')}"

    return {
        "success": True,
        "is_suspended": req.simulate,
        "message": msg,
        "account": get_account_by_id(account_id)
    }

class AccountSettingsRequest(BaseModel):
    title_template: Optional[str] = None
    desc_template: Optional[str] = None
    is_batch_enabled: Optional[bool] = None
    watermark_settings: Optional[Dict[str, Any]] = None

@app.get("/api/accounts/{account_id}/settings")
async def get_account_settings_endpoint(account_id: int):
    acc = get_account_by_id(account_id)
    if not acc:
        raise HTTPException(status_code=404, detail="Аккаунт не найден")
    return {
        "success": True,
        "account_id": account_id,
        "username": acc.get("username", ""),
        "title_template": acc.get("title_template", ""),
        "desc_template": acc.get("desc_template", ""),
        "is_batch_enabled": acc.get("is_batch_enabled", True),
        "watermark_settings": acc.get("watermark_settings", {}),
        "effective_processing": get_account_processing_settings(account_id)
    }

@app.post("/api/accounts/{account_id}/settings")
async def save_account_settings_endpoint(account_id: int, req: AccountSettingsRequest):
    acc = get_account_by_id(account_id)
    if not acc:
        raise HTTPException(status_code=404, detail="Аккаунт не найден")
    updated = update_account_settings(
        account_id=account_id,
        title_template=req.title_template,
        desc_template=req.desc_template,
        watermark_settings=req.watermark_settings,
        is_batch_enabled=req.is_batch_enabled
    )
    add_log("INFO", f"Обновлены персональные настройки аккаунта @{acc.get('username')}")
    return {
        "success": True,
        "account": updated,
        "message": f"Настройки для @{acc.get('username')} успешно сохранены!"
    }

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

    if stats.get("is_suspended"):
        mark_account_suspended(active["id"], stats.get("error", "Account suspended"))
        active = get_active_account()

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
            "fingerprint": active["fingerprint"],
            "is_suspended": active.get("is_suspended", False),
            "suspension_reason": active.get("suspension_reason", ""),
            "suspended_at": active.get("suspended_at", 0)
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
    if active.get("is_suspended"):
        raise HTTPException(
            status_code=400,
            detail=f"Выгрузка невозможна: Аккаунт @{active.get('username')} заблокирован (Account suspended). Смените аккаунт."
        )

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
        record_id=record_id,
        account_id=active["id"]
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
    if active.get("is_suspended"):
        raise HTTPException(
            status_code=400,
            detail=f"Выгрузка невозможна: Аккаунт @{active.get('username')} заблокирован (Account suspended). Смените аккаунт."
        )

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
        record_id=record_id,
        account_id=active["id"]
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
    loop_mode: Optional[bool] = True
    account_id: Optional[int] = None

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
    active = get_account_by_id(req.account_id) if req.account_id else get_active_account()
    if not active:
        raise HTTPException(status_code=400, detail="Сначала добавьте и активируйте аккаунт для выгрузки")
    if active.get("is_suspended"):
        raise HTTPException(
            status_code=400,
            detail=f"Пакетная публикация невозможна: Аккаунт @{active.get('username')} заблокирован (Account suspended)."
        )

    target = req.folder_path or "videos"
    p = VIDEOS_DIR if target.strip() in ["videos", "/videos", "\\videos", ""] else Path(target.strip())

    desc = req.desc_template.strip() if (req.desc_template and req.desc_template.strip()) else get_setting("batch_desc", "")
    title = req.title_template.strip() if (req.title_template and req.title_template.strip()) else get_setting("batch_title", "")
    res = await batch_worker.start(
        folder_path=str(p),
        delay_minutes=req.delay_minutes or 5.0,
        title_template=title,
        desc_template=desc,
        account_id=active["id"],
        loop_mode=True if req.loop_mode is None else req.loop_mode
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
        "desc": get_setting("batch_desc", ""),
        "loop_mode": get_setting("batch_loop_enabled", "1") == "1"
    }

class SaveBatchSettingsRequest(BaseModel):
    folder: Optional[str] = None
    interval_min: Optional[float] = None
    title: Optional[str] = None
    desc: Optional[str] = None
    loop_mode: Optional[bool] = None

@app.post("/api/batch/settings")
async def save_batch_settings(req: SaveBatchSettingsRequest):
    if req.folder is not None:
        set_setting("batch_folder", req.folder.strip())
    if req.interval_min is not None:
        set_setting("batch_interval_min", str(req.interval_min))
    if req.title is not None:
        set_setting("batch_title", req.title.strip())
    if req.desc is not None:
        set_setting("batch_desc", req.desc.strip())
    if req.loop_mode is not None:
        set_setting("batch_loop_enabled", "1" if req.loop_mode else "0")
    return {"success": True, "message": "Настройки очереди успешно сохранены"}

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

# Popular Videos Downloader (top category, multi-page cursor scan, min views filter)
class PopularDownloadRequest(BaseModel):
    min_views: Optional[int] = 0
    max_pages: Optional[int] = 0
    folder: Optional[str] = "video"
    limit: Optional[int] = 0

@app.get("/api/popular/status")
async def get_popular_status():
    return {
        "success": True,
        "status": popular_downloader.get_status()
    }

@app.get("/api/popular/scan")
async def scan_popular_videos(min_views: Optional[int] = 0, max_pages: Optional[int] = 0):
    popular_downloader._stop_requested = False
    active = get_active_account()
    client = get_client_for_account(active) if active else WvidsClient(use_tor=True)
    videos = await popular_downloader.scan_only(client=client, min_views=min_views or 0, max_pages=max_pages or 0)
    return {
        "success": True,
        "total": len(videos),
        "min_views": min_views or 0,
        "videos": videos
    }

@app.post("/api/popular/start")
async def start_popular_download(req: PopularDownloadRequest):
    active = get_active_account()
    client = get_client_for_account(active) if active else WvidsClient(use_tor=True)
    res = await popular_downloader.start(
        client=client,
        min_views=req.min_views or 0,
        max_pages=req.max_pages or 0,
        folder_path=req.folder or "video",
        limit=req.limit or 0
    )
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("error", "Не удалось запустить скачивание"))
    return res

@app.post("/api/popular/stop")
async def stop_popular_download():
    res = await popular_downloader.stop()
    return res

@app.get("/api/popular/files")
async def get_downloaded_popular_files(folder: Optional[str] = "video"):
    p = Path(folder.strip()) if folder and folder.strip() not in ["video", "/video", "\\video", ""] else DOWNLOAD_VIDEO_DIR
    if not p.is_absolute():
        p = BASE_DIR / p
    if not p.exists() or not p.is_dir():
        return {"folder": str(p), "files": [], "total": 0}

    files = []
    for ext in ["*.mp4", "*.webm", "*.mov"]:
        for f in p.glob(ext):
            if f.is_file():
                try:
                    stat = f.stat()
                    files.append({
                        "name": f.name,
                        "path": str(f.resolve()),
                        "size": stat.st_size,
                        "size_mb": round(stat.st_size / (1024 * 1024), 2),
                        "mtime": stat.st_mtime
                    })
                except Exception:
                    pass
    files.sort(key=lambda x: x["mtime"], reverse=True)
    return {
        "folder": str(p),
        "files": files,
        "total": len(files)
    }

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
    detected = auto_detect_tor_port()
    port = cfg.get("port") or detected or 9150
    status = check_tor_connection(port=port)
    service_status = tor_manager.get_status()
    return {
        "success": True,
        "tor_enabled": cfg.get("enabled", True),
        "tor_strict": cfg.get("strict", True),
        "status": status,
        "service": service_status
    }

@app.get("/api/tor/service/status")
async def get_tor_service_status():
    return {
        "success": True,
        "service": tor_manager.get_status()
    }

@app.post("/api/tor/service/start")
async def start_tor_service_endpoint():
    cfg = get_tor_config()
    target_port = cfg.get("port") or 9150
    res = tor_manager.start(port=target_port)
    return res

@app.post("/api/tor/service/stop")
async def stop_tor_service_endpoint():
    res = tor_manager.stop()
    return res

@app.post("/api/tor/new-identity")
async def tor_new_identity_endpoint():
    res = tor_manager.new_circuit()
    return res

@app.post("/api/tor/test")
async def test_tor_connection_endpoint(req: TorTestRequest):
    port = req.port
    if not port:
        cfg = get_tor_config()
        port = cfg.get("port") or auto_detect_tor_port() or 9150
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
        "error": "Активный процесс Tor не обнаружен на стандартных портах 9050 / 9150"
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

# Video Processing (Watermark & Uniqueizer)
class ProcessorSettingsRequest(BaseModel):
    settings: Dict[str, Any]
    account_id: Optional[int] = None

class ProcessorTestRequest(BaseModel):
    video_path: Optional[str] = None
    account_id: Optional[int] = None

@app.get("/api/processor/settings")
async def get_processor_settings_endpoint(account_id: Optional[int] = None):
    settings = get_account_processing_settings(account_id) if account_id else get_processing_settings()
    acc_name = None
    if account_id:
        acc = get_account_by_id(account_id)
        if acc:
            acc_name = acc.get("username")

    return {
        "success": True,
        "account_id": account_id,
        "account_username": acc_name,
        "settings": settings,
        "fonts": POPULAR_FONTS,
        "devices": DEVICE_PROFILES
    }

@app.post("/api/processor/settings")
async def save_processor_settings_endpoint(req: ProcessorSettingsRequest):
    if req.account_id:
        updated = save_account_processing_settings(req.account_id, req.settings)
        acc = get_account_by_id(req.account_id)
        u_name = f"@{acc.get('username')}" if acc else f"#{req.account_id}"
        add_log("INFO", f"Сохранены персональные настройки водяного знака для {u_name}")
        msg = f"Персональные настройки водяного знака для {u_name} успешно сохранены!"
    else:
        updated = save_processing_settings(req.settings)
        add_log("INFO", "Сохранены глобальные настройки водяного знака и уникализатора видео")
        msg = "Глобальные настройки водяного знака и уникализации успешно сохранены!"

    return {
        "success": True,
        "account_id": req.account_id,
        "settings": updated,
        "message": msg
    }

@app.post("/api/processor/upload-watermark-media")
async def upload_watermark_media_endpoint(
    file: UploadFile = File(...),
    account_id: Optional[int] = Form(None)
):
    """Handles upload of photo or video watermark via Drag & Drop (global or per-account)."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="Файл не передан")

    ext = Path(file.filename).suffix.lower()
    allowed_images = [".png", ".jpg", ".jpeg", ".webp", ".gif"]
    allowed_videos = [".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v"]

    if ext not in allowed_images and ext not in allowed_videos:
        raise HTTPException(
            status_code=400,
            detail=f"Неподдерживаемый формат: {ext}. Допустимы изображения (PNG, JPG, WEBP, GIF) и видео (MP4, MOV, WEBM)"
        )

    media_type = "video" if ext in allowed_videos else "image"

    prefix = f"wm_acc{account_id}" if account_id else "wm"
    safe_name = f"{prefix}_{int(time.time())}_{uuid.uuid4().hex[:6]}{ext}"
    dest_path = WATERMARKS_DIR / safe_name

    try:
        with open(dest_path, "wb") as f:
            while chunk := await file.read(1024 * 1024):
                f.write(chunk)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка сохранения файла: {str(e)}")

    if account_id:
        acc = get_account_by_id(account_id)
        old_wm = acc.get("watermark_settings", {}) if acc else {}
        old_p = old_wm.get("watermark_media_path")
        if old_p and Path(old_p).is_file() and str(old_p) != str(dest_path):
            try:
                Path(old_p).unlink()
            except Exception:
                pass

        updated = save_account_processing_settings(account_id, {
            "watermark_media_path": str(dest_path.resolve()),
            "watermark_media_name": file.filename,
            "watermark_media_type": media_type
        })
        u_name = f"@{acc.get('username')}" if acc else f"#{account_id}"
        add_log("INFO", f"Загружен индивидуальный медиа-вотемарк для {u_name}: {file.filename}")
        msg = f"Медиа-вотемарк '{file.filename}' для {u_name} успешно загружен!"
    else:
        cfg = get_processing_settings()
        old_p = cfg.get("watermark_media_path")
        if old_p and Path(old_p).is_file() and str(old_p) != str(dest_path):
            try:
                Path(old_p).unlink()
            except Exception:
                pass

        updated = save_processing_settings({
            "watermark_media_path": str(dest_path.resolve()),
            "watermark_media_name": file.filename,
            "watermark_media_type": media_type
        })
        add_log("INFO", f"Загружен глобальный медиа-вотемарк: {file.filename} ({media_type})")
        msg = f"Медиа-вотемарк '{file.filename}' успешно загружен!"

    return {
        "success": True,
        "account_id": account_id,
        "message": msg,
        "media_name": file.filename,
        "media_type": media_type,
        "settings": updated
    }

@app.get("/api/processor/watermark-media-file")
async def get_watermark_media_file(account_id: Optional[int] = None):
    """Returns the current watermark image/video for web panel preview."""
    cfg = get_account_processing_settings(account_id) if account_id else get_processing_settings()
    p_str = cfg.get("watermark_media_path")
    if not p_str or not Path(p_str).is_file():
        raise HTTPException(status_code=404, detail="Медиа-вотемарк не установлен")

    p = Path(p_str)
    ext = p.suffix.lower()
    if ext in [".mp4", ".mov", ".webm", ".mkv"]:
        media_type = "video/mp4"
    elif ext in [".jpg", ".jpeg"]:
        media_type = "image/jpeg"
    elif ext == ".png":
        media_type = "image/png"
    elif ext == ".webp":
        media_type = "image/webp"
    elif ext == ".gif":
        media_type = "image/gif"
    else:
        media_type = "application/octet-stream"

    return FileResponse(p, media_type=media_type)

@app.delete("/api/processor/watermark-media")
async def delete_watermark_media_endpoint(account_id: Optional[int] = None):
    """Removes current watermark media file."""
    if account_id:
        acc = get_account_by_id(account_id)
        old_wm = acc.get("watermark_settings", {}) if acc else {}
        p_str = old_wm.get("watermark_media_path")
        if p_str and Path(p_str).is_file():
            try:
                Path(p_str).unlink()
            except Exception:
                pass
        updated = save_account_processing_settings(account_id, {
            "watermark_media_path": "",
            "watermark_media_name": "",
            "watermark_media_type": "image"
        })
        u_name = f"@{acc.get('username')}" if acc else f"#{account_id}"
        add_log("INFO", f"Индивидуальный медиа-вотемарк для {u_name} удален")
        return {
            "success": True,
            "account_id": account_id,
            "message": f"Медиа-вотемарк для {u_name} удален",
            "settings": updated
        }
    else:
        cfg = get_processing_settings()
        p_str = cfg.get("watermark_media_path")
        if p_str and Path(p_str).is_file():
            try:
                Path(p_str).unlink()
            except Exception:
                pass

        updated = save_processing_settings({
            "watermark_media_path": "",
            "watermark_media_name": "",
            "watermark_media_type": "image"
        })

        add_log("INFO", "Глобальный медиа-вотемарк удален")
        return {
            "success": True,
            "message": "Медиа-вотемарк успешно удален",
            "settings": updated
        }

@app.post("/api/processor/test")
async def test_processor_endpoint(req: ProcessorTestRequest):
    res = await run_test_processing(req.video_path, account_id=req.account_id)
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("error", "Ошибка тестовой обработки видео"))
    return res
