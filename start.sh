#!/usr/bin/env bash
# ==============================================================================
#  WVIDS STUDIO PRO / LEGATO PANEL — QUICK START SCRIPT FOR UBUNTU / LINUX
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Ensure all runtime directories exist
mkdir -p data uploads videos logs bin backend/fonts

# Fix permissions for shell scripts
chmod +x *.sh 2>/dev/null || true

# CLI mode shortcut: ./start.sh --cli <command>
if [ "$1" = "--cli" ] || [ "$1" = "-c" ]; then
    shift
    if [ -f "./venv/bin/python3" ]; then
        exec ./venv/bin/python3 cli.py "$@"
    elif command -v python3 >/dev/null 2>&1; then
        exec python3 cli.py "$@"
    else
        echo "[ERROR] Python 3 не найден!"
        exit 1
    fi
fi

# Ensure Python Virtual Environment exists
if [ ! -d "venv" ]; then
    echo "[!] Виртуальное окружение ./venv не обнаружено."
    echo "[*] Рекомендуется выполнить полную настройку системы: bash setup_pm2.sh"
    echo "[*] Автоматическое создание локального venv..."
    if command -v python3 >/dev/null 2>&1; then
        python3 -m venv venv
        ./venv/bin/pip install --upgrade pip
        ./venv/bin/pip install -r requirements.txt
    else
        echo "[ERROR] Python3 не установлен в системе! Выполните: sudo apt install -y python3 python3-venv"
        exit 1
    fi
fi

# Direct console execution mode: ./start.sh --direct
if [ "$1" = "--direct" ] || [ "$1" = "-d" ]; then
    echo "[*] Запуск панели напрямую в консоли (Foreground mode)..."
    exec ./venv/bin/python3 run_panel.py
fi

# Default: Start via PM2 if installed
if command -v pm2 >/dev/null 2>&1; then
    echo "[*] Запуск/перезапуск панели через PM2..."
    if pm2 describe legato-panel >/dev/null 2>&1; then
        pm2 restart legato-panel
    else
        pm2 start ecosystem.config.js
    fi
    pm2 status legato-panel
    echo ""
    echo "[+] Панель активна в фоновом режиме через PM2!"
    echo "    Смотреть живые логи: pm2 logs legato-panel"
    echo "    Остановить:          pm2 stop legato-panel"
    echo "    Перезапустить:       pm2 restart legato-panel"
    exit 0
fi

# Fallback: if PM2 not installed, run via virtualenv python
echo "[!] PM2 не найден. Запуск напрямую через Python (фоновый запуск: bash setup_pm2.sh)..."
exec ./venv/bin/python3 run_panel.py
