#!/usr/bin/env bash
# ==============================================================================
#  WVIDS STUDIO PRO / LEGATO PANEL — QUICK START SCRIPT FOR UBUNTU
# ==============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Ensure runtime directories exist
mkdir -p data uploads videos logs

# Check if PM2 is available
if command -v pm2 >/dev/null 2>&1; then
    echo "[*] Запуск/перезапуск через PM2..."
    if pm2 describe legato-panel >/dev/null 2>&1; then
        pm2 restart legato-panel
    else
        pm2 start ecosystem.config.js
    fi
    pm2 status legato-panel
    echo ""
    echo "[+] Панель активна в фоновом режиме!"
    echo "    Смотреть логи: pm2 logs legato-panel"
    exit 0
fi

# Fallback: if PM2 not installed, run via virtualenv or python3 directly
echo "[!] PM2 не найден. Запуск напрямую через Python..."
if [ -f "./venv/bin/python3" ]; then
    ./venv/bin/python3 run_panel.py
elif command -v python3 >/dev/null 2>&1; then
    python3 run_panel.py
else
    echo "[ERROR] Python 3 не найден!"
    exit 1
fi
