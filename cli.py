import sys
import argparse
import asyncio
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Add project root to path
BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from backend.storage import get_active_account, save_account, update_account_stats, get_tor_config, get_setting
from backend.client import WvidsClient
from backend.uploader import ContentUploader
from backend.fingerprint import parse_cookies_input
from backend.tor import check_tor_connection, auto_detect_tor_port, tor_manager
from backend.vless import check_vless_connection, vless_manager
from backend.batch_worker import batch_worker

def get_client() -> WvidsClient:
    active = get_active_account()
    if not active:
        print("[!] Ошибка: Нет активного сохраненного аккаунта. Сначала выполните: python cli.py login --cookies \"...\"")
        sys.exit(1)
    tor_manager.ensure_running(port=9050)
    return WvidsClient(
        cookies=active.get("cookies", {}),
        fingerprint=active.get("fingerprint", "chrome124"),
        use_tor=True
    )

async def cmd_batch(args):
    active = get_active_account()
    if not active:
        print("[!] Ошибка: Нет активного аккаунта. Сначала выполните: python cli.py login")
        return
    folder = args.folder or "videos"
    delay_min = args.interval or 5.0
    desc = args.desc or get_setting("batch_desc", "")
    print(f"[*] Запуск пакетной публикации из папки '{folder}'...")
    print(f"[*] Интервал после успешной выгрузки: {delay_min} мин.")
    if desc:
        desc_preview = (desc[:45] + "...") if len(desc) > 45 else desc
        print(f"[*] Описание: '{desc_preview}'")
    print(f"[*] При обнаружении дубликата ('This file already exists') пропуск БЕЗ ПАУЗЫ.")
    print(f"[*] Маршрут: 🧅 Tor SOCKS5 (защита от утечек)")

    res = await batch_worker.start(
        folder_path=folder,
        delay_minutes=delay_min,
        title_template=args.title or "",
        desc_template=desc,
        account_id=active["id"]
    )
    if not res.get("success"):
        print(f"[!] {res.get('error')}")
        return

    try:
        while True:
            st = batch_worker.get_status()
            if not st["is_running"]:
                print(f"\n[+] {st['last_message']}")
                break
            if st["state"] == "waiting_delay":
                print(f"\r[⏳] Пауза: осталось {st['delay_remaining']} сек... (Успешно: {st['success_count']}, Дубликатов: {st['duplicate_count']})   ", end="", flush=True)
            else:
                curr = st["current_file"] or "подготовка"
                print(f"\r[*] Выгрузка: {curr} ({st['current_index']}/{st['total_files']}) [Успешно: {st['success_count']}, Дубликатов: {st['duplicate_count']}]   ", end="", flush=True)
            await asyncio.sleep(1)
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("\n[!] Остановка по требованию пользователя...")
        await batch_worker.stop()
        print("[+] Очередь остановлена.")

async def cmd_tor(args):
    action = getattr(args, "action", "status")
    if action == "start":
        print("[*] 🧅 Запуск автономного фонового Tor (без браузера)...")
        res = tor_manager.start(port=args.port or 9050)
        if res.get("success"):
            print(f"[+] {res.get('message', 'Tor успешно запущен')}")
            st = check_tor_connection(port=args.port or 9050)
            if st.get("connected"):
                print(f"[+] 🌐 Выходной Tor IP: {st.get('ip')}")
            print(f"[*] Для удержания службы нажмите Ctrl+C для выхода.")
            try:
                while True:
                    await asyncio.sleep(1)
            except (KeyboardInterrupt, asyncio.CancelledError):
                print("\n[*] Остановка Tor...")
                tor_manager.stop()
                print("[+] Служба Tor остановлена.")
        else:
            print(f"[!] {res.get('error', 'Ошибка запуска Tor')}")
        return
    elif action == "stop":
        print("[*] 🧅 Остановка фонового Tor...")
        res = tor_manager.stop()
        print(f"[+] {res.get('message')}")
        return

    print("[*] Проверка подключения к сети Tor (без DNS-утечек)...")
    detected = auto_detect_tor_port()
    if not detected:
        print("[*] Tor не обнаружен. Автоматический запуск автономного Tor в фоне...")
        tor_manager.ensure_running(port=args.port or 9050)
        detected = auto_detect_tor_port()

    port = args.port or detected or 9050
    status = check_tor_connection(port=port)
    mgr_status = tor_manager.get_status()
    mode = "Автономный фоновый" if (mgr_status.get("managed_by_panel") or port == 9050) else "Tor Browser"

    print("=" * 55)
    if status.get("connected"):
        print(f" 🧅 Tor Сеть:           ПОДКЛЮЧЕНО ({mode})")
        print(f" 🌐 Выходной IP (Exit): {status.get('ip')}")
        print(f" 🛡️  Подтвержден как Tor: {'ДА (Tor Exit Node)' if status.get('is_tor') else 'НЕТ'}")
        print(f" 🔌 Локальный порт SOCKS: {status.get('port')}")
        if mgr_status.get("pid"):
            print(f" ⚙️  PID процесса:       {mgr_status.get('pid')}")
        print(f" ⏱️  Задержка цепи:     {status.get('latency_ms')} мс")
        print(f" 🔒 DNS Резолвинг:      Удаленный (Zero-Leak via socks5h://)")
    else:
        print(" 🧅 Tor Сеть:           ОТКЛЮЧЕНО / ОШИБКА")
        print(f" [!] {status.get('error')}")
    print("=" * 55)

