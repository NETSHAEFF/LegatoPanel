import os
import mimetypes
import asyncio
import re
from pathlib import Path
from typing import Dict, Any, Optional, Callable
from curl_cffi.curl import CurlMime
from bs4 import BeautifulSoup

from .config import MAX_FILE_SIZE_BYTES, URL_SHARE, DEFAULT_HOST
from .client import WvidsClient
from .fingerprint import human_jitter
from .storage import add_log, update_upload_record, update_upload_feed_status, update_upload_feed_status_by_fid
from .video_processor import process_video_if_enabled

async def async_verify_post_in_feed(client: WvidsClient, record_id: Optional[int], fid: str):
    """
    Background verification after upload:
    Waits 20 seconds, then checks if post appeared in https://wvids.com/feed.
    If missing, retries after 25 seconds.
    Updates DB feed_status to 'in_feed' or 'shadowbanned'.
    """
    if not fid:
        return
    try:
        if record_id:
            update_upload_feed_status(record_id, "checking")

        add_log("INFO", f"⏳ Запущена автопроверка видимости поста #{fid} в общей ленте /feed...")
        await asyncio.sleep(20)

        # First attempt
        res = await client.check_post_in_feed(fid)
        if res.get("in_feed"):
            if record_id:
                update_upload_feed_status(record_id, "in_feed")
            else:
                update_upload_feed_status_by_fid(fid, "in_feed")
            add_log("SUCCESS", f"🟢 [FEED CONFIRMED] Пост #{fid} подтвержден в общей ленте /feed (виден зрителям!)")
            return

        # Second attempt after 25 seconds (in case of caching / queue lag)
        await asyncio.sleep(25)
        res2 = await client.check_post_in_feed(fid)
        if res2.get("in_feed"):
            if record_id:
                update_upload_feed_status(record_id, "in_feed")
            else:
                update_upload_feed_status_by_fid(fid, "in_feed")
            add_log("SUCCESS", f"🟢 [FEED CONFIRMED] Пост #{fid} подтвержден в общей ленте /feed (со 2-й попытки)")
            return

        # If still not in feed -> Shadowban!
        if record_id:
            update_upload_feed_status(record_id, "shadowbanned")
        else:
            update_upload_feed_status_by_fid(fid, "shadowbanned")
        add_log(
            "WARNING",
            f"🚨 [SHADOWBAN DETECTED] Пост #{fid} опубликован на канале, но отсутствует в общей ленте /feed! "
            "Возможен теневой бан из-за Tor IP, спам-ссылок или фильтра гео."
        )
    except Exception as e:
        add_log("WARNING", f"Ошибка проверки видимости поста #{fid} в ленте: {e}")

# Common media MIME types fallback
MIME_MAP = {
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".m4v": "video/x-m4v",
    ".avi": "video/x-msvideo",
    ".mkv": "video/x-matroska",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif"
}

def detect_mime_type(file_path: str) -> str:
    ext = Path(file_path).suffix.lower()
    if ext in MIME_MAP:
        return MIME_MAP[ext]
    mime, _ = mimetypes.guess_type(file_path)
    return mime or "application/octet-stream"

