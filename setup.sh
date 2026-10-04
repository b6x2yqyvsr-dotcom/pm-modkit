#!/bin/sh
# 口蘑 Mod 工坊 —— Linux / macOS 一次性环境准备
#
#   sh setup.sh
#
# 会创建自带的 .venv 并装好依赖，不动系统 Python。
set -e
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"
if ! command -v "$PY" >/dev/null 2>&1; then
    echo "没找到 $PY。Linux 上装一下：sudo apt install python3 python3-venv python3-pip"
    echo "（Debian/Ubuntu 需要 python3-venv 才能建虚拟环境）"
    exit 1
fi
echo "使用解释器：$($PY -V 2>&1)  ($(command -v "$PY"))"

if [ ! -d .venv ]; then
    echo "[1/3] 创建 .venv"
    "$PY" -m venv .venv
else
    echo "[1/3] .venv 已存在，跳过"
fi

echo "[2/3] 安装依赖（第一次会下载约 100MB）"
./.venv/bin/pip install --quiet --upgrade pip
./.venv/bin/pip install --quiet -r requirements.txt

echo "[3/3] 环境自检"
./.venv/bin/python - <<'PY' || true
import sys
sys.path.insert(0, ".")
from modkit import sysenv
for k, v in sysenv.describe().items():
    print(f"  {k:<12}{v or '未找到'}")
PY

echo
echo "完成。启动方式："
echo "    ./启动.sh          桌面图形界面（需要图形环境）"
echo "    ./启动网页版.sh     网页界面（手机连同一个 Wi-Fi 也能用）"
echo "或直接："
echo "    ./.venv/bin/python app/main.py"
echo "    ./.venv/bin/python web/server.py"
echo
echo "注意：APK 重打包签名需要 apksigner / zipalign / JDK。"
echo "      Debian/Ubuntu: sudo apt install android-sdk-build-tools default-jdk"
