#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Claude UI 文案 LLM 重译引擎
只处理「未翻译」+「可疑短译」条目，分批调用本地模型，带占位符校验与断点续传。
用法:
  python3 retranslate.py --prepare        # 生成待译清单
  python3 retranslate.py --run            # 执行重译（可中断续跑）
  python3 retranslate.py --apply          # 合并结果到 zh-Hans-final.json
"""
from __future__ import annotations
import argparse, json, os, re, sys, time, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import claude_paths as P

PATCH_DIR = P.PATCH_DIR
ZH_FILE = P.ZH_SRC
EN_FILE = P.require_en()
QUEUE = PATCH_DIR / "translate_queue.json"
DONE = PATCH_DIR / "translate_done.json"

from llm_endpoint import discover as _discover
try:
    API, MODEL = _discover()  # 自动探测可用本地端点
except RuntimeError as e:
    print("✗ " + str(e))
    raise SystemExit(1)
BATCH = 30

# ---------------- 术语表（注入 prompt） ----------------
GLOSSARY = """必须遵守的术语表（英文→中文）：
PR / Pull Request → PR（不要译作「公关」）
Branch → 分支（不要译作「分支机构」）
Run / Running → 运行 / 运行中（不要译作「跑步」）
Chrome → Chrome（不要译作「铬」）
Commit → 提交；Push → 推送；Merge → 合并；Repository → 仓库
Settings → 设置；Workspace → 工作区；Artifact(s) → 制品
Scheduled → 已定时；Customize → 自定义；Task → 任务；Session → 会话
Agent → 智能体；Prompt → 提示词；Token → 令牌；Model → 模型
Skill → 技能；Plugin → 插件；Connector → 连接器；Routine → 例程
Cowork / Code / Gateway / Claude / Anthropic / MCP / API / CLI / SDK → 保持原文
Button/label 类短语用简洁动宾结构：New→新建、Off→关闭、On→打开"""

SYSTEM_PROMPT = f"""你是资深软件 UI 中文本地化专家，为 Claude 桌面版做界面文案翻译。

{GLOSSARY}

翻译要求：
1. 输出自然、简洁、符合中文用户习惯的界面用语，不要生硬直译
2. 按钮/菜单/标签类：简洁（2-6 字），用动宾结构
3. 提示/错误信息：通顺的中文句子，语气专业克制
4. 保留原文中的占位符原样（如 {{count}}、{{name}}、{{link}}、@{{login}}），一个都不能少、不能改
5. 保留 HTML 标签（如 <b>、</b>）原样
6. 不要翻译品牌/产品名：Claude、Anthropic、Cowork、Chrome、GitHub、MCP、API 等
7. 不要添加引号、不要解释、不要加"译文："前缀

