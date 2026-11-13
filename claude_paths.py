#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
统一的路径与环境解析 —— 兼容两种 Claude 前端布局，适配任意 macOS 机器

Claude 4.42xx 起把前端资源从松散目录 `Resources/ion-dist/` 收进了
`Resources/ion-dist.asar`。两种布局下「en-US.json / zh-Hans.json 在哪里」
不一样：

  松散目录 (旧):  /Applications/Claude.app/Contents/Resources/ion-dist/i18n/
  asar    (新):   <工程目录>/.work/ion-dist/i18n/   (用 zh_patch.py sync 解包)

所有工具统一从这里取路径，不再各自硬编码。

可覆盖的环境变量
----------------
  CLAUDE_ZH_APP       Claude.app 路径（默认自动探测 /Applications 与 ~/Applications）
  CLAUDE_ZH_WORK      工作树目录（默认 <工程目录>/.work）
  CLAUDE_ZH_PROJECT   工程目录（仅 GUI 使用，命令行下由脚本位置推断）

环境要求：macOS + Python ≥ 3.9（系统自带 / Xcode CLT 的 3.9.6 即可）
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

# 3.9 是 macOS 自带（Xcode CLT）的版本，降低门槛让 release 用户少一步安装。
# 代码里所有新式注解都靠 `from __future__ import annotations` 延迟求值，
# 已确认 asar 打包 / 完整性摘要 / 翻译脚本在 3.9.6 下均正常。
MIN_PYTHON = (3, 9)
DEFAULT_APP_CANDIDATES = (
    "/Applications/Claude.app",
    "~/Applications/Claude.app",
    "/Applications/Claude Desktop.app",
    "~/Applications/Claude Desktop.app",
)
BUNDLE_ID = "com.anthropic.claudefordesktop"


# ---------------------------------------------------------------- 环境校验

def is_macos() -> bool:
    return sys.platform == "darwin"


def python_ok() -> bool:
    return sys.version_info[:2] >= MIN_PYTHON


def require_env():
    """所有入口统一调用：环境不满足时给出可执行的修复提示。"""
    if not is_macos():
        raise SystemExit(
            f"✗ 本工具只支持 macOS（当前平台: {sys.platform}）。\n"
            "  Claude Desktop 补丁依赖 codesign / Info.plist / asar 校验，暂无其他平台实现。"
        )
    if not python_ok():
        raise SystemExit(
            "✗ 需要 Python {}.{}+（当前 {}）。\n"
            "  brew install python@3.13\n"
            "  或用装好的解释器直接运行: python3.13 zh_patch.py ...".format(
                *MIN_PYTHON, ".".join(map(str, sys.version_info[:3])))
        )
    if not shutil.which("codesign"):
        raise SystemExit(
            "✗ 找不到 codesign。安装 Xcode 命令行工具:\n"
            "  xcode-select --install"
        )


# ---------------------------------------------------------------- 目标 app

def _app_from_spotlight() -> Path | None:
    """候选路径都不存在时，用 Spotlight 按 bundle id 查。"""
    try:
        out = subprocess.run(
            ["mdfind", f"kMDItemCFBundleIdentifier == '{BUNDLE_ID}'"],
            capture_output=True, text=True, timeout=5,
        ).stdout
    except Exception:
        return None
    for line in out.splitlines():
        p = Path(line.strip())
        if p.suffix == ".app" and p.is_dir():
            return p.resolve()
    return None


def detect_app() -> Path:
    env = os.environ.get("CLAUDE_ZH_APP")
    if env:
        return Path(env).expanduser().resolve()
    for cand in DEFAULT_APP_CANDIDATES:
        p = Path(cand).expanduser()
        if p.is_dir():
            return p.resolve()
    found = _app_from_spotlight()
    if found:
        return found
    # 都不存在时仍返回标准路径，后续会给出「未安装」的明确报错
    return Path(DEFAULT_APP_CANDIDATES[0])


