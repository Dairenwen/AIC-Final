#!/usr/bin/env python3
"""Once-only, transactional binding of exact local PDF names to restored papers."""
from __future__ import annotations

import os
from pathlib import Path
import sys

from sqlalchemy import create_engine, text

# Both scripts are part of the same verified, read-only release snapshot.
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "huawei"))
from database_state import JOURNAL, read_journal, verify_committed_restore  # noqa: E402
from bind_restored_pdfs import existing_pdf_bindings  # noqa: E402


def bind(engine, pdf_dir: Path, state_dir: Path) -> tuple[str, int]:
    if not pdf_dir.is_dir():
        raise ValueError("论文目录不可用")
    journal_path = state_dir / JOURNAL
    journal_exists = journal_path.exists() or journal_path.is_symlink()
    with engine.begin() as connection:
        # Serialize this migration operation without locking business tables
        # for the duration of subsequent application execution.
        connection.execute(text("SELECT pg_advisory_xact_lock(20261025, 2)"))
        proof_exists = connection.execute(text(
            "SELECT to_regclass('__zhiyan_deploy.restore_proof') IS NOT NULL"
        )).scalar_one()
        if not proof_exists and not journal_exists:
            return "existing", 0
        if not proof_exists or not journal_exists:
            raise ValueError("恢复凭据不完整，无法安全补齐论文路径")
        row = connection.execute(text(
            "SELECT system_identifier::text, d.oid::text "
            "FROM pg_control_system(), pg_database d WHERE d.datname=current_database()"
        )).one()
        journal = read_journal(journal_path, {"cluster": row[0], "database": row[1]})
        verify_committed_restore(connection, text, journal)
        connection.execute(text("""
            CREATE TABLE IF NOT EXISTS __zhiyan_deploy.pdf_binding_state (
              id boolean PRIMARY KEY DEFAULT true CHECK (id),
              token text NOT NULL, matched_count integer NOT NULL CHECK (matched_count >= 0)
            )
        """))
        completed = connection.execute(text(
            "SELECT token, matched_count FROM __zhiyan_deploy.pdf_binding_state WHERE id=true"
        )).one_or_none()
        if completed is not None:
            if completed[0] != journal["token"]:
                raise ValueError("论文路径补齐标记与恢复凭据不一致")
            return "completed", int(completed[1])

        files = {p.name for p in pdf_dir.glob("*.pdf") if not p.is_symlink() and p.is_file()}
        rows = connection.execute(text(
            "SELECT id, minio_pdf_key FROM knowledge_base.papers "
            "WHERE COALESCE(BTRIM(minio_pdf_key), '') = ''"
        )).all()
        matches = existing_pdf_bindings(rows, files)
        # Re-check emptiness in the actual UPDATE so a concurrent change is
        # never replaced. Count database updates, not the candidate list.
        changed = 0
        for key, paper_id in matches:
            result = connection.execute(text(
                "UPDATE knowledge_base.papers SET minio_pdf_key=:key "
                "WHERE id=:paper_id AND COALESCE(BTRIM(minio_pdf_key), '') = ''"
            ), {"key": key, "paper_id": paper_id})
            changed += result.rowcount
        connection.execute(text(
            "INSERT INTO __zhiyan_deploy.pdf_binding_state(id, token, matched_count) "
            "VALUES (true, :token, :matched)"
        ), {"token": journal["token"], "matched": changed})
        return "bound", changed


def main() -> int:
    try:
        if os.getenv("KB_DB_SCHEMA", "knowledge_base") != "knowledge_base":
            raise ValueError("知识库数据库模式与已核验备份不一致")
        data_root = Path(os.environ["KB_DATA_DIR"])
        state_root = Path(os.environ["ZHIYAN_DATA_ROOT"]) / ".state"
        if not data_root.is_absolute() or not (state_root / "db-initialised").is_file():
            raise ValueError("恢复状态或知识库目录不完整")
        reference_dir = Path(os.getenv("KB_REFERENCE_PDF_DIR", str(data_root / "pdfs")))
        if not reference_dir.is_absolute():
            raise ValueError("参考论文目录必须是绝对路径")
        outcome, count = bind(create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True),
                              reference_dir, state_root)
        if outcome == "existing":
            print("[zhiyan] 非本次可验证的新恢复库：未改写历史论文路径。")
        elif outcome == "completed":
            print(f"[zhiyan] 论文路径此前已补齐 {count} 条；本次未重新修改。")
        else:
            print(f"[zhiyan] 精确匹配并补齐论文路径 {count} 条；未覆盖已有路径。")
        return 0
    except Exception as exc:
        # Avoid printing connection strings, queries, file names or row data.
        print(f"[zhiyan] 论文路径补齐未完成：{type(exc).__name__}；事务未提交，请核对恢复状态。", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
