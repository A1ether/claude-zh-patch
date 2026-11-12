#!/bin/bash
# Claude 升级后一键补全汉化
#
# Claude 升级会完整覆盖 ion-dist（现在打包成 ion-dist.asar），并可能新增大量未翻译文案。
# 本脚本 = 解包 → 部署翻译 + 注入 → 补齐/补译 → 重新打包（更新 integrity）→ 重启。
#
# 用法:
#   ./update.sh            # 应用汉化 + 补译新增条目（推荐，升级后跑这个）
#   ./update.sh --quick    # 只应用汉化 + 词典/同值补齐，不跑 LLM（最快）

set -e
cd "$(dirname "${BASH_SOURCE[0]}")"

QUICK=0
[ "$1" = "--quick" ] && QUICK=1

echo "############################################################"
echo "#  Claude 中文汉化 - 升级后一键更新"
echo "############################################################"

# ---------- 1. 解包当前版本前端资源（同时备份原始 asar / Info.plist） ----------
echo ""
echo "############################################################"
echo "#  解包当前版本 ion-dist"
echo "############################################################"
python3 zh_patch.py sync

# ---------- 2. 部署翻译 + 注入语言白名单 ----------
echo ""
echo "############################################################"
echo "#  部署翻译 / 注入语言入口"
echo "############################################################"
python3 zh_patch.py stage

# ---------- 3. 短词补齐（秒级，无需模型） ----------
echo ""
echo "############################################################"
echo "#  短词/单值词条补齐"
echo "############################################################"
python3 fill_short.py

# ---------- 4. 术语修正（秒级，无需模型） ----------
echo ""
echo "############################################################"
echo "#  术语规范修正（PR/分支/运行 等）"
echo "############################################################"
python3 fix_terms.py

# ---------- 5. 同值传播（秒级，无需模型） ----------
echo ""
echo "############################################################"
echo "#  同值传播补译"
echo "############################################################"
python3 propagate.py

if [ "$QUICK" = "1" ]; then
  echo ""
  echo "== 已跳过 LLM 补译（--quick 模式）=="
else
  # ---------- 5. 统计剩余待译 ----------
  NEED=$(python3 - <<'PY'
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

  # 本地模型端点是否可用（不可用则跳过，不影响主流程）
  if python3 -c "import llm_endpoint; llm_endpoint.discover()" >/dev/null 2>&1; then
    if [ "$NEED" -gt 0 ]; then
      echo ""
      echo "############################################################"
      echo "#  LLM 补译普通文案"
      echo "############################################################"
      rm -f translate_done.json
      python3 retranslate.py --prepare
      python3 retranslate.py --run
      python3 retranslate.py --apply

      echo ""
      echo "############################################################"
      echo "#  LLM 补译 ICU 复合结构"
      echo "############################################################"
      rm -f icu_done.json
      python3 retranslate_icu.py
    else
      echo "✓ 无需 LLM 补译"
    fi
  else
    echo "⚠ 未探测到本地模型端点，跳过 LLM 补译（可设置 CLAUDE_ZH_API / CLAUDE_ZH_MODEL）"
  fi
fi

# ---------- 7. 用最新翻译源重建并打包 ----------
echo ""
echo "############################################################"
echo "#  重新打包 ion-dist.asar（更新 integrity 与 Info.plist）"
echo "############################################################"
python3 zh_patch.py stage
python3 zh_patch.py pack

# ---------- 8. 重启 Claude ----------
echo ""
echo "############################################################"
echo "#  重启 Claude"
echo "############################################################"
osascript -e 'tell application "Claude" to quit' 2>/dev/null || true
sleep 3
pkill -9 -x Claude 2>/dev/null || true
sleep 1
open -a "Claude"
echo "✓ 已重启 Claude"

# ---------- 9. 校验 ----------
echo ""
echo "############################################################"
echo "#  校验"
echo "############################################################"
python3 zh_patch.py verify || true

echo ""
echo "完成后若界面仍是英文: 左下角账户 → 「语言」→ 选「中文（简体）」"