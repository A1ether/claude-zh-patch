#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
本地 LLM 端点自动探测
扫描常见端口，返回第一个可用的 OpenAI 兼容端点与模型名。
供 retranslate.py / retranslate_icu.py 复用。
"""
from __future__ import annotations
import json, os, urllib.request

# 常见本地网关端口（按优先级）
PORTS = [17987, 3425, 8787, 7864, 1234, 8080, 11434]
# 模型候选（按优先级），* 表示可前缀
MODEL_CANDIDATES = [
    "glm-5.3", "workbuddy/deepseek-v4.1-flash", "deepseek-v4.1-flash",
    "workbuddy/glm-5.3-flash", "glm-5.3-flash",
    "workbuddy/hy3", "hy3", "auto", "default",
]

_CACHE = None

def _probe(port: int, timeout: float = 2.0):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/models", timeout=timeout) as r:
            d = json.load(r)
        ids = [m.get("id") for m in d.get("data", []) if m.get("id")]
        return ids
    except Exception:
        return None

def discover(verbose: bool = False) -> tuple[str, str]:
    """返回 (base_url, model)。找不到时抛 RuntimeError。"""
    global _CACHE
    if _CACHE:
        return _CACHE
    env_api = os.environ.get("CLAUDE_ZH_API")
    env_model = os.environ.get("CLAUDE_ZH_MODEL")
    if env_api and env_model:
        _CACHE = (env_api, env_model)
        return _CACHE

    tried = []
    for port in PORTS:
        ids = _probe(port)
        tried.append((port, bool(ids)))
        if not ids:
            continue
        base = f"http://127.0.0.1:{port}/v1/chat/completions"
        # 优先用候选里能匹配上的
        for cand in MODEL_CANDIDATES:
            if cand in ids:
                _CACHE = (base, cand)
                if verbose:
                    print(f"[LLM] 使用端口 {port}, 模型 {cand}")
                return _CACHE
        # 退路：用第一个非 group 模型
        fallback = next((i for i in ids if not i.startswith("group/")), ids[0])
        _CACHE = (base, fallback)
        if verbose:
            print(f"[LLM] 使用端口 {port}, 模型 {fallback}（回退）")
        return _CACHE

    raise RuntimeError(
        "未找到可用的本地模型端点。已尝试端口: "
        + ", ".join(f"{p}{'(开)' if ok else ''}" for p, ok in tried)
        + "\n可设置环境变量 CLAUDE_ZH_API / CLAUDE_ZH_MODEL 手动指定。"
    )

if __name__ == "__main__":
    print(discover(verbose=True))