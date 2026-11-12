#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
统一的路径解析 —— 兼容两种 Claude 前端布局

Claude 4.42xx 起把前端资源从松散目录 `Resources/ion-dist/` 收进了
`Resources/ion-dist.asar`。两种布局下「en-US.json / zh-Hans.json 在哪里」
不一样：

  松散目录 (旧):  /Applications/Claude.app/Contents/Resources/ion-dist/i18n/
  asar    (新):   ~/claude-zh-patch/.work/ion-dist/i18n/   (用 zh_patch.py sync 解包)

所有工具统一从这里取路径，不再各自硬编码 /Applications 下的目录。
"""
from __future__ import annotations

import os
from pathlib import Path

# ---------------- 目标 app ----------------
APP = Path(os.environ.get("CLAUDE_ZH_APP", "/Applications/Claude.app")).resolve()
RES = APP / "Contents" / "Resources"
INFO_PLIST = APP / "Contents" / "Info.plist"
ION_ASAR = RES / "ion-dist.asar"      # 新版：asar 包
ION_DIR = RES / "ion-dist"            # 旧版：松散目录

# ---------------- 补丁工程 ----------------
PATCH_DIR = Path(__file__).resolve().parent
ZH_SRC = PATCH_DIR / "zh-Hans-final.json"
BACKUP_DIR = PATCH_DIR / "backup"

WORK = Path(os.environ.get("CLAUDE_ZH_WORK", PATCH_DIR / ".work")).resolve()
WORK_TREE = WORK / "ion-dist"         # asar 解包后的工作树

LANG = "zh-Hans"


def layout() -> str:
    """返回 'asar' / 'dir'（都没有则抛错）。"""
    if ION_ASAR.is_file():
        return "asar"
    if ION_DIR.is_dir():
        return "dir"
    raise SystemExit(
        f"✗ 既没有 {ION_ASAR} 也没有 {ION_DIR}\n"
        f"  Claude 安装可能损坏或路径不对（可用 CLAUDE_ZH_APP 覆盖）"
    )


def i18n_dir() -> Path:
    """当前布局下 i18n 目录（工具读写的中文/英文文案所在目录）。"""
    if layout() == "dir":
        return ION_DIR / "i18n"
    return WORK_TREE / "i18n"


def en_file() -> Path:
    return i18n_dir() / "en-US.json"


def app_zh_file() -> Path:
    """部署到 app 侧的 zh-Hans.json（asar 布局下即工作树里的那份）。"""
    return i18n_dir() / f"{LANG}.json"


# 便于 `from claude_paths import EN_FILE, APP_ZH` 的旧式用法
EN_FILE = en_file()
APP_ZH = app_zh_file()


def require_en() -> Path:
    """工具入口调用：确保 en-US.json 可读，否则给出明确指引。"""
    p = en_file()
    if not p.is_file():
        raise SystemExit(
            f"✗ 找不到 {p}\n"
            f"  当前布局为 {layout()}。asar 布局请先运行: python3 zh_patch.py sync"
        )
    return p