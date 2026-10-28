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
    get_skipped_filenames,
    add_log,
    set_setting,
    get_setting
)
from .client import WvidsClient
from .uploader import ContentUploader

SUPPORTED_EXTS = {".mp4", ".webm", ".mov", ".m4v", ".avi", ".mkv", ".jpg", ".jpeg", ".png"}

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
        self.loop_mode: bool = True
        self.cycle: int = 1
        self.cycle_uploaded_files: set = set()
        
        self.current_file: Optional[str] = None
        self.current_index: int = 0
        self.total_files: int = 0
        self.success_count: int = 0
        self.duplicate_count: int = 0
        self.error_count: int = 0
        self.last_message: str = "Очередь готова к запуску"
        self.account_id: Optional[int] = None
        self.current_account_username: Optional[str] = None

    def scan_folder(self, custom_path: Optional[str] = None) -> List[Dict[str, Any]]:
        target_dir = Path(custom_path.strip()) if custom_path and custom_path.strip() else Path(self.folder_path)
        if not target_dir.exists() or not target_dir.is_dir():
            return []

        active = get_account_by_id(self.account_id) if self.account_id else get_active_account()
        acc_id = active["id"] if active else None

        uploaded_set = get_uploaded_filenames(acc_id)
        duplicate_set = get_duplicate_filenames(acc_id)
        skipped_set = get_skipped_filenames(acc_id)

        files: List[Dict[str, Any]] = []
        try:
            entries = sorted(list(target_dir.iterdir()), key=lambda x: x.name.lower())
            for p in entries:
                if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS:
                    fn = p.name
                    status = "pending"

                    # If batch queue is actively running
                    if self.state in ["running", "waiting_delay"]:
                        if fn == self.current_file and self.state == "running":
                            status = "uploading"
                        elif fn in self.cycle_uploaded_files:
                            status = "uploaded"
                        else:
                            status = "pending"
                    else:
                        # Idle: check DB history
                        if fn in uploaded_set:
                            status = "uploaded"
                        elif fn in duplicate_set:
                            status = "duplicate"
                        elif fn in skipped_set:
                            status = "skipped"

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
            "desc_template": self.desc_template,
            "loop_mode": self.loop_mode,
            "cycle": self.cycle,
            "account_id": self.account_id,
            "current_account_username": self.current_account_username
        }

    async def start(
        self,
        folder_path: Optional[str] = None,
        delay_minutes: float = 5.0,
        title_template: str = "",
        desc_template: str = "",
        account_id: Optional[int] = None,
        loop_mode: bool = True,
        **kwargs
    ) -> Dict[str, Any]:
        if self.state in ["running", "waiting_delay"]:
            return {"success": False, "error": "Пакетная публикация уже выполняется"}

        self._stop_requested = False
        self.folder_path = folder_path.strip() if folder_path and folder_path.strip() else str(VIDEOS_DIR)
        self.delay_seconds = max(5, int(delay_minutes * 60))
        self.title_template = title_template.strip() if (title_template and title_template.strip()) else get_setting("batch_title", "")
        self.desc_template = desc_template.strip() if (desc_template and desc_template.strip()) else get_setting("batch_desc", "")

        active = get_account_by_id(account_id) if account_id else get_active_account()
        if not active:
            return {"success": False, "error": "Нет активного аккаунта для публикации"}

        self.account_id = active["id"]
        self.current_account_username = active.get("username", "unknown")
        self.loop_mode = bool(loop_mode)
        self.cycle = 1
        self.cycle_uploaded_files = set()

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
        if self.title_template:
            set_setting("batch_title", self.title_template)
        set_setting("batch_loop_enabled", "1" if self.loop_mode else "0")
        if self.desc_template:
            set_setting("batch_desc", self.desc_template)

        self.task = asyncio.create_task(self._run_loop())
        loop_txt = "включен (по кругу)" if self.loop_mode else "выключен"
        add_log("INFO", f"▶️ Запущена пакетная публикация для @{self.current_account_username}, папка '{self.folder_path}', интервал: {delay_minutes} мин., бесконечный цикл: {loop_txt}")
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
            account = get_account_by_id(self.account_id) if self.account_id else get_active_account()
            if not account:
                self.state = "idle"
                self.last_message = "Нет активного аккаунта для выгрузки"
                add_log("ERROR", self.last_message)
                return

            if account.get("is_suspended"):
                self.state = "idle"
                self.last_message = f"Аккаунт @{account.get('username')} заблокирован (Account suspended)"
                add_log("ERROR", self.last_message)
                return

            self.account_id = account["id"]
            self.current_account_username = account.get("username", "unknown")
            add_log("INFO", f"👤 Запуск очереди для аккаунта: @{self.current_account_username} (UID: {account.get('uid')})")

            while not self._stop_requested:
                target_dir = Path(self.folder_path)
                if not target_dir.exists() or not target_dir.is_dir():
                    self.state = "idle"
                    self.last_message = f"Папка '{self.folder_path}' не существует"
                    add_log("ERROR", self.last_message)
                    return

                entries = sorted(list(target_dir.iterdir()), key=lambda x: x.name.lower())
                media_files = [p for p in entries if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS]
                self.total_files = len(media_files)

                if not media_files:
                    self.state = "idle"
                    self.last_message = f"В папке '{self.folder_path}' не найдено подходящих медиа файлов"
                    add_log("WARNING", self.last_message)
                    return

                # In cycle 1: check which files are pending
                if self.cycle == 1:
                    uploaded_set = get_uploaded_filenames(account["id"])
                    pending_files = [p for p in media_files if p.name not in uploaded_set]

                    if not pending_files:
                        if self.loop_mode:
                            add_log("INFO", f"Все {len(media_files)} файлов в папке уже публиковались на @{self.current_account_username}. В режиме бесконечного цикла запускаем заново с 1-го видео.")
                            files_to_process = media_files
                        else:
                            self.state = "idle"
                            self.last_message = f"Все файлы в папке уже были опубликованы на @{self.current_account_username}"
                            add_log("INFO", self.last_message)
                            return
                    else:
                        files_to_process = pending_files
                else:
                    files_to_process = media_files

                self.cycle_uploaded_files.clear()
                cycle_label = f"Круг #{self.cycle}"
                add_log("INFO", f"🚀 {cycle_label}: в очереди {len(files_to_process)} файлов из {len(media_files)} для @{self.current_account_username}")

                client = WvidsClient(
                    cookies=account.get("cookies", {}),
                    fingerprint=account.get("fingerprint", "chrome124"),
                    use_tor=True
                )
                uploader = ContentUploader(client)

                for idx, file_p in enumerate(files_to_process, 1):
                    if self._stop_requested:
                        break

                    filename = file_p.name
                    self.current_index = idx
                    self.current_file = filename
                    file_path = str(file_p.resolve())
                    file_size = file_p.stat().st_size
                    stem = file_p.stem

                    # Refresh account status
                    refreshed = get_account_by_id(account["id"])
                    if refreshed and refreshed.get("is_suspended"):
                        self.state = "idle"
                        self.last_message = f"Аккаунт @{self.current_account_username} заблокирован (Account suspended)"
                        add_log("ERROR", f"🚨 [ОСТАНОВКА ОЧЕРЕДИ] {self.last_message}")
                        return

                    # Title template
                    title_tpl = self.title_template or get_setting("batch_title", "")
                    if title_tpl:
                        title = (
                            title_tpl
                            .replace("{filename}", stem)
                            .replace("{name}", stem)
                            .replace("{stem}", stem)
                            .replace("{username}", f"@{self.current_account_username}")
                            .replace("{channel}", f"@{self.current_account_username}")
                            .replace("{index}", str(idx))
                            .replace("{i}", str(idx))
                            .replace("{cycle}", str(self.cycle))
                            .replace("{round}", str(self.cycle))
                        )
                    else:
                        title = stem

                    # Desc template
                    desc_tpl = self.desc_template or get_setting("batch_desc", "")
                    if desc_tpl:
                        description = (
                            desc_tpl
                            .replace("{title}", title)
                            .replace("{filename}", stem)
                            .replace("{name}", stem)
                            .replace("{stem}", stem)
                            .replace("{username}", f"@{self.current_account_username}")
                            .replace("{channel}", f"@{self.current_account_username}")
                            .replace("{index}", str(idx))
                            .replace("{i}", str(idx))
                            .replace("{cycle}", str(self.cycle))
                            .replace("{round}", str(self.cycle))
                        )
                    else:
                        description = ""

                    cycle_prefix = f"[{cycle_label}] " if self.loop_mode or self.cycle > 1 else ""
                    self.state = "running"
                    self.last_message = f"{cycle_prefix}[{idx}/{len(files_to_process)}] Публикация: '{filename}'..."
                    desc_preview = (description[:35] + "...") if len(description) > 35 else description
                    add_log("INFO", f"{cycle_prefix}Выгрузка: {filename} (Название: '{title}', Описание: '{desc_preview}')")

                    # Add upload record to DB
                    record_id = add_upload_record(
                        account_id=account["id"],
                        filename=filename,
                        file_path=file_path,
                        file_type="media",
                        file_size=file_size,
                        title=title,
                        description=description,
                        status="uploading"
                    )

                    # Execute upload
                    result = await uploader.upload(
                        file_path=file_path,
                        title=title,
                        description=description,
                        record_id=record_id,
                        account_id=account["id"]
                    )

                    if self._stop_requested:
                        break

                    # Check Duplicate
                    if result.get("is_duplicate"):
                        self.duplicate_count += 1
                        self.last_message = f"{cycle_prefix}Файл '{filename}' уже существует на сервере (дубликат)."
                        add_log("WARNING", f"⏩ {cycle_prefix}Дубликат: {filename}.")

                    # Check Skipped
                    elif result.get("is_skipped"):
                        skip_msg = result.get("error", "Время обработки превышает 5 минут")
                        self.last_message = f"{cycle_prefix}Файл '{filename}' пропущен (>5 мин): {skip_msg}"
                        add_log("WARNING", f"⏩ {cycle_prefix}Пропуск видео {filename}: {skip_msg}.")

                    # Check Success
                    elif result.get("success"):
                        self.success_count += 1
                        self.cycle_uploaded_files.add(filename)
                        fid = result.get("file_id", "")
                        self.last_message = f"{cycle_prefix}Файл '{filename}' успешно выложен (FID: {fid})!"
                        add_log("SUCCESS", f"✅ {cycle_prefix}Успешно опубликован {filename} (FID: {fid})")

                        # Countdown delay between files
                        self.state = "waiting_delay"
                        self.delay_remaining = self.delay_seconds
                        while self.delay_remaining > 0:
                            if self._stop_requested:
                                break
                            await asyncio.sleep(1)
                            self.delay_remaining -= 1

                    # Check Error
                    else:
                        self.error_count += 1
                        err_txt = result.get("error", "Сбой загрузки")
                        self.last_message = f"{cycle_prefix}Ошибка файла '{filename}': {err_txt}"
                        add_log("ERROR", f"❌ {cycle_prefix}Сбой публикации {filename}: {err_txt}")

                        if result.get("is_suspended") or "suspended" in err_txt.lower():
                            from .storage import mark_account_suspended
                            mark_account_suspended(account["id"], "Account suspended")
                            add_log("ERROR", f"🚨 Аккаунт @{self.current_account_username} заблокирован (Account suspended).")
                            self.state = "idle"
                            return

                        # Short wait before retry / next file on error
                        await asyncio.sleep(10)

                    self.cycle_uploaded_files.add(filename)

                # End of files_to_process
                if self._stop_requested:
                    break

                if not self.loop_mode:
                    self.state = "idle"
                    self.current_file = None
                    self.delay_remaining = 0
                    self.last_message = f"Очередь завершена! Успешно: {self.success_count}, Дубликатов: {self.duplicate_count}, Ошибок: {self.error_count}"
                    add_log("SUCCESS", f"🎉 Пакетная публикация завершена. Опубликовано: {self.success_count}, дубликатов: {self.duplicate_count}")
                    break
                else:
                    add_log("SUCCESS", f"🔄 Круг #{self.cycle} завершен ({len(files_to_process)} файлов обработано). Начинаем круг #{self.cycle + 1} заново!")
                    self.cycle += 1
                    self.cycle_uploaded_files.clear()
                    self.current_index = 0
                    self.current_file = None
                    self.last_message = f"Круг #{self.cycle - 1} успешно завершен! Переход к кругу #{self.cycle}..."

            self.state = "idle"
            self.current_file = None
            self.delay_remaining = 0
            if self._stop_requested:
                self.last_message = "Пакетная публикация остановлена пользователем"

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
