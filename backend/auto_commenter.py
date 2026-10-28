import asyncio
import json
import os
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional, Set

from .client import WvidsClient, parse_spintax
from .config import BASE_DIR
from .storage import add_log, get_active_account, get_tor_config
from .fingerprint import human_jitter
from .tor import tor_manager

STATE_FILE = BASE_DIR / "data" / "autocomment_state.json"

DEFAULT_TEMPLATES = [
    "{Огонь|Супер|Круто|Топ}! 🔥 {Заходи|Переходи} в профиль — там продолжение!",
    "{Классное|Отличное|Шикарное} видео! {Жми|Загляни} ко мне на страницу",
    "Это же просто {разрыв|пушка|бомба}! 💥 Больше интересного у меня в профиле",
    "{Красота|Кайф|Шедевр}! Больше похожего контента в профиле 👀",
]

class AutoCommenter:
    def __init__(self):
        self.is_running: bool = False
        self.status_text: str = "Ожидание запуска"
        
        # Configurable Settings
        self.interval_seconds: int = 180      # 3 minutes default
        self.jitter_seconds: int = 25         # ±25 seconds jitter
        self.min_views: int = 50              # Minimum views to comment
        self.max_views: int = 5000            # Maximum views (0 = no limit)
        self.max_pages_scan: int = 3          # Pages of /mov to scan
        self.daily_limit: int = 30            # Max comments per day
        self.comment_templates: List[str] = list(DEFAULT_TEMPLATES)
        self.enable_spintax: bool = True
        
        # Runtime State
        self.commented_fids: Set[str] = set()
        self.history: List[Dict[str, Any]] = []
        self.today_date: str = ""
        self.today_count: int = 0
        self.total_count: int = 0
        self.last_comment_time: Optional[float] = None
        self.next_run_timestamp: Optional[float] = None
        self.current_action: str = ""
        
        self._task: Optional[asyncio.Task] = None
        self._stop_requested: bool = False
        
        # Load persisted state
        self._load_state()

    def _load_state(self):
        """Loads persistent settings and history from disk."""
        if not STATE_FILE.exists():
            return
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            self.interval_seconds = int(data.get("interval_seconds", self.interval_seconds))
            self.min_views = int(data.get("min_views", self.min_views))
            self.max_views = int(data.get("max_views", self.max_views))
            self.max_pages_scan = int(data.get("max_pages_scan", self.max_pages_scan))
            self.daily_limit = int(data.get("daily_limit", self.daily_limit))
            
            loaded_templates = data.get("comment_templates")
            if isinstance(loaded_templates, list) and loaded_templates:
                self.comment_templates = [str(t).strip() for t in loaded_templates if str(t).strip()]
                
            self.enable_spintax = bool(data.get("enable_spintax", True))
            
            fids = data.get("commented_fids")
            if isinstance(fids, list):
                self.commented_fids = set(str(x) for x in fids)
                
            self.history = data.get("history", [])[-100:]
            self.today_date = data.get("today_date", "")
            self.today_count = int(data.get("today_count", 0))
            self.total_count = int(data.get("total_count", len(self.commented_fids)))
        except Exception as e:
            add_log("WARNING", f"Не удалось загрузить состояние автокомментинга: {e}")

    def _save_state(self):
        """Saves settings and recent history to disk."""
        try:
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "interval_seconds": self.interval_seconds,
                "min_views": self.min_views,
                "max_views": self.max_views,
                "max_pages_scan": self.max_pages_scan,
                "daily_limit": self.daily_limit,
                "comment_templates": self.comment_templates,
                "enable_spintax": self.enable_spintax,
                "commented_fids": list(self.commented_fids)[-1000:],  # keep last 1000 fids
                "history": self.history[-100:],                        # keep last 100 events
                "today_date": self.today_date,
                "today_count": self.today_count,
                "total_count": self.total_count
            }
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            add_log("WARNING", f"Не удалось сохранить состояние автокомментинга: {e}")

    def _check_and_reset_daily(self):
        """Resets today_count when date changes (UTC)."""
        now_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self.today_date != now_date:
            self.today_date = now_date
            self.today_count = 0
            self._save_state()

    def get_status(self) -> Dict[str, Any]:
        self._check_and_reset_daily()
        time_to_next = 0
        if self.is_running and self.next_run_timestamp:
            time_to_next = max(0, int(self.next_run_timestamp - time.time()))

        return {
            "is_running": self.is_running,
            "status_text": self.status_text,
            "current_action": self.current_action,
            "interval_seconds": self.interval_seconds,
            "interval_minutes": round(self.interval_seconds / 60, 1),
            "min_views": self.min_views,
            "max_views": self.max_views,
            "daily_limit": self.daily_limit,
            "today_count": self.today_count,
            "total_count": self.total_count,
            "unique_videos_commented": len(self.commented_fids),
            "time_to_next_seconds": time_to_next,
            "last_comment_time": self.last_comment_time,
            "comment_templates": self.comment_templates,
            "enable_spintax": self.enable_spintax,
            "history": self.history[-30:]
        }

    def update_settings(self, settings: Dict[str, Any]) -> Dict[str, Any]:
        """Updates configurable parameters."""
        if "interval_seconds" in settings:
            sec = int(settings["interval_seconds"])
            if sec >= 30:
                self.interval_seconds = sec
        elif "interval_minutes" in settings:
            mins = float(settings["interval_minutes"])
            if mins >= 0.5:
                self.interval_seconds = int(mins * 60)

        if "min_views" in settings:
            self.min_views = max(0, int(settings["min_views"]))
        if "max_views" in settings:
            self.max_views = max(0, int(settings["max_views"]))
        if "daily_limit" in settings:
            self.daily_limit = max(1, int(settings["daily_limit"]))
        if "enable_spintax" in settings:
            self.enable_spintax = bool(settings["enable_spintax"])
        if "comment_templates" in settings:
            tmps = settings["comment_templates"]
            if isinstance(tmps, str):
                lines = [line.strip() for line in tmps.splitlines() if line.strip()]
            elif isinstance(tmps, list):
                lines = [str(t).strip() for t in tmps if str(t).strip()]
            else:
                lines = []
            if lines:
                self.comment_templates = lines

        self._save_state()
        return {"success": True, "message": "Настройки автокомментинга сохранены"}

    async def start(self) -> Dict[str, Any]:
        """Starts the background autocomment loop."""
        if self.is_running:
            return {"success": False, "message": "Автокомментинг уже запущен"}

        active = get_active_account()
        if not active:
            return {"success": False, "error": "Нет активного аккаунта для публикации комментариев"}

        self.is_running = True
        self._stop_requested = False
        self.status_text = "Автокомментинг запущен"
        add_log("INFO", f"🚀 Запущен модуль автокомментинга (интервал: {self.interval_seconds}с, мин. просмотры: {self.min_views})")

        self._task = asyncio.create_task(self._run_loop())
        return {"success": True, "message": "Автокомментинг успешно запущен"}

    async def stop(self) -> Dict[str, Any]:
        """Stops the background autocomment loop."""
        if not self.is_running:
            return {"success": False, "message": "Автокомментинг не активен"}

        self._stop_requested = True
        self.status_text = "Остановка автокомментинга..."
        add_log("WARNING", "⏹️ Остановка модуля автокомментинга пользователем")

        if self._task and not self._task.done():
            self._task.cancel()

        self.is_running = False
        self.next_run_timestamp = None
        self.current_action = ""
        self.status_text = "Остановлен пользователем"
        return {"success": True, "message": "Автокомментинг остановлен"}

    def _create_client(self) -> WvidsClient:
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

    def _render_random_comment(self) -> str:
        """Picks a random template and resolves spintax if enabled."""
        if not self.comment_templates:
            return "Отличное видео! Больше интересного в профиле 🔥"
        template = random.choice(self.comment_templates)
        if self.enable_spintax:
            return parse_spintax(template)
        return template

    async def test_single_comment(self, fid: str, text: Optional[str] = None) -> Dict[str, Any]:
        """Manually posts a test comment to a specific video ID."""
        client = self._create_client()
        comment_text = text.strip() if text and text.strip() else self._render_random_comment()
        res = await client.post_comment(fid=str(fid), msg=comment_text)
        
        hist_item = {
            "file_id": str(fid),
            "title": f"Тестовое видео {fid}",
            "views": 0,
            "comment": comment_text,
            "status": "success" if res.get("success") else "error",
            "error": res.get("error", ""),
            "timestamp": int(time.time()),
            "time_str": datetime.now().strftime("%H:%M:%S"),
            "post_url": f"https://wvids.com/{fid}"
        }
        self.history.append(hist_item)
        if res.get("success"):
            self.commented_fids.add(str(fid))
            self.total_count += 1
        self._save_state()
        return res

    async def _run_loop(self):
        """Main background loop."""
        try:
            while not self._stop_requested:
                self._check_and_reset_daily()

                # Check daily limit
                if self.today_count >= self.daily_limit:
                    msg = f"Достигнут дневной лимит комментариев ({self.today_count}/{self.daily_limit}). Ожидание нового дня..."
                    self.status_text = msg
                    add_log("WARNING", f"💬 Автокомментинг: {msg}")
                    await asyncio.sleep(300)
                    continue

                client = self._create_client()

                # Step 1: Scan fresh videos in /mov
                self.current_action = f"Сканирование свежих видео (просмотры >= {self.min_views})..."
                self.status_text = self.current_action
                
                new_videos = await client.get_new_videos(
                    min_views=self.min_views,
                    max_views=self.max_views,
                    max_pages=self.max_pages_scan,
                    stop_check=lambda: self._stop_requested
                )

                if self._stop_requested:
                    break

                # Filter out already commented videos
                eligible_videos = [
                    v for v in new_videos
                    if str(v.get("file_id")) not in self.commented_fids
                ]

                if not eligible_videos:
                    self.status_text = f"Подходящих новых видео не найдено (мин. просмотры: {self.min_views}). Повтор через 60 сек."
                    await asyncio.sleep(60)
                    continue

                # Step 2: Randomly select an eligible video
                target_video = random.choice(eligible_videos)
                target_fid = str(target_video["file_id"])
                target_slug = target_video.get("slug") or target_fid
                target_title = target_video.get("title") or target_fid
                target_views = target_video.get("views", 0)

                # Step 3: Generate comment text
                comment_msg = self._render_random_comment()

                self.current_action = f"Публикация комментария на видео {target_fid} ({target_views} просм.)..."
                self.status_text = self.current_action
                add_log("INFO", f"💬 Автокомментинг: публикация на '{target_title[:30]}' (ID {target_fid}, {target_views} просм.): '{comment_msg}'")

                # Step 4: Post comment
                res = await client.post_comment(
                    fid=target_fid,
                    msg=comment_msg,
                    slug=target_slug
                )

                success = bool(res.get("success"))
                now_ts = int(time.time())
                now_str = datetime.now().strftime("%H:%M:%S")

                hist_item = {
                    "file_id": target_fid,
                    "title": target_title,
                    "views": target_views,
                    "comment": comment_msg,
                    "status": "success" if success else "error",
                    "error": res.get("error", ""),
                    "timestamp": now_ts,
                    "time_str": now_str,
                    "post_url": target_video.get("post_url", f"https://wvids.com/{target_slug}")
                }
                self.history.append(hist_item)

                if success:
                    self.commented_fids.add(target_fid)
                    self.today_count += 1
                    self.total_count += 1
                    self.last_comment_time = now_ts
                    self.status_text = f"Успешно оставлен комментарий на ID {target_fid} (Сегодня: {self.today_count}/{self.daily_limit})"
                else:
                    self.status_text = f"Ошибка комментария на ID {target_fid}: {res.get('error')}"

                self._save_state()

                # Step 5: Sleep for interval with human-like jitter
                jitter = random.uniform(-self.jitter_seconds, self.jitter_seconds)
                sleep_duration = max(30, self.interval_seconds + jitter)
                self.next_run_timestamp = time.time() + sleep_duration

                add_log("INFO", f"⏳ Следующий комментарий через {int(sleep_duration)} сек. ({round(sleep_duration / 60, 1)} мин.)")

                # Sleep in 1-second chunks for responsive stopping
                sleep_start = time.time()
                while time.time() - sleep_start < sleep_duration:
                    if self._stop_requested:
                        break
                    remain = int(sleep_duration - (time.time() - sleep_start))
                    self.current_action = f"Ожидание {remain}с до следующего комментария"
                    await asyncio.sleep(1)

        except asyncio.CancelledError:
            self.status_text = "Процесс отменен"
        except Exception as e:
            err = f"Критическая ошибка автокомментинга: {e}"
            self.status_text = err
            add_log("ERROR", err)
        finally:
            self.is_running = False
            self.next_run_timestamp = None
            self.current_action = ""
            self._stop_requested = False

# Global Singleton
auto_commenter = AutoCommenter()
