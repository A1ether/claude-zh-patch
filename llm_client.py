#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""统一的对话调用封装：同一份调用代码适配 OpenAI 兼容 / Anthropic 两种协议。

各翻译脚本只需要：
    from llm_client import chat
    text = chat(system_prompt, user_prompt, temperature=0.15)

端点、模型、Key 由 llm_endpoint 从环境变量解析。
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

import llm_endpoint as E


def chat(system: str, user: str, *, temperature: float = 0.15,
         timeout: float = 180, max_tokens: int | None = None) -> str:
    """发送一次对话，返回纯文本。失败抛 RuntimeError / ValueError。"""
    url, model = E.discover()
    proto = E.protocol()
    headers = {"Content-Type": "application/json", **E.auth_headers(proto)}

    if proto == "anthropic":
        payload = {
            "model": model,
            "max_tokens": int(max_tokens or
                              os.environ.get("CLAUDE_ZH_MAX_TOKENS", "8192")),
            "temperature": temperature,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
    else:
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "response_format": {"type": "json_object"},
        }

    req = urllib.request.Request(
        url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = json.load(r)
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "ignore")[:300]
        except Exception:
            pass
        raise RuntimeError(f"{url} → HTTP {e.code} {e.reason} {detail}".strip())
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise RuntimeError(f"{url} → {e}")

    if proto == "anthropic":
        blocks = body.get("content") or []
        text = "".join(b.get("text", "") for b in blocks
                       if isinstance(b, dict) and b.get("type") == "text")
        if not text:
            raise ValueError(f"Anthropic 返回内容为空: {str(body)[:200]}")
        return text

    try:
        return body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise ValueError(f"响应格式异常: {str(body)[:200]}")