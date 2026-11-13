#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LLM 端点解析：协议 / Base URL / 模型 / 鉴权

支持两种协议（GUI 设置里可选）：
  openai     OpenAI 兼容（/v1/chat/completions，含 Ollama、LM Studio、各类网关）
  anthropic  Anthropic Messages API（/v1/messages）

环境变量（GUI 会把设置写成这些变量再启动子进程）：
  CLAUDE_ZH_PROTOCOL   openai | anthropic（默认 openai）
  CLAUDE_ZH_API        Base URL，如 http://127.0.0.1:1234/v1；
                       直接给完整 chat 地址也会被归一化
  CLAUDE_ZH_MODEL      模型名
  CLAUDE_ZH_KEY        API Key（openai 用 Bearer，anthropic 用 x-api-key）

未显式配置时：并行探测常见本地 OpenAI 兼容端口。
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

PROTOCOLS = {"openai": "OpenAI 兼容", "anthropic": "Anthropic"}
DEFAULT_PROTOCOL = "openai"

# 常见本地网关端口（并行探测，顺序只影响打印）
PORTS = [17987, 3425, 8787, 7864, 1234, 8080, 8000, 11434, 5000, 3000]
# 探测不到模型列表时按顺序试
MODEL_CANDIDATES = [
    "workbuddy/deepseek-v4.1-flash", "workbuddy/deepseek-v4.1-flash",
    "deepseek-v4.1-flash",
    "glm-5.3", "workbuddy/glm-5.3-flash", "workbuddy/glm-5.3-flash", "glm-5.3-flash",
    "hy3", "workbuddy/hy3", "workbuddy/hy3", "auto", "default",
]

_CACHE: tuple[str, str] | None = None


# ---------------------------------------------------------------- 基础

def protocol() -> str:
    p = (os.environ.get("CLAUDE_ZH_PROTOCOL") or "").strip().lower()
    return p if p in PROTOCOLS else DEFAULT_PROTOCOL


def api_key() -> str:
    return os.environ.get("CLAUDE_ZH_KEY") or os.environ.get("CLAUDE_ZH_API_KEY") or ""


def auth_headers(proto: str | None = None) -> dict:
    key = api_key()
    if not key:
        return {}
    if (proto or protocol()) == "anthropic":
        return {"x-api-key": key, "anthropic-version": "2023-06-01"}
    return {"Authorization": f"Bearer {key}"}


def normalize_base(api: str) -> str:
    """把 base URL 归一化成 .../v1（去掉可能误填的 chat 路径）。"""
    api = (api or "").strip().rstrip("/")
    for suffix in ("/chat/completions", "/messages"):
        if api.endswith(suffix):
            api = api[: -len(suffix)]
    if api.endswith("/v1"):
        return api
    return api + "/v1"


def chat_url(base: str, proto: str | None = None) -> str:
    base = normalize_base(base)
    return base + ("/messages" if (proto or protocol()) == "anthropic"
                   else "/chat/completions")


def models_url(base: str) -> str:
    return normalize_base(base) + "/models"


def _timeout() -> float:
    try:
        return max(0.1, float(os.environ.get("CLAUDE_ZH_PROBE_TIMEOUT", "0.6")))
    except ValueError:
        return 0.6


# ---------------------------------------------------------------- 模型列表

def fetch_models(base: str | None = None, proto: str | None = None,
                 timeout: float = 10.0) -> list[str]:
    """请求 {base}/models，返回模型 id 列表。失败抛 RuntimeError。"""
    proto = proto or protocol()
    base = base or os.environ.get("CLAUDE_ZH_API") or "http://127.0.0.1:1234/v1"
    url = models_url(base)
    req = urllib.request.Request(url, headers=auth_headers(proto))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "ignore")[:200]
        except Exception:
            pass
        raise RuntimeError(f"{url} → HTTP {e.code} {e.reason} {detail}".strip())
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise RuntimeError(f"{url} → {e}")

    items = data.get("data") if isinstance(data, dict) else data
    ids = []
    for m in items or []:
        if isinstance(m, dict):
            mid = m.get("id") or m.get("name")
        else:
            mid = m
        if isinstance(mid, str) and mid:
            ids.append(mid)
    if not ids:
        raise RuntimeError(f"{url} 未返回模型列表")
    return ids


def _pick_model(ids: list[str]) -> str:
    for cand in MODEL_CANDIDATES:
        if cand in ids:
            return cand
    return next((i for i in ids if not i.startswith("group/")), ids[0])


# ---------------------------------------------------------------- 端点解析

def _probe(port: int, timeout: float) -> list[str] | None:
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/models",
                                     headers=auth_headers("openai"))
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.load(r)
        return [m.get("id") for m in d.get("data", []) if m.get("id")]
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        return None


def discover(verbose: bool = False) -> tuple[str, str]:
    """返回 (chat_completions_or_messages_url, model)。找不到时抛 RuntimeError。"""
    global _CACHE
    if _CACHE:
        return _CACHE

    proto = protocol()
    env_api = os.environ.get("CLAUDE_ZH_API")
    env_model = os.environ.get("CLAUDE_ZH_MODEL")

    # 1) 显式配置
    if env_api:
        base = normalize_base(env_api)
        model = env_model
        if not model:
            try:
                ids = fetch_models(base, proto, timeout=max(2.0, _timeout() * 4))
                model = _pick_model(ids)
            except RuntimeError:
                model = MODEL_CANDIDATES[0]
        _CACHE = (chat_url(base, proto), model)
        if verbose:
            print(f"[LLM] {PROTOCOLS[proto]}  {_CACHE[0]}  模型 {model}")
        return _CACHE

    # 2) 自动探测本地 OpenAI 兼容端口
    ports = _ports()
    with ThreadPoolExecutor(max_workers=min(8, len(ports))) as ex:
        results = list(ex.map(lambda p: (p, _probe(p, _timeout())), ports))
    tried = [p for p, ids in results if not ids]
    for port, ids in results:
        if not ids:
            continue
        model = env_model or _pick_model(ids)
        _CACHE = (chat_url(f"http://127.0.0.1:{port}/v1", "openai"), model)
        if verbose:
            print(f"[LLM] 使用端口 {port}, 模型 {model}")
        return _CACHE

    raise RuntimeError(
        "未找到可用的模型端点。已尝试端口: "
        + ", ".join(str(p) for p in tried)
        + "\n  · 在设置里填写 Base URL / API Key 并选择模型，或设置环境变量："
        + "\n    CLAUDE_ZH_PROTOCOL=openai CLAUDE_ZH_API=http://127.0.0.1:<port>/v1 "
        + "CLAUDE_ZH_MODEL=<model>"
    )


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


if __name__ == "__main__":
    print(discover(verbose=True))