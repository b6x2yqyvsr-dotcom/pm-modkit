#!/bin/bash
# 双击本文件即可启动「口蘑 Mod 工坊」图形界面（macOS）。
cd "$(dirname "$0")" || exit 1

if [ ! -x .venv/bin/python ]; then
    osascript -e 'display dialog "还没装运行环境。\n\n请先在终端里执行：\n  cd '"$(pwd)"'\n  sh setup.sh" buttons {"好"} default button 1 with icon caution' >/dev/null 2>&1
    echo "缺 .venv，请先执行 sh setup.sh"
    exit 1
fi

exec ./.venv/bin/python app/main.py "$@"
