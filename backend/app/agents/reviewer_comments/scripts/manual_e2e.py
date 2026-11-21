"""联机手动整流程：TASK_INIT → ANALYSIS → REPLY → FINALIZE。

前置：
    1. 已配置 .env（DATABASE_URL / LLM_*）
    2. python scripts/init_db.py
    3. python scripts/seed_manual.py

用法：
    python scripts/manual_e2e.py
    python scripts/manual_e2e.py --auto-approve
    python scripts/manual_e2e.py --stop-after task_init
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

# 与 seed_manual.py / sample_task_init.json 对齐
WS = UUID("00000000-0000-4000-8000-000000000001")
USER = "demo-user"


def _dump(result: Any) -> None:
    if hasattr(result, "model_dump"):
        data = result.model_dump(mode="json")
    else:
        data = result
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


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
        print("interaction_id:", pending.interaction_id)
        if pending.context:
            print("context:")
            print(
                json.dumps(pending.context, ensure_ascii=False, indent=2, default=str)
            )

        if auto_approve:
            print("[auto-approve] 自动确认")
        else:
            ans = input("输入 y 确认并继续，其它键退出: ").strip().lower()
            if ans != "y":
                print("已停止。可稍后用同一 thread_id 继续 resume。")
                return result

        cmd = _build_resume_command(pending)
        result = agent.resume(result.thread_id, cmd)
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
        for key in ("sources", "source_list", "suggestion_sources"):
            sources = bundle.get(key)
            if isinstance(sources, list) and sources:
                first = sources[0]
                if isinstance(first, dict):
                    raw = first.get("source_id") or first.get("id")
                    if raw:
                        return UUID(str(raw))
                raw = getattr(first, "source_id", None) or getattr(first, "id", None)
                if raw:
                    return UUID(str(raw))
        raw = bundle.get("source_id")
        if raw:
            return UUID(str(raw))
    sources = getattr(bundle, "sources", None)
    if sources:
        first = sources[0]
        raw = getattr(first, "source_id", None) or getattr(first, "id", None)
        if raw:
            return UUID(str(raw))
    return None


def _load_source_id_from_db(suggestion_id: UUID) -> UUID | None:
    from sqlalchemy import text

    from langgraph_agent.adapters.postgres.db import create_session_factory

    sf = create_session_factory()
    with sf() as session:
        row = session.execute(
            text(
                "SELECT source_id FROM suggestion_sources "
                "WHERE suggestion_id = :sid LIMIT 1"
            ),
            {"sid": suggestion_id},
        ).first()
        if row is None:
            return None
        return UUID(str(row[0]))


def main() -> int:
    parser = argparse.ArgumentParser(description="联机手动 E2E 全流程")
    parser.add_argument(
        "--auto-approve",
        action="store_true",
        help="所有 WAITING_HUMAN 自动确认，无需输入 y",
    )
    parser.add_argument(
        "--stop-after",
        choices=("task_init", "analysis", "reply", "finalize"),
        default="finalize",
        help="跑到指定阶段后结束（默认跑完全流程）",
    )
    args = parser.parse_args()

    print("创建 ReviewAgent.from_settings() ...")
    try:
        agent = ReviewAgent.from_settings()
    except Exception as error:  # noqa: BLE001
        print(
            f"[失败] 创建 Agent 失败：{type(error).__name__}: {error}",
            file=sys.stderr,
        )
        traceback.print_exc()
        return 1

    print("\n##### 1. TASK_INIT #####")
    try:
        result = agent.start_task_init(
            {
                "workspace_id": WS,
                "user_id": USER,
                "mode": "FAST",
            }
        )
    except Exception as error:  # noqa: BLE001
        print(
            f"[失败] start_task_init 异常：{type(error).__name__}: {error}",
            file=sys.stderr,
        )
        traceback.print_exc()
        return 1

    _dump(result)
    if result.status is AgentStatus.FAILED:
        print(
            f"[失败] TASK_INIT 直接失败 error_code={result.error_code} "
            f"artifacts={result.artifacts}",
            file=sys.stderr,
        )
        print("提示：请先运行 python scripts/seed_manual.py", file=sys.stderr)
        return 1

    result = wait_loop(agent, result, auto_approve=args.auto_approve)
    if result.status is not AgentStatus.SUCCEEDED:
        print(
            f"TASK_INIT 未成功，停止。status={result.status} error={result.error_code}"
        )
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
        print("[失败] 没有 suggestion，无法继续 analysis", file=sys.stderr)
        return 1

    sid = suggestion_ids[0]
    print(f"\n##### 2. ANALYSIS suggestion_id={sid} #####")
    try:
        result = agent.start_analysis(
            {
                "workspace_id": WS,
                "suggestion_id": sid,
                "user_id": USER,
                "mode": "FAST",
            }
        )
    except Exception as error:  # noqa: BLE001
        print(
            f"[失败] start_analysis 异常：{type(error).__name__}: {error}",
            file=sys.stderr,
        )
        traceback.print_exc()
        return 1

    _dump(result)
    result = wait_loop(agent, result, auto_approve=args.auto_approve)
    if result.status is not AgentStatus.SUCCEEDED:
        print(
            f"ANALYSIS 未成功，停止。status={result.status} error={result.error_code}"
        )
        return 1
    if args.stop_after == "analysis":
        print("已按 --stop-after=analysis 结束。")
        return 0

    source_id: UUID | None = None
    stores = getattr(agent, "_stores", None)
    suggestion_store = _pick_store(stores, "suggestion", "suggestion_store")
    if suggestion_store is not None and hasattr(
        suggestion_store, "load_suggestion_bundle"
    ):
        try:
            bundle = suggestion_store.load_suggestion_bundle(sid)
            source_id = _extract_source_id(bundle)
        except Exception as error:  # noqa: BLE001
            print(f"[警告] load_suggestion_bundle 失败：{error}")

    if source_id is None:
        try:
            source_id = _load_source_id_from_db(sid)
        except Exception as error:  # noqa: BLE001
            print(f"[警告] 从 DB 查 source_id 失败：{error}")

    if source_id is None:
        print("[失败] 未能解析 source_id。", file=sys.stderr)
        return 1

    print(f"\n##### 3. REPLY source_id={source_id} #####")
    try:
        result = agent.start_reply(
            {
                "workspace_id": WS,
                "suggestion_id": sid,
                "source_id": source_id,
                "user_id": USER,
            }
        )
    except Exception as error:  # noqa: BLE001
        print(
            f"[失败] start_reply 异常：{type(error).__name__}: {error}",
            file=sys.stderr,
        )
        traceback.print_exc()
        return 1

    _dump(result)
    result = wait_loop(agent, result, auto_approve=args.auto_approve)
    if result.status is not AgentStatus.SUCCEEDED:
        print(f"REPLY 未成功，停止。status={result.status} error={result.error_code}")
        return 1
    if args.stop_after == "reply":
        print("已按 --stop-after=reply 结束。")
        return 0

    print("\n##### 4. FINALIZE #####")
    try:
        result = agent.finalize({"workspace_id": WS, "user_id": USER})
    except Exception as error:  # noqa: BLE001
        print(
            f"[失败] finalize 异常：{type(error).__name__}: {error}",
            file=sys.stderr,
        )
        traceback.print_exc()
        return 1

    _dump(result)
    print(f"FINALIZE status={result.status} phase={result.phase}")
    if result.artifacts:
        print("artifacts:", result.artifacts)
    print("导出文件一般在 langgraph-agent/.tmp/finalize_exports/ 下。")
    return 0 if result.status is not AgentStatus.FAILED else 1


if __name__ == "__main__":
    raise SystemExit(main())
