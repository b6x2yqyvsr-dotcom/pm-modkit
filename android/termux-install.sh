#!/data/data/com.termux/files/usr/bin/bash
# 口蘑 Mod 工坊 —— 在 Android (Termux) 上安装
#
# 手机端**不能**装 imgui-bundle（没有 Android 轮子），所以这里装的是
# **网页版 + 命令行版**：装完在手机浏览器打开 http://127.0.0.1:8765 就是完整界面。
#
# 用法：
#   pkg install -y git
#   git clone <本项目> && cd pm-modkit
#   bash android/termux-install.sh
#
# 之后每次启动：
#   bash android/termux-run.sh

set -u
cd "$(dirname "$0")/.." || exit 1
echo
echo "  口蘑 Mod 工坊 · Android/Termux 安装"
echo "  ────────────────────────────────────────"

step() { printf '\n\033[1;34m==> %s\033[0m\n' "$1"; }
ok()   { printf '    \033[32m✓\033[0m %s\n' "$1"; }
bad()  { printf '    \033[33m!\033[0m %s\n' "$1"; }

# ---------------------------------------------------------------- 1. 系统包
step "1/4 安装系统包（Termux 仓库里的是预编译的，比 pip 编译快得多）"
pkg update -y >/dev/null 2>&1 || bad "pkg update 有警告，继续"

# Pillow 走 Termux 包最省事；lz4/brotli 是解包必需
BASE="python python-pip"
OPT="python-pillow python-lz4 python-brotli"
pkg install -y $BASE >/dev/null 2>&1 && ok "python / pip" || { bad "装 python 失败"; exit 1; }
for p in $OPT; do
    if pkg install -y "$p" >/dev/null 2>&1; then ok "$p"; else bad "$p 装不上（稍后用 pip 兜底）"; fi
done

# 编译兜底（有些包 Termux 仓库没有，要现场编）
pkg install -y clang make libjpeg-turbo libpng >/dev/null 2>&1 && ok "clang/make（编译兜底用）" || bad "编译工具没装上"

# ---------------------------------------------------------------- 2. 存储权限
step "2/4 申请存储权限（这样工具能读 /sdcard 上的 apk 和数据包）"
if command -v termux-setup-storage >/dev/null 2>&1; then
    termux-setup-storage 2>/dev/null && ok "已申请（手机上会弹权限框，点允许）" || bad "跳过"
else
    bad "没有 termux-setup-storage，跳过"
fi

# ---------------------------------------------------------------- 3. Python 依赖
step "3/4 安装 Python 依赖"
PY=python
if [ ! -d .venv ]; then
    $PY -m venv .venv 2>/dev/null && ok "建好 .venv" || bad "venv 建不了，直接用系统 python"
fi
if [ -x .venv/bin/python ]; then PYBIN=.venv/bin/python; PIP=".venv/bin/python -m pip"; else PYBIN=python; PIP="python -m pip"; fi
ok "解释器：$($PYBIN -V 2>&1)"

# 先补上 Termux 仓库已经装好的，pip 就不会再去编译它们
$PIP install --quiet --upgrade pip >/dev/null 2>&1

# UnityPy 的 C 扩展（texture2ddecoder / etcpak / astc-encoder-py）只有
# 遇到压缩纹理才用得到。本游戏的贴图是 ARGB4444/RGBA32 未压缩格式，
# 所以这几个装不上也不影响 —— 先试装，失败就跳过。
echo "    试装完整依赖…"
if $PIP install --quiet -r requirements-web.txt >/dev/null 2>&1; then
    ok "完整依赖装好了"
else
    bad "完整装失败，改用精简装：只装真正必需的"
    $PIP install --quiet Pillow lz4 brotli fsspec attrs >/dev/null 2>&1 && ok "Pillow / lz4 / brotli / fsspec / attrs"
    $PIP install --quiet --no-deps UnityPy tpk_ar >/dev/null 2>&1 && ok "UnityPy（--no-deps）"
    bad "压缩纹理（ASTC/ETC）和音频导出可能不可用 —— 本游戏用不到"
fi

# ---------------------------------------------------------------- 4. 自检
step "4/4 自检"
$PYBIN - <<'PY' || true
import sys
sys.path.insert(0, ".")
try:
    import UnityPy
    print("    ✓ UnityPy 可用")
except Exception as e:
    print("    ✗ UnityPy 导入失败：", e)
try:
    from PIL import Image
    print("    ✓ Pillow 可用")
except Exception as e:
    print("    ✗ Pillow 导入失败：", e)
for m in ("lz4", "brotli"):
    try:
        __import__(m); print(f"    ✓ {m} 可用")
    except Exception:
        print(f"    ! {m} 缺失（解包会失败，务必装）")
for m in ("texture2ddecoder", "etcpak", "astc_encoder", "fmod_toolkit"):
    try:
        __import__(m)
    except Exception:
        print(f"    · {m} 没装（只影响压缩纹理/音频，本游戏用不到）")
try:
    from modkit import sysenv
    print("    ✓ 平台识别：", sysenv.platform_name())
except Exception as e:
    print("    ✗ modkit 导入失败：", e)
PY

echo
echo "  ────────────────────────────────────────"
echo "  装完了。启动："
echo "      bash android/termux-run.sh"
echo "  然后在手机浏览器打开：  http://127.0.0.1:8765"
echo
echo "  游戏文件通常在："
echo "      ~/storage/shared/Android/data/com.conspiracyrick.pocketmortys/files/"
echo "      或者你下载的 dp.apk / 口蘑数据包.zip"
echo
