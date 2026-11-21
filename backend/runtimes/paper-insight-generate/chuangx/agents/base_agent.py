from __future__ import annotations

from typing import Any

from ..models import InnovationState, utc_now_iso


class BaseAgent:
    name = "BaseAgent"

    def trace(self, state: InnovationState, message: str, **extra: Any) -> None:
        payload = {"time": utc_now_iso(), "step": self.name, "message": message}
        payload.update(extra)
        state.workflow_trace.append(payload)

    def run(self, state: InnovationState) -> InnovationState:  # pragma: no cover - interface only
        raise NotImplementedError