严格输出 JSON 对象，key 用传入的 id，value 是译文。只输出 JSON，不要其他任何文字。"""

# ---------------- 工具函数 ----------------
PH_RE = re.compile(r"\{[a-zA-Z_][a-zA-Z0-9_]*(?:,[^{}]*)?\}|@\{[a-zA-Z_]+\}")
TAG_RE = re.compile(r"</?[a-zA-Z][a-zA-Z0-9]*>")

def placeholders(s: str) -> set:
    """提取简单占位符（非 ICU 复合），用于校验完整性"""
    out = set()
    # 只提取不带逗号的简单 {var}
    for m in re.finditer(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", s or ""):
        out.add(m.group(1))
    for m in re.finditer(r"@\{([a-zA-Z_]+)\}", s or ""):
        out.add("@" + m.group(1))
    return out

def tags(s: str) -> list:
    return TAG_RE.findall(s or "")

def has_icu(s: str) -> bool:
    return bool(re.search(r"\{\s*\w+\s*,\s*(plural|select|selectordinal)\s*,", s or ""))

def needs_work(v: str, en: str) -> bool:
    """判断是否值得重译"""
    if not isinstance(v, str) or not v.strip():
        return False
    if has_icu(en):   # ICU 复合结构交给 retranslate_icu.py
        return False

    # ---- 只排除「确实不该翻译」的，其余一律翻译 ----
    e = en.strip()
    # 1) 纯路径 / URL / 协议 / JSON / 命令行参数
    if re.match(r"^(~?/|[A-Za-z]:\\|[\"']\[|[\"'][-]{0,2}[a-z])", e):
        return False
    if re.match(r"^(https?|claude|api|file)://", e):
        return False
    # 2) 纯扩展名/格式名：Word (.docx)、Latin-1 (ISO-8859-1)
    if re.fullmatch(r"[A-Za-z0-9 .\-]+\([^)]*\)", e):
        return False
    # 3) 纯标识符：X-Header-Name、snake_case、context,repos,issues
    core = PH_RE.sub("", TAG_RE.sub("", e)).strip()
    if not re.search(r"[A-Za-z]{2,}\s+[A-Za-z]{2,}", core):
        # 单个词：仅当是动作词/常见UI词才译，否则跳过（品牌、标识符）
        return False
    if "/" in core and len(re.findall(r"[A-Za-z]{2,}", core)) <= 3:
        return False

    # 以下都是「该翻译但没译」的情形
    # A. 完全未翻译
    if v.strip() == e:
        return True
    # B. 中文为 0（漏译）
    if len(re.findall(r"[\u4e00-\u9fa5]", v)) == 0 and len(re.findall(r"[A-Za-z]", v)) >= 8:
        return True
    # C. 可疑单字
    if len(v.strip()) == 1 and re.match(r"^[\u4e00-\u9fa5]$", v.strip()):
        if e in {"New", "Delete", "Add", "Edit", "Save", "Cancel", "Close", "Open",
                 "Retry", "Submit", "Apply", "Reset", "Back", "Next", "Done", "Skip",
                 "Copy", "Paste", "Cut", "Clear", "Search", "Filter", "Sort", "Refresh",
                 "Share", "Remove", "Update", "Upload", "Download", "Send", "Stop",
                 "Start", "Pause", "Resume", "Run", "Off", "On", "Go", "Push", "Pull"}:
            return True
    return False

def prepare():
    zh = json.loads(ZH_FILE.read_text(encoding="utf-8"))
    en = json.loads(EN_FILE.read_text(encoding="utf-8"))
    queue = []
    # 以 en-US 为基准遍历：凡译文为空/等于英文，且值得翻译的，都进队列
    for k, e in en.items():
        v = zh.get(k)
        if not isinstance(v, str) or not v.strip():
            v = e            # 缺失 → 视为未翻译
        if needs_work(v, e):
            queue.append({"id": k, "en": e.strip()})
    QUEUE.write_text(json.dumps(queue, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"待重译条目: {len(queue)}")
    print(f"清单已写入: {QUEUE}")
    from collections import Counter
    c = Counter()
    for q in queue:
        c[" ".join(q["en"].split()[:2])] += 1
    print("\n高频开头:")
    for w, n in c.most_common(12):
        print(f"  {w:<32} {n}")
    return 0

def call_llm(batch: list) -> dict:
    items = [{"id": it["id"], "en": it["en"]} for it in batch]
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(items, ensure_ascii=False, indent=1)},
        ],
        "temperature": 0.15,
        "response_format": {"type": "json_object"},
    }
    req = urllib.request.Request(
        API, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        body = json.load(r)
    content = body["choices"][0]["message"]["content"].strip()
    # 容错：剥掉可能的 ```json 包裹
    content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip())
    # 找第一个 { 到最后一个 }
    s, e = content.find("{"), content.rfind("}")
    if s >= 0 and e > s:
        content = content[s:e+1]
    return json.loads(content)

def verify(it: dict, translated: str) -> tuple[bool, str]:
    """校验占位符与 HTML 标签完整性"""
    en = it["en"]
    t = translated or ""
    if not t.strip():
        return False, "空译文"
    # 占位符必须一致
    if placeholders(en) != placeholders(t):
        return False, f"占位符不匹配 {placeholders(en)} vs {placeholders(t)}"
    # HTML 标签必须一致
    if sorted(tags(en)) != sorted(tags(t)):
        return False, "HTML 标签不匹配"
    # 不能原样返回英文
    if t.strip() == en.strip():
        return False, "未产生翻译"
    return True, "ok"

def run():
    if not QUEUE.exists():
        print("✗ 请先运行 --prepare"); return 1
    queue = json.loads(QUEUE.read_text(encoding="utf-8"))
    done = json.loads(DONE.read_text(encoding="utf-8")) if DONE.exists() else {}

    todo = [q for q in queue if q["id"] not in done]
    print(f"总待译: {len(queue)}  已完成: {len(done)}  剩余: {len(todo)}")
    if not todo:
        print("全部完成"); return 0

    total = len(todo)
    ok_cnt = fail_cnt = 0
    t0 = time.time()

    for i in range(0, len(todo), BATCH):
        batch = todo[i:i+BATCH]
        idx = i // BATCH + 1
        nbatch = (len(todo) + BATCH - 1) // BATCH
        print(f"\n[{idx}/{nbatch}] 批次 {len(batch)} 条 ...", flush=True)
        try:
            res = call_llm(batch)
        except Exception as e:
            print(f"  ✗ 调用失败: {type(e).__name__}: {str(e)[:120]}")
            fail_cnt += len(batch)
            time.sleep(2)
            continue

        for it in batch:
            tr = res.get(it["id"])
            if tr is None:
                fail_cnt += 1
                continue
            good, why = verify(it, tr)
            if good:
                done[it["id"]] = tr
                ok_cnt += 1
            else:
                fail_cnt += 1
                if fail_cnt <= 5:
                    print(f"  ⚠ {it['id']}: {why} | EN={it['en'][:50]}")

        # 每批保存（断点续传）
        DONE.write_text(json.dumps(done, ensure_ascii=False, indent=2), encoding="utf-8")
        elapsed = time.time() - t0
        speed = (ok_cnt + fail_cnt) / elapsed * 60 if elapsed > 0 else 0
        print(f"  累计: 成功 {ok_cnt} / 失败 {fail_cnt} | {speed:.0f} 条/分", flush=True)

    print(f"\n{'='*60}")
    print(f"完成: 成功 {ok_cnt}, 失败 {fail_cnt}")
    print(f"结果: {DONE}")
    print(f"运行 --apply 合并到翻译文件")
    return 0

def apply():
    if not DONE.exists():
        print("✗ 无重译结果"); return 1
    done = json.loads(DONE.read_text(encoding="utf-8"))
    zh = json.loads(ZH_FILE.read_text(encoding="utf-8"))
    en = json.loads(EN_FILE.read_text(encoding="utf-8"))

    changed = new_keys = 0
    for k, v in done.items():
        if k not in en:
            continue            # 已不存在的 key 跳过
        if zh.get(k) == v:
            continue            # 无变化
        # 二次校验（防止损坏）
        good, why = verify({"id": k, "en": en.get(k, "")}, v)
        if not good:
            continue
        if k not in zh:
            new_keys += 1
        zh[k] = v
        changed += 1

    # 以最新 en-US 为基座补齐缺失 key（保持英文占位，避免运行时缺 key）
    missing = 0
    for k, e in en.items():
        if k not in zh:
            zh[k] = e
            missing += 1

    import shutil
    from datetime import datetime
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    bak = PATCH_DIR / f"zh-Hans-final.json.bak-{ts}"
    shutil.copy2(ZH_FILE, bak)
    print(f"已备份: {bak}")

    ZH_FILE.write_text(json.dumps(zh, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已更新 {changed} 条（其中新 key {new_keys}）→ {ZH_FILE}")
    if missing:
        print(f"已补齐 {missing} 个缺失 key（英文占位）")

    app_zh = P.app_zh_file()
    app_zh.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ZH_FILE, app_zh)
    print(f"已同步应用: {app_zh}")
    return 0

def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--prepare", action="store_true")
    g.add_argument("--run", action="store_true")
    g.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    if args.prepare: return prepare()
    if args.run: return run()
    if args.apply: return apply()

if __name__ == "__main__":
    sys.exit(main())