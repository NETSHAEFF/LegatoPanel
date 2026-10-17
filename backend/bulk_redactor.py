import asyncio
import time
from typing import Dict, Any, List, Optional
from .client import WvidsClient
from .fingerprint import human_jitter
from .storage import add_log

class BulkRedactor:
    def __init__(self):
        self.is_running: bool = False
        self.is_scanning: bool = False
        self.status_text: str = "Ожидание действий"
        self.total: int = 0
        self.current_index: int = 0
        self.percent: int = 0
        self.success_count: int = 0
        self.error_count: int = 0
        self.posts: List[Dict[str, Any]] = []
        self.errors: List[str] = []
        self.last_scanned_user: Optional[str] = None
        self._task: Optional[asyncio.Task] = None

    def get_status(self) -> Dict[str, Any]:
        return {
            "is_running": self.is_running,
            "is_scanning": self.is_scanning,
            "status_text": self.status_text,
            "total": self.total,
            "current_index": self.current_index,
            "percent": self.percent,
            "success_count": self.success_count,
            "error_count": self.error_count,
            "total_scanned_posts": len(self.posts),
            "posts": self.posts,
            "last_scanned_user": self.last_scanned_user,
            "errors": self.errors[-10:]
        }

    async def scan_posts(self, client: WvidsClient, username: str, max_pages: int = 50) -> List[Dict[str, Any]]:
        if self.is_scanning:
            return self.posts

        self.is_scanning = True
        self.status_text = f"Сканирование публикаций канала @{username} (страницы 1, 2, 3...)"
        try:
            posts = await client.get_all_user_posts(username=username, max_pages=max_pages)
            for p in posts:
                p["status"] = "ready"
            self.posts = posts
            self.last_scanned_user = username
            self.status_text = f"Сканирование завершено: найдено {len(posts)} публикаций"
            return posts
        except Exception as e:
            err = f"Ошибка сканирования: {str(e)}"
            self.status_text = err
            add_log("ERROR", err)
            return []
        finally:
            self.is_scanning = False

    async def start(
        self,
        client: WvidsClient,
        title_template: Optional[str] = "",
        desc_template: Optional[str] = "",
        file_ids: Optional[List[str]] = None,
        username: Optional[str] = None
    ) -> Dict[str, Any]:
        if self.is_running:
            return {"success": False, "error": "Массовое редактирование уже выполняется"}

        # If posts list is empty, scan first
        if not self.posts and username:
            add_log("INFO", f"Список постов пуст, автоматический запуск сканирования @{username}...")
            await self.scan_posts(client, username)

        if not self.posts:
            return {"success": False, "error": "Нет найденных публикаций для редактирования. Сначала выполните сканирование."}

        title_tpl = title_template.strip() if title_template else None
        desc_tpl = desc_template.strip() if desc_template else None

        if not title_tpl and not desc_tpl:
            return {"success": False, "error": "Укажите хотя бы новое имя/заголовок или новое описание"}

        # Filter targets
        if file_ids and len(file_ids) > 0:
            target_ids = set(str(fid).strip() for fid in file_ids)
            targets = [p for p in self.posts if str(p.get("file_id")) in target_ids]
        else:
            targets = list(self.posts)

        if not targets:
            return {"success": False, "error": "Выбранные посты не найдены в списке"}

        self.is_running = True
        self.total = len(targets)
        self.current_index = 0
        self.percent = 0
        self.success_count = 0
        self.error_count = 0
        self.errors = []
        self.status_text = f"Подготовка к обновлению {self.total} публикаций..."

        add_log("INFO", f"Запущен процесс массового редактирования {self.total} публикаций (все страницы)")
        self._task = asyncio.create_task(self._run_bulk_loop(client, targets, title_tpl, desc_tpl))

        return {
            "success": True,
            "total": self.total,
            "message": f"Запущен процесс обновления {self.total} публикаций"
        }

    async def _run_bulk_loop(
        self,
        client: WvidsClient,
        targets: List[Dict[str, Any]],
        title_template: Optional[str],
        desc_template: Optional[str]
    ):
        try:
            for idx, post in enumerate(targets):
                if not self.is_running:
                    add_log("WARNING", "Массовое редактирование принудительно остановлено пользователем")
                    self.status_text = "Процесс остановлен пользователем"
                    break

                fid = str(post.get("file_id"))
                orig_title = post.get("title", "")
                i_num = idx + 1

                # Calculate formatted title
                cur_title = None
                if title_template:
                    cur_title = title_template.replace("{i}", str(i_num))
                    cur_title = cur_title.replace("{title}", orig_title)
                    cur_title = cur_title.replace("{fid}", fid)
                    cur_title = cur_title.replace("{id}", fid)

                # Calculate formatted description
                cur_desc = None
                if desc_template:
                    cur_desc = desc_template.replace("{i}", str(i_num))
                    cur_desc = cur_desc.replace("{title}", orig_title)
                    cur_desc = cur_desc.replace("{fid}", fid)
                    cur_desc = cur_desc.replace("{id}", fid)

                self.status_text = f"[{i_num}/{self.total}] Редактирование ID {fid} (Стр. {post.get('page', 1)})..."

                res = await client.redact_post(
                    file_id=fid,
                    title=cur_title,
                    description=cur_desc
                )

                if res.get("success"):
                    self.success_count += 1
                    post["status"] = "updated"
                    if cur_title:
                        post["title"] = cur_title
                    add_log("SUCCESS", f"Пост ID {fid} обновлен: {cur_title or orig_title}")
                else:
                    self.error_count += 1
                    post["status"] = "failed"
                    err_detail = "; ".join(res.get("errors", ["Неизвестная ошибка"]))
                    self.errors.append(f"ID {fid}: {err_detail}")
                    add_log("WARNING", f"Ошибка обновления ID {fid}: {err_detail}")

                self.current_index = i_num
                self.percent = int((self.current_index / self.total) * 100)

                # Jitter between calls to prevent rate-limiting and stay stealthy
                await human_jitter(0.35, 0.75)

            if self.is_running:
                final_msg = f"Массовое обновление завершено: успешно {self.success_count}/{self.total}, ошибок: {self.error_count}"
                self.status_text = final_msg
                self.percent = 100
                add_log("SUCCESS", final_msg)

        except asyncio.CancelledError:
            self.status_text = "Операция отменена"
            add_log("INFO", "Задача массового редактирования отменена")
        except Exception as e:
            err = f"Сбой в цикле массового редактирования: {str(e)}"
            self.status_text = err
            add_log("ERROR", err)
        finally:
            self.is_running = False

    async def stop(self):
        if self.is_running:
            self.is_running = False
            self.status_text = "Остановка процесса..."
            if self._task and not self._task.done():
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
            self.status_text = "Процесс остановлен"
            add_log("INFO", "Массовое редактирование остановлено пользователем")

bulk_redactor = BulkRedactor()
