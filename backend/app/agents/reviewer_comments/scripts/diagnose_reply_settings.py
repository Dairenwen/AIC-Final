"""诊断 REPLY 的 ResponseSettings 来源与校验结果。

用法（在 langgraph-agent 目录、已激活 venv）：
    python scripts/diagnose_reply_settings.py
    python scripts/diagnose_reply_settings.py --suggestion-id <uuid> --source-id <uuid>
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

from sqlalchemy import text

from langgraph_agent.adapters.postgres.db import create_session_factory
from langgraph_agent.adapters.postgres.repositories import (
    SuggestionRepository,
    WorkspaceRepository,
    get_effective_response_settings,
)
from langgraph_agent.adapters.postgres.repositories.suggestion_repo import (
    default_response_settings,
)
from langgraph_agent.adapters.postgres.stores import PostgresReplyStore
from langgraph_agent.agent.facade import ReviewAgent
from langgraph_agent.agent.reply.graph import _ensure_response_settings
from langgraph_agent.agent.reply.thread_ids import build_reply_thread_id
from langgraph_agent.schemas.workspace import ResponseSettings

WS = UUID("00000000-0000-4000-8000-000000000001")


def _pp(title: str, value: Any) -> None:
    print(f"\n===== {title} =====")
    if isinstance(value, (dict, list)):
        print(json.dumps(value, ensure_ascii=False, indent=2, default=str))
    else:
        print(value)


def _pick_latest_ready(workspace_id: UUID) -> tuple[UUID, UUID, str | None]:
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
            {"ws": workspace_id},
        ).first()
        if row is None:
            raise SystemExit(
                "库中没有「已确认分析 + facts + ACTIVE source」。"
                "请先跑 ANALYSIS，或手动传 --suggestion-id / --source-id。"
            )
        return UUID(str(row[0])), UUID(str(row[1])), (str(row[2]) if row[2] else None)


def main() -> int:
    parser = argparse.ArgumentParser(description="诊断 REPLY ResponseSettings")
    parser.add_argument("--workspace-id", type=str, default=str(WS))
    parser.add_argument("--suggestion-id", type=str, default=None)
    parser.add_argument("--source-id", type=str, default=None)
    args = parser.parse_args()

    workspace_id = UUID(args.workspace_id)
    if args.suggestion_id and args.source_id:
        suggestion_id = UUID(args.suggestion_id)
        source_id = UUID(args.source_id)
        input_version = None
    else:
        suggestion_id, source_id, input_version = _pick_latest_ready(workspace_id)

    print("workspace_id  =", workspace_id)
    print("suggestion_id =", suggestion_id)
    print("source_id     =", source_id)
    print("input_version =", input_version)

    # 1) 当前实际 import 到的代码路径
    import langgraph_agent.agent.facade as facade_mod
    import langgraph_agent.agent.reply.graph as reply_graph_mod
    import langgraph_agent.adapters.postgres.repositories.suggestion_repo as sug_repo_mod

    _pp("代码路径", {
        "facade": str(getattr(facade_mod, "__file__", None)),
        "reply.graph": str(getattr(reply_graph_mod, "__file__", None)),
        "suggestion_repo": str(getattr(sug_repo_mod, "__file__", None)),
        "has_default_response_settings": hasattr(sug_repo_mod, "default_response_settings"),
        "has__ensure_response_settings": hasattr(reply_graph_mod, "_ensure_response_settings"),
    })

    # 2) 空 dict 直接校验（复现原始错误形态）
    print("\n===== ResponseSettings.model_validate({}) =====")
    try:
        ResponseSettings.model_validate({})
        print("意外通过（不应发生）")
    except Exception as error:  # noqa: BLE001
        print(f"{type(error).__name__}: {error}")

    # 3) 系统默认 / 兜底函数
    _pp("default_response_settings()", default_response_settings())
    _pp("_ensure_response_settings({})", _ensure_response_settings({}))
    _pp("_ensure_response_settings(None)", _ensure_response_settings(None))

    # 4) 库中真实数据
    sf = create_session_factory()
    with sf() as session:
        workspace = WorkspaceRepository().get_by_id(session, workspace_id)
        source = SuggestionRepository().get_source(session, source_id)
        if workspace is None:
            raise SystemExit(f"Workspace 不存在: {workspace_id}")
        if source is None:
            raise SystemExit(f"Source 不存在: {source_id}")

        _pp("workspace.global_settings", workspace.global_settings)
        _pp(
            "source.expression_settings_override",
            source.expression_settings_override,
        )
        try:
            effective = get_effective_response_settings(workspace, source)
            _pp("get_effective_response_settings() OK", effective)
        except Exception as error:  # noqa: BLE001
            print("\n===== get_effective_response_settings() FAIL =====")
            print(f"{type(error).__name__}: {error}")
            traceback.print_exc()
            return 1

        # 已有 reply 行（若有）
        rows = session.execute(
            text(
                """
                SELECT reply_id, status, expression_settings, input_version, current_draft_id
                FROM source_replies
                WHERE source_id = :source_id
                ORDER BY created_at DESC NULLS LAST
                LIMIT 5
                """
            ),
            {"source_id": source_id},
        ).mappings().all()
        _pp("source_replies(最近5条)", [dict(r) for r in rows])

        # suggestion.input_version
        sug = SuggestionRepository().get_suggestion(session, suggestion_id)
        db_input_version = sug.input_version if sug is not None else None
        _pp("suggestion.input_version", db_input_version)

    resolved_input_version = input_version or db_input_version or "unknown"
    base_thread = build_reply_thread_id(workspace_id, source_id, resolved_input_version)
    _pp("build_reply_thread_id(无 run 后缀)", base_thread)
    print("说明：当前 facade.start_reply 会再追加 :run:<run_id>")

    # 5) 走 store.load_reply_context
    print("\n===== PostgresReplyStore.load_reply_context =====")
    try:
        store = PostgresReplyStore(sf)
        context = store.load_reply_context(
            workspace_id=workspace_id,
            suggestion_id=suggestion_id,
            source_id=source_id,
            expression_settings=None,
        )
        _pp("load_reply_context.expression_settings", context.get("expression_settings"))
        _pp("load_reply_context.analysis_ready", context.get("analysis_ready"))
        facts = context.get("confirmed_modification_facts") or []
        print(f"confirmed_modification_facts count = {len(facts)}")
    except Exception as error:  # noqa: BLE001
        print(f"[失败] {type(error).__name__}: {error}")
        traceback.print_exc()
        return 1

    # 6) 只构造 start_reply 的 thread 策略，不真正跑 LLM
    print("\n===== ReviewAgent.start_reply 线程策略抽样 =====")
    try:
        agent = ReviewAgent.from_settings()
        # 不真正 invoke；只展示 facade 文件与方法签名线索
        import inspect

        src = inspect.getsource(agent.start_reply)
        has_run_suffix = ":run:" in src or "run:{resolved_run_id}" in src or 'f"{base_thread}:run:' in src
        print("start_reply 源码是否包含 :run: 后缀逻辑 =", has_run_suffix)
        if not has_run_suffix:
            print("警告：当前 import 的 facade 可能是旧版，thread 可能复用脏 checkpoint。")
    except Exception as error:  # noqa: BLE001
        print(f"[跳过 agent 构造] {type(error).__name__}: {error}")

    print("\n===== 结论提示 =====")
    print("1) 若 global_settings 为 {} 且 get_effective 失败 → 数据层空 settings 是根因")
    print("2) 若 get_effective / load_context 都 OK → 再跑 manual_reply_only 看是否已修复")
    print("3) 若 thread 无 :run: 且反复同 thread_id 失败 → 可能是脏 checkpoint 复用")
    print("\n下一步请执行：")
    print("  python scripts/manual_reply_only.py --auto-approve")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
