@echo off
title Wvids Studio Pro - Web Panel
cd /d "%~dp0"

echo =====================================================================
echo   Starting Wvids Studio Pro Panel...
echo =====================================================================

python --version >nul 2>&1
if %errorlevel% == 0 (
    python run_panel.py
    goto done
)

py --version >nul 2>&1
if %errorlevel% == 0 (
    py run_panel.py
    goto done
)

echo.
echo [ERROR] Python not found in PATH!
echo Please install Python 3.10+ from https://www.python.org/
echo Ensure you check 'Add Python to PATH' during installation.
echo.
pause
exit /b 1

:done
if %errorlevel% neq 0 (
    echo.
    echo [!] Server stopped.
    pause
)
