import asyncio
import time
from pathlib import Path
from typing import Dict, Any, List, Optional

from .config import VIDEOS_DIR, DEFAULT_HOST
from .storage import (
    get_active_account,
    get_account_by_id,
    add_upload_record,
    update_upload_record,
    get_uploaded_filenames,
    get_duplicate_filenames,
    add_log,
    set_setting,
    get_setting
)
from .client import WvidsClient
from .uploader import ContentUploader

SUPPORTED_EXTS = {".mp4", ".webm", ".mov", ".m4v", ".avi", ".mkv", ".jpg", ".jpeg", ".png", ".webp"}

class BatchUploadWorker:
    def __init__(self):
        self.state: str = "idle"  # idle, running, waiting_delay, stopped
        self.task: Optional[asyncio.Task] = None
        self._stop_requested: bool = False
        
        self.folder_path: str = str(VIDEOS_DIR)
        self.delay_seconds: int = 300  # Default 5 minutes
        self.delay_remaining: int = 0
        self.title_template: str = ""
        self.desc_template: str = ""
        
        self.current_file: Optional[str] = None
        self.current_index: int = 0
        self.total_files: int = 0
        self.success_count: int = 0
        self.duplicate_count: int = 0
        self.error_count: int = 0
        self.last_message: str = "Очередь готова к запуску"
        self.account_id: Optional[int] = None

    def scan_folder(self, custom_path: Optional[str] = None) -> List[Dict[str, Any]]:
        target_dir = Path(custom_path.strip()) if custom_path and custom_path.strip() else VIDEOS_DIR
        if not target_dir.exists() or not target_dir.is_dir():
            return []

        uploaded_set = get_uploaded_filenames()
        duplicate_set = get_duplicate_filenames()

        files: List[Dict[str, Any]] = []
        try:
            # Sort naturally by name
            entries = sorted(list(target_dir.iterdir()), key=lambda x: x.name.lower())
            for p in entries:
                if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS:
                    fn = p.name
                    status = "pending"
                    if fn in uploaded_set:
                        status = "uploaded"
                    elif fn in duplicate_set:
                        status = "duplicate"

                    files.append({
                        "name": fn,
                        "path": str(p.resolve()),
                        "size": p.stat().st_size,
                        "size_mb": round(p.stat().st_size / (1024 * 1024), 2),
                        "ext": p.suffix.lower(),
                        "status": status
                    })
        except Exception as e:
            add_log("ERROR", f"Ошибка сканирования папки видео: {str(e)}")

        return files

    def get_status(self) -> Dict[str, Any]:
        return {
            "state": self.state,
            "is_running": self.state in ["running", "waiting_delay"],
            "current_file": self.current_file,
            "current_index": self.current_index,
            "total_files": self.total_files,
            "success_count": self.success_count,
            "duplicate_count": self.duplicate_count,
            "error_count": self.error_count,
            "delay_seconds": self.delay_seconds,
            "delay_remaining": self.delay_remaining,
            "last_message": self.last_message,
            "folder_path": self.folder_path,
            "title_template": self.title_template,
            "desc_template": self.desc_template
        }

    async def start(
        self,
        folder_path: Optional[str] = None,
        delay_minutes: float = 5.0,
        title_template: str = "",
        desc_template: str = "",
        account_id: Optional[int] = None
    ) -> Dict[str, Any]:
        if self.state in ["running", "waiting_delay"]:
            return {"success": False, "error": "Пакетная публикация уже выполняется"}

        self._stop_requested = False
        self.folder_path = folder_path.strip() if folder_path and folder_path.strip() else str(VIDEOS_DIR)
        self.delay_seconds = max(5, int(delay_minutes * 60))
        self.title_template = title_template.strip()
        self.desc_template = desc_template.strip() if (desc_template and desc_template.strip()) else get_setting("batch_desc", "")
        self.account_id = account_id

        self.success_count = 0
        self.duplicate_count = 0
        self.error_count = 0
        self.current_index = 0
        self.current_file = None
        self.delay_remaining = 0
        self.state = "running"
        self.last_message = "Запуск конвейера пакетной публикации..."

        # Save settings for convenience
        set_setting("batch_folder", self.folder_path)
        set_setting("batch_interval_min", str(delay_minutes))
        set_setting("batch_title", self.title_template)
        if self.desc_template:
            set_setting("batch_desc", self.desc_template)

        self.task = asyncio.create_task(self._run_loop())
        add_log("INFO", f"▶️ Запущена пакетная публикация из '{self.folder_path}' (интервал: {delay_minutes} мин.)")
        return {"success": True, "message": "Пакетная публикация запущена"}

    async def stop(self):
        if self.state in ["running", "waiting_delay"]:
            self._stop_requested = True
            self.state = "stopped"
            self.last_message = "Остановка очереди запрошена пользователем"
            add_log("INFO", "⏹️ Пакетная публикация остановлена пользователем")
            if self.task and not self.task.done():
                self.task.cancel()
        self.state = "idle"
        self.delay_remaining = 0

    async def _run_loop(self):
        try:
            files = self.scan_folder(self.folder_path)
            self.total_files = len(files)

            if not files:
                self.state = "idle"
                self.last_message = f"В папке '{self.folder_path}' не найдено подходящих медиа файлов"
                add_log("WARNING", self.last_message)
                return

            # Filter only pending files
            pending_files = [f for f in files if f["status"] == "pending"]
            if not pending_files:
                self.state = "idle"
                self.last_message = "Все файлы в папке уже были опубликованы ранее"
                add_log("INFO", self.last_message)
                return

            self.total_files = len(files)
            idx = 0

            # Get client
            account = get_account_by_id(self.account_id) if self.account_id else get_active_account()
            if not account:
                self.state = "idle"
                self.last_message = "Нет активного авторизованного аккаунта"
                add_log("ERROR", self.last_message)
                return

            client = WvidsClient(
                cookies=account.get("cookies", {}),
                fingerprint=account.get("fingerprint", "chrome124"),
                use_tor=True
            )
            uploader = ContentUploader(client)

            for file_info in files:
                if self._stop_requested:
                    break

                # Skip already completed
                if file_info["status"] in ["uploaded", "duplicate"]:
                    continue

                idx += 1
                self.current_index = idx
                self.current_file = file_info["name"]
                file_path = file_info["path"]
                filename = file_info["name"]
                stem = Path(file_path).stem

                # Construct Title: template support
                if self.title_template:
                    title = (
                        self.title_template
                        .replace("{filename}", stem)
                        .replace("{name}", stem)
                        .replace("{stem}", stem)
                        .replace("{index}", str(idx))
                        .replace("{i}", str(idx))
                    )
                else:
                    title = stem

                # Construct Description: template support
                desc_tpl = self.desc_template if self.desc_template else get_setting("batch_desc", "")
                if desc_tpl:
                    description = (
                        desc_tpl
                        .replace("{title}", title)
                        .replace("{filename}", stem)
                        .replace("{name}", stem)
                        .replace("{stem}", stem)
                        .replace("{index}", str(idx))
                        .replace("{i}", str(idx))
                    )
                else:
                    description = ""

                self.state = "running"
                self.last_message = f"[{idx}/{self.total_files}] Публикация: '{filename}'..."
                desc_preview = (description[:35] + "...") if len(description) > 35 else description
                add_log("INFO", f"Выгрузка файла очереди: {filename} (Название: '{title}', Описание: '{desc_preview}')")

                # Add upload record to DB
                record_id = add_upload_record(
                    account_id=account["id"],
                    filename=filename,
                    file_path=file_path,
                    file_type="media",
                    file_size=file_info["size"],
                    title=title,
                    description=description,
                    status="uploading"
                )

                # Execute upload
                result = await uploader.upload(
                    file_path=file_path,
                    title=title,
                    description=description,
                    record_id=record_id
                )

                # Check Duplicate
                if result.get("is_duplicate"):
                    self.duplicate_count += 1
                    self.last_message = f"Файл '{filename}' уже существует на сервере (дубликат). Сразу берем следующий..."
                    add_log("WARNING", f"⏩ Дубликат: {filename}. Пропуск БЕЗ ПАУЗЫ.")
                    # NO DELAY: continue immediately to the next video
                    continue

                # Check Success
                if result.get("success"):
                    self.success_count += 1
                    fid = result.get("file_id", "")
                    self.last_message = f"Файл '{filename}' успешно выложен (FID: {fid})! Пауза {self.delay_seconds // 60} мин."
                    add_log("SUCCESS", f"✅ Успешно опубликован {filename} (FID: {fid})")

                    # Enter countdown delay
                    self.state = "waiting_delay"
                    self.delay_remaining = self.delay_seconds
                    while self.delay_remaining > 0:
                        if self._stop_requested:
                            break
                        await asyncio.sleep(1)
                        self.delay_remaining -= 1

                else:
                    self.error_count += 1
                    err_txt = result.get("error", "Сбой загрузки")
                    self.last_message = f"Ошибка файла '{filename}': {err_txt}"
                    add_log("ERROR", f"❌ Сбой публикации {filename}: {err_txt}")

                    # If account suspended, halt immediately
                    if result.get("is_suspended") or "suspended" in err_txt.lower():
                        from .storage import mark_account_suspended
                        mark_account_suspended(account["id"], "Account suspended")
                        self.last_message = f"🚨 Аккаунт @{account.get('username')} заблокирован (Account suspended)!"
                        add_log("ERROR", f"🚨 [ОСТАНОВКА ОЧЕРЕДИ] {self.last_message}")
                        self.state = "idle"
                        break

                    # Short backoff for regular errors (e.g. 10 sec)
                    await asyncio.sleep(10)

            # Loop finished
            self.state = "idle"
            self.current_file = None
            self.delay_remaining = 0
            self.last_message = f"Очередь завершена! Успешно: {self.success_count}, Дубликатов: {self.duplicate_count}, Ошибок: {self.error_count}"
            add_log("SUCCESS", f"🎉 Пакетная публикация завершена. Опубликовано: {self.success_count}, дубликатов: {self.duplicate_count}")

        except asyncio.CancelledError:
            self.state = "idle"
            self.delay_remaining = 0
            self.last_message = "Публикация остановлена"
        except Exception as e:
            self.state = "idle"
            self.delay_remaining = 0
            self.last_message = f"Критическая ошибка очереди: {str(e)}"
            add_log("ERROR", self.last_message)

# Global singleton worker instance
batch_worker = BatchUploadWorker()
