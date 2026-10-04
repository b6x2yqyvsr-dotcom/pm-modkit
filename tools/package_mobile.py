#!/usr/bin/env python3
"""打手机端的包。

手机端**不是原生 App** —— 这个工具的核心是 Python + UnityPy，解包要
``lz4`` / ``brotli`` / ``texture2ddecoder`` / ``astc_encoder`` / ``etcpak`` /
``fmod_toolkit`` 这些原生扩展，它们没有 Android / iOS 轮子。所以手机端走
**网页版**：后端 Python 跑在 Termux 里（Android），或者跑在电脑上、手机浏览器
连过来（iOS）。

这里打的就是那两个 zip：

* ``口蘑Mod工坊-Android.zip`` —— 源码 + Termux 一键装/启动脚本
* ``口蘑Mod工坊-iOS.zip``     —— 源码 + 在电脑上起服务的脚本 + iPhone 连接说明

用法::

    python3 tools/package_mobile.py -o dist
"""

def _force_utf8() -> None:
    """Windows 控制台默认不是 UTF-8，``print`` 中文会直接抛 UnicodeEncodeError。
    所有入口脚本开头都调一下这个。"""
    import sys
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            pass


_force_utf8()

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: 两个包都需要的源码（网页版 + 解析核心 + 命令行）
COMMON = [
    "modkit", "web", "tools", "docs",
    "requirements-web.txt", "README.md",
]

ANDROID_README = """口蘑 Mod 工坊 · Android（Termux）
==================================

这不是原生 App —— 手机端跑的是**网页版**，后端 Python 装在 Termux 里，
界面用手机浏览器打开（可以「添加到主屏幕」，装完跟 App 一样）。

为什么不做原生 APK
------------------
这个工具的核心是 Python + UnityPy，解包要用 lz4 / brotli /
texture2ddecoder / astc_encoder / etcpak / fmod_toolkit 这些**原生扩展**。
它们没有 Android 的预编译轮子，python-for-android 也没有现成 recipe。
硬做出来的 APK 装不上或者一解包就崩，不如老实走网页版。

怎么装
------
1. 装 Termux
   从 **F-Droid** 装：https://f-droid.org/packages/com.termux/
   （别用 Google Play 那个，版本太老，装不上新包）

2. 把本压缩包传到手机上，解压到 Termux 能访问的地方
   最省事的做法是用手机浏览器下载到 Download 目录，然后在 Termux 里：

       termux-setup-storage        # 授权访问存储
       cd ~ && cp /sdcard/Download/口蘑Mod工坊-Android.zip .
       pkg install -y unzip && unzip -q 口蘑Mod工坊-Android.zip
       cd pm-modkit

   （或者你有电脑，直接用 `adb push` / 数据线拷进去也行）

3. 一键安装

       bash android/termux-install.sh

4. 启动

       bash android/termux-run.sh

   它会打印地址，手机浏览器打开 http://127.0.0.1:8765 就是完整界面。

装成 App（推荐）
----------------
Chrome 里点右上角菜单 →「添加到主屏幕」。
之后从桌面图标进去是全屏的，没有地址栏，跟 App 一样。

能干什么
--------
解包、拖拽添加、改数据表（中文字段名）、换贴图、音频导出、
新增条目、完全新增角色、角色图鉴、数据表体检、导出 .pmmod / UnityCache。

**重打包 APK 在手机上做不了**（需要 JDK + Android build-tools）。
导出的时候会自动退到 UnityCache / .pmmod 路线 —— 那两条路本来就不需要重打包。
"""

IOS_README = """口蘑 Mod 工坊 · iOS / iPadOS
=============================

iOS 上**跑不了这个后端**（原生扩展没有轮子，App Store 也不让装 Python 运行时）。
正确用法是：

    在电脑上跑服务  →  iPhone / iPad 用 Safari 连过去

**算力在电脑上，手机只负责显示和操作** —— 手机上拖进 APK、改数据、导出，
实际都是电脑在算，所以手机不会卡。

三步
----
1. 在电脑上起服务

   macOS：双击 启动服务.command
   Windows：双击 启动服务.bat
   或者命令行：

       python3 web/server.py --port 8765

   服务默认监听所有网卡，手机能连。

2. 查电脑的局域网 IP

   macOS：  ipconfig getifaddr en0
   Windows：ipconfig     （看「IPv4 地址」，一般是 192.168.x.x）

3. 在 iPhone 的 Safari 里打开

       http://<电脑的IP>:8765

   例如 http://192.168.2.14:8765

装成 App（推荐）
----------------
Safari 里点底部「**分享**」→「**添加到主屏幕**」→ 添加。
桌面会出现「口蘑Mod工坊」图标，点开是全屏的，没有地址栏，跟 App 一样。

界面外壳会被缓存，第二次打开很快。

安全提醒
--------
这一步是把服务开给**整个局域网**。只在自己家的 Wi-Fi 上这么用，
公共网络（咖啡厅、酒店、公司）别开。
"""

