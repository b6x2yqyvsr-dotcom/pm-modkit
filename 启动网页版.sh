#!/bin/sh
# 网页界面（Linux / macOS / Termux）
# 手机会话：电脑跑这个，手机浏览器打开 http://<电脑IP>:8765
cd "$(dirname "$0")" || exit 1
[ -x .venv/bin/python ] || { echo "缺 .venv，请先执行 sh setup.sh"; exit 1; }
exec ./.venv/bin/python web/server.py --port "${PORT:-8765}" "$@"
