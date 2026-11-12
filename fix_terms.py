#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Claude 中文本地化术语修正器
基于 EN 原文精确匹配，只修正已确认的错误译法。
用法:
  python3 fix_terms.py --dry-run   # 预演
  python3 fix_terms.py             # 实际修正
"""
from __future__ import annotations
import argparse, json, re, shutil, sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import claude_paths as P

PATCH_DIR = P.PATCH_DIR
ZH_FILE = P.ZH_SRC
EN_FILE = P.require_en()
APP_ZH = P.app_zh_file()

# ============================================================
# 修正规则表
#   "en_exact": 精确匹配 EN 原文（key 未知时用）
#   "zh_fix": 修正后译文
# ============================================================

# --- A. 高危技术术语误译（PR/Branch/Run 等） ---
FIX_BY_EN = {
    # PR = Pull Request，绝不能译成「公关」
    "Create PR": "创建 PR",
    "Creating PR": "正在创建 PR",
    "Creating PR…": "正在创建 PR…",
    "Created PR": "已创建 PR",
    "Merged PR": "已合并 PR",
    "PR merged": "PR 已合并",
    "Closed PR": "已关闭 PR",
    "Closing PR": "正在关闭 PR",
    "Edited PR": "已编辑 PR",
    "Commenting on PR": "正在评论 PR",
    "Commented on PR": "已评论 PR",
    "View PR": "查看 PR",
    "PRs": "PR",
    "Pull request merged.": "拉取请求已合并。",
    # Branch = 代码分支，不是「分支机构」
    "No branches available": "没有可用的分支",
    "Search branches…": "搜索分支…",
    # Run = 运行，不是「跑步」
    "Running": "运行中",
    "Run": "运行",
    # Chrome = 浏览器名，绝不能译成「铬」
    "Chrome": "Chrome",
    # Ran = 已运行
    "Ran": "已运行",
    # 其他
    "Pen": "钢笔",
    "WoW": "WoW",
    "Go": "前往",
    "Off": "关闭",
    "On": "打开",
    "Light": "浅色",
    "Less": "更少",
    "the pool": "资源池",
    "tree": "树形",
    "Table": "表格",
    "Diagram": "图表",
    "Library": "库",
    "Volume": "卷",
    "Domains": "域",
    "Domain": "域",
    "Pushed": "已推送",
    "Pushing": "正在推送",
    "write": "写入",
    "go to": "前往",
    "Go to": "前往",
}

# --- B. 单字按钮/标签 → 规范动宾短语（仅当 EN 是 New/Delete 等动作词）---
BUTTON_FIX = {
    "New": "新建",
    "Delete": "删除",
    "Add": "添加",
    "Edit": "编辑",
    "Save": "保存",
    "Cancel": "取消",
    "Close": "关闭",
    "Open": "打开",
    "Retry": "重试",
    "Submit": "提交",
    "Apply": "应用",
    "Reset": "重置",
    "Back": "返回",
    "Next": "下一步",
    "Done": "完成",
    "Skip": "跳过",
    "Copy": "复制",
    "Paste": "粘贴",
    "Cut": "剪切",
    "Clear": "清除",
    "Search": "搜索",
    "Filter": "筛选",
    "Sort": "排序",
    "Refresh": "刷新",
    "Share": "分享",
    "Remove": "移除",
    "Update": "更新",
    "Upload": "上传",
    "Download": "下载",
    "Send": "发送",
    "Stop": "停止",
    "Start": "开始",
    "Pause": "暂停",
    "Resume": "继续",
}

# --- C. 子串级修正（对长句内部的关键术语做替换，安全替换）---
SUBSTR_FIX = [
    # PR 相关（避免「公关」出现在任何语境）
    ("合并公关", "合并 PR"),
    ("创建公关", "创建 PR"),
    ("创建公关...", "创建 PR..."),
    ("已创建公关", "已创建 PR"),
    ("公关评论", "PR 评论"),
    ("对公关发表评论", "对 PR 发表评论"),
    ("公关已合并", "PR 已合并"),
    ("已关闭公关", "已关闭 PR"),
    ("关闭公关", "关闭 PR"),
    ("已编辑公关", "已编辑 PR"),
    ("查看公关", "查看 PR"),
    ("公关归因", "PR 归因"),
    ("公关", "PR"),  # 兜底：剩余「公关」一律改 PR
    # Branch 相关
    ("搜索分支机构", "搜索分支"),
    ("没有可用的分支机构", "没有可用的分支"),
    ("分支机构", "分支"),
    # Run 相关
    ("跑步", "运行"),
    # 其他明显错误
    ("拉取 请求", "拉取请求"),
    ("铬", "Chrome"),   # 仅在 Chrome 语境，但中文「铬」几乎不会合法出现
]

def load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))

def save(p, data):
    Path(p).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=25)
    args = ap.parse_args()

    zh = load(ZH_FILE)
    en = load(EN_FILE)

    changes = []

    # 1) 按 EN 精确匹配修正
    for k, eval_ in en.items():
        if k not in zh:
            continue
        cur = zh[k]
        new = None

        # A. 高优先级：EN 在 FIX_BY_EN 里
        if eval_ in FIX_BY_EN:
            new = FIX_BY_EN[eval_]
        # B. 按钮词修正（仅当前译文是单字且是动作词）
        elif eval_ in BUTTON_FIX and len(cur.strip()) <= 1 and re.match(r"^[\u4e00-\u9fa5]$", cur.strip()):
            new = BUTTON_FIX[eval_]

        if new is not None and new != cur:
            changes.append((k, eval_, cur, new, "术语/按钮规范"))
            zh[k] = new

    # 2) 子串级修正（扫描所有 value）
    substr_changes = []
    for k, cur in list(zh.items()):
        if not isinstance(cur, str):
            continue
        orig = cur
        newc = cur
        for bad, good in SUBSTR_FIX:
            if bad in newc:
                newc = newc.replace(bad, good)
        if newc != orig:
            substr_changes.append((k, en.get(k, ""), orig, newc, "子串术语替换"))
            zh[k] = newc

    all_changes = changes + substr_changes

    print("=" * 70)
    print(" 术语修正" + ("（预演 DRY-RUN）" if args.dry_run else ""))
    print("=" * 70)
    print(f"精确/按钮修正: {len(changes)} 条")
    print(f"子串术语替换: {len(substr_changes)} 条")
    print(f"合计: {len(all_changes)} 条")
    print()

    for k, e, old, new, why in all_changes[:args.limit]:
        print(f"[{why}] key={k}")
        print(f"  EN: {e[:100]}")
        print(f"  旧: {old[:100]}")
        print(f"  新: {new[:100]}")
        print()
    if len(all_changes) > args.limit:
        print(f"... 还有 {len(all_changes) - args.limit} 条")

    if args.dry_run:
        print("\n[预演] 未写入任何文件")
        return 0

    if not all_changes:
        print("无变化")
        return 0

    # 备份
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    bak = PATCH_DIR / f"zh-Hans-final.json.bak-{ts}"
    shutil.copy2(ZH_FILE, bak)
    print(f"\n已备份: {bak}")

    save(ZH_FILE, zh)
    print(f"已写入: {ZH_FILE}")

    # 同步到应用目录
    APP_ZH.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ZH_FILE, APP_ZH)
    print(f"已同步到应用: {APP_ZH}")

    print("\n完成。重启 Claude 生效。")
    return 0

if __name__ == "__main__":
    sys.exit(main())