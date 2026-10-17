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

def is_port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex((host, port)) == 0

def find_available_port(start_port: int, host: str = "127.0.0.1") -> int:
    port = start_port
    while is_port_in_use(port, host):
        port += 1
    return port

def main():
    selected_port = find_available_port(PORT, HOST)
    panel_url = f"http://{HOST}:{selected_port}"

    from backend.tor import auto_detect_tor_port, check_tor_connection
    detected_tor = auto_detect_tor_port()
    tor_info_str = "НЕ ОБНАРУЖЕН на 9150/9050 (Запустите Tor Browser)"
    if detected_tor:
        st = check_tor_connection(port=detected_tor, timeout=4)
        if st.get("connected"):
            tor_info_str = f"ПОДКЛЮЧЕНО (Exit IP: {st.get('ip')}, порт: {detected_tor}, 0 DNS leak)"
        else:
            tor_info_str = f"Порт {detected_tor} открыт, ожидание цепи Tor..."

    print("=" * 65)
    print(" 🚀 WVIDS STUDIO PRO — ANTI-DETECT & VIDEO AUTOMATION PANEL")
    print("=" * 65)
    print(f" ▸ Панель управления доступна по адресу: {panel_url}")
    print(f" ▸ 🧅 Tor Network изоляция: {tor_info_str}")
    print(" ▸ Глубокая анонимизация: curl_cffi (JA3/JA4 TLS + HTTP/2)")
    print(" ▸ Авторизация: по Cookies (auth_user_id, auth_sess, auth_p)")
    print(" ▸ Выгрузка: Видео и фото с ПК с кастомным именем и описанием")
    print(" ▸ Смена никнейма и статистика просмотров/подписчиков активны")
    print("=" * 65)
    print(" Нажмите Ctrl+C в консоли для остановки сервера.\n")

    # Open browser automatically after a short delay
    try:
        webbrowser.open(panel_url)
    except Exception:
        pass

    # Run Uvicorn server
    uvicorn.run(
        "backend.app:app",
        host=HOST,
        port=selected_port,
        reload=False,
        log_level="info"
    )

if __name__ == "__main__":
    main()
