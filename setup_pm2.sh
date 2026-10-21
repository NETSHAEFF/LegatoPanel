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
        echo -e "    Убедитесь, что все системные пакеты уже установлены."
    fi
fi

# 2. Update apt and install required system packages
echo -e "\n${GREEN}[1/7] Установка системных пакетов (Python3, Tor, FFmpeg, Шрифты, Curl)...${NC}"
if [ -n "$SUDO" ] || [ "$EUID" -eq 0 ]; then
    $SUDO apt-get update -y
    $SUDO apt-get install -y \
        python3 python3-pip python3-venv \
        tor tor-geoipdb \
        curl ffmpeg \
        fonts-dejavu-core fonts-liberation \
        build-essential 2>/dev/null || true

    # Attempt to install obfs4proxy or lyrebird for Tor bridges
    $SUDO apt-get install -y obfs4proxy 2>/dev/null || $SUDO apt-get install -y lyrebird 2>/dev/null || true
else
    if ! command -v python3 >/dev/null 2>&1; then
        echo -e "${RED}[ERROR] Python3 не установлен! Установите python3.${NC}"
        exit 1
    fi
    if ! command -v ffmpeg >/dev/null 2>&1; then
        echo -e "${YELLOW}[!] FFmpeg не обнаружен в PATH. Видеообработка может быть ограничена.${NC}"
    fi
fi

# 3. Check / Install Xray-core for VLESS Reality (optional but recommended)
echo -e "\n${GREEN}[2/7] Проверка Xray-core для VLESS Reality...${NC}"
if ! command -v xray >/dev/null 2>&1 && [ ! -f "/usr/local/bin/xray" ] && [ ! -f "./bin/xray" ]; then
    echo -e " ▸ Установка официального Xray-core (Linux)..."
    if [ -n "$SUDO" ] || [ "$EUID" -eq 0 ]; then
        curl -fsSL https://github.com/XTLS/Xray-install/raw/main/install-release.sh | $SUDO bash -s -- install 2>/dev/null || true
    fi
fi
if command -v xray >/dev/null 2>&1 || [ -f "/usr/local/bin/xray" ] || [ -f "./bin/xray" ]; then
    echo -e "${GREEN} ▸ Xray-core доступен для работы с VLESS Reality!${NC}"
else
    echo -e "${YELLOW} ▸ Xray не установлен. Если вам нужен VLESS, установите Xray вручную.${NC}"
fi

# 4. Ensure Tor service is running
echo -e "\n${GREEN}[3/7] Проверка и запуск системной службы Tor...${NC}"
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

if ss -tuln | grep -q ":9050 " || netstat -tuln 2>/dev/null | grep -q ":9050 "; then
    echo -e "${GREEN} ▸ Tor успешно запущен и слушает порт 9050.${NC}"
else
    echo -e "${YELLOW} ▸ Порт 9050 пока свободен. Панель запустит автономный Tor при старте.${NC}"
fi

# 5. Check / Install Node.js, npm and PM2
echo -e "\n${GREEN}[4/7] Проверка Node.js, npm и PM2...${NC}"
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

# 6. Setup Python Virtual Environment and install requirements
echo -e "\n${GREEN}[5/7] Настройка виртуального окружения Python (venv)...${NC}"
if [ ! -d "venv" ]; then
    echo -e " ▸ Создание нового venv в папке ./venv..."
    python3 -m venv venv
fi

echo -e " ▸ Установка/обновление зависимостей из requirements.txt..."
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt

# Ensure runtime directories exist
mkdir -p data uploads videos logs bin backend/fonts
chmod +x *.sh 2>/dev/null || true

# Determine port from ecosystem.config.js or fallback to 80
PORT=$(grep -oP '"PORT":\s*"?\K[0-9]+' ecosystem.config.js 2>/dev/null || echo "80")

# Allow Python to bind to privileged ports (< 1024 like port 80) without root
if [ "$PORT" -lt 1024 ] && [ -n "$SUDO" ] && [ "$EUID" -ne 0 ]; then
    PY_BIN=$(readlink -f ./venv/bin/python3 2>/dev/null || which python3)
    if [ -f "$PY_BIN" ]; then
        $SUDO setcap 'cap_net_bind_service=+ep' "$PY_BIN" 2>/dev/null || true
    fi
fi

# Firewall check (UFW)
if command -v ufw >/dev/null 2>&1; then
    if ufw status 2>/dev/null | grep -q "Status: active"; then
        echo -e "${YELLOW} ▸ UFW активен. Открытие входящего порта ${PORT}/tcp...${NC}"
        if [ -n "$SUDO" ] || [ "$EUID" -eq 0 ]; then
            $SUDO ufw allow "${PORT}/tcp" 2>/dev/null || true
        fi
    fi
fi

# 7. Start / Restart application via PM2
echo -e "\n${GREEN}[6/7] Запуск приложения в PM2 через ecosystem.config.js...${NC}"
pm2 delete legato-panel 2>/dev/null || true

# Kill any dangling manual python processes
pkill -f run_panel.py 2>/dev/null || true
sleep 1

# Check if port 80 is occupied by apache or nginx
if [ "$PORT" = "80" ] && (ss -tuln | grep -q ":80 " || netstat -tuln 2>/dev/null | grep -q ":80 "); then
    echo -e "${YELLOW}[!] Внимание: Порт 80 занят другой службой на сервере (например, Apache или Nginx).${NC}"
    echo -e "    Если хотите использовать порт 80, остановите конфликтную службу:"
    echo -e "    sudo systemctl stop apache2 2>/dev/null || sudo systemctl stop nginx 2>/dev/null"
fi

pm2 start ecosystem.config.js

# 8. Save PM2 configuration so it survives reboots
echo -e "\n${GREEN}[7/7] Сохранение конфигурации автозапуска PM2...${NC}"
pm2 save

# Detect Server IP
SERVER_IP=$(curl -s --max-time 3 https://api.ipify.org || hostname -I | awk '{print $1}')

if [ "$PORT" = "80" ]; then
    URL_EXTERNAL="http://${SERVER_IP}"
    URL_LOCAL="http://127.0.0.1"
else
    URL_EXTERNAL="http://${SERVER_IP}:${PORT}"
    URL_LOCAL="http://127.0.0.1:${PORT}"
fi

echo -e "\n${BLUE}======================================================================${NC}"
echo -e "${GREEN} 🎉 ПАНЕЛЬ УСПЕШНО НАСТРОЕНА И ЗАПУЩЕНА В PM2 НА ПОРТУ ${PORT}!${NC}"
echo -e "${BLUE}======================================================================${NC}"
echo -e " ▸ Адрес веб-панели (внешний): ${YELLOW}${URL_EXTERNAL}${NC}"
echo -e " ▸ Адрес локальный:            ${YELLOW}${URL_LOCAL}${NC}"
echo -e "\n${BLUE} Полезные команды для управления на Ubuntu:${NC}"
echo -e " • Статус процесса:    ${GREEN}pm2 status legato-panel${NC}"
echo -e " • Живые логи:         ${GREEN}pm2 logs legato-panel${NC}"
echo -e " • Перезапуск:         ${GREEN}pm2 restart legato-panel${NC}"
echo -e " • Остановка:          ${GREEN}pm2 stop legato-panel${NC}"
echo -e " • Запуск напрямую:    ${GREEN}./start.sh --direct${NC}"
echo -e " • Консольный CLI:     ${GREEN}./start.sh --cli --help${NC}"
echo -e " • Автозапуск при перезагрузке сервера: ${YELLOW}pm2 startup${NC}"
echo -e "${BLUE}======================================================================${NC}\n"
