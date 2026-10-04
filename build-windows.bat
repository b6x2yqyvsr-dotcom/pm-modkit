@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo.
echo   口蘑 Mod 工坊 · Windows 打包
echo   ========================================
echo.

where python >nul 2>&1
if errorlevel 1 (
  echo   [X] 没找到 python。先装 Python 3.10+ 并勾上 "Add to PATH"
  echo       https://www.python.org/downloads/
  pause & exit /b 1
)

echo   [1/4] 建虚拟环境
if not exist ".venv\Scripts\python.exe" python -m venv .venv
if errorlevel 1 ( echo   [X] 建虚拟环境失败 & pause & exit /b 1 )

echo   [2/4] 装依赖（第一次要几分钟）
".venv\Scripts\python.exe" -m pip install --upgrade pip -q
".venv\Scripts\python.exe" -m pip install -r requirements.txt -q
if errorlevel 1 ( echo   [X] 依赖装失败 & pause & exit /b 1 )

echo   [3/4] 装 PyInstaller
".venv\Scripts\python.exe" -m pip install pyinstaller -q

echo   [4/4] 打包
".venv\Scripts\python.exe" build\build_app.py --keep-build
if errorlevel 1 ( echo   [X] 打包失败 & pause & exit /b 1 )

echo.
echo   打包完成！产物在：
echo       dist\口蘑Mod工坊\口蘑Mod工坊.exe
echo.
echo   整个 dist\口蘑Mod工坊 文件夹一起拷走就能用（免安装）。
echo.
pause
