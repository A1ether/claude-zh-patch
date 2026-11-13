#!/bin/bash
# 打一个「下载即用」的 release 包：
#   1. 编译 Flutter GUI
#   2. 把汉化引擎（脚本 + 翻译数据）打进 app 包
#   3. ad-hoc 重签并校验
#   4. 压成 zip 放进 dist/
#
# 用法： ./make_release.sh [--fast]     --fast 跳过 flutter clean（日常迭代用）
set -euo pipefail

GUI="$(cd "$(dirname "$0")/.." && pwd)"
ROOT="$(cd "$GUI/.." && pwd)"
APP="$GUI/build/macos/Build/Products/Release/ClaudeHans.app"
PY="${PYTHON:-python3}"

cd "$GUI"

if [[ "${1:-}" != "--fast" ]]; then
  # 增量构建偶尔会留下过期的嵌套签名（App.framework），先清一遍最稳
  flutter clean >/dev/null
  flutter pub get >/dev/null
fi

echo "▸ 编译 GUI…"
flutter build macos --release >/dev/null

echo "▸ 打包内置引擎…"
"$PY" tool/bundle_engine.py "$APP"

echo "▸ 重新签名…"
# 往 Resources/ 里塞了文件，原签名已失效；ad-hoc 签一份自用即可
codesign --force --deep --sign - "$APP" 2>/dev/null
codesign --verify --deep --strict "$APP"
echo "  ✓ 签名校验通过"

VER="$(awk '/^version:/{print $2}' pubspec.yaml)"
VER="${VER%%+*}"   # 1.0.0+1 → 1.0.0，文件名别带构建号
mkdir -p "$ROOT/dist"
ZIP="$ROOT/dist/ClaudeHans-$VER.zip"
rm -f "$ZIP"
ditto -c -k --sequesterRsrc --keepParent "$APP" "$ZIP"

echo
echo "✓ $ZIP"
echo "  大小 $(du -h "$ZIP" | cut -f1)   SHA256 $(shasum -a 256 "$ZIP" | cut -d' ' -f1)"