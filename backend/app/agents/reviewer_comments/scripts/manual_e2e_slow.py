"""SLOW 冒烟联机：TASK_INIT(含基线) → ANALYSIS → REPLY → FINALIZE。

前置：
    1. 已配置 .env
    2. python scripts/init_db.py
    3. python scripts/seed_manual_slow.py

用法：
    python scripts/manual_e2e_slow.py --auto-approve
    python scripts/manual_e2e_slow.py --auto-approve --stop-after task_init
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

from langgraph_agent import AgentStatus, ResumeCommand, ReviewAgent
from langgraph_agent.schemas.interaction import EditableFieldType

SAMPLE_JSON = _ROOT / "assets" / "examples" / "sample_task_init_slow.json"
DEFAULT_WS = UUID("00000000-0000-4000-8000-000000000010")
DEFAULT_USER = "demo-user-slow"


def _dump(result: Any) -> None:
    if hasattr(result, "model_dump"):
        data = result.model_dump(mode="json")
    else:
        data = result
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def _load_sample() -> dict[str, Any]:
    if SAMPLE_JSON.is_file():
        return json.loads(SAMPLE_JSON.read_text(encoding="utf-8"))
    return {
        "workspace_id": str(DEFAULT_WS),
        "user_id": DEFAULT_USER,
        "mode": "SLOW",
        "manuscript_version_id": None,
    }


def _build_resume_command(pending: Any) -> ResumeCommand:
    payload: dict[str, Any] = {}
    for field in getattr(pending, "editable_fields", None) or []:
        if field.default is not None:
            payload[field.key] = field.default
        elif field.type is EditableFieldType.CHECKBOX:
            payload[field.key] = True
        elif field.key == "approved":
            payload[field.key] = True
        else:
            payload[field.key] = True if field.required else field.default

    # 基线确认：无卡片时也要 approved=True 才能继续
    if pending.interaction_type == "CONFIRM_BASELINE":
        payload.setdefault("approved", True)
        if "selected_card_ids" not in payload:
            # 默认全选 context.cards
            cards = (pending.context or {}).get("cards") or []
            payload["selected_card_ids"] = [
                str(item.get("paper_card_id"))
                for item in cards
                if isinstance(item, dict) and item.get("paper_card_id")
            ]
    if pending.interaction_type == "REVIEW_REPLY_DRAFT":
        payload.setdefault("action", "approve")
    if not payload:
        payload = {"approved": True}
    return ResumeCommand(
        workspace_id=pending.workspace_id,
        thread_id=pending.thread_id,
        interaction_id=pending.interaction_id,
        input_version=pending.input_version,
        payload=payload,
    )


def wait_loop(agent: ReviewAgent, result: Any, *, auto_approve: bool) -> Any:
    step = 0
    while result.status is AgentStatus.WAITING_HUMAN and result.pending:
        step += 1
        pending = result.pending
        print("\n======== 需要人工确认 ========")
        print("step:", step)
        print("type:", pending.interaction_type)
        print("title:", pending.title)
        print("question:", pending.question)
        print("thread_id:", result.thread_id)
        if pending.interaction_type == "CONFIRM_BASELINE":
            cards = (pending.context or {}).get("cards") or []
            print(f"baseline cards = {len(cards)} degraded={ (pending.context or {}).get('baseline_degraded') }")
        if auto_approve:
            print("[auto-approve] 自动确认")
        else:
            ans = input("输入 y 确认并继续，其它键退出: ").strip().lower()
            if ans != "y":
                print("已停止。")
                return result
        result = agent.resume(result.thread_id, _build_resume_command(pending))
        print("\n--- resume 后 ---")
        _dump(result)
    return result


def _pick_store(stores: Any, *names: str) -> Any:
    if stores is None:
        return None
    if isinstance(stores, dict):
        for name in names:
            if name in stores and stores[name] is not None:
                return stores[name]
        return None
    for name in names:
        value = getattr(stores, name, None)
        if value is not None:
            return value
    return None


def _extract_source_id(bundle: Any) -> UUID | None:
    if bundle is None:
        return None
    if isinstance(bundle, dict):
        sources = bundle.get("sources") or bundle.get("active_sources") or []
        if sources:
            first = sources[0]
            if isinstance(first, dict) and first.get("source_id"):
                return UUID(str(first["source_id"]))
            if hasattr(first, "source_id"):
                return UUID(str(first.source_id))
        suggestion = bundle.get("suggestion")
        if isinstance(suggestion, dict) and suggestion.get("source_id"):
            return UUID(str(suggestion["source_id"]))
    return None


def _load_source_id_from_db(suggestion_id: UUID) -> UUID | None:
    from sqlalchemy import text

    from langgraph_agent.adapters.postgres.db import create_session_factory

    sf = create_session_factory()
    with sf() as session:
        row = session.execute(
            text(
                """
                SELECT source_id
                FROM suggestion_sources
                WHERE suggestion_id = :sid AND status = 'ACTIVE'
                ORDER BY created_at
                LIMIT 1
                """
            ),
            {"sid": suggestion_id},
        ).first()
        return UUID(str(row[0])) if row else None


def main() -> int:
    parser = argparse.ArgumentParser(description="SLOW 冒烟 E2E")
    parser.add_argument("--auto-approve", action="store_true")
    parser.add_argument(
        "--stop-after",
        choices=("task_init", "analysis", "reply", "finalize"),
        default="finalize",
    )
    args = parser.parse_args()

    sample = _load_sample()
    workspace_id = UUID(str(sample["workspace_id"]))
    user_id = str(sample.get("user_id") or DEFAULT_USER)
    manuscript_version_id = sample.get("manuscript_version_id")
    if not manuscript_version_id:
        print(
            "[失败] sample_task_init_slow.json 缺少 manuscript_version_id。"
            "请先运行：python scripts/seed_manual_slow.py",
            file=sys.stderr,
        )
        return 1

    print("创建 ReviewAgent.from_settings() ...")
    try:
        agent = ReviewAgent.from_settings()
    except Exception as error:  # noqa: BLE001
        print(f"[失败] {type(error).__name__}: {error}", file=sys.stderr)
        traceback.print_exc()
        return 1

    print("\n##### 1. TASK_INIT (SLOW) #####")
    print("workspace_id =", workspace_id)
    print("manuscript_version_id =", manuscript_version_id)
    try:
        result = agent.start_task_init(
            {
                "workspace_id": str(workspace_id),
                "user_id": user_id,
                "mode": "SLOW",
                "manuscript_version_id": str(manuscript_version_id),
            }
        )
    except Exception as error:  # noqa: BLE001
        print(f"[失败] start_task_init：{type(error).__name__}: {error}", file=sys.stderr)
        traceback.print_exc()
        return 1

    _dump(result)
    if result.status is AgentStatus.FAILED:
        print(
            f"[失败] TASK_INIT 失败 error_code={result.error_code} artifacts={result.artifacts}",
            file=sys.stderr,
        )
        return 1

    result = wait_loop(agent, result, auto_approve=args.auto_approve)
    if result.status is not AgentStatus.SUCCEEDED:
        print(f"TASK_INIT 未成功 status={result.status} error={result.error_code}")
        return 1

    suggestion_ids = [
        UUID(str(ref.id))
        for ref in (result.result_refs or [])
        if ref.type == "suggestion"
    ]
    print("suggestion_ids:", suggestion_ids)
    if args.stop_after == "task_init":
        print("已按 --stop-after=task_init 结束。")
        return 0
    if not suggestion_ids:
        print("[失败] 没有 suggestion", file=sys.stderr)
        return 1

    sid = suggestion_ids[0]
    print(f"\n##### 2. ANALYSIS (SLOW) suggestion_id={sid} #####")
    try:
        result = agent.start_analysis(
            {
                "workspace_id": str(workspace_id),
                "suggestion_id": str(sid),
                "user_id": user_id,
                "mode": "SLOW",
                "manuscript_version_id": str(manuscript_version_id),
            }
        )
    except Exception as error:  # noqa: BLE001
        print(f"[失败] start_analysis：{type(error).__name__}: {error}", file=sys.stderr)
        traceback.print_exc()
        return 1
    _dump(result)
    result = wait_loop(agent, result, auto_approve=args.auto_approve)
    if result.status is not AgentStatus.SUCCEEDED:
        print(f"ANALYSIS 未成功 status={result.status} error={result.error_code}")
        return 1
    if args.stop_after == "analysis":
        print("已按 --stop-after=analysis 结束。")
        return 0

    source_id: UUID | None = None
    stores = getattr(agent, "_stores", None)
    suggestion_store = _pick_store(stores, "suggestion", "suggestion_store")
    if suggestion_store is not None and hasattr(suggestion_store, "load_suggestion_bundle"):
        try:
            bundle = suggestion_store.load_suggestion_bundle(sid)
            source_id = _extract_source_id(bundle)
        except Exception as error:  # noqa: BLE001
            print(f"[警告] load_suggestion_bundle 失败：{error}")
    if source_id is None:
        try:
            source_id = _load_source_id_from_db(sid)
        except Exception as error:  # noqa: BLE001
            print(f"[警告] DB 查 source 失败：{error}")
    if source_id is None:
        print("[失败] 未能解析 source_id", file=sys.stderr)
        return 1

    print(f"\n##### 3. REPLY source_id={source_id} #####")
    try:
        result = agent.start_reply(
            {
                "workspace_id": str(workspace_id),
                "suggestion_id": str(sid),
                "source_id": str(source_id),
                "user_id": user_id,
            }
        )
    except Exception as error:  # noqa: BLE001
        print(f"[失败] start_reply：{type(error).__name__}: {error}", file=sys.stderr)
        traceback.print_exc()
        return 1
    _dump(result)
    result = wait_loop(agent, result, auto_approve=args.auto_approve)
    if result.status is not AgentStatus.SUCCEEDED:
        print(f"REPLY 未成功 status={result.status}")
        return 1
    if args.stop_after == "reply":
        print("已按 --stop-after=reply 结束。")
        return 0

    print("\n##### 4. FINALIZE #####")
    try:
        result = agent.finalize({"workspace_id": str(workspace_id), "user_id": user_id})
    except Exception as error:  # noqa: BLE001
        print(f"[失败] finalize：{type(error).__name__}: {error}", file=sys.stderr)
        traceback.print_exc()
        return 1
    _dump(result)
    print(f"FINALIZE status={result.status} phase={result.phase}")
    print(
        "说明：SLOW 冒烟只回了 1 条 suggestion，FINALIZE 出现 BLOCKED/MISSING_REPLY 是预期闸门行为。"
    )
    return 0 if result.status is not AgentStatus.FAILED else 1


if __name__ == "__main__":
    raise SystemExit(main())
