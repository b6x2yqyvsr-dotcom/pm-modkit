"""跨平台适配：路径、字体、Android 构建工具的查找。

原来这些散在各个文件里，且默认假设是 macOS（``/opt/homebrew``、
``~/Library/Android/sdk``、``/System/Library/Fonts``）。这里集中处理，
支持 **Windows / Linux / macOS / Termux(Android)**。

设计原则：**能找到就用，找不到就老实说找不到**，不猜、不静默失败。
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

# ---------------------------------------------------------------- 平台识别

IS_WINDOWS = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")

#: 是不是在 Android 上的 Termux 里跑（Termux 会把 PREFIX 指到 /data/data/com.termux/...）
IS_TERMUX = IS_LINUX and (
    "com.termux" in os.environ.get("PREFIX", "")
    or Path("/data/data/com.termux/files/usr").is_dir()
)
IS_ANDROID = IS_TERMUX

PLATFORM_LABEL = (
    "Windows" if IS_WINDOWS
    else "macOS" if IS_MAC
    else "Android/Termux" if IS_TERMUX
    else "Linux" if IS_LINUX
    else sys.platform
)


def platform_name() -> str:
    return PLATFORM_LABEL


def exe(name: str) -> str:
    """补上 Windows 的可执行后缀。"""
    if not IS_WINDOWS:
        return name
    return name + ".exe"


# ---------------------------------------------------------------- 输出目录


def default_output_dir() -> Path:
    """跨平台的默认输出目录。

    Windows/Linux 上不一定有 ``Desktop``（尤其 Termux），所以用家目录下的
    固定名字；桌面存在的话优先桌面，用着顺手。
    """
    home = Path.home()
    desktop = home / "Desktop"
    if desktop.is_dir() and os.access(desktop, os.W_OK):
        return desktop / "PM-Mod-输出"
    return home / "PM-Mod-输出"


def ensure_dir(p: str | Path) -> Path:
    d = Path(p).expanduser()
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------- Android SDK

#: Android SDK / build-tools 的常见位置（按平台）
def _sdk_hints() -> list[Path]:
    out: list[Path] = []
    for var in ("ANDROID_SDK_ROOT", "ANDROID_HOME", "ANDROID_SDK"):
        v = os.environ.get(var)
        if v:
            out.append(Path(v))

    home = Path.home()
    if IS_WINDOWS:
        local = os.environ.get("LOCALAPPDATA")
        out += [
            home / "AppData/Local/Android/Sdk",
            Path(local) / "Android/Sdk" if local else home,
            Path("C:/Android/Sdk"),
            Path("C:/Program Files (x86)/Android/android-sdk"),
        ]
    else:
        out += [
            home / "Library/Android/sdk",           # macOS
            home / "Android/Sdk",                   # Linux 常见
            Path("/usr/lib/android-sdk"),           # Debian/Ubuntu 包
            Path("/opt/android-sdk"),
            Path("/usr/local/share/android-sdk"),
        ]
        if IS_TERMUX:
            out += [
                Path(os.environ.get("PREFIX", "/data/data/com.termux/files/usr")) / "opt/android-sdk",
                Path("/sdcard/Android/sdk"),
            ]

    # 本项目隔壁那个前期工程里拉好的一套（开发机上常见）
    for cand in (
        Path.cwd().parent / "pocketmortys-server/.android-sdk",
        home / "Documents/deepseek-harness/default-workspace/pocketmortys-server/.android-sdk",
    ):
        if cand.is_dir():
            out.append(cand)
    return [p for p in out if p and p.is_dir()]


def find_build_tool(name: str) -> str | None:
    """找 apksigner / zipalign / keytool / java。

    顺序：PATH → PATH 上加后缀 → Android SDK build-tools（版本从高到低）
    → Termux 的 ``$PREFIX/bin``。
    """
    for cand in (name, exe(name)):
        hit = shutil.which(cand)
        if hit:
            return hit

    for base in _sdk_hints():
        bt = base / "build-tools"
        if not bt.is_dir():
            continue
        # 版本号排序：34.0.0 > 9.0.0 这种要按数字比，不能按字符串
        def key(p: Path):
            parts = []
            for x in p.name.replace("-", ".").split("."):
                parts.append(int(x) if x.isdigit() else -1)
            return parts

        for ver in sorted(bt.iterdir(), key=key, reverse=True):
            for cand in (ver / name, ver / exe(name)):
                if cand.is_file():
                    return str(cand)
            # Windows 上 apksigner 是 .bat
            if IS_WINDOWS:
                for cand in (ver / f"{name}.bat", ver / f"{name}.cmd"):
                    if cand.is_file():
                        return str(cand)

    if IS_TERMUX:
        prefix = Path(os.environ.get("PREFIX", "/data/data/com.termux/files/usr"))
        for cand in (prefix / "bin" / name, prefix / "bin" / exe(name)):
            if cand.is_file():
                return str(cand)
    return None


def find_java_home() -> str | None:
    """找 java 所在的 bin 目录。"""
    jh = os.environ.get("JAVA_HOME")
    if jh and (Path(jh) / "bin" / exe("java")).is_file():
        return str(Path(jh) / "bin")

    which = shutil.which(exe("java"))
    if which:
        return str(Path(which).parent)

    cands: list[Path] = []
    if IS_MAC:
        cands += [
            Path("/opt/homebrew/opt/openjdk/bin"),
            Path("/opt/homebrew/opt/openjdk@17/bin"),
            Path("/usr/local/opt/openjdk/bin"),
        ]
        # macOS 的 java_home 工具最靠谱
        try:
            import subprocess

            r = subprocess.run(["/usr/libexec/java_home"], capture_output=True, text=True)
            if r.returncode == 0 and r.stdout.strip():
                cands.insert(0, Path(r.stdout.strip()) / "bin")
        except Exception:  # noqa: BLE001
            pass
    elif IS_WINDOWS:
        for base in (Path("C:/Program Files/Java"), Path("C:/Program Files/Eclipse Adoptium")):
            if base.is_dir():
                cands += [d / "bin" for d in base.iterdir() if d.is_dir()]
    else:
        cands += [
            Path("/usr/lib/jvm/default-java/bin"),
            Path("/usr/lib/jvm/java-17-openjdk-amd64/bin"),
            Path("/usr/lib/jvm/java-11-openjdk-amd64/bin"),
        ]
        jvm = Path("/usr/lib/jvm")
        if jvm.is_dir():
            cands += [d / "bin" for d in sorted(jvm.iterdir()) if d.is_dir()]

    for c in cands:
        if (c / exe("java")).is_file():
            return str(c)
    return None


# ---------------------------------------------------------------- 中文字体

#: 各平台自带的中文字体（优先挑覆盖率好的）
def cjk_font_candidates() -> list[str]:
    home = Path.home()
    out: list[str] = []
    if IS_MAC:
        out += [
            "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
            "/System/Library/Fonts/Hiragino Sans GB.ttc",
            "/System/Library/Fonts/STHeiti Light.ttc",
            "/System/Library/Fonts/PingFang.ttc",
        ]
    elif IS_WINDOWS:
        windir = os.environ.get("WINDIR", "C:/Windows")
        out += [
            f"{windir}/Fonts/msyh.ttc",       # 微软雅黑
            f"{windir}/Fonts/msyh.ttf",
            f"{windir}/Fonts/simhei.ttf",     # 黑体
            f"{windir}/Fonts/simsun.ttc",     # 宋体
            f"{windir}/Fonts/Deng.ttf",       # 等线
        ]
        # 用户自己装的字体
        out += [str(home / "AppData/Local/Microsoft/Windows/Fonts/msyh.ttc")]
    else:
        out += [
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
            "/usr/share/fonts/truetype/arphic/uming.ttc",
            "/usr/share/fonts/google-noto-cjk/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
        ]
        if IS_TERMUX:
            prefix = os.environ.get("PREFIX", "/data/data/com.termux/files/usr")
            out += [
                f"{prefix}/share/fonts/TTF/NotoSansCJK-Regular.ttc",
                "/system/fonts/NotoSansCJK-Regular.ttc",
                "/system/fonts/DroidSansFallbackFull.ttf",   # 安卓兜底字体
                "/system/fonts/NotoSansSC-Regular.otf",
            ]
    out += [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",  # 最后兜底（可能没中文）
    ]
    return out


def find_cjk_font() -> str | None:
    for c in cjk_font_candidates():
        if Path(c).is_file():
            return c
    return None


#: 等宽字体（界面里那种小号大写的拉丁小标用它）
def mono_font_candidates() -> list[str]:
    out: list[str] = []
    if IS_MAC:
        out += [
            "/System/Library/Fonts/Menlo.ttc",
            "/System/Library/Fonts/SFNSMono.ttf",
            "/System/Library/Fonts/Monaco.ttf",
        ]
    elif IS_WINDOWS:
        windir = os.environ.get("WINDIR", "C:/Windows")
        out += [
            f"{windir}/Fonts/consola.ttf",
            f"{windir}/Fonts/CascadiaMono.ttf",
            f"{windir}/Fonts/lucon.ttf",
        ]
    else:
        out += [
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
            "/usr/share/fonts/TTF/DejaVuSansMono.ttf",
        ]
        if IS_TERMUX:
            prefix = os.environ.get("PREFIX", "/data/data/com.termux/files/usr")
            out += [f"{prefix}/share/fonts/TTF/DejaVuSansMono.ttf"]
    return out


def find_mono_font() -> str | None:
    for c in mono_font_candidates():
        if Path(c).is_file():
            return c
    return None


# ---------------------------------------------------------------- 环境自述


def describe() -> dict[str, str | None]:
    """给「工具链自检」用的一句话环境概览。"""
    return {
        "平台": PLATFORM_LABEL,
        "Python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "解释器": sys.executable,
        "默认输出目录": str(default_output_dir()),
        "中文字体": find_cjk_font(),
        "apksigner": find_build_tool("apksigner"),
        "zipalign": find_build_tool("zipalign"),
        "keytool": find_build_tool("keytool"),
        "java": find_java_home(),
    }
