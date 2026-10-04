@echo off
chcp 65001 >nul
rem 口蘑 Mod 工坊 —— Windows 一次性环境准备
cd /d "%~dp0"
setlocal

set PY=
where py >nul 2>nul && set PY=py -3
if "%PY%"=="" ( where python >nul 2>nul && set PY=python )
if "%PY%"=="" (
    echo.
    echo   没找到 Python。请先装 Python 3.10 或更新版本：
    echo     https://www.python.org/downloads/windows/
    echo   安装时记得勾选 "Add python.exe to PATH"。
    echo.
    pause
    exit /b 1
)

echo   用解释器：%PY%
%PY% -c "import sys;print('  版本：',sys.version)"

if not exist ".venv\Scripts\python.exe" (
    echo   [1/2] 创建 .venv
    %PY% -m venv .venv || ( echo   创建失败 & pause & exit /b 1 )
) else (
    echo   [1/2] .venv 已存在，跳过
)

echo   [2/2] 安装依赖（第一次会下载约 100MB）
".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
".venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt || ( echo   装依赖失败 & pause & exit /b 1 )

echo.
echo   完成。可以双击：
echo      启动.bat        桌面图形界面
echo      启动网页版.bat   网页界面（手机连同一个 Wi-Fi 也能用）
echo.
".venv\Scripts\python.exe" tools\cli.py doctor
pause
