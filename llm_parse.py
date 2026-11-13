#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""解析 LLM 返回的 JSON。

模型经常把一批结果拆成多个对象（`{...}{...}` 或换行分隔），甚至混入说明文字，
直接 json.loads 会报 "Extra data: line 4 column 3"。这里用 raw_decode 逐个提取
所有对象/数组并合并，坏片段跳过，最大程度保住已经翻好的条目。
"""
from __future__ import annotations

import json


def _merge_into(out: dict, obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str):
                out[str(k)] = v
    elif isinstance(obj, list):
        for item in obj:
            if isinstance(item, dict):
                for k, v in item.items():
                    if isinstance(v, str):
                        out[str(k)] = v


def parse_objects(text: str) -> dict:
    """从任意文本里提取所有 JSON 对象，合并成一个 {id: 译文}。"""
    dec = json.JSONDecoder()
    out: dict = {}
    i, n = 0, len(text or "")
    while i < n:
        candidates = [x for x in (text.find("{", i), text.find("[", i)) if x >= 0]
        if not candidates:
            break
        start = min(candidates)
        try:
            obj, end = dec.raw_decode(text, start)
        except json.JSONDecodeError:
            i = start + 1
            continue
        _merge_into(out, obj)
        i = end
    return out