import os
import re
import shutil
import asyncio
import time
import uuid
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
    WATERMARKS_DIR,
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
    get_tor_proxy_url,
    tor_manager
)
from .vless import (
    vless_manager,
    check_vless_connection,
    get_vpn_config_content,
    apply_vpn_config_content
)
from .storage import (
    get_all_accounts,
    get_account_by_id,
    get_active_account,
    set_active_account,
    save_account,
    update_account_stats,
    update_account_username,
    mark_account_suspended,
    mark_account_active,
    delete_account,
    add_upload_record,
    get_recent_uploads,
    update_upload_feed_status,
    update_upload_feed_status_by_fid,
    get_recent_logs,
    clear_logs,
    add_log,
    get_tor_config,
    save_tor_config,
    get_network_mode,
    set_network_mode,
    get_vless_config,
    save_vless_config,
    get_setting,
    set_setting,
    get_processing_settings,
    save_processing_settings,
    get_popular_comments_history,
    clear_popular_comments_history
)
from .popular_commenter import popular_commenter
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

@app.post("/api/uploads/{upload_id}/check-feed")
async def check_single_upload_feed(upload_id: int):
    """
    Checks if a specific uploaded video appears in wvids.com/feed (detects shadowban).
    """
    uploads = get_recent_uploads(100)
    target = next((u for u in uploads if u.get("id") == upload_id), None)
    if not target:
        raise HTTPException(status_code=404, detail="Запись о загрузке не найдена")

    remote_fid = target.get("remote_fid")
    if not remote_fid:
        raise HTTPException(status_code=400, detail="У данной записи нет опубликованного FID")

    client = get_client_for_account()
    res = await client.check_post_in_feed(fid=remote_fid)
    if not res.get("success"):
        raise HTTPException(status_code=502, detail=res.get("error", "Ошибка проверки ленты"))

    in_feed = res.get("in_feed", False)
    status_str = "in_feed" if in_feed else "shadowbanned"
    update_upload_feed_status(upload_id, status_str)

    msg = f"Пост #{remote_fid}: {'🟢 Виден в общей ленте /feed' if in_feed else '🚨 Теневой бан (отсутствует в /feed)'}"
    add_log("SUCCESS" if in_feed else "WARNING", msg)

    return {
        "success": True,
        "upload_id": upload_id,
        "fid": remote_fid,
        "in_feed": in_feed,
        "feed_status": status_str,
        "visible_feed_posts": res.get("visible_count", 0),
        "message": msg
    }

