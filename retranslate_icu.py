#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ICU MessageFormat 条目重译器
处理含 {var, plural/select, ...} 复合结构的界面文案：
保留结构骨架与变量名，只翻译其中自然语言。
用法: python3 retranslate_icu.py [--dry-run]
"""
from __future__ import annotations
import argparse, json, re, shutil, sys, time, urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import claude_paths as P

PATCH_DIR = P.PATCH_DIR
ZH_FILE = P.ZH_SRC
EN_FILE = P.require_en()
APP_ZH = P.app_zh_file()
DONE_F = PATCH_DIR / "icu_done.json"

from llm_endpoint import auth_headers, discover as _discover
from llm_parse import parse_objects
try:
    API, MODEL = _discover()  # 自动探测可用本地端点
except RuntimeError as e:
    print("✗ " + str(e))
    raise SystemExit(1)
BATCH = 12   # ICU 结构复杂，批小一点

SYSTEM = """你是资深软件 UI 中文本地化专家，翻译 ICU MessageFormat 格式的界面文案。

【极重要】输入是 ICU MessageFormat，必须保持结构完全不变：
- 保留所有 {变量名}、{变量名, plural, ...}、{变量名, select, ...} 的结构
- 保留 plural/select 的所有分支键（one/other/=0/=1 等）原样
- 保留 # 号（plural 中的数字占位）原样
- 保留 <b>、</b>、<muted> 等 HTML 标签原样
- 每个分支 { } 内部的中文是你要翻译的部分

