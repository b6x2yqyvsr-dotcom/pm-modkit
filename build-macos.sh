#!/usr/bin/env bash
# 口蘑 Mod 工坊 · macOS 打包（同时出 .dmg）
set -euo pipefail
cd "$(dirname "$0")"

echo
echo "  口蘑 Mod 工坊 · macOS 打包"
echo "  ────────────────────────────────────────"

PY=python3
if [ -x ".venv/bin/python" ]; then PY=".venv/bin/python"; fi

echo "  [1/4] 依赖"
"$PY" -m pip install -q --upgrade pip
"$PY" -m pip install -q -r requirements.txt
"$PY" -m pip install -q pyinstaller

echo "  [2/4] 打包 .app"
rm -rf dist build/work
"$PY" build/build_app.py --keep-build

echo "  [3/4] 生成 .dmg"
APP="dist/口蘑Mod工坊.app"
if [ -d "$APP" ]; then
  rm -rf build/dmg && mkdir -p build/dmg
  cp -R "$APP" build/dmg/
  ln -s /Applications build/dmg/Applications
  rm -f "dist/口蘑Mod工坊.dmg"
  hdiutil create -volname "口蘑Mod工坊" -srcfolder build/dmg \
      -ov -format UDZO "dist/口蘑Mod工坊.dmg" >/dev/null
  echo "      dist/口蘑Mod工坊.dmg"
fi

echo "  [4/4] 完成"
echo
echo "  产物：dist/口蘑Mod工坊.app   （拖到「应用程序」即可）"
echo "        dist/口蘑Mod工坊.dmg"
echo
echo "  首次打开若提示「无法验证开发者」：右键点图标 → 打开 → 再点「打开」。"
echo