@app.post("/api/uploads/check-all-feed")
async def check_all_recent_uploads_feed():
    """
    Batch scans wvids.com/feed once and updates visibility status for all recent successful uploads.
    """
    uploads = get_recent_uploads(50)
    successful = [u for u in uploads if u.get("status") in ["success", "published_with_warning"] and u.get("remote_fid")]
    if not successful:
        return {"success": True, "checked": 0, "results": []}

    client = get_client_for_account()
    session = client.get_session()

    try:
        r = session.get(f"{client.DEFAULT_HOST if hasattr(client, 'DEFAULT_HOST') else 'https://wvids.com'}/feed", timeout=15)
        if r.status_code != 200:
            raise HTTPException(status_code=502, detail=f"Ошибка запроса /feed: HTTP {r.status_code}")
        feed_html = r.text
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Не удалось получить /feed: {e}")

    results = []
    in_feed_count = 0
    shadowbanned_count = 0

    for u in successful:
        fid = str(u.get("remote_fid")).strip()
        uid = u.get("id")
        pattern = rf'id=[\'"](?:progress)?{re.escape(fid)}[\'"]'
        found = bool(re.search(pattern, feed_html)) or (fid in feed_html)

        status_str = "in_feed" if found else "shadowbanned"
        update_upload_feed_status(uid, status_str)
        if found:
            in_feed_count += 1
        else:
            shadowbanned_count += 1

        results.append({
            "id": uid,
            "fid": fid,
            "in_feed": found,
            "feed_status": status_str
        })

    summary_msg = f"Проверка ленты /feed: проверено {len(successful)} постов (В ленте: {in_feed_count}, Shadowban: {shadowbanned_count})"
    add_log("INFO" if shadowbanned_count == 0 else "WARNING", summary_msg)

    return {
        "success": True,
        "checked": len(successful),
        "in_feed_count": in_feed_count,
        "shadowbanned_count": shadowbanned_count,
        "results": results
    }

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
    if active.get("is_suspended"):
        raise HTTPException(
            status_code=400,
            detail=f"Пакетная публикация невозможна: Аккаунт @{active.get('username')} заблокирован (Account suspended)."
        )
    
    target = req.folder_path or "videos"
    p = VIDEOS_DIR if target.strip() in ["videos", "/videos", "\\videos", ""] else Path(target.strip())
    
    desc = req.desc_template.strip() if (req.desc_template and req.desc_template.strip()) else get_setting("batch_desc", "")
    res = await batch_worker.start(
        folder_path=str(p),
        delay_minutes=req.delay_minutes or 5.0,
        title_template=req.title_template or "",
        desc_template=desc,
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

# --- Popular Videos & Commenting Endpoints ---

class SinglePopularCommentRequest(BaseModel):
    fid: str
    text: str
    video_title: Optional[str] = ""
    video_url: Optional[str] = ""
    account_id: Optional[int] = None

class BatchPopularCommentRequest(BaseModel):
    fids: List[str]
    templates: List[str]
    delay_min: Optional[float] = 5.0
    delay_max: Optional[float] = 12.0
    account_ids: Optional[List[int]] = None

@app.get("/api/popular/videos")
async def get_popular_videos_endpoint(fresh: bool = False, limit: int = 50):
    active = get_active_account()
    client = get_client_for_account(active)
    videos = await popular_commenter.fetch_popular_videos(client, max_items=limit, force=fresh)
    return {
        "success": True,
        "count": len(videos),
        "videos": videos,
        "last_fetched_at": popular_commenter.last_fetched_at
    }

@app.get("/api/popular/comments/{fid}")
async def get_video_comments_endpoint(fid: str, page: int = 0):
    active = get_active_account()
    client = get_client_for_account(active)
    res = await client.get_video_comments(fid=fid, page=page)
    return res

@app.post("/api/popular/comment")
async def post_popular_comment_endpoint(req: SinglePopularCommentRequest):
    if req.account_id:
        acc = get_account_by_id(req.account_id)
    else:
        acc = get_active_account()

    if not acc:
        raise HTTPException(status_code=400, detail="Нет доступного активного аккаунта для комментирования")

    if acc.get("is_suspended"):
        raise HTTPException(status_code=400, detail=f"Аккаунт @{acc.get('username')} заблокирован")

    client = get_client_for_account(acc)
    res = await popular_commenter.post_single_comment(
        client=client,
        fid=req.fid,
        text=req.text,
        video_title=req.video_title or "",
        video_url=req.video_url or "",
        account_id=acc.get("id"),
        account_username=acc.get("username", "Unknown")
    )
    return res

@app.post("/api/popular/batch-comment/start")
async def start_batch_comment_endpoint(req: BatchPopularCommentRequest):
    res = await popular_commenter.start_batch(
        target_fids=req.fids,
        comment_templates=req.templates,
        delay_min=req.delay_min or 5.0,
        delay_max=req.delay_max or 12.0,
        account_ids=req.account_ids
    )
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("error", "Не удалось запустить авто-комментирование"))
    return res

@app.post("/api/popular/batch-comment/stop")
async def stop_batch_comment_endpoint():
    res = await popular_commenter.stop()
    return res

@app.get("/api/popular/batch-comment/status")
async def get_batch_comment_status_endpoint():
    return popular_commenter.get_status()

@app.get("/api/popular/history")
async def get_popular_history_endpoint(limit: int = 100):
    return {
        "success": True,
        "history": get_popular_comments_history(limit=limit)
    }

@app.delete("/api/popular/history")
async def clear_popular_history_endpoint():
    clear_popular_comments_history()
    return {"success": True, "message": "История комментирования очищена"}

# Network Proxy Mode & VLESS Management
class NetworkModeRequest(BaseModel):
    mode: str  # 'vless', 'tor', 'direct', 'proxy'

