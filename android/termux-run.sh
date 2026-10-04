#!/data/data/com.termux/files/usr/bin/bash
# 口蘑 Mod 工坊 · Android 启动（Termux）
#
# 起本地服务并打印手机浏览器要打开的地址。手机自己访问 127.0.0.1 就是它。
set -u
cd "$(dirname "$0")/.." || exit 1

PORT="${PM_MODKIT_PORT:-8765}"
WORK="${PM_MODKIT_WORK:-$HOME/pm-modkit-工作区}"

if [ ! -d ".venv" ] && ! python3 -c "import PIL" 2>/dev/null; then
  echo
  echo "  还没装好。先跑：bash android/termux-install.sh"
  echo
  exit 1
fi

PY=python3
[ -x ".venv/bin/python" ] && PY=".venv/bin/python"

mkdir -p "$WORK"
IP=$(ip route get 1 2>/dev/null | awk '{print $7; exit}')
[ -z "${IP:-}" ] && IP=$(ifconfig 2>/dev/null | awk '/inet /{print $2; exit}')

echo
echo "  口蘑 Mod 工坊 · 网页版已启动"
echo "  ────────────────────────────────────────"
echo "  手机浏览器打开：  http://127.0.0.1:$PORT"
[ -n "${IP:-}" ] && echo "  同一 Wi-Fi 的电脑：  http://$IP:$PORT"
echo
echo "  工作区：$WORK"
echo "  （Chrome 菜单里可以「添加到主屏幕」，装成 App 用）"
echo "  按 Ctrl+C 停止"
echo
exec env PM_MODKIT_WORK="$WORK" "$PY" web/server.py --port "$PORT" --host 0.0.0.0
