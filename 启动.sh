#!/bin/sh
# 桌面图形界面（Linux / macOS）
cd "$(dirname "$0")" || exit 1
[ -x .venv/bin/python ] || { echo "缺 .venv，请先执行 sh setup.sh"; exit 1; }
if [ -z "$DISPLAY" ] && [ -z "$WAYLAND_DISPLAY" ] && [ "$(uname)" != "Darwin" ]; then
    echo "没有检测到图形环境（DISPLAY 为空）。"
    echo "如果是服务器或手机（Termux），请改用：  ./启动网页版.sh"
    exit 1
fi
exec ./.venv/bin/python app/main.py "$@"