@app.get("/api/network/status")
async def get_network_status():
    mode = get_network_mode()
    vless_cfg = get_vless_config()
    tor_cfg = get_tor_config()

    # Fast connection status of current active mode
    active_status = {}
    if mode == "vless":
        active_status = check_vless_connection(port=vless_cfg.get("port", 10808), timeout=6)
    elif mode == "tor":
        port = tor_cfg.get("port") or auto_detect_tor_port() or 9050
        active_status = check_tor_connection(port=port, timeout=6)
    else:
        active_status = {
            "connected": True,
            "ip": "Direct IP",
            "country": "Local",
            "country_code": "",
            "is_tor": False,
            "latency_ms": 0
        }

    return {
        "success": True,
        "mode": mode,
        "active_status": active_status,
        "vless_status": vless_manager.get_status(),
        "tor_status": tor_manager.get_status(),
        "vless_config": vless_cfg,
        "tor_config": tor_cfg
    }

@app.post("/api/network/mode")
async def switch_network_mode(req: NetworkModeRequest):
    new_mode = req.mode.strip().lower()
    if new_mode not in ("vless", "tor", "direct", "proxy"):
        raise HTTPException(status_code=400, detail="Недопустимый сетевой режим. Выберите: vless, tor, direct")

    set_network_mode(new_mode)
    labels = {
        "vless": "⚡ VLESS Reality (Германия)",
        "tor": "🧅 Tor Network",
        "direct": "🌐 Прямое соединение (Direct)",
        "proxy": "🔌 Пользовательский Proxy"
    }
    label = labels.get(new_mode, new_mode)
    add_log("SUCCESS", f"Сетевой режим переключен на: {label}")

    # Ensure background worker starts if vless or tor
    if new_mode == "vless":
        vless_manager.ensure_running(port=10808)
    elif new_mode == "tor":
        tor_cfg = get_tor_config()
        port = tor_cfg.get("port") or 9050
        tor_manager.ensure_running(port=port)

    return {
        "success": True,
        "mode": new_mode,
        "label": label,
        "message": f"Сетевой маршрутизатор переключен на: {label}"
    }

@app.post("/api/vless/test")
async def test_vless_endpoint():
    cfg = get_vless_config()
    res = check_vless_connection(port=cfg.get("port", 10808), timeout=10)
    return {
        "success": True,
        "result": res
    }

@app.post("/api/vless/start")
async def start_vless_endpoint():
    res = vless_manager.start(port=10808)
    return res

@app.post("/api/vless/stop")
async def stop_vless_endpoint():
    res = vless_manager.stop()
    return res

class VlessUpdateConfigRequest(BaseModel):
    config: str  # Can be 'vless://...' URL or raw JSON text

@app.get("/api/vless/config")
async def get_vless_config_endpoint():
    content = get_vpn_config_content()
    return {
        "success": True,
        "content": content
    }

@app.post("/api/vless/config")
async def update_vless_config_endpoint(req: VlessUpdateConfigRequest):
    res = apply_vpn_config_content(req.config)
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("error", "Ошибка применения конфигурации"))
    return res

@app.post("/api/vless/config/upload")
async def upload_vless_config_file(file: UploadFile = File(...)):
    try:
        raw_bytes = await file.read()
        content = raw_bytes.decode("utf-8", errors="replace")
        res = apply_vpn_config_content(content)
        if not res.get("success"):
            raise HTTPException(status_code=400, detail=res.get("error", "Ошибка применения файла"))
        return res
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="Файл должен быть текстовым в кодировке UTF-8")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Ошибка загрузки файла: {str(e)}")

# Tor Network Management
class TorConfigRequest(BaseModel):
    enabled: bool
    port: int
    strict: Optional[bool] = True
    exit_country: Optional[str] = "all"
    strict_nodes: Optional[bool] = True

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
    old_cfg = get_tor_config()
    exit_country_clean = (req.exit_country or "all").strip().lower()
    save_tor_config(
        enabled=req.enabled,
        port=req.port,
        strict=bool(req.strict),
        exit_country=exit_country_clean,
        strict_nodes=bool(req.strict_nodes)
    )

    geo_changed = (old_cfg.get("exit_country") != exit_country_clean) or (old_cfg.get("strict_nodes") != bool(req.strict_nodes))
    restart_result = None
    if geo_changed and req.enabled and req.port == 9050:
        restart_result = tor_manager.restart(port=9050)

    add_log("INFO", f"Настройки Tor обновлены: enabled={req.enabled}, port={req.port}, exit_country={exit_country_clean}, strict_nodes={req.strict_nodes}")
    return {
        "success": True,
        "config": get_tor_config(),
        "geo_changed": geo_changed,
        "restart_result": restart_result,
        "message": f"Настройки маршрутизации сохранены (ГЕО: {exit_country_clean.upper() if exit_country_clean != 'all' else 'Авто'})"
    }

