from __future__ import annotations

import re
import unicodedata


_ALIASES = {
    "acc": "accuracy",
    "avg": "average",
    "err": "error",
    "errs": "error",
    "val": "validation",
}
_STOP_TOKENS = {"the", "model", "value", "score"}
_GENERIC_LABEL_TOKENS = {
    "baseline",
    "column",
    "layer",
    "layers",
    "method",
    "model",
    "row",
    "target",
}
_CJK_PATTERN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")


def _normalized(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return normalized.translate(
        str.maketrans(
            {
                "‐": "-",
                "‑": "-",
                "‒": "-",
                "–": "-",
                "—": "-",
                "−": "-",
            }
        )
    )


def canonical_table_label(value: str) -> str:
    """Return a punctuation-insensitive Unicode label without losing digits."""

    return "".join(character for character in _normalized(value) if character.isalnum())


def _cjk_tokens(value: str) -> set[str]:
    tokens: set[str] = set()
    for run in _CJK_PATTERN.findall(value):
        if len(run) <= 2:
            tokens.add(run)
        else:
            tokens.update(run[index : index + 2] for index in range(len(run) - 1))
    return tokens


def table_tokens(value: str) -> set[str]:
    """Tokenize table labels, metrics, and scopes across Latin, digits, and CJK."""

    normalized = _normalized(value)
    tokens = {
        _ALIASES.get(token, token)
        for token in re.findall(r"[a-z]+|\d+(?:\.\d+)?", normalized)
        if token not in _STOP_TOKENS
    }
    tokens.update(_cjk_tokens(normalized))
    return tokens


def _numeric_tokens(value: str) -> tuple[str, ...]:
    return tuple(re.findall(r"\d+(?:\.\d+)?", _normalized(value)))


def table_label_matches(expected: str, actual: str) -> bool:
    """Conservatively match visible table labels while preserving identifiers."""

    expected_compact = canonical_table_label(expected)
    actual_compact = canonical_table_label(actual)
    if not expected_compact or not actual_compact:
        return False

    expected_numbers = _numeric_tokens(expected)
    actual_numbers = _numeric_tokens(actual)
    if (expected_numbers or actual_numbers) and expected_numbers != actual_numbers:
        return False
    if expected_compact == actual_compact:
        return True

    expected_cjk = "".join(_CJK_PATTERN.findall(_normalized(expected)))
    actual_cjk = "".join(_CJK_PATTERN.findall(_normalized(actual)))
    if expected_cjk or actual_cjk:
        # Without a tokenizer, CJK substring matching is not fail-closed:
        # "本文方法" is contained in "非本文方法". Match the visible CJK
        # label exactly while still allowing a Latin annotation such as Ours.
        return bool(expected_cjk) and expected_cjk == actual_cjk

    expected_tokens = table_tokens(expected)
    actual_tokens = table_tokens(actual)
    if not expected_tokens or not actual_tokens:
        return False

    expected_distinctive = {
        token
        for token in expected_tokens - _GENERIC_LABEL_TOKENS
        if not (len(token) == 1 and token.isalpha())
    }
    actual_distinctive = {
        token
        for token in actual_tokens - _GENERIC_LABEL_TOKENS
        if not (len(token) == 1 and token.isalpha())
    }
    if not expected_distinctive or not actual_distinctive:
        return False
    overlap = expected_distinctive & actual_distinctive
    return (
        len(overlap) / len(expected_distinctive) >= 0.75
        and len(overlap) / len(actual_distinctive) >= 0.5
    )
