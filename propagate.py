#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
同值传播补译器
Claude 升级后新增的 key 往往与旧 key 的英文原文完全相同（同一文案的多个 key）。
本脚本按「英文原文」建立 英文→已有译文 的映射，把旧译文传播到新 key 上。

用法:
  python3 propagate.py --dry-run   # 预演
  python3 propagate.py             # 执行
"""
from __future__ import annotations
import argparse, json, re, shutil, sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import claude_paths as P

PATCH_DIR = P.PATCH_DIR
ZH_FILE = P.ZH_SRC
EN_FILE = P.require_en()
APP_ZH = P.app_zh_file()

def should_translate(en_text: str) -> bool:
    """过滤掉不该翻译的（纯路径/URL/标识符/占位符）"""
    s = en_text or ""
    # 纯路径 / URL / 协议 / JSON / 命令行参数
    if re.match(r"^(~?/|[A-Za-z]:\\|[\"']\[|[\"'][-]{0,2}[a-z])", s.strip()):
        return False
    if re.match(r"^(https?|claude|api|file)://", s.strip()):
        return False
    # 纯格式名 Word (.docx)
    if re.fullmatch(r"[A-Za-z0-9 .\-]+\([^)]*\)", s.strip()):
        return False
    s_clean = re.sub(r"\{[^{}]*\}|</?[a-zA-Z][a-zA-Z0-9]*>", "", s).strip()
    if not s_clean:
        return False
    # 只剩下技术标识符（无空格且含 - _ : 等）
    words = re.findall(r"[A-Za-z]{2,}", s_clean)
    if not words:
        return False
    if re.fullmatch(r"[A-Za-z0-9_.\-/{}:@]+", s_clean) and len(words) <= 2 and " " not in s_clean:
        return False
    return True

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    # 用升级后的 en-US 作为基准（apply.sh 已同步到 zh-Hans-final.json 的 key 集）
    en = json.loads(EN_FILE.read_text(encoding="utf-8"))

    # 1) 建立「英文原文 → 已有译文」语料库
    #    来源：当前 zh 文件 + 所有历史备份；按 en 映射取已翻译项
    corpus = defaultdict(set)
    sources = []
    if ZH_FILE.exists():
        sources.append(ZH_FILE)
    baks = sorted(PATCH_DIR.glob("zh-Hans-final.json.bak-*"))
    sources += baks
    if not baks:
        print("  · 未发现历史备份（zh-Hans-final.json.bak-*），同值传播只用当前文件")
        print("    新克隆的仓库通常是这种情况：功能正常，只是命中率略低")
    for src in sources:
        try:
            d = json.loads(Path(src).read_text(encoding="utf-8"))
        except Exception:
            continue
        for k, v in d.items():
            e = en.get(k)
            if not e or not isinstance(v, str):
                continue
            if v.strip() and v.strip() != e.strip() and should_translate(e):
                corpus[e.strip()].add(v.strip())

    # 2) 当前文件
    zh = json.loads(ZH_FILE.read_text(encoding="utf-8"))

    filled = []
    conflicts = []
    for k, e in en.items():
        cur = zh.get(k, e)
        if cur != e:
            continue   # 已有译文
        if not should_translate(e):
            continue
        cands = corpus.get(e.strip())
        if not cands:
            continue
        if len(cands) == 1:
            filled.append((k, e, next(iter(cands))))
        else:
            # 多候选：取最短且无「待翻译」标记的
            best = sorted(cands, key=lambda x: (("待翻译" in x), len(x)))[0]
            filled.append((k, e, best))
            conflicts.append((k, e, cands))

    print("=" * 68)
    print(" 同值传播补译" + ("（预演 DRY-RUN）" if args.dry_run else ""))
    print("=" * 68)
    print(f"语料来源: {len(sources)} 个文件（当前 + 历史备份）")
    print(f"英文原文→译文 映射数: {len(corpus)}")
    print(f"可传播填补: {len(filled)} 条  (其中多候选 {len(conflicts)} 条)")
    print()

    shown = defaultdict(int)
    for k, e, t in filled:
        key0 = e[:40]
        if shown[key0] < 1 and shown["_n"] < 22:
            shown[key0] += 1; shown["_n"] += 1
            print(f"  {k}")
            print(f"    EN: {e[:80]}")
            print(f"    ZH: {t[:80]}")

    # 剩余未译统计
    still = [k for k, e in en.items()
             if zh.get(k, e) == e and should_translate(e) and e.strip() not in corpus]
    print(f"\n传播后仍需 LLM 重译: {len(still)} 条")

    if args.dry_run:
        print("\n[预演] 未写入")
        return 0
    if not filled:
        print("无可传播条目"); return 0

    for k, e, t in filled:
        zh[k] = t

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    shutil.copy2(ZH_FILE, PATCH_DIR / f"zh-Hans-final.json.bak-{ts}")
    ZH_FILE.write_text(json.dumps(zh, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    APP_ZH.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ZH_FILE, APP_ZH)
    print(f"\n✓ 已补译 {len(filled)} 条 → {ZH_FILE}")
    print(f"✓ 已同步应用: {APP_ZH}")
    return 0

if __name__ == "__main__":
    sys.exit(main())