async def cmd_vless(args):
    action = getattr(args, "action", "status")
    port = args.port or 10808
    if action == "start":
        print("[*] ⚡ Запуск службы VLESS Reality (xray)...")
        res = vless_manager.start(port=port)
        if res.get("success"):
            print(f"[+] {res.get('message', 'VLESS успешно запущен')}")
            st = check_vless_connection(port=port)
            if st.get("connected"):
                print(f"[+] 🌐 Внешний IP через VLESS: {st.get('ip')}")
            print(f"[*] Для удержания службы нажмите Ctrl+C для выхода.")
            try:
                while True:
                    await asyncio.sleep(1)
            except (KeyboardInterrupt, asyncio.CancelledError):
                print("\n[*] Остановка VLESS...")
                vless_manager.stop()
                print("[+] Служба VLESS остановлена.")
        else:
            print(f"[!] {res.get('error', 'Ошибка запуска VLESS')}")
        return
    elif action == "stop":
        print("[*] ⚡ Остановка фонового VLESS...")
        res = vless_manager.stop()
        print(f"[+] {res.get('message')}")
        return

    print("[*] Проверка подключения VLESS Reality...")
    status = check_vless_connection(port=port)
    mgr_status = vless_manager.get_status()

    print("=" * 55)
    if status.get("connected"):
        print(f" ⚡ VLESS Reality:      ПОДКЛЮЧЕНО")
        print(f" 🌐 Выходной IP:        {status.get('ip')}")
        print(f" 🔌 Локальный порт:     {port}")
        if mgr_status.get("pid"):
            print(f" ⚙️  PID процесса:       {mgr_status.get('pid')}")
        print(f" ⏱️  Задержка:           {status.get('latency_ms')} мс")
    else:
        print(" ⚡ VLESS Reality:      ОТКЛЮЧЕНО / ОШИБКА")
        print(f" [!] {status.get('error')}")
    print("=" * 55)

async def cmd_login(args):
    cookies_dict = parse_cookies_input(args.cookies)
    if not cookies_dict:
        print("[!] Ошибка: Не удалось распарсить куки.")
        return

    client = WvidsClient(cookies=cookies_dict, proxy=args.proxy, fingerprint=args.fingerprint)
    print("[*] Проверка сессии через анонимный TLS...")
    verify = await client.verify_session()
    if not verify.get("valid"):
        print(f"[!] Авторизация не удалась: {verify.get('error', 'Проверьте cookies')}")
        return

    uid = verify.get("uid")
    username = verify.get("username")
    acc_id = save_account(
        name=args.name or f"@{username}",
        username=username,
        uid=uid,
        cookies_raw=args.cookies,
        cookies_dict=cookies_dict,
        proxy=args.proxy or "",
        fingerprint=args.fingerprint
    )
    print(f"[+] Успешно авторизован! Аккаунт: @{username} (UID: {uid}), ID в базе: {acc_id}")

async def cmd_stats(args):
    client = get_client()
    active = get_active_account()
    print(f"[*] Получение статистики для @{active['username']} (UID: {active['uid']})...")
    stats = await client.get_statistics(active["uid"], active["username"])
    if not stats.get("success"):
        print(f"[!] Ошибка получения статистики: {stats.get('error')}")
        return

    print("=" * 45)
    print(f" 👤 Аккаунт: @{active['username']} (UID: {active['uid']})")
    print(f" 👥 Подписчики: {stats.get('subscribers', 0)}")
    print(f" 📈 Просмотры за сегодня: {stats.get('views_today', 0)}")
    print(f" 👁️  Всего просмотров: {stats.get('total_views', 0)}")
    print("=" * 45)

async def cmd_rename(args):
    client = get_client()
    active = get_active_account()
    new_nick = args.new_nickname.strip().lstrip("@")
    print(f"[*] Смена никнейма с @{active['username']} на @{new_nick}...")
    res = await client.change_nickname(new_nick)
    if res.get("success"):
        print(f"[+] {res.get('message', 'Никнейм успешно изменен!')}")
    else:
        print(f"[!] Ошибка смены никнейма: {res.get('error')}")

