#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把汉化引擎打进 ClaudeHans.app，让 release 包下载后无需 clone 仓库。

    python3 gui/tool/bundle_engine.py build/macos/Build/Products/Release/ClaudeHans.app

产物：<App>.app/Contents/Resources/engine/，里面是运行期需要的脚本与数据。
GUI 首次启动时会把这份引擎复制到
    ~/Library/Application Support/claude-zh-patch/engine
再在那份可写的副本上工作——app 包内不能写（会破坏代码签名）。
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# 运行期真正会用到的文件；别把 .bak-* 历史备份和 docs 塞进去
RUNTIME_FILES = [
    # 引擎
    "zh_patch.py",
    "claude_paths.py",
    "llm_endpoint.py",
    "llm_client.py",
    "llm_parse.py",
    # 翻译流水线
    "retranslate.py",
    "retranslate_icu.py",
    "fill_short.py",
    "fix_terms.py",
    "propagate.py",
    "scan_quality.py",
    # 脚本
    "update.sh",
    "apply.sh",
    "restore.sh",
    # 数据
    "zh-Hans-final.json",
    "zh-Hans-dynamic.json",
    "translate_done.json",
    "translate_queue.json",
    "icu_done.json",
]

VERSION_FILE = "ENGINE_VERSION"


def engine_version() -> str:
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
    except Exception:
        sha = ""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"{stamp}-{sha or 'nogit'}"


def bundle(app: Path) -> Path:
    dest = app / "Contents" / "Resources" / "engine"
    if not (app / "Contents" / "Info.plist").is_file():
        raise SystemExit(f"✗ 不是有效的 .app: {app}")

    missing = [f for f in RUNTIME_FILES if not (ROOT / f).is_file()]
    if missing:
        raise SystemExit("✗ 缺少运行期文件: " + ", ".join(missing))

    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    for name in RUNTIME_FILES:
        shutil.copy2(ROOT / name, dest / name)
        if name.endswith(".sh"):
            (dest / name).chmod(0o755)

    ver = engine_version()
    (dest / VERSION_FILE).write_text(ver + "\n", encoding="utf-8")

    total = sum(f.stat().st_size for f in dest.iterdir() if f.is_file())
    print(f"✓ 引擎已打包到 {dest}")
    print(f"  版本 {ver}  {len(RUNTIME_FILES)} 个文件  {total / 1e6:.1f} MB")
    return dest


def main() -> int:
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    bundle(Path(sys.argv[1]).expanduser().resolve())
    return 0


if __name__ == "__main__":
    sys.exit(main())
