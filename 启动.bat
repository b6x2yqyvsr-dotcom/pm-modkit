@echo off
chcp 65001 >nul
rem 口蘑 Mod 工坊 —— Windows 桌面版启动器
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo   还没装运行环境。请先双击运行  setup.bat
    echo.
    pause
    exit /b 1
)

echo   启动图形界面…
".venv\Scripts\python.exe" app\main.py %*
if errorlevel 1 pause