只翻译「自然语言文字」，例如：
  输入: Up to {agents, plural, one {# agent} other {# agents}} scheduled.
  输出: 最多可安排 {agents, plural, one {# 个智能体} other {# 个智能体}}。

  输入: The code expires in {minutes, plural, one {# minute} other {# minutes}}.
  输出: 验证码将在 {minutes, plural, one {# 分钟} other {# 分钟}}后失效。

  输入: {count, plural, one {# unread} other {# unread}}
  输出: {count, plural, one {# 条未读} other {# 条未读}}

术语: PR→PR（不是公关）、Branch→分支、Run→运行、Chrome→Chrome、Settings→设置、
Task→任务、Session→会话、Agent→智能体、Artifact→制品、Routine→例程、Cowork→Cowork

严格输出 JSON 对象（key=传入 id，value=译文），只输出 JSON，无其他文字。"""

def _parse_icu(s: str) -> dict:
    """严谨解析 ICU：返回 {'vars': [...], 'branches': [...], 'hashes': n, 'tags': [...]}"""
    vars_, branches = [], []
    i, n = 0, len(s or "")
    while i < n:
        ch = s[i]
        if ch != "{":
            i += 1
            continue
        # 找匹配右括号
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
        # 判断是简单变量 {var} 还是复合 {var, type, 分支...}
        parts = inner.split(",", 1)
        varname = parts[0].strip()
        if re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", varname):
            vars_.append(varname)
        # 复合结构：找类型后的分支
        if len(parts) > 1 and re.match(r"\s*(plural|select|selectordinal)\s*,", parts[1]):
            rest = parts[1].split(",", 1)[1] if "," in parts[1] else ""
            # 分支格式: key {content} key {content}
            # 只有「深度为 0」的 key 才是分支键（排除嵌套 {var} 内部）
            depth = 0
            buf = ""
            for ch2 in rest:
                if ch2 == "{":
                    # 前面的 buf 若形如 " one " / " =0 " / " other "，则是分支键
                    b = buf.strip()
                    if depth == 0 and b and re.fullmatch(r"=\d+|[a-zA-Z_][a-zA-Z0-9_]*", b.split()[-1] if b.split() else ""):
                        branches.append(b.split()[-1])
                    buf = ""
                    depth += 1
                elif ch2 == "}":
                    depth -= 1
                    buf = ""
                elif depth == 0:
                    buf += ch2
        i = j + 1
    return {
        "vars": tuple(vars_),
        "branches": tuple(sorted(branches)),
        "hashes": (s or "").count("#"),
        "tags": tuple(sorted(re.findall(r"</?[a-zA-Z][a-zA-Z0-9]*>", s or ""))),
    }

def structure(s: str) -> tuple:
    d = _parse_icu(s)
    return d["vars"], d["branches"], d["hashes"], d["tags"]

def verify(en: str, tr: str) -> tuple[bool, str]:
    if not tr or not tr.strip():
        return False, "空译文"
    ev, eb, eh, et = structure(en)
    tv, tb, th, tt = structure(tr)
    # 中文语序与英文不同，变量顺序可调换 —— 只比较「集合」是否一致
    if sorted(ev) != sorted(tv):
        return False, f"变量集合不匹配 {sorted(set(ev))} vs {sorted(set(tv))}"
    if eb != tb:
        return False, f"分支键不匹配 {eb} vs {tb}"
    if eh != th:
        return False, f"# 数量不匹配 {eh} vs {th}"
    if sorted(et) != sorted(tt):
        return False, f"HTML 标签不匹配 {et} vs {tt}"
    if tr.strip() == en.strip():
        return False, "未产生翻译"
    return True, "ok"

def call_llm(items: list) -> dict:
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": json.dumps(items, ensure_ascii=False, indent=1)},
        ],
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
    }
    req = urllib.request.Request(
        API, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **auth_headers()})
    with urllib.request.urlopen(req, timeout=240) as r:
        body = json.load(r)
    c = body["choices"][0]["message"]["content"].strip()
    c = re.sub(r"^```(?:json)?\s*|\s*```$", "", c)
    res = parse_objects(c)
    if not res:
        raise ValueError("模型未返回可解析的 JSON: " + c[:120].replace("\n", " "))
    return res

def collect():
    """收集待译 ICU 条目（以 en-US 为基准，排除路径/URL 类）"""
    zh = json.loads(ZH_FILE.read_text(encoding="utf-8"))
    en = json.loads(EN_FILE.read_text(encoding="utf-8"))
    out = []
    for k, e in en.items():
        if not e:
            continue
        v = zh.get(k)
        if isinstance(v, str) and v.strip() and v != e:
            continue        # 已有译文
        if not re.search(r"\b(?:plural|select|selectordinal)\s*,", e):
            continue
        # 排除纯技术串
        if re.match(r"^\s*\[?[\"'/(]|^\s*[A-Za-z]:\\|^\s*~?/", e):
            continue
        out.append({"id": k, "en": e.strip()})
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    todo_all = collect()
    done = json.loads(DONE_F.read_text(encoding="utf-8")) if DONE_F.exists() else {}
    todo = [t for t in todo_all if t["id"] not in done]

    print("=" * 66)
    print(f" ICU 条目重译  (共 {len(todo_all)} 条, 已完成 {len(done)}, 剩余 {len(todo)})")
    print("=" * 66)
    if not todo:
        print("全部完成"); return 0

    if args.dry_run:
        for t in todo[:8]:
            print(f"\nkey={t['id']}\n  {t['en'][:130]}")
        print(f"\n[预演] 共 {len(todo)} 条待译")
        return 0

    ok = fail = 0
    nb = (len(todo) + BATCH - 1) // BATCH
    for i in range(0, len(todo), BATCH):
        batch = todo[i:i+BATCH]
        print(f"\n[{i//BATCH+1}/{nb}] {len(batch)} 条 ...", flush=True)
        try:
            res = call_llm(batch)
        except Exception as e:
            print(f"  ✗ 调用失败: {type(e).__name__}: {str(e)[:100]}")
            fail += len(batch); time.sleep(2); continue

        for it in batch:
            tr = res.get(it["id"])
            if tr is None:
                fail += 1; continue
            good, why = verify(it["en"], tr)
            if good:
                done[it["id"]] = tr; ok += 1
            else:
                fail += 1
                if fail <= 6:
                    print(f"  ⚠ {why}\n     EN: {it['en'][:90]}\n     ZH: {str(tr)[:90]}")
        DONE_F.write_text(json.dumps(done, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  累计: 成功 {ok} / 失败 {fail}", flush=True)

    print(f"\n完成: 成功 {ok} 失败 {fail}")
    if ok and not args.dry_run:
        # 合并
        zh = json.loads(ZH_FILE.read_text(encoding="utf-8"))
        en = json.loads(EN_FILE.read_text(encoding="utf-8"))
        n = 0
        for k, v in done.items():
            good, _ = verify(en.get(k, ""), v)
            if good and k in zh and zh[k] != v:
                zh[k] = v; n += 1
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        shutil.copy2(ZH_FILE, PATCH_DIR / f"zh-Hans-final.json.bak-{ts}")
        ZH_FILE.write_text(json.dumps(zh, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        APP_ZH.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ZH_FILE, APP_ZH)
        print(f"已合并 {n} 条并同步应用")
    return 0

if __name__ == "__main__":
    sys.exit(main())