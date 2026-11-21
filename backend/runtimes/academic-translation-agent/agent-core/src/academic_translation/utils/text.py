from __future__ import annotations

import re


_PROTECTED = re.compile(
    r"(\$\$.*?\$\$|\$[^$\n]+\$|\\\(.*?\\\)|\\\[.*?\\\]|\[[0-9,;\-\s]+\])",
    re.DOTALL,
)


def normalize_whitespace(text: str) -> str:
    return re.sub(r"[ \t]+", " ", text).strip()


def mask_protected_content(text: str) -> tuple[str, dict[str, str]]:
    tokens: dict[str, str] = {}

    def replace(match: re.Match[str]) -> str:
        key = f"[[KEEP_{len(tokens)}]]"
        tokens[key] = match.group(0)
        return key

    return _PROTECTED.sub(replace, text), tokens


def restore_protected_content(text: str, tokens: dict[str, str]) -> str:
    for key, value in tokens.items():
        text = text.replace(key, value)
    return text
