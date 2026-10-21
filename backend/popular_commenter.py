import asyncio
import random
import re
import time
from typing import Dict, Any, List, Optional

from .client import WvidsClient
from .storage import (
    add_log,
    add_popular_comment_record,
    get_account_by_id,
    get_active_account,
    get_all_accounts,
    mark_account_suspended
)

def parse_spintax(text: str) -> str:
    """
    Expands spintax patterns like {Класс|Супер|Огонь} into a randomly selected alternative.
    Supports nested braces.
    """
    pattern = re.compile(r'\{([^{}]+)\}')
    current = text
    while pattern.search(current):
        current = pattern.sub(lambda m: random.choice(m.group(1).split('|')), current)
    return current.strip()

class PopularCommenter:
    """
    Manages automated and manual commenting workflows on Popular videos from wvids.com.
    Supports spintax generation, delay intervals, multi-account rotation, and live tracking.
    """
    def __init__(self):
        self.is_running: bool = False
        self.is_fetching: bool = False
        self.status_text: str = "Ожидание запуска"
        self.total: int = 0
        self.current_index: int = 0
        self.percent: int = 0
        self.success_count: int = 0
        self.error_count: int = 0
        self.logs: List[str] = []
        self.cached_videos: List[Dict[str, Any]] = []
        self.last_fetched_at: int = 0
        self._task: Optional[asyncio.Task] = None

    def get_status(self) -> Dict[str, Any]:
        return {
            "is_running": self.is_running,
            "is_fetching": self.is_fetching,
            "status_text": self.status_text,
            "total": self.total,
            "current_index": self.current_index,
            "percent": self.percent,
            "success_count": self.success_count,
            "error_count": self.error_count,
            "total_popular_videos": len(self.cached_videos),
            "videos": self.cached_videos,
            "last_fetched_at": self.last_fetched_at,
            "logs": self.logs[-20:]
        }

    async def fetch_popular_videos(
        self,
        client: WvidsClient,
        max_items: int = 50,
        force: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Retrieves popular videos from wvids.com. Uses cached list if within 2 minutes unless force=True
        or requested max_items exceeds cached count.
        """
        now = int(time.time())
        if not force and self.cached_videos and (now - self.last_fetched_at < 120) and len(self.cached_videos) >= max_items:
            return self.cached_videos[:max_items]

        self.is_fetching = True
        self.status_text = "Загрузка видео из раздела Популярные..."
        try:
            videos = await client.get_popular_videos(max_items=max_items)
            self.cached_videos = videos
            self.last_fetched_at = now
            self.status_text = f"Загружено {len(videos)} популярных видео"
            add_log("INFO", f"Успешно обновлен список популярных видео: {len(videos)} шт.")
            return videos
        except Exception as e:
            err = f"Ошибка обновления популярных видео: {str(e)}"
            self.status_text = err
            add_log("ERROR", err)
            return self.cached_videos
        finally:
            self.is_fetching = False

    async def post_single_comment(
        self,
        client: WvidsClient,
        fid: str,
        text: str,
        video_title: str = "",
        video_url: str = "",
        account_id: Optional[int] = None,
        account_username: str = ""
    ) -> Dict[str, Any]:
        """
        Posts a single comment on a video and records history in SQLite DB.
        """
        resolved_text = parse_spintax(text)
        if not resolved_text:
            return {"success": False, "error": "Текст комментария пуст"}

        clean_fid = str(fid).strip()
        slug = video_url.rstrip("/").split("/")[-1] if video_url else ""

        res = await client.post_comment(fid=clean_fid, text=resolved_text, slug=slug)

        status_str = "success" if res.get("success") else "failed"
        response_msg = ""
        if res.get("success"):
            cid = res.get("comment_id") or "ok"
            response_msg = f"Комментарий успешно опубликован (ID #{cid})"
            add_log("SUCCESS", f"Опубликован комментарий на видео #{clean_fid} '{video_title}': \"{resolved_text[:40]}...\"")
        else:
            response_msg = res.get("error") or "Неизвестная ошибка"
            add_log("WARNING", f"Ошибка публикации комментария на видео #{clean_fid}: {response_msg}")

            if res.get("is_suspended") and account_id:
                mark_account_suspended(account_id, response_msg)

        add_popular_comment_record(
            account_id=account_id,
            account_username=account_username,
            video_fid=clean_fid,
            video_title=video_title or f"Video #{clean_fid}",
            video_url=video_url or f"https://wvids.com/v/{clean_fid}",
            comment_text=resolved_text,
            status=status_str,
            response_msg=response_msg
        )

        return {
            "success": res.get("success", False),
            "comment_id": res.get("comment_id"),
            "comment_text": resolved_text,
            "error": res.get("error"),
            "status": status_str,
            "response_msg": response_msg
        }

    async def start_batch(
        self,
        target_fids: List[str],
        comment_templates: List[str],
        delay_min: float = 5.0,
        delay_max: float = 12.0,
        account_ids: Optional[List[int]] = None
    ) -> Dict[str, Any]:
        """
        Launches background batch commenting on selected videos.
        """
        if self.is_running:
            return {"success": False, "error": "Процесс авто-комментирования уже запущен"}

        if not target_fids:
            return {"success": False, "error": "Не выбрано ни одного видео для комментирования"}

        if not comment_templates or all(not t.strip() for t in comment_templates):
            return {"success": False, "error": "Не указаны шаблоны комментариев"}

        self.is_running = True
        self.total = len(target_fids)
        self.current_index = 0
        self.percent = 0
        self.success_count = 0
        self.error_count = 0
        self.logs = []
        self.status_text = f"Запуск комментирования {self.total} видео..."

        # Start loop
        self._task = asyncio.create_task(
            self._run_batch_loop(
                target_fids=target_fids,
                comment_templates=[t.strip() for t in comment_templates if t.strip()],
                delay_min=max(1.0, float(delay_min)),
                delay_max=max(float(delay_min), float(delay_max)),
                account_ids=account_ids or []
            )
        )

        return {
            "success": True,
            "message": f"Запущен процесс комментирования {self.total} видео",
            "total": self.total
        }

    async def _run_batch_loop(
        self,
        target_fids: List[str],
        comment_templates: List[str],
        delay_min: float,
        delay_max: float,
        account_ids: List[int]
    ):
        video_lookup = {v.get("fid"): v for v in self.cached_videos}
        acc_index = 0

        # Build list of accounts to rotate
        valid_accounts = []
        if account_ids:
            for aid in account_ids:
                acc = get_account_by_id(aid)
                if acc and not acc.get("is_suspended"):
                    valid_accounts.append(acc)

        if not valid_accounts:
            active_acc = get_active_account()
            if active_acc and not active_acc.get("is_suspended"):
                valid_accounts.append(active_acc)

        if not valid_accounts:
            self.status_text = "Ошибка: нет доступных активных аккаунтов"
            self.logs.append("❌ Ошибка: нет авторизованных аккаунтов")
            add_log("ERROR", "Авто-комментирование остановлено: нет доступных аккаунтов")
            self.is_running = False
            return

        self.logs.append(f"🚀 Старт массового комментирования {len(target_fids)} видео (Аккаунтов: {len(valid_accounts)})")

        for idx, fid in enumerate(target_fids):
            if not self.is_running:
                self.logs.append("⏹️ Процесс остановлен пользователем")
                break

            self.current_index = idx + 1
            self.percent = int((self.current_index / self.total) * 100)

            video_info = video_lookup.get(fid, {})
            title = video_info.get("title") or f"Video #{fid}"
            url = video_info.get("url") or f"https://wvids.com/v/{fid}"

            # Pick account (rotation)
            current_acc = valid_accounts[acc_index % len(valid_accounts)]
            acc_index += 1
            acc_name = current_acc.get("username", "Unknown")
            acc_id = current_acc.get("id")

            # Pick comment template & parse spintax
            template = random.choice(comment_templates)
            comment_text = parse_spintax(template)

            self.status_text = f"[{self.current_index}/{self.total}] Комментирование видео #{fid} (@{acc_name})..."

            # Instantiate client for account
            client = WvidsClient(
                cookies=current_acc.get("cookies", {}),
                fingerprint=current_acc.get("fingerprint", "chrome124"),
                proxy=current_acc.get("proxy"),
                use_tor=True
            )

            try:
                res = await self.post_single_comment(
                    client=client,
                    fid=fid,
                    text=comment_text,
                    video_title=title,
                    video_url=url,
                    account_id=acc_id,
                    account_username=acc_name
                )

                if res.get("success"):
                    self.success_count += 1
                    self.logs.append(f"✅ [#{fid}] @{acc_name}: \"{comment_text[:35]}...\" — Успешно!")
                else:
                    self.error_count += 1
                    err_msg = res.get("error", "Ошибка")
                    self.logs.append(f"⚠️ [#{fid}] @{acc_name}: \"{comment_text[:35]}...\" — {err_msg}")

            except Exception as e:
                self.error_count += 1
                self.logs.append(f"❌ [#{fid}] Сбой: {str(e)}")

            # Random human delay before next comment if not last
            if idx < len(target_fids) - 1 and self.is_running:
                wait_sec = round(random.uniform(delay_min, delay_max), 1)
                self.status_text = f"Ожидание {wait_sec} сек перед следующим видео..."
                for _ in range(int(wait_sec * 10)):
                    if not self.is_running:
                        break
                    await asyncio.sleep(0.1)

        self.is_running = False
        summary = f"Комментирование завершено: успешно {self.success_count}, ошибок {self.error_count}"
        self.status_text = summary
        self.logs.append(f"🏁 {summary}")
        add_log("INFO", f"Завершено комментирование в Популярном: {self.success_count} успешно, {self.error_count} ошибок")

    async def stop(self) -> Dict[str, Any]:
        """
        Gracefully halts batch commenter.
        """
        if not self.is_running:
            return {"success": False, "message": "Процесс уже остановлен"}

        self.is_running = False
        if self._task and not self._task.done():
            self._task.cancel()

        self.status_text = "Процесс принудительно остановлен"
        self.logs.append("⏹️ Процесс принудительно остановлен пользователем")
        add_log("WARNING", "Авто-комментирование в разделе Популярные остановлено пользователем")
        return {"success": True, "message": "Процесс остановлен"}

popular_commenter = PopularCommenter()
