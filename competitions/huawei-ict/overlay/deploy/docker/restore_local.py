#!/usr/bin/env python3
"""Restore the supplied PostgreSQL 18 backup into the local container database.

deploy/huawei/restore_database.py targets Huawei Cloud RDS: its production path
demands `sslmode=verify-full` plus an RDS CA file, and its `--local-test` path
only accepts the database name `zhiyan_migration_test` on a whitelisted host.
Neither fits a local container whose host is `db` and database is `zhiyan`.

Rather than relax those checks, this wrapper reuses that script's
`prepare_dump()` verbatim, so the same guarantees still apply:

  * the backup's SHA-256 must match the reviewed hand-off copy,
  * its structure must still be exactly 100 tables and 111 owner statements,
  * the old server login is not recreated and no rights are granted elsewhere,
  * the target database must be empty and PostgreSQL 18 or newer,
  * the approved correction that marks pre-migration unfinished tasks as failed
    runs inside the same transaction, before any application process starts.

The whole restore is one transaction. An interrupted client connection can
leave the commit outcome unknown; the durable proof lets startup check it.

  restore_local.py --database-url postgresql+psycopg://... --dump <path> [--apply]
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import subprocess
import sys

# Inside the container this file lives in /opt/zhiyan while the repository is
# mounted read-only elsewhere, so the sibling script is located through
# ZHIYAN_SOURCE and only falls back to a path relative to this file when run
# directly from a checkout.
def _locate_huawei_dir() -> Path:
    candidates = []
    if source := os.getenv("ZHIYAN_SOURCE"):
        candidates.append(Path(source))
    here = Path(__file__).resolve()
    # In the container this file sits in /opt/zhiyan, which has no third
    # parent, so walk the ancestors instead of indexing into them.
    candidates.extend(here.parents)
    for root in candidates:
        if (root / "deploy" / "huawei" / "restore_database.py").is_file():
            return root / "deploy" / "huawei"
    raise SystemExit(
        "找不到 deploy/huawei/restore_database.py；"
        "请设置 ZHIYAN_SOURCE 指向仓库根目录"
    )


sys.path.insert(0, str(_locate_huawei_dir()))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from restore_database import prepare_dump  # noqa: E402  (needs the path above)
from database_state import JOURNAL, read_journal  # noqa: E402


def proof_sql(journal: dict[str, str]) -> str:
    """The proof is committed in the same transaction as the restored data."""
    if (not re.fullmatch(r"[0-9]+", journal["cluster"])
            or not re.fullmatch(r"[0-9]+", journal["database"])
            or not re.fullmatch(r"[0-9a-f]{64}", journal["token"])
            or not re.fullmatch(r"[0-9a-f]{64}", journal["backup"])):
        raise ValueError("数据库恢复凭据格式不正确")
    return f"""
DO $$ BEGIN
 IF NOT EXISTS (SELECT 1 FROM pg_control_system() s, pg_database d
                WHERE d.datname=current_database()
                  AND s.system_identifier::text='{journal['cluster']}'
                  AND d.oid::text='{journal['database']}') THEN
  RAISE EXCEPTION 'restore target changed';
 END IF;
END $$;
CREATE SCHEMA __zhiyan_deploy;
CREATE TABLE __zhiyan_deploy.restore_proof (
 id boolean PRIMARY KEY DEFAULT true CHECK (id), token text NOT NULL, backup text NOT NULL
);
INSERT INTO __zhiyan_deploy.restore_proof(id, token, backup)
VALUES (true, '{journal['token']}', '{journal['backup']}');
"""


def psql_environment(url) -> dict[str, str]:
    if url.drivername != "postgresql+psycopg" or not url.database or not url.username:
        raise ValueError("DATABASE_URL 必须是完整的 postgresql+psycopg 连接串")
    # Drop inherited PG* settings so only this target is addressed, and keep
    # the password out of argv.
    env = {k: v for k, v in os.environ.items() if not k.startswith("PG")}
    env.update(
        PGHOST=url.host or "",
        PGPORT=str(url.port or 5432),
        PGDATABASE=url.database,
        PGUSER=url.username,
        PGPASSWORD=url.password or "",
        PGCONNECT_TIMEOUT="10",
        PGAPPNAME="zhiyan-local-restore",
        PGCLIENTENCODING="UTF8",
    )
    return env


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dump", type=Path, required=True)
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL", ""))
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not args.database_url:
        parser.error("设置 --database-url 或 DATABASE_URL")
    if not args.dump.is_file():
        print(f"备份文件不存在：{args.dump}", file=sys.stderr)
        return 1

    try:
        payload, counts = prepare_dump(args.dump.read_bytes())
    except ValueError as exc:
        print(f"备份校验失败：{exc}", file=sys.stderr)
        return 1
    print(f"备份校验通过：{counts}")
    if not args.apply:
        print("仅校验，未连接数据库。加 --apply 才会写入。")
        return 0

    if args.state_dir is None:
        parser.error("--apply requires --state-dir with the prepared restore journal")

    from sqlalchemy.engine import make_url

    try:
        env = psql_environment(make_url(args.database_url))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    try:
        from sqlalchemy import create_engine, text
        engine = create_engine(args.database_url, pool_pre_ping=True)
        with engine.connect() as connection:
            row = connection.execute(text(
                "SELECT system_identifier::text, d.oid::text "
                "FROM pg_control_system(), pg_database d WHERE d.datname=current_database()"
            )).one()
        journal = read_journal(args.state_dir / JOURNAL, {"cluster": row[0], "database": row[1]})
        payload += proof_sql(journal)
    except Exception as exc:
        print(f"恢复凭据检查失败：{type(exc).__name__}；未执行恢复", file=sys.stderr)
        return 1

    result = subprocess.run(
        ["psql", "-X", "--no-password", "--single-transaction",
         "--set=ON_ERROR_STOP=1", "--quiet", "--file=-"],
        input=payload, text=True, env=env, capture_output=True,
    )
    if result.returncode:
        # psql diagnostics can echo COPY row contents, which is private data.
        # Report that it failed and rolled back, not what was in the rows.
        print("恢复未确认完成；连接中断时提交状态可能未知。请重试并核验恢复状态。",
              file=sys.stderr)
        tail = (result.stderr or "").strip().splitlines()[-1:]
        for line in tail:
            if "COPY" not in line and "CONTEXT" not in line:
                print(f"  {line[:200]}", file=sys.stderr)
        return 1
    print("恢复已提交。未改写任何模型凭据，未导入种子数据，未发起模型调用。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
