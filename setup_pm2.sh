#!/usr/bin/env bash
# ==============================================================================
#  WVIDS STUDIO PRO / LEGATO PANEL — UBUNTU PM2 INSTALLER & STARTER
# ==============================================================================

set -e

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo -e "${BLUE}======================================================================${NC}"
echo -e "${BLUE}  🚀 WVIDS STUDIO PRO — НАСТРОЙКА И ЗАПУСК PM2 НА UBUNTU${NC}"
echo -e "${BLUE}======================================================================${NC}"

# 1. Check root / sudo privileges
SUDO=""
if [ "$EUID" -ne 0 ]; then
    if command -v sudo >/dev/null 2>&1; then
        SUDO="sudo"
    else
        echo -e "${YELLOW}[!] Предупреждение: Скрипт запущен не от root и sudo не найден.${NC}"
        echo -e "    Убедитесь, что все системные зависимости уже установлены."
    fi
fi

# 2. Update apt and install required system packages (Python, venv, Tor, Curl)
echo -e "\n${GREEN}[1/6] Проверка и установка системных зависимостей (Python3, Tor, curl)...${NC}"
if [ -n "$SUDO" ] || [ "$EUID" -eq 0 ]; then
    $SUDO apt-get update -y
    $SUDO apt-get install -y python3 python3-pip python3-venv tor curl ffmpeg fonts-dejavu-core
else
    if ! command -v python3 >/dev/null 2>&1; then
        echo -e "${RED}[ERROR] Python3 не установлен! Установите python3.${NC}"
        exit 1
    fi
fi

# 3. Ensure Tor service is running
echo -e "\n${GREEN}[2/6] Проверка и запуск системной службы Tor...${NC}"
if command -v systemctl >/dev/null 2>&1; then
    if [ -n "$SUDO" ] || [ "$EUID" -eq 0 ]; then
        $SUDO systemctl enable tor 2>/dev/null || true
        $SUDO systemctl start tor 2>/dev/null || true
    fi
elif command -v service >/dev/null 2>&1; then
    if [ -n "$SUDO" ] || [ "$EUID" -eq 0 ]; then
        $SUDO service tor start 2>/dev/null || true
    fi
fi

# Check if Tor is listening on 9050
if ss -tuln | grep -q ":9050 " || netstat -tuln 2>/dev/null | grep -q ":9050 "; then
    echo -e "${GREEN} ▸ Tor успешно запущен и слушает порт 9050.${NC}"
else
    echo -e "${YELLOW} ▸ Tor пока не открыл порт 9050. Панель запустит автономный Tor при старте.${NC}"
fi

# 4. Check / Install Node.js, npm and PM2
echo -e "\n${GREEN}[3/6] Проверка Node.js, npm и PM2...${NC}"
if ! command -v pm2 >/dev/null 2>&1; then
    echo -e "${YELLOW} ▸ PM2 не найден. Установка Node.js и PM2...${NC}"
    if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then
        if [ -n "$SUDO" ] || [ "$EUID" -eq 0 ]; then
            curl -fsSL https://deb.nodesource.com/setup_20.x | $SUDO -E bash -
            $SUDO apt-get install -y nodejs
        else
            echo -e "${RED}[ERROR] Node.js и PM2 не установлены! Установите их вручную: npm install -g pm2${NC}"
            exit 1
        fi
    fi
    echo -e "${GREEN} ▸ Установка глобального пакета PM2...${NC}"
    if [ -n "$SUDO" ] || [ "$EUID" -eq 0 ]; then
        $SUDO npm install -g pm2
    else
        npm install -g pm2
    fi
fi

PM2_VER=$(pm2 -v)
echo -e "${GREEN} ▸ PM2 доступен (версия: v${PM2_VER})${NC}"

# 5. Setup Python Virtual Environment and install requirements
echo -e "\n${GREEN}[4/6] Настройка виртуального окружения Python (venv)...${NC}"
if [ ! -d "venv" ]; then
    echo -e " ▸ Создание нового venv в папке ./venv..."
    python3 -m venv venv
fi

echo -e " ▸ Установка/обновление зависимостей из requirements.txt..."
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt

# Allow Python to bind to privileged ports (< 1024 like port 80) without root
if [ -n "$SUDO" ] && [ "$EUID" -ne 0 ]; then
    PY_BIN=$(readlink -f ./venv/bin/python3 2>/dev/null || which python3)
    if [ -f "$PY_BIN" ]; then
        $SUDO setcap 'cap_net_bind_service=+ep' "$PY_BIN" 2>/dev/null || true
    fi
fi

# 6. Ensure required runtime directories exist
mkdir -p data uploads videos logs

# 7. Start / Restart application via PM2
echo -e "\n${GREEN}[5/6] Запуск приложения в PM2 через ecosystem.config.js...${NC}"
pm2 delete legato-panel 2>/dev/null || true

# Завершаем старые фоновые процессы Python, если они были запущены вручную
pkill -f run_panel.py 2>/dev/null || true
sleep 1

# Проверка, свободен ли порт 80
if ss -tuln | grep -q ":80 "; then
    echo -e "${YELLOW}[!] Внимание: Порт 80 занят другой службой на сервере (например, Apache или Nginx).${NC}"
    echo -e "    Если панель переключится на другой порт, освободите 80 командой:"
    echo -e "    sudo systemctl stop apache2 2>/dev/null || sudo systemctl stop nginx 2>/dev/null"
fi

pm2 start ecosystem.config.js

# 8. Save PM2 configuration so it survives reboots
echo -e "\n${GREEN}[6/6] Сохранение конфигурации PM2...${NC}"
pm2 save

# Detect Server IP
SERVER_IP=$(curl -s --max-time 3 https://api.ipify.org || hostname -I | awk '{print $1}')
PORT=$(grep -oP '"PORT":\s*"?\K[0-9]+' ecosystem.config.js 2>/dev/null || echo "80")

if [ "$PORT" = "80" ]; then
    URL_EXTERNAL="http://${SERVER_IP}"
    URL_LOCAL="http://127.0.0.1"
else
    URL_EXTERNAL="http://${SERVER_IP}:${PORT}"
    URL_LOCAL="http://127.0.0.1:${PORT}"
fi

echo -e "\n${BLUE}======================================================================${NC}"
echo -e "${GREEN} 🎉 ПАНЕЛЬ УСПЕШНО ЗАПУЩЕНА В PM2 НА ПОРТУ ${PORT}!${NC}"
echo -e "${BLUE}======================================================================${NC}"
echo -e " ▸ Адрес веб-панели (внешний): ${YELLOW}${URL_EXTERNAL}${NC}"
echo -e " ▸ Адрес локальный:            ${YELLOW}${URL_LOCAL}${NC}"
echo -e "\n${BLUE} Полезные команды для управления:${NC}"
echo -e " • Статус процесса:    ${GREEN}pm2 status legato-panel${NC}"
echo -e " • Живые логи:         ${GREEN}pm2 logs legato-panel${NC}"
echo -e " • Перезапуск:         ${GREEN}pm2 restart legato-panel${NC}"
echo -e " • Остановка:          ${GREEN}pm2 stop legato-panel${NC}"
echo -e " • Автозапуск при загрузке сервера: ${YELLOW}pm2 startup${NC} (выполните команду, которую выдаст PM2)"
echo -e "${BLUE}======================================================================${NC}\n"