class ContentUploader:
    def __init__(self, client: WvidsClient):
        self.client = client

    async def upload(
        self,
        file_path: str,
        title: str = "",
        description: str = "",
        record_id: Optional[int] = None,
        progress_callback: Optional[Callable[[str, int], None]] = None,
        account_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Full automated pipeline for uploading video or photo to wvids.com:
        1. Token & Config retrieval + suspension check
        2. Binary upload via curl_cffi CurlMime (TLS/HTTP2 impersonated)
        3. Custom Title & Description metadata attachment (both pre-share and post-share)
        4. Publication (share: 1)
        """
        if not account_id:
            try:
                from .storage import get_active_account
                active_acc = get_active_account()
                if active_acc:
                    account_id = active_acc.get("id")
            except Exception:
                account_id = None

        def report(msg: str, pct: int):
            if progress_callback:
                progress_callback(msg, pct)

        file_p = Path(file_path)
        if not file_p.exists():
            err = f"Файл не найден на ПК: {file_path}"
            add_log("ERROR", err)
            if record_id: update_upload_record(record_id, "failed", error=err)
            return {"success": False, "error": err}

        if account_id:
            from .storage import get_account_by_id
            acc = get_account_by_id(account_id)
            if acc and acc.get("is_suspended"):
                err = f"Выгрузка отменена: Аккаунт @{acc.get('username')} заблокирован (Account suspended)"
                add_log("ERROR", f"🚨 {err}")
                if record_id: update_upload_record(record_id, "failed", error=err)
                return {"success": False, "is_suspended": True, "error": err}

        file_size = file_p.stat().st_size
        if file_size > MAX_FILE_SIZE_BYTES:
            err = f"Размер файла превышает лимит 300 МБ: {file_size / (1024*1024):.1f} МБ"
            add_log("ERROR", err)
            if record_id: update_upload_record(record_id, "failed", error=err)
            return {"success": False, "error": err}

        mime_type = detect_mime_type(file_path)
        filename = file_p.name
        original_stem = file_p.stem

        report("1/4: Получение конфигурации и проверка статуса аккаунта...", 15)
        add_log("INFO", f"Начало выгрузки {filename} ({file_size / (1024*1024):.1f} MB, {mime_type})")

        # 1. Fetch config and API Key
        config = await self.client.get_upload_config()
        if config.get("is_suspended"):
            err = "Аккаунт заблокирован на wvids.com: Account suspended"
            if account_id:
                from .storage import mark_account_suspended
                mark_account_suspended(account_id, "Account suspended")
            add_log("ERROR", f"🚨 [БЛОКИРОВКА АККАУНТА] {err}")
            if record_id: update_upload_record(record_id, "failed", error=err)
            return {"success": False, "is_suspended": True, "error": err}

        api_key = config.get("apiKey")
        upload_url = config.get("uploadUrl")
        api_url = config.get("apiUrl")

        if not api_key:
            err = "Не удалось получить токен загрузки apiKey. Проверьте cookies аккаунта."
            add_log("ERROR", err)
            if record_id: update_upload_record(record_id, "failed", error=err)
            return {"success": False, "error": err}

        # Pre-processing: Apply Watermark & Uniqueization if enabled
        processed_file_path = None
        effective_file_p = file_p
        effective_filename = filename
        effective_mime_type = mime_type

        is_video = mime_type.startswith("video/") or file_p.suffix.lower() in [
            ".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v"
        ]

        if is_video:
            username = ""
            try:
                from .storage import get_active_account
                acc = get_active_account()
                if acc:
                    username = acc.get("username", "")
            except Exception:
                pass

            report("Обработка: наложение водяного знака и уникализация...", 25)
            try:
                proc_res = await process_video_if_enabled(
                    input_file=str(file_p),
                    title=title or original_stem,
                    username=username,
                    progress_callback=lambda msg, pct: report(msg, pct)
                )
                if proc_res.get("processed"):
                    processed_file_path = proc_res.get("output_path")
                    effective_file_p = Path(processed_file_path)
                    effective_filename = effective_file_p.name
                    effective_mime_type = detect_mime_type(str(effective_file_p))
                    file_size = effective_file_p.stat().st_size
                    add_log("SUCCESS", f"Видео {filename} уникализировано: {proc_res.get('details')}")
            except Exception as pe:
                add_log("WARNING", f"Не удалось выполнить уникализацию {filename}: {pe}. Загружаем исходный файл.")

        report("2/4: Передача файла через антидетект TLS/HTTP2...", 35)
        await human_jitter(0.3, 0.7)

        # 2. Upload file binary via CurlMime
        session = self.client.get_session()
        headers = {
            "Origin": "https://wvids.com",
            "Referer": URL_SHARE,
            "X-Requested-With": "XMLHttpRequest"
        }

        try:
            mime = CurlMime()
            mime.addpart(
                name="file",
                filename=effective_filename,
                local_path=str(effective_file_p.resolve()),
                content_type=effective_mime_type
            )
            mime.addpart(name="key", data=api_key.encode("utf-8"))

            upload_res = session.post(
                upload_url,
                multipart=mime,
                headers=headers,
                timeout=300
            )

            if upload_res.status_code != 200:
                err = f"Сервер загрузки вернул HTTP {upload_res.status_code}"
                add_log("ERROR", err, upload_res.text[:500])
                if record_id: update_upload_record(record_id, "failed", error=err)
                return {"success": False, "error": err}

            try:
                res_json = upload_res.json()
            except Exception:
                res_json = {}

            if not res_json.get("success", False) and not res_json.get("id"):
                raw_err = res_json.get("error", "")
                full_text = upload_res.text.lower()
                is_duplicate = "already exist" in raw_err.lower() or "already exist" in full_text
                if is_duplicate:
                    err = "This file already exists"
                    add_log("WARNING", f"Файл {filename} уже есть на сервере (This file already exists).")
                    if record_id: update_upload_record(record_id, "duplicate", error=err)
                    return {"success": False, "is_duplicate": True, "error": err, "filename": filename}

                is_suspended = "suspended" in raw_err.lower() or "suspended" in full_text
                if is_suspended:
                    err = "Аккаунт заблокирован на wvids.com: Account suspended"
                    if account_id:
                        from .storage import mark_account_suspended
                        mark_account_suspended(account_id, "Account suspended")
                    add_log("ERROR", f"🚨 [БЛОКИРОВКА АККАУНТА] Выгрузка отклонена: {err}")
                    if record_id: update_upload_record(record_id, "failed", error=err)
                    return {"success": False, "is_suspended": True, "is_duplicate": False, "error": err}
                else:
                    err = raw_err or "Сервер отклонил файл загрузки"
                add_log("ERROR", f"Сбой загрузки: {err}")
                if record_id: update_upload_record(record_id, "failed", error=err)
                return {"success": False, "is_duplicate": False, "error": err}

            file_id = res_json.get("id")
            if not file_id:
                err = "Сервер не вернул fileId после загрузки"
                add_log("ERROR", err)
                if record_id: update_upload_record(record_id, "failed", error=err)
                return {"success": False, "error": err}

            add_log("SUCCESS", f"Файл выгружен на сервер, получен ID: {file_id}")
            report(f"3/4: Сохранение метаданных (название: '{title}', описание)...", 65)
            await human_jitter(0.3, 0.6)

            # 3. Set custom title and description via staging edit form
            # Call edit: "1", fid: file_id to retrieve server form structure
            form_fields: Dict[str, str] = {}
            try:
                edit_res = session.post(
                    api_url,
                    data={"edit": "1", "fid": str(file_id), "key": api_key},
                    headers=headers,
                    timeout=15
                )
                if edit_res.status_code == 200:
                    try:
                        e_json = edit_res.json()
                        html_data = e_json.get("data", "")
                        if html_data:
                            soup = BeautifulSoup(html_data, "html.parser")
                            for inp in soup.find_all(["input", "textarea"]):
                                name_attr = inp.get("name")
                                val_attr = inp.get("value", "")
                                if name_attr:
                                    form_fields[name_attr] = val_attr
                    except Exception:
                        pass
            except Exception as e:
                add_log("WARNING", f"Ошибка получения формы редактирования: {e}")

            # Populate metadata payload with exact form values
            meta_payload = dict(form_fields)
            meta_payload["fileId"] = str(file_id)
            meta_payload["key"] = api_key
            # Title fields
            target_title = title if title else original_stem
            meta_payload["name"] = target_title
            meta_payload["title"] = target_title
            # Description fields
            if description:
                meta_payload["description"] = description
                meta_payload["desc"] = description
                meta_payload["text"] = description

            meta_res = session.post(
                api_url,
                data=meta_payload,
                headers={
                    **headers,
                    "Content-Type": "application/x-www-form-urlencoded"
                },
                timeout=15
            )

            add_log("INFO", f"Ответ сохранения метаданных (share.php): {meta_res.status_code} {meta_res.text[:150]}")
            report("4/4: Финализация и публикация на канале...", 85)
            await human_jitter(0.4, 0.8)

            # 4. Finalize and Share
            share_payload = {
                "share": "1",
                "key": api_key
            }

            share_res = session.post(
                api_url,
                data=share_payload,
                headers={
                    **headers,
                    "Content-Type": "application/x-www-form-urlencoded"
                },
                timeout=15
            )

            share_json = {}
            try:
                share_json = share_res.json()
            except Exception:
                pass

            # 5. Dual update: attach Description & Title on the live post on wvids.com
            # Note: file_id is a staging ID from upload.php (e.g. 8563194).
            # When published via share.php, wvids creates a live post with a new post ID (e.g. 9489861).
            # We locate the newly published post on the user channel to attach description & title.
            real_fid = None
            username = config.get("username")
            if not username:
                try:
                    from .storage import get_active_account
                    acc = get_active_account()
                    if acc and acc.get("username"):
                        username = acc.get("username")
                except Exception:
                    pass

            if not username:
                try:
                    v = await self.client.verify_session()
                    username = v.get("username")
                except Exception:
                    pass

            report("4/4: Применение описания к опубликованному посту...", 92)
            await human_jitter(0.8, 1.4)

            if username:
                for attempt in range(4):
                    try:
                        latest_posts = await self.client.get_all_user_posts(username=username, max_pages=1)
                        if latest_posts:
                            # 1. Match by title first
                            for p in latest_posts[:5]:
                                if p.get("title") and (p.get("title").strip() == target_title.strip() or target_title.strip() in p.get("title")):
                                    real_fid = str(p.get("file_id"))
                                    break
                            # 2. Fallback to topmost (most recent) post
                            if not real_fid and latest_posts[0].get("file_id"):
                                real_fid = str(latest_posts[0].get("file_id"))
                            if real_fid:
                                break
                    except Exception as e:
                        add_log("WARNING", f"Попытка {attempt + 1}: ожидание появления публикации на канале: {e}")
                    await asyncio.sleep(1.2)

            target_post_fid = real_fid or str(file_id)

            if description or target_title:
                try:
                    redact_res = await self.client.redact_post(
                        file_id=target_post_fid,
                        title=target_title if title else None,
                        description=description if description else None
                    )
                    if redact_res.get("success"):
                        add_log("SUCCESS", f"✅ Описание успешно установлено для публикации #{target_post_fid}")
                    else:
                        err_msg = "; ".join(redact_res.get("errors", ["Неизвестная ошибка"]))
                        add_log("WARNING", f"⚠️ Предупреждение установки описания для #{target_post_fid}: {err_msg}")
                except Exception as e:
                    add_log("WARNING", f"Исключение при установке описания: {e}")

            if share_res.status_code == 200 and (share_json.get("success") is True or "success" in share_res.text):
                success_msg = f"Контент '{target_title}' успешно выложен на wvids.com!"
                report(success_msg, 100)
                add_log("SUCCESS", success_msg, f"fid={target_post_fid}")
                if record_id:
                    update_upload_record(record_id, "success", remote_fid=str(target_post_fid))
                # Trigger background Shadowban detection in /feed
                asyncio.create_task(async_verify_post_in_feed(self.client, record_id, str(target_post_fid)))
                return {
                    "success": True,
                    "file_id": target_post_fid,
                    "staging_id": file_id,
                    "title": target_title,
                    "description": description,
                    "filename": filename,
                    "message": success_msg
                }
            else:
                err = share_json.get("error") or "Не удалось завершить публикацию (share)"
                add_log("WARNING", f"Предупреждение публикации: {err}")
                if record_id: update_upload_record(record_id, "published_with_warning", remote_fid=str(target_post_fid))
                asyncio.create_task(async_verify_post_in_feed(self.client, record_id, str(target_post_fid)))
                return {
                    "success": True,
                    "file_id": target_post_fid,
                    "staging_id": file_id,
                    "title": target_title,
                    "description": description,
                    "message": "Файл выгружен и метаданные сохранены"
                }

        except Exception as e:
            err = f"Ошибка конвейера загрузки: {str(e)}"
            add_log("ERROR", err)
            if record_id: update_upload_record(record_id, "error", error=err)
            return {"success": False, "error": err}
        finally:
            if processed_file_path and Path(processed_file_path).exists() and str(processed_file_path) != str(file_path):
                try:
                    os.remove(processed_file_path)
                except Exception:
                    pass
