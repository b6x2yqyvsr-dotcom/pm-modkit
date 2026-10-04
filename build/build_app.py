#!/usr/bin/env python3
"""把「口蘑 Mod 工坊」打包成免安装的可执行程序。

    python3 build/build_app.py            # 打成目录（推荐，启动快）
    python3 build/build_app.py --onefile  # 打成单个文件（启动慢一些）

产物在 ``dist/`` 下：

============================  ==========================================
平台                          产物
============================  ==========================================
Windows                       ``dist/口蘑Mod工坊/口蘑Mod工坊.exe``
Linux                         ``dist/口蘑Mod工坊/口蘑Mod工坊``
macOS                         ``dist/口蘑Mod工坊.app``
============================  ==========================================

**注意 PyInstaller 不能交叉编译** —— 要给 Windows 打包就必须在 Windows 上跑，
Linux 同理。三个平台的自动化打包见 ``.github/workflows/build.yml``。

懒得打包也行：``setup.sh`` / ``setup.bat`` 那种「建 venv + 启动脚本」的方式
同样能跑，而且更透明。
"""

from __future__ import annotations

def _force_utf8() -> None:
    """Windows 控制台默认 cp1252/cp936，print 中文会 UnicodeEncodeError。"""
    import sys
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            pass


_force_utf8()


import argparse
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

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAME = "口蘑Mod工坊"


def have_pyinstaller() -> bool:
    try:
        import PyInstaller  # noqa: F401
        return True
    except ImportError:
        return False


#: 这些包带**原生扩展**（.so/.pyd/.dylib）或**数据文件**（.tpk/.dylib），
#: PyInstaller 默认分析不到，必须显式收集。漏了会在运行时才炸，报错还很难懂：
#:   UnityPy/resources/lzma.tpk          → 解 LZMA 包时报 FileNotFoundError
#:   texture2ddecoder/_texture2ddecoder   → 读贴图时报 PyInstallerImportError
#:   fmod_toolkit/libfmod/*/libfmod.dylib → 音频相关
NATIVE_PACKAGES = [
    "UnityPy",
    "tpk_ar",
    "fmod_toolkit",
    "astc_encoder",
    "etcpak",
    "texture2ddecoder",
    # 下面这些是传递依赖，同样带数据文件，漏了照样在运行时炸：
    #   archspec 用 JSON 决定该加载哪个 SIMD 变体（neon / none）
    "archspec",
    "lz4",
    "brotli",
    "attrs",
    "fsspec",
]


#: 这些包会用 ``Path(__file__).parent / ".." / "data"`` 这种方式找数据文件。
#: 打包后 .py 都在归档里，磁盘上**没有** ``<包>/<子模块>/`` 这个目录，
#: 于是 ``子模块/../data`` 解析失败（内核要目录真实存在才能解析 ``..``），
#: 报一个看起来莫名其妙的 FileNotFoundError。
#: 解法：把这些包的**源码目录也当数据拷一份**，让目录真实存在。
#: archspec 就是这么挂的 —— ``archspec/cpu/schema.py`` 去找
#: ``archspec/cpu/../json/cpu/*.json``。
MIRROR_PACKAGES = ["archspec"]


def collect_flags() -> list[str]:
    """给 PyInstaller 的原生扩展 / 数据文件收集开关。"""
    args: list[str] = []
    for pkg in NATIVE_PACKAGES:
        args += ["--collect-binaries", pkg, "--collect-data", pkg]
    return args


def collect_datas() -> list[tuple[str, str]]:
    """要一起塞进包里的非代码文件。

    ``imgui_bundle/assets`` 是 Hello ImGui 的主题/图标/设置，漏了启动就崩。
    注意不要用 ``--collect-data imgui_bundle`` —— 那会把几十 MB 的演示资源
    也带上，所以这里只挑 ``assets``。另外把网页版前端、文档、示例也带上。
    """
    import imgui_bundle

    datas: list[tuple[str, str]] = []
    d = Path(imgui_bundle.__file__).parent / "assets"
    if d.is_dir():
        datas.append((str(d), "imgui_bundle/assets"))
    for sub in ("web", "docs", "examples"):
        d = ROOT / sub
        if d.is_dir():
            datas.append((str(d), sub))
    return datas


def mirror_packages() -> list[tuple[str, str]]:
    """把整个包目录当数据拷一份，让 ``<包>/<子模块>/`` 在磁盘上真实存在。"""
    import importlib

    out: list[tuple[str, str]] = []
    for pkg_name in MIRROR_PACKAGES:
        try:
            mod = importlib.import_module(pkg_name)
        except ImportError:
            continue
        pkg = Path(mod.__file__).parent  # type: ignore[arg-type]
        if pkg.is_dir():
            out.append((str(pkg), pkg_name))
    return out


def icon_path() -> Path | None:
    """按平台挑图标（build/icon/ 下的那几个）。"""
    ic = ROOT / "build" / "icon"
    if sys.platform == "darwin":
        return ic / "icon.icns" if (ic / "icon.icns").is_file() else None
    if os.name == "nt":
        return ic / "icon.ico" if (ic / "icon.ico").is_file() else None
    return ic / "icon-256.png" if (ic / "icon-256.png").is_file() else None


def build(onefile: bool, console: bool, keep_build: bool) -> int:
    if not have_pyinstaller():
        print("没装 PyInstaller。先执行：")
        print(f"    {sys.executable} -m pip install pyinstaller")
        return 2

    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--name", NAME,
        "--paths", str(ROOT),
        "--hidden-import", "UnityPy",
        "--hidden-import", "PIL._tkinter_finder",
        "--collect-submodules", "UnityPy",
        "--collect-submodules", "imgui_bundle",
        # 这些用不上，排掉能小一大截
        "--exclude-module", "matplotlib",
        "--exclude-module", "cv2",
        "--exclude-module", "opencv-python",
        "--exclude-module", "tkinter",
        "--exclude-module", "pytest",
        "--exclude-module", "IPython",
        "--exclude-module", "PyQt5",
        "--exclude-module", "PySide2",
    ]
    args += collect_flags()
    sep = ";" if os.name == "nt" else ":"
    for src, dst in collect_datas():
        args += ["--add-data", f"{src}{sep}{dst}"]
    for src, dst in mirror_packages():
        args += ["--add-data", f"{src}{sep}{dst}"]

    icon = icon_path()
    if icon:
        args += ["--icon", str(icon)]

    if onefile:
        args.append("--onefile")
    if console:
        args.append("--console")
    else:
        args.append("--windowed")

    if sys.platform == "darwin":
        args += ["--osx-bundle-identifier", "com.pmmodkit.app"]

    args.append(str(Path(__file__).resolve().parent / "entry.py"))

    print("运行：\n  " + " \\\n  ".join(args) + "\n")
    r = subprocess.run(args, cwd=ROOT)
    if r.returncode != 0:
        return r.returncode

    if not keep_build:
        shutil.rmtree(ROOT / "build" / NAME, ignore_errors=True)

    out = ROOT / "dist"
    print(f"\n产物在：{out}")
    for p in sorted(out.rglob("*")):
        if p.is_file() and p.stat().st_size > 1_000_000:
            print(f"  {p.relative_to(ROOT)}   {p.stat().st_size / 1e6:.1f} MB")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--onefile", action="store_true", help="打成单个可执行文件")
    ap.add_argument("--console", action="store_true", help="保留控制台窗口（排错用）")
    ap.add_argument("--keep-build", action="store_true", help="保留中间文件")
    a = ap.parse_args()
    return build(a.onefile, a.console, a.keep_build)


if __name__ == "__main__":
    sys.exit(main())