async def cmd_upload(args):
    client = get_client()
    uploader = ContentUploader(client)
    file_p = Path(args.file)
    if not file_p.exists():
        print(f"[!] Файл не найден: {args.file}")
        return

    title = args.title or file_p.stem
    desc = args.desc or ""

    def on_progress(msg, pct):
        print(f"[{pct}%] {msg}")

    print(f"[*] Начало выгрузки файла: {file_p.name} ({title})")
    res = await uploader.upload(
        file_path=str(file_p.resolve()),
        title=title,
        description=desc,
        progress_callback=on_progress
    )

    if res.get("success"):
        print(f"\n[+] 🎉 Готово! Файл успешно опубликован на wvids.com!")
        print(f"    FID: {res.get('file_id')}")
        print(f"    Название: {res.get('title')}")
    else:
        print(f"\n[!] Ошибка загрузки: {res.get('error')}")

async def cmd_scan(args):
    client = get_client()
    active = get_active_account()
    username = active["username"]
    print(f"[*] Сканирование всех страниц публикаций канала @{username} (page=1, 2, 3, 4...)...")
    posts = await client.get_all_user_posts(username, max_pages=args.max_pages or 50)
    print("=" * 65)
    print(f"Всего найдено постов: {len(posts)} на канале @{username}")
    print("=" * 65)
    for idx, p in enumerate(posts, 1):
        print(f"[{idx:3d}] ID: {p['file_id']:<8} | Стр: {p['page']} | Тип: {p['type']:<5} | Заголовок: {p['title'][:40]}")
    print("=" * 65)

async def cmd_redact_all(args):
    client = get_client()
    active = get_active_account()
    username = active["username"]
    if not args.title and not args.desc:
        print("[!] Ошибка: Укажите хотя бы --title или --desc для изменения.")
        return

    print(f"[*] 1/2: Сканирование всех публикаций канала @{username} (все страницы)...")
    posts = await client.get_all_user_posts(username, max_pages=args.max_pages or 50)
    if not posts:
        print("[!] Публикации не найдены.")
        return

    print(f"[+] Найдено {len(posts)} публикаций.")
    print(f"[*] 2/2: Применение изменений...")

    success_cnt = 0
    fail_cnt = 0
    for idx, p in enumerate(posts, 1):
        fid = p["file_id"]
        cur_title = args.title.replace("{i}", str(idx)).replace("{title}", p["title"]) if args.title else None
        cur_desc = args.desc.replace("{i}", str(idx)).replace("{title}", p["title"]) if args.desc else None

        print(f"\r[*] Редактирование {idx}/{len(posts)}: ID {fid} (Стр. {p['page']})... ", end="", flush=True)
        res = await client.redact_post(fid, title=cur_title, description=cur_desc)
        if res.get("success"):
            success_cnt += 1
        else:
            fail_cnt += 1

    print(f"\n[+] 🎉 Завершено! Успешно обновлено: {success_cnt}/{len(posts)}, ошибок: {fail_cnt}")

async def cmd_popular_list(args):
    client = get_client()
    print(f"[*] Получение списка популярных видео с wvids.com (лимит: {args.limit})...")
    videos = await client.get_popular_videos(max_items=args.limit)
    if not videos:
        print("[-] Популярные видео не найдены или не удалось загрузить.")
        return

    print(f"\n[+] Найдено {len(videos)} популярных видео:")
    print("-" * 80)
    for idx, v in enumerate(videos, 1):
        print(f"[{idx:02d}] FID: {v['fid']:<9} | {v['title'][:35]:<35} | {v['url']}")
    print("-" * 80)

async def cmd_popular_comment(args):
    client = get_client()
    acc = get_active_account()
    acc_name = acc.get("username", "Unknown") if acc else "Unknown"
    acc_id = acc.get("id") if acc else None

    from backend.popular_commenter import popular_commenter
    print(f"[*] Отправка комментария на видео FID {args.fid} от лица @{acc_name}...")
    res = await popular_commenter.post_single_comment(
        client=client,
        fid=args.fid,
        text=args.text,
        account_id=acc_id,
        account_username=acc_name
    )
    if res.get("success"):
        print(f"[+] ✅ Комментарий успешно опубликован! ID: {res.get('comment_id', 'ok')}")
    else:
        print(f"[-] ❌ Ошибка публикации: {res.get('error', 'Неизвестная ошибка')}")

