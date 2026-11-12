"""写入 live 手动测试用的工作区 + 审稿意见种子数据。

用法（在 langgraph-agent 目录、已激活 venv、已配置 .env）：

    python scripts/init_db.py
    python scripts/seed_manual.py
    python main.py demo-task-init --live --auto-approve

默认 workspace_id / user_id 与 assets/examples/sample_task_init.json 一致。
可重复执行：会先删除同 workspace 下的旧种子再写入。
"""

from __future__ import annotations

import sys
from pathlib import Path
from uuid import UUID, uuid4

# 保证从任意 cwd 运行都能找到包与 config
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv

load_dotenv(_ROOT / ".env", override=False)

from sqlalchemy import text

from langgraph_agent.adapters.postgres.db import create_session_factory
from langgraph_agent.adapters.postgres.models.review import ReviewInput, ReviewParty
from langgraph_agent.adapters.postgres.models.workspace import Workspace

# 与 assets/examples/sample_task_init.json 对齐
WS = UUID("00000000-0000-4000-8000-000000000001")
PARTY = UUID("00000000-0000-4000-8000-000000000002")
USER = "demo-user"

SAMPLE_REVIEW = """
1. The experimental setup is unclear. Please add more details about the dataset split and evaluation protocol.
2. The related work section misses recent methods on this topic. Please cite and compare with the latest baselines.
3. The writing is generally clear, but Figure 2 captions need improvement and more precise descriptions.
""".strip()


def main() -> int:
    sf = create_session_factory()
    with sf() as session:
        session.execute(
            text("DELETE FROM review_inputs WHERE workspace_id = :w"),
            {"w": WS},
        )
        session.execute(
            text("DELETE FROM review_parties WHERE workspace_id = :w"),
            {"w": WS},
        )
        session.execute(
            text("DELETE FROM workspaces WHERE workspace_id = :w"),
            {"w": WS},
        )
        session.commit()

        # 分步 flush，避免 ORM 在无 relationship 时把 review_inputs 插在 parties 前面
        from langgraph_agent.adapters.postgres.repositories.suggestion_repo import (
            default_response_settings,
        )

        session.add(
            Workspace(
                workspace_id=WS,
                user_id=USER,
                title="live-manual-demo",
                mode="FAST",
                status="ACTIVE",
                global_settings=default_response_settings(),
            )
        )
        session.flush()

        session.add(
            ReviewParty(
                party_id=PARTY,
                workspace_id=WS,
                role="REVIEWER",
                display_name="Reviewer 1",
                raw_label="R1",
            )
        )
        session.flush()

        session.add(
            ReviewInput(
                review_input_id=uuid4(),
                workspace_id=WS,
                party_id=PARTY,
                version_no=1,
                raw_text=SAMPLE_REVIEW,
                storage_uri=None,
                content_hash="manual-seed-v1",
                language="en",
                is_current=True,
            )
        )
        session.commit()

    print("[成功] 种子数据已写入")
    print(f"  workspace_id = {WS}")
    print(f"  user_id      = {USER}")
    print(f"  party_id     = {PARTY}")
    print()
    print("下一步：")
    print("  python main.py demo-task-init --live --auto-approve")
    print("或交互整流程：")
    print("  python scripts/manual_e2e.py")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:  # noqa: BLE001
        print(f"[失败] {type(error).__name__}: {error}", file=sys.stderr)
        raise
