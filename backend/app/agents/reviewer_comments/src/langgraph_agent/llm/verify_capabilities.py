"""Print a provider-neutral StructuredOutputEngine capability matrix."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from config.settings import get_settings

from .structured_output import StructuredOutputEngine


class CapabilityProbe(BaseModel):
    ok: bool
    label: Literal["structured-output-probe"]


_PURPOSES = ("split", "analyze", "draft", "paper_card")
_MESSAGES = [
    {
        "role": "user",
        "content": (
            "Return a test object with ok=true and "
            "label='structured-output-probe'. This request contains no business data."
        ),
    }
]


def _print_matrix(rows: list[dict[str, object]]) -> None:
    headers = ("purpose", "model", "endpoint", "strategy", "status", "detail")
    printable = [
        (
            str(row["purpose"]),
            str(row["model"]),
            str(row["endpoint_host"]),
            str(row["strategy"]),
            str(row["status"]),
            str(row["detail"]),
        )
        for row in rows
    ]
    widths = [
        max(len(headers[index]), *(len(item[index]) for item in printable))
        for index in range(len(headers))
    ]
    print(" | ".join(value.ljust(widths[index]) for index, value in enumerate(headers)))
    print("-+-".join("-" * width for width in widths))
    for row in printable:
        print(" | ".join(value.ljust(widths[index]) for index, value in enumerate(row)))


def main() -> int:
    try:
        settings = get_settings()
        validate = getattr(settings, "validate", None)
        if callable(validate):
            validate()
        settings.require_llm()
    except Exception as error:  # noqa: BLE001 - command must report config errors
        print(f"[configuration error] {error}")
        return 1

    engine = StructuredOutputEngine()
    rows: list[dict[str, object]] = []
    for purpose in _PURPOSES:
        rows.extend(
            engine.probe_capabilities(
                purpose,
                CapabilityProbe,
                _MESSAGES,
            )
        )

    _print_matrix(rows)
    successful = {
        str(row["purpose"])
        for row in rows
        if row["status"] == "supported"
    }
    return 0 if successful == set(_PURPOSES) else 1


if __name__ == "__main__":
    raise SystemExit(main())