SERVER_COMMAND = """#!/bin/bash
# 口蘑 Mod 工坊 · 在 macOS 上起服务（给 iPhone / iPad 连）
cd "$(dirname "$0")" || exit 1

PORT="${PM_MODKIT_PORT:-8765}"

# 一定要用自己的 venv：macOS / 新版 Linux 的系统 Python 默认**拒绝** pip 安装
# （PEP 668 externally-managed-environment），直接 pip install 会失败。
if [ ! -x ".venv/bin/python" ]; then
  echo
  echo "  第一次要先建环境并装依赖（大概一两分钟）…"
  echo
  python3 -m venv .venv || { echo "  建虚拟环境失败，先装 Python 3.10+"; exit 1; }
  .venv/bin/python -m pip install -q --upgrade pip
  .venv/bin/python -m pip install -q -r requirements-web.txt || {
    echo "  装依赖失败。手动试试： .venv/bin/python -m pip install -r requirements-web.txt"
    exit 1; }
fi
PY=.venv/bin/python

IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null)
echo
echo "  口蘑 Mod 工坊 · 服务已启动"
echo "  ────────────────────────────────────────"
echo "  在这台电脑上用：      http://127.0.0.1:$PORT"
[ -n "${IP:-}" ] && echo "  在 iPhone 上用 Safari 打开：  http://$IP:$PORT"
echo
echo "  然后 Safari 底部「分享」→「添加到主屏幕」就能装成 App"
echo "  按 Ctrl+C 停止"
echo
exec "$PY" web/server.py --port "$PORT"
"""

SERVER_BAT = r"""@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PORT=8765
if "%PM_MODKIT_PORT%"=="" goto :go
set PORT=%PM_MODKIT_PORT%
:go
where python >nul 2>&1 || (echo   没找到 python，先装 Python 3.10+ & pause & exit /b 1)
if not exist ".venv\Scripts\python.exe" (
  echo   第一次要先建环境并装依赖（大概一两分钟）…
  python -m venv .venv || (echo   建虚拟环境失败 & pause & exit /b 1)
  ".venv\Scripts\python.exe" -m pip install -q --upgrade pip
  ".venv\Scripts\python.exe" -m pip install -q -r requirements-web.txt || (
    echo   装依赖失败 & pause & exit /b 1)
)
echo.
echo   口蘑 Mod 工坊 · 服务已启动
echo   ----------------------------------------
echo   在这台电脑上用：      http://127.0.0.1:%PORT%
echo.
echo   查本机 IP：下面能看到 IPv4 地址
ipconfig | findstr /i "IPv4"
echo.
echo   在 iPhone 的 Safari 里打开  http://^<上面的IP^>:%PORT%
echo   然后「分享」-^>「添加到主屏幕」就能装成 App
echo.
".venv\Scripts\python.exe" web/server.py --port %PORT%
pause
"""


def _copy_tree(dst: Path, extras: dict[str, str] | None = None) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    for rel in COMMON:
        src = ROOT / rel
        if not src.exists():
            continue
        target = dst / rel
        if src.is_dir():
            shutil.copytree(src, target, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)
    (dst / "android").mkdir(exist_ok=True)
    for f in ("termux-install.sh", "termux-run.sh"):
        s = ROOT / "android" / f
        if s.exists():
            shutil.copy2(s, dst / "android" / f)
    for name, text in (extras or {}).items():
        p = dst / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        if name.endswith((".sh", ".command")):
            p.chmod(0o755)


def _zip(src: Path, out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in sorted(src.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(src.parent).as_posix()
            zi = zipfile.ZipInfo(rel, date_time=(2026, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = (0o755 if rel.endswith((".sh", ".command", ".bat")) else 0o644) << 16
            # 显式声明文件名是 UTF-8（bit 11）。不设的话 macOS 的 unzip
            # 会按 CP437 解，中文文件名全是乱码 —— 踩过一次。
            zi.flag_bits |= 0x800
            z.writestr(zi, p.read_bytes())
    return out


def build(out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    made: list[Path] = []
    with tempfile.TemporaryDirectory() as tmp:
        tmpd = Path(tmp)

        a = tmpd / "pm-modkit"
        _copy_tree(a, {"安卓安装说明.md": ANDROID_README})
        made.append(_zip(a, out_dir / "口蘑Mod工坊-Android.zip"))

        i = tmpd / "pm-modkit-ios"
        _copy_tree(i, {
            "iPhone安装说明.md": IOS_README,
            "启动服务.command": SERVER_COMMAND,
            "启动服务.bat": SERVER_BAT,
        })
        made.append(_zip(i, out_dir / "口蘑Mod工坊-iOS.zip"))

    return made


def main() -> int:
    ap = argparse.ArgumentParser(description="打手机端（Android / iOS）的包")
    ap.add_argument("-o", "--out", default="dist", help="输出目录")
    a = ap.parse_args()
    for p in build(Path(a.out)):
        print(f"  {p}  ({p.stat().st_size / 1048576:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
