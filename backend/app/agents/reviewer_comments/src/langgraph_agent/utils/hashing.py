"""稳定哈希工具。

对齐 backend/app/graphs/finalize_graph.py 中的 ``_stable_hash``：
对可 JSON 序列化对象做键排序后的 SHA-256 摘要。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def stable_json(value: Any) -> str:
    """将对象编码为键排序、无多余空白的稳定 JSON 字符串。"""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def stable_hash(value: Any) -> str:
    """对可 JSON 序列化对象做稳定 SHA-256 十六进制摘要。

    键顺序无关：``{"a":1,"b":2}`` 与 ``{"b":2,"a":1}`` 得到相同摘要。
    """
    encoded = stable_json(value).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