APP = detect_app()
APP_NAME = APP.stem                      # 例如 "Claude"
RES = APP / "Contents" / "Resources"
INFO_PLIST = APP / "Contents" / "Info.plist"
ION_ASAR = RES / "ion-dist.asar"         # 新版：asar 包
ION_DIR = RES / "ion-dist"               # 旧版：松散目录
FRAMEWORKS = APP / "Contents" / "Frameworks"

# ---------------- 补丁工程 ----------------
PATCH_DIR = Path(__file__).resolve().parent
ZH_SRC = PATCH_DIR / "zh-Hans-final.json"
BACKUP_DIR = PATCH_DIR / "backup"

WORK = Path(os.environ.get("CLAUDE_ZH_WORK", PATCH_DIR / ".work")).expanduser().resolve()
WORK_TREE = WORK / "ion-dist"            # asar 解包后的工作树
WORK_BACKUP = WORK / "backup"

LANG = "zh-Hans"
LANG_NAME = "Chinese (Simplified)"
LANG_LOCAL = "中文（简体）"


def app_exists() -> bool:
    return APP.is_dir() and (ION_ASAR.is_file() or ION_DIR.is_dir())


def app_version() -> str:
    import plistlib
    try:
        with open(INFO_PLIST, "rb") as f:
            return plistlib.load(f).get("CFBundleShortVersionString", "?")
    except Exception:
        return "?"


def executable() -> Path:
    """真实可执行文件名（可能不等于 app 目录名，改名安装时也能用）。"""
    import plistlib
    name = APP_NAME
    try:
        with open(INFO_PLIST, "rb") as f:
            name = plistlib.load(f).get("CFBundleExecutable") or name
    except Exception:
        pass
    return APP / "Contents" / "MacOS" / name


def layout() -> str:
    """返回 'asar' / 'dir'（都没有则抛错）。"""
    if ION_ASAR.is_file():
        return "asar"
    if ION_DIR.is_dir():
        return "dir"
    raise SystemExit(
        f"✗ 既没有 {ION_ASAR} 也没有 {ION_DIR}\n"
        f"  目标 app: {APP}\n"
        f"  Claude 可能没安装、装在别处或结构已变。\n"
        f"  可用环境变量指定路径: CLAUDE_ZH_APP=/Applications/Claude.app python3 zh_patch.py info"
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


def require_en() -> Path:
    """工具入口调用：确保 en-US.json 可读，否则给出明确指引。"""
    p = en_file()
    if not p.is_file():
        hint = ("先运行: python3 zh_patch.py sync" if layout() == "asar"
                else "检查 Claude.app 是否完整（Resources/ion-dist/i18n 缺失）")
        raise SystemExit(f"✗ 找不到 {p}\n  当前布局为 {layout()}。{hint}")
    return p


def writable(paths: list[Path]) -> bool:
    return all(os.access(str(p), os.W_OK) for p in paths)


def describe() -> dict:
    """给 GUI / doctor 用的结构化环境信息（不做任何写操作）。"""
    import json
    info = {
        "platform": sys.platform,
        "macos": is_macos(),
        "python": ".".join(map(str, sys.version_info[:3])),
        "python_ok": python_ok(),
        "codesign": bool(shutil.which("codesign")),
        "app": str(APP),
        "app_exists": APP.is_dir(),
        "app_name": APP_NAME,
        "app_version": app_version(),
        "project": str(PATCH_DIR),
        "work": str(WORK),
        "work_tree": WORK_TREE.is_dir(),
        "layout": None,
        "error": None,
    }
    try:
        info["layout"] = layout()
    except SystemExit as e:
        info["error"] = str(e)
    info["writable"] = writable([APP, RES]) if APP.is_dir() else False
    if ION_ASAR.is_file():
        info["asar"] = str(ION_ASAR)
        info["asar_size"] = ION_ASAR.stat().st_size
    info["translation_source"] = str(ZH_SRC)
    try:
        info["translation_entries"] = len(json.loads(ZH_SRC.read_text(encoding="utf-8")))
    except Exception:
        info["translation_entries"] = 0
    return info
