#!/bin/bash
# 还原 Claude Desktop 原状（移除中文汉化补丁）

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

exec python3 zh_patch.py restore "$@"