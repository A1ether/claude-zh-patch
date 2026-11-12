#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Claude 中文本地化质量扫描器
检测: 术语误译 / 直译单字 / 占位符缺失 / 漏译 / 表达异常
用法: python3 scan_quality.py [--limit N] [--json out.json]
"""
from __future__ import annotations
import argparse, json, re, sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import claude_paths as P

PATCH_DIR = P.PATCH_DIR
ZH_FILE = P.ZH_SRC
EN_FILE = P.require_en()

# ---------- 规则定义 ----------

# 1) 严重术语误译（关键词命中即高危）
#    注意: 只保留「确实错误」的模式，避免误报正确译法
TERM_ERRORS = {
    r"公关": ("PR", "PR 被当成 Public Relations 误译"),
    r"跑步": ("Run", "Run 在软件语境应为「运行」"),
    r"铬(?![a-zA-Z])": ("Chrome", "Chrome 被译成化学元素「铬」"),
    r"分支机构": ("Branch", "Branch 应译「分支」而非「分支机构」"),
    r"杀死|杀掉": ("Kill", "应为「终止」"),
    r"主人分支": ("master", "应为「主分支」"),
}

# 2) 直译单字/过短译文（按钮/标签位置应为动宾短语）
TOO_SHORT_ZH = re.compile(r"^[\u4e00-\u9fa5]{1}$")
SUSPICIOUS_SHORT = {
    "新": "新建", "旧": "旧版", "删": "删除", "加": "添加",
    "开": "打开", "关": "关闭", "查": "查看", "改": "修改",
    "跑": "运行", "停": "停止", "存": "保存", "读": "读取",
}

# 3) 占位符模式（正确解析 ICU MessageFormat）
#    ICU 结构: {var}、{count, plural, one {..} other {..}}、{kind, select, a {..} other {..}}
#    只提取「变量名」，不提取分支内部的字面量
PLACEHOLDER_RE = re.compile(r"%\d*\$?[sd]|%\([^)]+\)[sd]")

def icu_placeholders(s: str) -> Counter:
    """提取 ICU MessageFormat 的变量名（含嵌套 select/plural 的嵌套变量）"""
    names = Counter()
    s = s or ""
    i, n = 0, len(s)
    while i < n:
        if s[i] != "{":
            i += 1
            continue
        # 找到匹配的右括号深度
        depth, j = 0, i
        while j < n:
            if s[j] == "{":
                depth += 1
            elif s[j] == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        if j >= n:
            break
        inner = s[i + 1:j]
        # 取第一段（逗号前）作为变量名，如 "count, plural, ..." -> "count"
        head = inner.split(",", 1)[0].strip()
        if head and not re.search(r"[\s{}#]", head):
            names[head] += 1
        # 若含 plural/select，继续递归扫描分支内部（找嵌套变量）
        if ", plural," in inner or ", select," in inner or ", selectordinal," in inner:
            rest = inner.split(",", 1)[1] if "," in inner else ""
            sub = icu_placeholders(rest)
            for k, v in sub.items():
                names[k] += 0  # 计入但不重复计数外层
                if k not in names or names[k] == 0:
                    names[k] = 1
        i = j + 1
    for m in PLACEHOLDER_RE.findall(s):
        names[m] += 1
    return names

def placeholders(s: str) -> Counter:
    return icu_placeholders(s)

# 4) 中文里夹杂的整句英文（疑似漏译）
MOSTLY_EN = re.compile(r"^[A-Za-z0-9\s\.,;:!\?'\"\(\)\-–—/&%\$#@\+=]{12,}$")

# 5) 常见生硬表达
AWKWARD = {
    r"请点击这里以": "请点击（冗赘）",
    r"的的|了了|是是": "重复字",
    r"将会被|将是会被": "冗赘被动",
    r"您可以通过点击": "可简化为「点击」",
    r"进行的?操作": "冗赘（「操作」多余）",
    r"一个的|这个的一个": "语序错误",
}

# 6) 品牌/技术名不应翻译（若被译则为误译）
BRAND_KEEP = ["Cowork", "Code", "Gateway", "Artifacts", "Claude", "Anthropic",
              "MCP", "API", "CLI", "SDK", "PR", "JSON", "YAML", "Git", "GitHub"]

def placeholders(s: str) -> Counter:
    return Counter(PLACEHOLDER_RE.findall(s or ""))

def has_chinese(s: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fa5]", s or ""))

def scan():
    zh = json.loads(ZH_FILE.read_text(encoding="utf-8"))
    en = json.loads(EN_FILE.read_text(encoding="utf-8"))

    findings = defaultdict(list)  # category -> [(key, en, zh, note)]

    for k, zval in zh.items():
        eval_ = en.get(k)
        if eval_ is None:
            continue
        zval = zval or ""
        eval_ = eval_ or ""
        if not eval_.strip():
            continue

        # --- A. 占位符不匹配（最高危，会导致运行时异常） ---
        pe, pz = placeholders(eval_), placeholders(zval)
        if pe != pz:
            missing = pe - pz
            extra = pz - pe
            if missing or extra:
                findings["占位符不匹配"].append(
                    (k, eval_, zval, f"缺失{sorted(missing)} 多余{sorted(extra)}"))

        # --- B. 术语误译 ---
        for pat, (eng, note) in TERM_ERRORS.items():
            if re.search(pat, zval):
                findings["术语误译"].append((k, eval_, zval, note))

        # --- C. 直译单字 ---
        if TOO_SHORT_ZH.match(zval.strip()):
            sugg = SUSPICIOUS_SHORT.get(zval.strip(), "")
            findings["疑似直译单字"].append(
                (k, eval_, zval, f"建议改为「{sugg}」" if sugg else "单字译文需人工确认"))

        # --- D. 漏译（译文与英文完全相同且含自然语言） ---
        if zval.strip() == eval_.strip() and not has_chinese(zval) and len(eval_) > 8:
            findings["疑似漏译"].append((k, eval_, zval, "与英文完全相同"))

        # --- E. 整句英文残留 ---
        if MOSTLY_EN.match(zval.strip()) and not has_chinese(zval):
            # 排除纯占位符/纯符号/品牌名
            stripped = PLACEHOLDER_RE.sub("", zval).strip()
            if len(stripped) > 15 and not any(b == stripped for b in BRAND_KEEP):
                findings["疑似未翻译整句"].append((k, eval_, zval, "整段仍是英文"))

        # --- F. 生硬表达 ---
        for pat, note in AWKWARD.items():
            if re.search(pat, zval):
                findings["生硬表达"].append((k, eval_, zval, note))

    return zh, en, findings

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=10, help="每类展示条数")
    ap.add_argument("--json", help="输出完整 JSON 到指定路径")
    ap.add_argument("--min-level", choices=["all", "serious"], default="all")
    args = ap.parse_args()

    zh, en, findings = scan()

    SERIOUS = {"占位符不匹配", "术语误译"}

    print("=" * 70)
    print(" Claude 中文本地化质量扫描报告")
    print("=" * 70)
    print(f"文件: {ZH_FILE}")
    print(f"总词条: {len(zh)}")
    print()

    total = 0
    for cat, items in sorted(findings.items(), key=lambda x: -len(x[1])):
        total += len(items)
        tag = "🔴" if cat in SERIOUS else "🟡"
        print(f"{tag} {cat}: {len(items)} 条")
    print(f"\n问题总数: {total}")

    print("\n" + "=" * 70)
    print(" 详细清单")
    print("=" * 70)
    for cat, items in sorted(findings.items(), key=lambda x: -len(x[1])):
        if args.min_level == "serious" and cat not in SERIOUS:
            continue
        print(f"\n### {cat} ({len(items)} 条)")
        for k, e, z, note in items[:args.limit]:
            print(f"  key={k}")
            print(f"    EN: {e[:150]}")
            print(f"    ZH: {z[:150]}")
            print(f"    → {note}")
        if len(items) > args.limit:
            print(f"  ... 还有 {len(items) - args.limit} 条")

    if args.json:
        out = {cat: [{"key": k, "en": e, "zh": z, "note": n} for k, e, z, n in items]
               for cat, items in findings.items()}
        Path(args.json).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n完整结果已写入: {args.json}")

    return 0

if __name__ == "__main__":
    sys.exit(main())