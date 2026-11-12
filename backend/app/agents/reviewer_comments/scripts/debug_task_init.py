"""打印 start_task_init 的真实异常（不吞细节）。

用法：
    python scripts/debug_task_init.py
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path
from uuid import UUID

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv

load_dotenv(_ROOT / ".env", override=False)


def main() -> int:
    from config.settings import get_settings
    from langgraph_agent import ReviewAgent, TaskInitInput
    from langgraph_agent.adapters.postgres.db import create_session_factory
    from langgraph_agent.adapters.postgres.models.review import ReviewInput
    from langgraph_agent.adapters.postgres.models.workspace import Workspace
    from sqlalchemy import select

    cfg = get_settings()
    print("LLM_BASE_URL =", (cfg.LLM_BASE_URL or "")[:80])
    print("LLM_TIMEOUT_SECONDS =", cfg.LLM_TIMEOUT_SECONDS)
    print("MODEL_SPLIT =", cfg.MODEL_SPLIT)
    print("has LLM_API_KEY =", bool(cfg.LLM_API_KEY))
    print("has DATABASE_URL =", bool(cfg.DATABASE_URL))

    ws = UUID("00000000-0000-4000-8000-000000000001")
    sf = create_session_factory()
    with sf() as session:
        workspace = session.get(Workspace, ws)
        print(
            "workspace =",
            None
            if workspace is None
            else f"user_id={workspace.user_id} status={workspace.status}",
        )
        inputs = session.scalars(
            select(ReviewInput).where(
                ReviewInput.workspace_id == ws,
                ReviewInput.is_current.is_(True),
            )
        ).all()
        print("current review_inputs =", len(inputs))
        if not inputs:
            print("提示：请先 python scripts/seed_manual.py", file=sys.stderr)

    agent = ReviewAgent.from_settings()
    task = TaskInitInput.model_validate(
        {
            "workspace_id": str(ws),
            "user_id": "demo-user",
            "mode": "FAST",
        }
    )
    print("\n调用 start_task_init ...")
    result = agent.start_task_init(task)
    print("status =", result.status)
    print("error_code =", result.error_code)
    print("phase =", result.phase)
    print("artifacts =", result.artifacts)
    if result.pending is not None:
        print("pending.interaction_type =", result.pending.interaction_type)
    return 0 if result.status.value != "FAILED" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        raise SystemExit(1)