def main():
    parser = argparse.ArgumentParser(description="Wvids Studio CLI Automation Tool")
    subparsers = parser.add_subparsers(dest="command", help="Команда для выполнения")

    # Login
    p_login = subparsers.add_parser("login", help="Авторизация по куки")
    p_login.add_argument("--cookies", required=True, help="Куки в формате 'k=v; ...' или JSON")
    p_login.add_argument("--name", default="My Wvids", help="Понятное имя аккаунта")
    p_login.add_argument("--proxy", default="", help="Прокси (например, socks5://127.0.0.1:1080)")
    p_login.add_argument("--fingerprint", default="chrome124", help="TLS отпечаток (chrome124, safari17_0, etc)")

    # Stats
    subparsers.add_parser("stats", help="Просмотр статистики (подписчики, просмотры за день)")

    # Rename
    p_rename = subparsers.add_parser("rename", help="Смена никнейма аккаунта")
    p_rename.add_argument("new_nickname", help="Новый никнейм")

    # Upload
    p_upload = subparsers.add_parser("upload", help="Выложить видео или фото")
    p_upload.add_argument("--file", "-f", required=True, help="Путь к файлу на ПК")
    p_upload.add_argument("--title", "-t", default="", help="Кастомное имя / название")
    p_upload.add_argument("--desc", "-d", default="", help="Кастомное описание")

    # Batch Upload
    p_batch = subparsers.add_parser("batch", help="Пакетная публикация видео из папки с авто-паузой и пропуском дубликатов")
    p_batch.add_argument("--folder", "-F", default="videos", help="Папка с видеофайлами (по умолчанию: videos)")
    p_batch.add_argument("--interval", "-i", type=float, default=5.0, help="Пауза между успешными публикациями в минутах (по умолчанию: 5)")
    p_batch.add_argument("--title", "-t", default="", help="Шаблон заголовка (можно использовать {name})")
    p_batch.add_argument("--desc", "-d", default="", help="Кастомное описание видео")

    # Tor Status & Control
    p_tor = subparsers.add_parser("tor", help="Проверка статуса и управление автономным Tor")
    p_tor.add_argument("action", nargs="?", choices=["status", "start", "stop"], default="status", help="Действие: status, start, stop (по умолчанию: status)")
    p_tor.add_argument("--port", "-p", type=int, default=None, help="Порт SOCKS Tor (по умолчанию: 9050)")

    # VLESS Reality Status & Control
    p_vless = subparsers.add_parser("vless", help="Проверка статуса и управление VLESS Reality (Xray)")
    p_vless.add_argument("action", nargs="?", choices=["status", "start", "stop"], default="status", help="Действие: status, start, stop (по умолчанию: status)")
    p_vless.add_argument("--port", "-p", type=int, default=10808, help="Порт SOCKS VLESS (по умолчанию: 10808)")

    # Scan Posts
    p_scan = subparsers.add_parser("scan", help="Сканировать все публикации канала (page=1, 2, 3...)")
    p_scan.add_argument("--max-pages", type=int, default=50, help="Максимум страниц (по умолчанию 50)")

    # Redact All Posts
    p_redact = subparsers.add_parser("redact-all", help="Массовое редактирование заголовка и описания всех прошлых публикаций")
    p_redact.add_argument("--title", "-t", default="", help="Новый заголовок / шаблон (поддерживает {i}, {title})")
    p_redact.add_argument("--desc", "-d", default="", help="Новое описание")
    p_redact.add_argument("--max-pages", type=int, default=50, help="Максимум страниц для сканирования")

    # Popular Videos & Commenting
    p_pop_list = subparsers.add_parser("popular-list", help="Список видео из раздела Популярные")
    p_pop_list.add_argument("--limit", "-l", type=int, default=20, help="Количество видео (по умолчанию: 20)")

    p_pop_comment = subparsers.add_parser("popular-comment", help="Опубликовать комментарий на популярное видео")
    p_pop_comment.add_argument("--fid", required=True, help="ID публикации (FID)")
    p_pop_comment.add_argument("--text", "-t", required=True, help="Текст комментария (поддерживает spintax {вариант1|вариант2})")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    if args.command == "login":
        asyncio.run(cmd_login(args))
    elif args.command == "stats":
        asyncio.run(cmd_stats(args))
    elif args.command == "rename":
        asyncio.run(cmd_rename(args))
    elif args.command == "upload":
        asyncio.run(cmd_upload(args))
    elif args.command == "batch":
        asyncio.run(cmd_batch(args))
    elif args.command == "tor":
        asyncio.run(cmd_tor(args))
    elif args.command == "vless":
        asyncio.run(cmd_vless(args))
    elif args.command == "scan":
        asyncio.run(cmd_scan(args))
    elif args.command == "redact-all":
        asyncio.run(cmd_redact_all(args))
    elif args.command == "popular-list":
        asyncio.run(cmd_popular_list(args))
    elif args.command == "popular-comment":
        asyncio.run(cmd_popular_comment(args))

if __name__ == "__main__":
    main()

