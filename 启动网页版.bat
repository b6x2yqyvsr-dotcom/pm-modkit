@echo off
chcp 65001 >nul
rem 口蘑 Mod 工坊 —— 网页版（手机也能连）
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo   还没装运行环境。请先双击运行  setup.bat
    pause
    exit /b 1
)

echo   启动网页版服务，浏览器会自动打开…
start "" http://127.0.0.1:8765
".venv\Scripts\python.exe" web\server.py --port 8765 %*
pause
