import asyncio
import os
import re
import time
from pathlib import Path
from typing import Dict, Any, List, Optional, Callable

from .client import WvidsClient
from .config import BASE_DIR, DOWNLOAD_VIDEO_DIR
from .storage import add_log, get_active_account, get_tor_config
from .fingerprint import human_jitter
from .tor import tor_manager

def sanitize_filename(name: str, max_len: int = 70) -> str:
    """Removes invalid characters from filename and trims length."""
    cleaned = re.sub(r'[\\/*?:"<>|\r\n\t]', "_", name).strip()
    cleaned = re.sub(r'\s+', " ", cleaned)
    cleaned = re.sub(r'_+', "_", cleaned)
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len].rstrip()
    return cleaned or "video"

class PopularDownloader:
    def __init__(self):
        self.is_running: bool = False
        self.is_scanning: bool = False
        self.status_text: str = "Ожидание действий"
        
        # Stats
        self.current_page: int = 0
        self.total_pages_scanned: int = 0
        self.min_views: int = 0
        self.max_pages: int = 0
        self.folder_path: str = str(DOWNLOAD_VIDEO_DIR)
        
        self.matching_videos: List[Dict[str, Any]] = []
        self.current_video: Optional[Dict[str, Any]] = None
        self.current_index: int = 0
        self.total_to_download: int = 0
        
        self.downloaded_count: int = 0
        self.skipped_count: int = 0
        self.failed_count: int = 0
        self.percent: int = 0
        
        self.current_file_bytes: int = 0
        self.current_file_total: int = 0
        
        self.errors: List[str] = []
        self._task: Optional[asyncio.Task] = None
        self._stop_requested: bool = False

    def get_status(self) -> Dict[str, Any]:
        return {
            "is_running": self.is_running,
            "is_scanning": self.is_scanning,
            "status_text": self.status_text,
            "current_page": self.current_page,
            "total_pages_scanned": self.total_pages_scanned,
            "min_views": self.min_views,
            "max_pages": self.max_pages,
            "folder_path": self.folder_path,
            "matching_count": len(self.matching_videos),
            "matching_videos": self.matching_videos[:50],  # preview top 50 in status
            "current_video": self.current_video,
            "current_index": self.current_index,
            "total_to_download": self.total_to_download,
            "downloaded_count": self.downloaded_count,
            "skipped_count": self.skipped_count,
            "failed_count": self.failed_count,
            "percent": self.percent,
            "current_file_bytes": self.current_file_bytes,
            "current_file_total": self.current_file_total,
            "errors": self.errors[-15:]
        }

    def _is_file_already_downloaded(self, fid: str, folder: Path) -> Optional[Path]:
        """Checks if a video file with this file_id already exists in folder."""
        if not folder.exists():
            return None
        # Check direct or prefixed filenames: {fid}.mp4 or {fid}_*.mp4
        pattern_exact = folder / f"{fid}.mp4"
        if pattern_exact.exists() and pattern_exact.stat().st_size > 1024:
            return pattern_exact
        for p in folder.glob(f"{fid}_*.mp4"):
            if p.is_file() and p.stat().st_size > 1024:
                return p
        return None

    async def scan_only(
        self,
        client: Optional[WvidsClient] = None,
        min_views: int = 0,
        max_pages: int = 0
    ) -> List[Dict[str, Any]]:
        """Scans popular section without downloading, returning matching videos."""
        if self.is_scanning or self.is_running:
            return self.matching_videos

        self.is_scanning = True
        self._stop_requested = False
        self.status_text = f"Сканирование раздела 'Популярное' (фильтр: просмотры >= {min_views})..."
        self.matching_videos = []
        self.min_views = min_views
        self.max_pages = max_pages

        try:
            if not client:
                client = self._create_client()

            def on_page_progress(page, matches, total_seen, has_more):
                self.current_page = page
                self.total_pages_scanned = page
                self.status_text = f"Стр. {page}: найдено {matches} видео (фильтр: >={min_views} просм.)"

            posts = await client.get_popular_videos(
                min_views=min_views,
                max_pages=max_pages,
                stop_check=lambda: self._stop_requested,
                progress_callback=on_page_progress
            )
            self.matching_videos = posts
            self.status_text = f"Сканирование завершено: найдено {len(posts)} видео (просм. >={min_views})"
            return posts
        except Exception as e:
            err = f"Ошибка сканирования популярного: {e}"
            self.status_text = err
            self.errors.append(err)
            add_log("ERROR", err)
            return []
        finally:
            self.is_scanning = False
            self._stop_requested = False

    async def start(
        self,
        client: Optional[WvidsClient] = None,
        min_views: int = 0,
        max_pages: int = 0,
        folder_path: str = "video",
        limit: int = 0,
        scan_and_download: bool = True
    ) -> Dict[str, Any]:
        """Starts background download of popular videos."""
        if self.is_running:
            return {"success": False, "error": "Процесс скачивания уже выполняется"}

        self.is_running = True
        self._stop_requested = False
        if self.min_views != min_views or self.max_pages != max_pages:
            self.matching_videos = []
        self.min_views = min_views
        self.max_pages = max_pages
        self.folder_path = folder_path.strip() if folder_path and folder_path.strip() else str(DOWNLOAD_VIDEO_DIR)
        
        # Reset counters
        self.current_index = 0
        self.total_to_download = 0
        self.downloaded_count = 0
        self.skipped_count = 0
        self.failed_count = 0
        self.percent = 0
        self.current_file_bytes = 0
        self.current_file_total = 0
        self.errors = []
        self.status_text = "Подготовка к скачиванию..."

        if not client:
            client = self._create_client()

        self._task = asyncio.create_task(
            self._download_loop(client, min_views, max_pages, self.folder_path, limit, scan_and_download)
        )
        return {"success": True, "message": "Процесс скачивания популярного запущен"}

    async def stop(self) -> Dict[str, Any]:
        """Requests graceful stop of running scan/download."""
        if not self.is_running and not self.is_scanning:
            return {"success": False, "message": "Процесс не запущен"}

        self._stop_requested = True
        self.status_text = "Остановка процесса по запросу..."
        add_log("WARNING", "⏹️ Запрос остановки скачивания 'Популярное'")

        if self._task and not self._task.done():
            try:
                self._task.cancel()
            except Exception:
                pass

        self.is_running = False
        self.is_scanning = False
        self.status_text = "Остановлено пользователем"
        return {"success": True, "message": "Процесс остановлен"}

    def _create_client(self) -> WvidsClient:
        """Instantiates WvidsClient with active account session or Tor fallback."""
        active = get_active_account()
        cfg = get_tor_config()
        target_port = cfg.get("port") or 9150
        try:
            tor_manager.ensure_running(port=target_port)
        except Exception:
            pass

        if active:
            return WvidsClient(
                cookies=active.get("cookies", {}),
                fingerprint=active.get("fingerprint", "chrome124"),
                use_tor=True
            )
        return WvidsClient(use_tor=True)

    async def _download_loop(
        self,
        client: WvidsClient,
        min_views: int,
        max_pages: int,
        folder_str: str,
        limit: int,
        scan_and_download: bool
    ):
        try:
            # Resolve target folder
            f_path = Path(folder_str)
            if not f_path.is_absolute():
                f_path = BASE_DIR / folder_str
            f_path.mkdir(parents=True, exist_ok=True)
            self.folder_path = str(f_path)

            add_log("INFO", f"📥 Запуск скачивания популярного в папку '{self.folder_path}' (мин. просмотры: {min_views}, макс. стр: {max_pages or 'все'})")

            # Step 1: Scan popular pages if matching_videos is empty
            if not self.matching_videos:
                self.status_text = f"Сканирование раздела 'Популярное' (просмотры >= {min_views})..."
                
                def on_page_progress(page, matches, total_seen, has_more):
                    self.current_page = page
                    self.total_pages_scanned = page
                    self.status_text = f"Стр. {page}: найдено {matches} подходящих видео..."

                videos = await client.get_popular_videos(
                    min_views=min_views,
                    max_pages=max_pages,
                    stop_check=lambda: self._stop_requested,
                    progress_callback=on_page_progress
                )
                self.matching_videos = videos
            else:
                videos = self.matching_videos

            if self._stop_requested:
                self.status_text = "Остановлено пользователем"
                return

            if not videos:
                self.status_text = f"Не найдено видео с просмотрами >= {min_views}"
                add_log("WARNING", f"Популярное: нет видео, удовлетворяющих фильтру просмотров >= {min_views}")
                return

            # Apply limit if specified
            to_download = videos[:limit] if limit > 0 else videos
            self.total_to_download = len(to_download)
            add_log("INFO", f"📋 Подготовлено к выгрузке: {self.total_to_download} видео")

            # Step 2: Download each video
            for idx, v in enumerate(to_download, 1):
                if self._stop_requested:
                    self.status_text = "Остановлено пользователем"
                    break

                self.current_index = idx
                self.current_video = v
                self.percent = int((idx - 1) / self.total_to_download * 100)
                
                fid = v["file_id"]
                title = v.get("title") or ""
                views = v.get("views", 0)
                slug = v.get("slug") or fid

                # Check if already downloaded
                existing = self._is_file_already_downloaded(fid, f_path)
                if existing:
                    self.skipped_count += 1
                    msg = f"⏭️ Пропуск [{idx}/{self.total_to_download}]: {existing.name} уже скачан ({existing.stat().st_size / (1024*1024):.1f} MB)"
                    self.status_text = msg
                    add_log("INFO", msg)
                    continue

                safe_t = sanitize_filename(title)
                dest_filename = f"{fid}_{safe_t}.mp4" if safe_t else f"{fid}.mp4"
                dest_path = f_path / dest_filename

                self.status_text = f"[{idx}/{self.total_to_download}] Получение ссылки: ID {fid} ({views} просм.)..."
                add_log("INFO", f"⬇️ [{idx}/{self.total_to_download}] Скачивание: ID {fid} — '{title[:35]}' ({views} просм.)")

                # Download video file with per-item resilience
                try:
                    # Get stream URL
                    stream_url = await client.get_video_download_url(slug)
                    if not stream_url:
                        self.failed_count += 1
                        err_msg = f"Не удалось извлечь ссылку потока для видео {fid} ({slug})"
                        self.errors.append(err_msg)
                        add_log("WARNING", err_msg)
                        continue

                    # Progress callback
                    def on_chunk(bytes_dl, bytes_tot):
                        self.current_file_bytes = bytes_dl
                        self.current_file_total = bytes_tot
                        pct = (bytes_dl / bytes_tot * 100) if bytes_tot else 0
                        mb_dl = bytes_dl / (1024 * 1024)
                        mb_tot = bytes_tot / (1024 * 1024)
                        self.status_text = (
                            f"[{idx}/{self.total_to_download}] Скачивание {dest_filename[:30]}... "
                            f"{mb_dl:.1f}/{mb_tot:.1f} MB ({pct:.1f}%)"
                        )

                    success = await client.download_video_file(
                        video_url=stream_url,
                        dest_path=dest_path,
                        on_progress=on_chunk,
                        stop_check=lambda: self._stop_requested,
                        timeout=300
                    )

                    if success:
                        self.downloaded_count += 1
                        file_mb = dest_path.stat().st_size / (1024 * 1024)
                        add_log("SUCCESS", f"✅ Сохранено [{idx}/{self.total_to_download}]: {dest_filename} ({file_mb:.2f} MB)")
                    else:
                        if not self._stop_requested:
                            self.failed_count += 1
                            err_msg = f"Ошибка скачивания файла {dest_filename}"
                            self.errors.append(err_msg)
                            add_log("ERROR", err_msg)
                except Exception as file_err:
                    self.failed_count += 1
                    err_msg = f"Ошибка обработки видео {fid}: {file_err}"
                    self.errors.append(err_msg)
                    add_log("ERROR", err_msg)

                # Polite delay between video downloads
                await human_jitter(0.4, 0.8)

            self.percent = 100
            final_msg = (
                f"🎉 Завершено! Скачано: {self.downloaded_count}, "
                f"пропущено дубликатов: {self.skipped_count}, ошибок: {self.failed_count} "
                f"(в папку {self.folder_path})"
            )
            self.status_text = final_msg
            add_log("SUCCESS", final_msg)

        except asyncio.CancelledError:
            self.status_text = "Процесс отменен"
            add_log("WARNING", "⏹️ Процесс скачивания популярного отменен")
        except Exception as e:
            err = f"Критическая ошибка загрузчика популярного: {e}"
            self.status_text = err
            self.errors.append(err)
            add_log("ERROR", err)
        finally:
            self.is_running = False
            self.is_scanning = False
            self._stop_requested = False
            self.current_video = None

# Global singleton
popular_downloader = PopularDownloader()
