#!/bin/bash
# Claude 升级后一键补全汉化
#
# Claude 升级会完整覆盖 ion-dist（现在打包成 ion-dist.asar），并可能新增大量未翻译文案。
# 本脚本 = 解包 → 补齐/补译 → 部署 + 注入 → 重新打包（更新 integrity）→ 重启。
#
# 用法:
#   ./update.sh              # 应用汉化 + 补译新增条目（推荐，升级后跑这个）
#   ./update.sh --quick      # 只应用汉化 + 词典/同值补齐，不跑 LLM（最快）
#   ./update.sh --no-restart # 完成后不自动重启 Claude（供 GUI / 自动化使用）
#
# 环境变量:
#   CLAUDE_ZH_APP     Claude.app 路径（默认自动探测）
#   CLAUDE_ZH_WORK    工作树目录（默认 .work）
#   CLAUDE_ZH_API / CLAUDE_ZH_MODEL / CLAUDE_ZH_KEY   LLM 端点与鉴权
#   PYTHON            解释器（默认 python3，需 ≥ 3.10）

set -e
cd "$(dirname "${BASH_SOURCE[0]}")"

QUICK=0
RESTART=1
for arg in "$@"; do
  case "$arg" in
    --quick) QUICK=1 ;;
    --no-restart) RESTART=0 ;;
    *) echo "未知参数: $arg"; exit 2 ;;
  esac
done

PY="${PYTHON:-python3}"
APP="${CLAUDE_ZH_APP:-/Applications/Claude.app}"

echo "############################################################"
echo "#  Claude 中文汉化 - 升级后一键更新"
echo "############################################################"

# ---------- 0. 环境检查（跨机器可用的前置条件） ----------
if [ "$(uname -s)" != "Darwin" ]; then
  echo "✗ 只支持 macOS（当前: $(uname -s)）"
  exit 1
fi
if ! command -v "$PY" >/dev/null 2>&1; then
  echo "✗ 找不到 $PY。安装 Python 3.10+ 或设置 PYTHON=/path/to/python3"
  exit 1
fi
if ! "$PY" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)'; then
  echo "✗ 需要 Python ≥ 3.10（当前 $("$PY" -V 2>&1)）"
  echo "  brew install python@3.13 后设置 PYTHON=python3.13"
  exit 1
fi
if [ ! -d "$APP" ]; then
  echo "✗ 找不到 $APP"
  echo "  用 CLAUDE_ZH_APP=/path/to/Claude.app 指定"
  exit 1
fi
APP_NAME=$(defaults read "$APP/Contents/Info" CFBundleExecutable 2>/dev/null || basename "$APP" .app)

# ---------- 1. 解包当前版本前端资源（同时备份原始 asar / Info.plist） ----------
echo ""
echo "############################################################"
echo "#  解包当前版本 ion-dist"
echo "############################################################"
"$PY" zh_patch.py sync

# ---------- 2. 短词补齐（秒级，无需模型） ----------
echo ""
echo "############################################################"
echo "#  短词/单值词条补齐"
echo "############################################################"
"$PY" fill_short.py

# ---------- 3. 术语修正（秒级，无需模型） ----------
echo ""
echo "############################################################"
echo "#  术语规范修正（PR/分支/运行 等）"
echo "############################################################"
"$PY" fix_terms.py

# ---------- 4. 同值传播（秒级，无需模型） ----------
echo ""
echo "############################################################"
echo "#  同值传播补译"
echo "############################################################"
"$PY" propagate.py

if [ "$QUICK" = "1" ]; then
  echo ""
  echo "== 已跳过 LLM 补译（--quick 模式）=="
else
  # ---------- 5. 统计剩余待译 ----------
  NEED=$("$PY" - <<'PY'
import json, sys
sys.path.insert(0, '.')
import claude_paths as P
from retranslate import needs_work
en = json.loads(P.en_file().read_text(encoding='utf-8'))
zh = json.loads(P.ZH_SRC.read_text(encoding='utf-8'))
n = sum(1 for k, e in en.items() if needs_work(zh.get(k, e) or e, e))
print(n)
PY
)

  echo ""
  echo "待 LLM 补译(主文案): ${NEED} 条"

  # 端点是否可用（不可用则跳过，不影响主流程）
  if "$PY" -c "import llm_endpoint; llm_endpoint.discover()" >/dev/null 2>&1; then
    if [ "$NEED" -gt 0 ]; then
      echo ""
      echo "############################################################"
      echo "#  LLM 补译普通文案"
      echo "############################################################"
      rm -f translate_done.json
      "$PY" retranslate.py --prepare
      "$PY" retranslate.py --run
      "$PY" retranslate.py --apply

      echo ""
      echo "############################################################"
      echo "#  LLM 补译 ICU 复合结构"
      echo "############################################################"
      rm -f icu_done.json
      "$PY" retranslate_icu.py
    else
      echo "✓ 无需 LLM 补译"
    fi
  else
    echo "⚠ 未探测到本地模型端点，跳过 LLM 补译"
    echo "  可设置 CLAUDE_ZH_API / CLAUDE_ZH_MODEL / CLAUDE_ZH_KEY 指定端点"
  fi
fi

# ---------- 6. 部署翻译 + 注入 + 重新打包 ----------
echo ""
echo "############################################################"
echo "#  部署翻译 / 注入语言入口 / 重新打包"
echo "############################################################"
"$PY" zh_patch.py stage --no-sync
"$PY" zh_patch.py pack

# ---------- 7. 重启 Claude ----------
if [ "$RESTART" = "1" ]; then
  echo ""
  echo "############################################################"
  echo "#  重启 Claude"
  echo "############################################################"
  osascript -e "tell application \"$APP_NAME\" to quit" 2>/dev/null || true
  sleep 3
  pkill -9 -x "$APP_NAME" 2>/dev/null || true
  sleep 1
  open "$APP"
  echo "✓ 已重启 Claude"
fi

# ---------- 8. 校验 ----------
echo ""
echo "############################################################"
echo "#  校验"
echo "############################################################"
"$PY" zh_patch.py verify || true

echo ""
echo "完成后若界面仍是英文: 左下角账户 → 「语言」→ 选「中文（简体）」"
