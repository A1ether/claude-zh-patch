#!/bin/bash
# Claude Desktop 中文汉化补丁（新版 ion-dist.asar / 旧版 ion-dist 目录通用）
#
# 说明: Claude 4.42xx 起把前端资源收进了 Resources/ion-dist.asar，
#       该 asar 受 ElectronAsarIntegrity 校验，不能像旧的松散目录那样直接写文件。
#       本脚本现在只是 zh_patch.py 的入口：解包 → 部署翻译/注入 → 重新打包并更新校验值。
#
# 用法:
#   ./apply.sh        # 应用/更新中文汉化（升级后重跑即可）
#   ./restore.sh      # 还原官方原版（从备份恢复）

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

exec python3 zh_patch.py apply "$@"