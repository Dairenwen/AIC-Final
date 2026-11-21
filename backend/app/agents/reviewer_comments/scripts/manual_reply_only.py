"""只跑 REPLY 阶段（要求库中已有确认分析 + modification facts）。

用法：
    python scripts/manual_reply_only.py
    python scripts/manual_reply_only.py --auto-approve
    python scripts/manual_reply_only.py --suggestion-id <uuid> --source-id <uuid>
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path
from typing import Any
from uuid import UUID

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv

load_dotenv(_ROOT / ".env", override=False)

from langgraph_agent import AgentStatus, GraphKind, ReplyInput, ResumeCommand, ReviewAgent
from langgraph_agent.schemas.interaction import EditableFieldType

WS = UUID("00000000-0000-4000-8000-000000000001")
USER = "demo-user"


def _dump(result: Any) -> None:
    print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2, default=str))


def _build_resume(pending: Any) -> ResumeCommand:
    payload: dict[str, Any] = {}
    for field in getattr(pending, "editable_fields", None) or []:
        if field.default is not None:
            payload[field.key] = field.default
        elif field.type is EditableFieldType.CHECKBOX:
            payload[field.key] = True
        elif field.key == "approved":
            payload[field.key] = True
    if pending.interaction_type == "REVIEW_REPLY_DRAFT" and "action" not in payload:
        payload["action"] = "approve"
    if not payload:
        payload = {"approved": True}
    return ResumeCommand(
        workspace_id=pending.workspace_id,
        thread_id=pending.thread_id,
        interaction_id=pending.interaction_id,
        input_version=pending.input_version,
        payload=payload,
    )


def _wait(agent: ReviewAgent, result: Any, *, auto_approve: bool) -> Any:
    while result.status is AgentStatus.WAITING_HUMAN and result.pending:
        p = result.pending
        print("\n======== 需要人工确认 ========")
        print("type:", p.interaction_type)
        print("title:", p.title)
        if auto_approve:
            print("[auto-approve]")
        else:
            ans = input("输入 y 确认并继续，其它键退出: ").strip().lower()
            if ans != "y":
                return result
        result = agent.resume(
            result.thread_id,
            _build_resume(p),
            graph_kind=GraphKind.REPLY,
        )
        print("\n--- resume 后 ---")
        _dump(result)
    return result


def _pick_latest_ready() -> tuple[UUID, UUID, str | None]:
    from sqlalchemy import text

    from langgraph_agent.adapters.postgres.db import create_session_factory

    sf = create_session_factory()
    with sf() as session:
        row = session.execute(
            text(
                """
                SELECT s.suggestion_id, ss.source_id, s.input_version
                FROM suggestions s
                JOIN suggestion_sources ss
                  ON ss.suggestion_id = s.suggestion_id AND ss.status = 'ACTIVE'
                JOIN analysis_snapshots a
                  ON a.suggestion_id = s.suggestion_id AND a.status = 'CONFIRMED'
                WHERE s.workspace_id = :ws
                  AND EXISTS (
                    SELECT 1 FROM modification_facts f
                    WHERE f.suggestion_id = s.suggestion_id AND f.status = 'CONFIRMED'
                  )
                ORDER BY a.confirmed_at DESC NULLS LAST
                LIMIT 1
                """
            ),
            {"ws": WS},
        ).first()
        if row is None:
            raise SystemExit(
                "库中没有「已确认分析 + facts + ACTIVE source」的记录。"
                "请先跑通 ANALYSIS：python scripts/manual_e2e.py --auto-approve --stop-after analysis"
            )
        return UUID(str(row[0])), UUID(str(row[1])), (str(row[2]) if row[2] else None)


def main() -> int:
    parser = argparse.ArgumentParser(description="只测 REPLY")
    parser.add_argument("--auto-approve", action="store_true")
    parser.add_argument("--suggestion-id", type=str, default=None)
    parser.add_argument("--source-id", type=str, default=None)
    parser.add_argument("--user-id", type=str, default=USER)
    parser.add_argument("--workspace-id", type=str, default=str(WS))
    args = parser.parse_args()

    if args.suggestion_id and args.source_id:
        suggestion_id = UUID(args.suggestion_id)
        source_id = UUID(args.source_id)
        input_version = None
    else:
        suggestion_id, source_id, input_version = _pick_latest_ready()

    print("suggestion_id =", suggestion_id)
    print("source_id     =", source_id)
    print("input_version =", input_version)

    agent = ReviewAgent.from_settings()
    payload: dict[str, Any] = {
        "workspace_id": args.workspace_id,
        "suggestion_id": str(suggestion_id),
        "source_id": str(source_id),
        "user_id": args.user_id,
    }
    if input_version:
        payload["input_version"] = input_version

    print("\n##### REPLY only #####")
    try:
        result = agent.start_reply(ReplyInput.model_validate(payload))
    except Exception as error:  # noqa: BLE001
        print(f"[失败] {type(error).__name__}: {error}", file=sys.stderr)
        traceback.print_exc()
        return 1

    _dump(result)
    if result.status is AgentStatus.FAILED:
        print("artifacts =", result.artifacts, file=sys.stderr)
        return 1

    result = _wait(agent, result, auto_approve=args.auto_approve)
    print("\n最终 status =", result.status)
    if result.status is AgentStatus.FAILED:
        print("artifacts =", result.artifacts, file=sys.stderr)
        return 1
    return 0 if result.status is AgentStatus.SUCCEEDED else 1


if __name__ == "__main__":
    raise SystemExit(main())