@app.get("/api/tor/status")
async def get_tor_status():
    cfg = get_tor_config()
    detected = auto_detect_tor_port()
    port = cfg.get("port") or detected or 9050
    status = check_tor_connection(port=port)
    service_status = tor_manager.get_status()
    return {
        "success": True,
        "tor_enabled": cfg.get("enabled", True),
        "tor_strict": cfg.get("strict", True),
        "exit_country": cfg.get("exit_country", "all"),
        "strict_nodes": cfg.get("strict_nodes", True),
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
    res = tor_manager.start(port=9050)
    return res

@app.post("/api/tor/service/stop")
async def stop_tor_service_endpoint():
    res = tor_manager.stop()
    return res

@app.post("/api/tor/service/restart")
async def restart_tor_service_endpoint():
    cfg = get_tor_config()
    port = cfg.get("port", 9050)
    res = tor_manager.restart(port=port)
    return res

@app.post("/api/tor/test")
async def test_tor_connection_endpoint(req: TorTestRequest):
    port = req.port
    if not port:
        cfg = get_tor_config()
        port = cfg.get("port") or auto_detect_tor_port() or 9050
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

class ProcessorTestRequest(BaseModel):
    video_path: Optional[str] = None

@app.get("/api/processor/settings")
async def get_processor_settings_endpoint():
    return {
        "success": True,
        "settings": get_processing_settings(),
        "fonts": POPULAR_FONTS,
        "devices": DEVICE_PROFILES
    }

@app.post("/api/processor/settings")
async def save_processor_settings_endpoint(req: ProcessorSettingsRequest):
    updated = save_processing_settings(req.settings)
    add_log("INFO", "Сохранены новые настройки водяного знака и уникализатора видео")
    return {
        "success": True,
        "settings": updated,
        "message": "Настройки водяного знака и уникализации успешно сохранены!"
    }

@app.post("/api/processor/upload-watermark-media")
async def upload_watermark_media_endpoint(file: UploadFile = File(...)):
    """Handles upload of photo or video watermark via Drag & Drop."""
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

    # Clean previous watermark media file if present
    cfg = get_processing_settings()
    old_p = cfg.get("watermark_media_path")
    if old_p and Path(old_p).is_file():
        try:
            Path(old_p).unlink()
        except Exception:
            pass

    # Save new watermark file
    safe_name = f"wm_{int(time.time())}_{uuid.uuid4().hex[:6]}{ext}"
    dest_path = WATERMARKS_DIR / safe_name

    try:
        with open(dest_path, "wb") as f:
            while chunk := await file.read(1024 * 1024):
                f.write(chunk)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка сохранения файла: {str(e)}")

    # Update settings
    updated = save_processing_settings({
        "watermark_media_path": str(dest_path.resolve()),
        "watermark_media_name": file.filename,
        "watermark_media_type": media_type
    })

    add_log("INFO", f"Загружен медиа-вотемарк: {file.filename} ({media_type})")

    return {
        "success": True,
        "message": f"Медиа-вотемарк '{file.filename}' успешно загружен!",
        "media_name": file.filename,
        "media_type": media_type,
        "settings": updated
    }

@app.get("/api/processor/watermark-media-file")
async def get_watermark_media_file():
    """Returns the current watermark image/video for web panel preview."""
    cfg = get_processing_settings()
    p_str = cfg.get("watermark_media_path")
    if not p_str or not Path(p_str).is_file():
        raise HTTPException(status_code=404, detail="Медиа-вотемарк не установлен")

    p = Path(p_str)
    ext = p.suffix.lower()
    if ext in [".mp4", ".mov", ".webm", ".mkv"]:
        media_type = "video/mp4"
    elif ext == ".jpg" or ext == ".jpeg":
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
async def delete_watermark_media_endpoint():
    """Removes current watermark media file."""
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

    add_log("INFO", "Медиа-вотемарк удален")
    return {
        "success": True,
        "message": "Медиа-вотемарк успешно удален",
        "settings": updated
    }

@app.post("/api/processor/test")
async def test_processor_endpoint(req: ProcessorTestRequest):
    res = await run_test_processing(req.video_path)
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("error", "Ошибка тестовой обработки видео"))
    return res
