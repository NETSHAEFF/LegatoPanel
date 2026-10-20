import sys
import os
import webbrowser
import socket
import uvicorn
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Add project root to sys.path
BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from backend.config import HOST, PORT

def is_port_in_use(port: int, host: str = "0.0.0.0") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((host, port))
            return False
        except OSError:
            return True

def find_available_port(start_port: int, host: str = "0.0.0.0") -> int:
    port = start_port
    while is_port_in_use(port, host):
        port += 1
    return port

def main():
    selected_port = find_available_port(PORT, HOST)
    display_host = "localhost" if HOST in ("0.0.0.0", "127.0.0.1") else HOST
    panel_url = f"http://{display_host}" if selected_port == 80 else f"http://{display_host}:{selected_port}"

    from backend.tor import auto_detect_tor_port, check_tor_connection, tor_manager
    detected_tor = auto_detect_tor_port()
    if not detected_tor:
        print("[*] 🧅 Запуск автономного фонового Tor (без запуска браузера)...")
        tor_manager.ensure_running(port=9050)
        detected_tor = auto_detect_tor_port()

    tor_info_str = "НЕ ОБНАРУЖЕН на 9050/9150"
    if detected_tor:
        st = check_tor_connection(port=detected_tor, timeout=6)
        if st.get("connected"):
            mode_str = "Автономный фоновый" if detected_tor == 9050 else "Tor Browser"
            tor_info_str = f"ПОДКЛЮЧЕНО [{mode_str}] (Exit IP: {st.get('ip')}, порт: {detected_tor}, 0 DNS leak)"
        else:
            tor_info_str = f"Порт {detected_tor} открыт, построение цепи Tor..."

    print("=" * 65)
    print(" 🚀 WVIDS STUDIO PRO — ANTI-DETECT & VIDEO AUTOMATION PANEL")
    print("=" * 65)
    if selected_port != PORT:
        print(f" [!] ВНИМАНИЕ: Порт {PORT} уже ЗАНЯТ другим процессом!")
        print(f"     Сервер автоматически переключился на свободный порт: {selected_port}")
        print(f"     Чтобы освободить порт {PORT} на сервере Ubuntu, выполните:")
        print(f"     sudo fuser -k {PORT}/tcp  (или sudo lsof -i :{PORT})")
        print("=" * 65)
    print(f" ▸ Панель управления доступна по адресу: {panel_url}")
    if HOST == "0.0.0.0":
        ext_url = "http://<IP_СЕРВЕРА>" if selected_port == 80 else f"http://<IP_СЕРВЕРА>:{selected_port}"
        print(f" ▸ Внешний доступ: {ext_url}")
    print(f" ▸ 🧅 Tor Network изоляция: {tor_info_str}")
    print(" ▸ Глубокая анонимизация: curl_cffi (JA3/JA4 TLS + HTTP/2)")
    print(" ▸ Авторизация: по Cookies (auth_user_id, auth_sess, auth_p)")
    print(" ▸ Выгрузка: Видео и фото с ПК с кастомным именем и описанием")
    print(" ▸ Смена никнейма и статистика просмотров/подписчиков активны")
    print("=" * 65)
    print(" Нажмите Ctrl+C в консоли для остановки сервера.\n")

    # Open browser automatically if GUI desktop is available
    if sys.platform == "win32" or os.environ.get("DISPLAY"):
        try:
            webbrowser.open(panel_url)
        except Exception:
            pass

    # Run Uvicorn server
    try:
        uvicorn.run(
            "backend.app:app",
            host=HOST,
            port=selected_port,
            reload=False,
            log_level="info"
        )
    finally:
        print("\n[*] Завершение работы Wvids Studio Pro и фоновых служб Tor...")
        tor_manager.stop()

if __name__ == "__main__":
    main()
