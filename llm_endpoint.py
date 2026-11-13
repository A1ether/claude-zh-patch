#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LLM 端点解析（本地网关自动探测 / 环境变量显式指定）

默认行为：并行探测常见本地 OpenAI 兼容端口，返回第一个可用端点与模型。
换一台机器时不需要改代码，任选其一：

  export CLAUDE_ZH_API=http://127.0.0.1:1234/v1          # base URL
  export CLAUDE_ZH_API=http://host:8000/v1/chat/completions  # 完整 URL 也可以
  export CLAUDE_ZH_MODEL=qwen2.5-7b-instruct
  export CLAUDE_ZH_KEY=sk-xxx                          # 需要鉴权时

其他可调项：
  CLAUDE_ZH_PORTS=1234,8080     覆盖探测端口列表
  CLAUDE_ZH_PROBE_TIMEOUT=0.8   单端口探测超时（秒）
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

# 常见本地网关端口（并行探测，顺序只影响打印）
PORTS = [17987, 3425, 8787, 7864, 1234, 8080, 8000, 11434, 5000, 3000]
# 模型候选（按优先级）；探测不到列表时按顺序试
MODEL_CANDIDATES = [
    "glm-5.3", "workbuddy/deepseek-v4.1-flash", "deepseek-v4.1-flash",
    "workbuddy/glm-5.3-flash", "glm-5.3-flash",
    "workbuddy/hy3", "hy3", "auto", "default",
]

_CACHE: tuple[str, str] | None = None


def _ports() -> list[int]:
    raw = os.environ.get("CLAUDE_ZH_PORTS")
    if not raw:
        return PORTS
    out = []
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            out.append(int(part))
    return out or PORTS


def _timeout() -> float:
    try:
        return max(0.1, float(os.environ.get("CLAUDE_ZH_PROBE_TIMEOUT", "0.6")))
    except ValueError:
        return 0.6


def _normalize_base(api: str) -> str:
    """把用户给的 base / 完整 URL 统一成 .../v1/chat/completions。"""
    api = api.strip().rstrip("/")
    if api.endswith("/chat/completions"):
        return api
    if api.endswith("/v1"):
        return api + "/chat/completions"
    return api + "/v1/chat/completions"


def auth_headers() -> dict:
    key = os.environ.get("CLAUDE_ZH_KEY") or os.environ.get("CLAUDE_ZH_API_KEY")
    return {"Authorization": f"Bearer {key}"} if key else {}


def _probe(port: int, timeout: float) -> list[str] | None:
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/models", headers=auth_headers())
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.load(r)
        return [m.get("id") for m in d.get("data", []) if m.get("id")]
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        return None


def _pick_model(ids: list[str]) -> str:
    for cand in MODEL_CANDIDATES:
        if cand in ids:
            return cand
    return next((i for i in ids if not i.startswith("group/")), ids[0])


def discover(verbose: bool = False) -> tuple[str, str]:
    """返回 (chat_completions_url, model)。找不到时抛 RuntimeError。"""
    global _CACHE
    if _CACHE:
        return _CACHE

    timeout = _timeout()

    # 1) 显式指定 base URL（只有 API 没指定模型时，仍尝试自动取模型）
    env_api = os.environ.get("CLAUDE_ZH_API")
    env_model = os.environ.get("CLAUDE_ZH_MODEL")
    if env_api:
        base = _normalize_base(env_api)
        model = env_model
        if not model:
            root = base.rsplit("/v1/", 1)[0]
            try:
                with urllib.request.urlopen(
                        urllib.request.Request(root + "/v1/models",
                                               headers=auth_headers()),
                        timeout=timeout * 4) as r:
                    ids = [m.get("id") for m in json.load(r).get("data", [])
                           if m.get("id")]
                model = _pick_model(ids) if ids else MODEL_CANDIDATES[0]
            except Exception:
                model = MODEL_CANDIDATES[0]
        _CACHE = (base, model)
        if verbose:
            print(f"[LLM] 使用 CLAUDE_ZH_API: {base}  模型 {model}")
        return _CACHE

    # 2) 并行探测本地端口
    ports = _ports()
    with ThreadPoolExecutor(max_workers=min(8, len(ports))) as ex:
        results = list(ex.map(lambda p: (p, _probe(p, timeout)), ports))
    tried = [p for p, ids in results if not ids]
    for port, ids in results:
        if not ids:
            continue
        model = env_model or _pick_model(ids)
        if env_model and env_model not in ids:
            if verbose:
                print(f"[LLM] 警告: 指定模型 {env_model} 不在 {port} 的模型列表中，仍然使用")
        _CACHE = (f"http://127.0.0.1:{port}/v1/chat/completions", model)
        if verbose:
            print(f"[LLM] 使用端口 {port}, 模型 {model}")
        return _CACHE

    raise RuntimeError(
        "未找到可用的本地模型端点。已尝试端口: "
        + ", ".join(str(p) for p in tried)
        + "\n  · 指定端点: export CLAUDE_ZH_API=http://127.0.0.1:<port>/v1"
        + "\n  · 指定模型: export CLAUDE_ZH_MODEL=<model>"
    )


if __name__ == "__main__":
    print(discover(verbose=True))
