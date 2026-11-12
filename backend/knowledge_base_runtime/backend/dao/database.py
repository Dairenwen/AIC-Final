from __future__ import annotations

import re
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from knowledge_base_runtime.backend.config.settings import (
    DATABASE_URL,
    DB_BACKEND,
    DB_PATH,
    KB_DB_SCHEMA,
    ensure_data_dirs,
)


KNOWLEDGE_TABLES = (
    "papers",
    "search_index",
    "paper_chunks",
    "paper_fulltexts",
    "user_collections",
    "collection_papers",
    "upload_tasks",
    "agent_jobs",
    "audit_logs",
    "crawler_task_runs",
    "qa_generation_runs",
    "qa_candidates",
    "qa_review_sessions",
    "qa_review_items",
    "dpo_generation_runs",
    "dpo_pairs",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def db_label() -> str:
    return "PostgreSQL" if DB_BACKEND == "postgresql" else "SQLite"


@contextmanager
def get_db() -> Iterator[Any]:
    ensure_data_dirs()
    if DB_BACKEND == "postgresql":
        conn = _connect_postgres()
        wrapper = PostgresConnection(conn)
    else:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        wrapper = SQLiteConnection(conn)
    try:
        yield wrapper
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def row_to_dict(row: Any | None) -> dict | None:
    return dict(row) if row is not None else None


def init_db(db_path: Path | None = None) -> None:
    ensure_data_dirs()
    if DB_BACKEND == "postgresql":
        _init_postgres()
    else:
        _init_sqlite(db_path)


class SQLiteConnection:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def execute(self, sql: str, params: list | tuple | None = None):
        return self.conn.execute(sql, params or [])

    def executescript(self, sql: str) -> None:
        self.conn.executescript(sql)


class PostgresConnection:
    def __init__(self, conn: Any):
        self.conn = conn

    def execute(self, sql: str, params: list | tuple | None = None):
        sql = _convert_sqlite_sql_to_postgres(sql)
        return self.conn.execute(sql, params or [])

    def executescript(self, sql: str) -> None:
        for statement in _split_sql_script(sql):
            self.execute(statement)


def _connect_postgres():
    local_packages = Path(__file__).resolve().parents[1] / ".local_packages"
    if local_packages.exists() and str(local_packages) not in sys.path:
        sys.path.insert(0, str(local_packages))
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:
        raise RuntimeError(
            "当前环境没有安装 psycopg，无法连接 PostgreSQL。请先执行：pip install \"psycopg[binary]\""
        ) from exc
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL 未设置，无法连接 PostgreSQL。")
    return psycopg.connect(
        DATABASE_URL,
        row_factory=dict_row,
        options=f"-c search_path={KB_DB_SCHEMA},public",
    )


def _convert_sqlite_sql_to_postgres(sql: str) -> str:
    converted = sql.strip()
    converted = re.sub(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", "INSERT INTO", converted, flags=re.IGNORECASE)
    if "INSERT INTO" in converted.upper() and "ON CONFLICT" not in converted.upper():
        converted = f"{converted} ON CONFLICT DO NOTHING"
    converted = _replace_qmark_placeholders(converted)
    return converted


def _replace_qmark_placeholders(sql: str) -> str:
    parts: list[str] = []
    in_single = False
    in_double = False
    for ch in sql:
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        if ch == "?" and not in_single and not in_double:
            parts.append("%s")
        else:
            parts.append(ch)
    return "".join(parts)


def _split_sql_script(sql: str) -> list[str]:
    return [part.strip() for part in sql.split(";") if part.strip()]


def _init_sqlite(db_path: Path | None = None) -> None:
    target = db_path or DB_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target)
    try:
        _migrate_sqlite_service_accounts(conn)
        conn.executescript(SQLITE_SCHEMA)
        _ensure_sqlite_fulltext_lifecycle_schema(conn)
        _ensure_sqlite_crawler_schema(conn)
        _ensure_sqlite_chunk_metadata_columns(conn)
        _ensure_sqlite_qa_schema(conn)
        _ensure_sqlite_audit_schema(conn)
        now = utc_now()
        conn.execute(
            """
            INSERT OR IGNORE INTO user_collections(user_id, collection_name, created_at)
            VALUES (?, ?, ?)
            """,
            ("demo-user", "默认文献库", now),
        )
        conn.commit()
    finally:
        conn.close()


def _init_postgres() -> None:
    conn = _connect_postgres()
    try:
        with conn.cursor() as cur:
            # Every worker runs the embedded runtime initializer. Keep the
            # advisory lock on the same connection that performs ALTER TABLE,
            # otherwise separate connections can still deadlock.
            cur.execute("SELECT pg_advisory_lock(hashtext('zhiyan-kb-schema-init'))")
            _prepare_postgres_schema(cur)
            for statement in _split_sql_script(POSTGRES_SCHEMA):
                cur.execute(statement)
            _ensure_postgres_fulltext_lifecycle_schema(cur)
            _ensure_postgres_crawler_schema(cur)
            cur.execute("ALTER TABLE paper_chunks ADD COLUMN IF NOT EXISTS section_path TEXT")
            cur.execute("ALTER TABLE paper_chunks ADD COLUMN IF NOT EXISTS parent_chunk_id TEXT")
            _ensure_postgres_qa_schema(cur)
            _ensure_postgres_audit_schema(cur)
            cur.execute(
                """
                INSERT INTO user_collections(user_id, collection_name, created_at)
                VALUES (%s, %s, %s)
                ON CONFLICT(user_id, collection_name) DO NOTHING
                """,
                ("demo-user", "默认文献库", utc_now()),
            )
        conn.commit()
    finally:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(hashtext('zhiyan-kb-schema-init'))")
        except Exception:
            pass
        conn.close()


def _migrate_sqlite_service_accounts(conn: sqlite3.Connection) -> None:
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    if "users" in tables and "service_accounts" not in tables:
        conn.execute("ALTER TABLE users RENAME TO service_accounts")


def _prepare_postgres_schema(cur: Any) -> None:
    """Select the KB schema and adopt tables from the legacy public layout.

    The migration only runs when the target schema has no papers table and the
    public table matches the knowledge platform's distinctive columns. This
    avoids moving an unrelated application table named ``papers``.
    """
    cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{KB_DB_SCHEMA}"')
    cur.execute(
        """
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'papers'
              AND column_name = 'parse_status'
        ) AS legacy,
        to_regclass(%s) IS NOT NULL AS initialized
        """,
        (f"{KB_DB_SCHEMA}.papers",),
    )
    state = cur.fetchone()
    if state["legacy"] and not state["initialized"]:
        for table in KNOWLEDGE_TABLES:
            cur.execute("SELECT to_regclass(%s) AS relation", (f"public.{table}",))
            if cur.fetchone()["relation"] is not None:
                cur.execute(
                    f'ALTER TABLE public."{table}" SET SCHEMA "{KB_DB_SCHEMA}"'
                )
        cur.execute(
            """
            SELECT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'users'
                  AND column_name = 'api_key'
            ) AS legacy_accounts
            """
        )
        if cur.fetchone()["legacy_accounts"]:
            cur.execute(f'ALTER TABLE public.users SET SCHEMA "{KB_DB_SCHEMA}"')

    cur.execute(f'SET search_path TO "{KB_DB_SCHEMA}", public')
    cur.execute(
        "SELECT to_regclass(%s) AS users, to_regclass(%s) AS accounts",
        (f"{KB_DB_SCHEMA}.users", f"{KB_DB_SCHEMA}.service_accounts"),
    )
    account_state = cur.fetchone()
    if account_state["users"] is not None and account_state["accounts"] is None:
        cur.execute(f'ALTER TABLE "{KB_DB_SCHEMA}".users RENAME TO service_accounts')


def _ensure_sqlite_chunk_metadata_columns(conn: sqlite3.Connection) -> None:
    """Upgrade databases created before splitter metadata was persisted."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(paper_chunks)")}
    if "section_path" not in columns:
        conn.execute("ALTER TABLE paper_chunks ADD COLUMN section_path TEXT")
    if "parent_chunk_id" not in columns:
        conn.execute("ALTER TABLE paper_chunks ADD COLUMN parent_chunk_id TEXT")


def _ensure_sqlite_fulltext_lifecycle_schema(conn: sqlite3.Connection) -> None:
    paper_columns = {row[1] for row in conn.execute("PRAGMA table_info(papers)")}
    paper_lifecycle_columns = {
        "parse_error": "TEXT",
        "upload_time": "TEXT",
        "parse_finish_time": "TEXT",
        "chunk_gen_time": "TEXT",
        "vector_index_time": "TEXT",
        "last_refresh_time": "TEXT",
        "update_time": "TEXT",
        "last_access_time": "TEXT",
        "delete_time": "TEXT",
    }
    for name, column_type in paper_lifecycle_columns.items():
        if name not in paper_columns:
            conn.execute(f"ALTER TABLE papers ADD COLUMN {name} {column_type}")

    chunk_columns = {row[1] for row in conn.execute("PRAGMA table_info(paper_chunks)")}
    chunk_lifecycle_columns = {
        "splitter": "TEXT",
        "cut_method": "TEXT",
        "chunk_create_time": "TEXT",
        "chunk_update_time": "TEXT",
        "chunk_expire_time": "TEXT",
    }
    for name, column_type in chunk_lifecycle_columns.items():
        if name not in chunk_columns:
            conn.execute(f"ALTER TABLE paper_chunks ADD COLUMN {name} {column_type}")

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS paper_fulltexts (
            paper_id TEXT PRIMARY KEY,
            minio_pdf_key TEXT,
            extraction_method TEXT NOT NULL DEFAULT 'fallback_decode',
            clean_strategy TEXT NOT NULL DEFAULT 'body_sections_v1',
            raw_text TEXT,
            clean_text TEXT,
            raw_chars INTEGER NOT NULL DEFAULT 0,
            clean_chars INTEGER NOT NULL DEFAULT 0,
            mojibake_hits INTEGER NOT NULL DEFAULT 0,
            parse_finish_time TEXT NOT NULL,
            upload_time TEXT NOT NULL,
            update_time TEXT NOT NULL,
            FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_papers_parse_status ON papers(parse_status);
        CREATE INDEX IF NOT EXISTS idx_papers_delete_time ON papers(delete_time);
        CREATE INDEX IF NOT EXISTS idx_paper_fulltexts_update_time ON paper_fulltexts(update_time);
        CREATE INDEX IF NOT EXISTS idx_chunks_expire_time ON paper_chunks(chunk_expire_time);
        """
    )


def _ensure_postgres_fulltext_lifecycle_schema(cur: Any) -> None:
    for column in (
        "parse_error TEXT",
        "upload_time TEXT",
        "parse_finish_time TEXT",
        "chunk_gen_time TEXT",
        "vector_index_time TEXT",
        "last_refresh_time TEXT",
        "update_time TEXT",
        "last_access_time TEXT",
        "delete_time TEXT",
    ):
        cur.execute(f"ALTER TABLE papers ADD COLUMN IF NOT EXISTS {column}")
    for column in (
        "splitter TEXT",
        "cut_method TEXT",
        "chunk_create_time TEXT",
        "chunk_update_time TEXT",
        "chunk_expire_time TEXT",
    ):
        cur.execute(f"ALTER TABLE paper_chunks ADD COLUMN IF NOT EXISTS {column}")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS paper_fulltexts (
            paper_id TEXT PRIMARY KEY REFERENCES papers(id) ON DELETE CASCADE,
            minio_pdf_key TEXT,
            extraction_method TEXT NOT NULL DEFAULT 'fallback_decode',
            clean_strategy TEXT NOT NULL DEFAULT 'body_sections_v1',
            raw_text TEXT,
            clean_text TEXT,
            raw_chars INTEGER NOT NULL DEFAULT 0,
            clean_chars INTEGER NOT NULL DEFAULT 0,
            mojibake_hits INTEGER NOT NULL DEFAULT 0,
            parse_finish_time TEXT NOT NULL,
            upload_time TEXT NOT NULL,
            update_time TEXT NOT NULL
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_papers_parse_status ON papers(parse_status)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_papers_delete_time ON papers(delete_time)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_paper_fulltexts_update_time ON paper_fulltexts(update_time)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_chunks_expire_time ON paper_chunks(chunk_expire_time)")


def _ensure_sqlite_crawler_schema(conn: sqlite3.Connection) -> None:
    paper_columns = {row[1] for row in conn.execute("PRAGMA table_info(papers)")}
    crawler_columns = {
        "arxiv_url": "TEXT",
        "project_url": "TEXT",
        "source_url": "TEXT",
        "arxiv_id": "TEXT",
        "citations": "INTEGER",
        "tasks": "TEXT NOT NULL DEFAULT '[]'",
        "methods": "TEXT NOT NULL DEFAULT '[]'",
        "source_file": "TEXT",
        "raw_metadata": "TEXT",
        "metadata_updated_at": "TEXT",
    }
    for name, column_type in crawler_columns.items():
        if name not in paper_columns:
            conn.execute(f"ALTER TABLE papers ADD COLUMN {name} {column_type}")

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS crawler_task_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_name TEXT NOT NULL,
            task_start_time TEXT NOT NULL,
            task_end_time TEXT NOT NULL,
            add_paper_count INTEGER NOT NULL DEFAULT 0,
            skip_paper_count INTEGER NOT NULL DEFAULT 0,
            exception_create_time TEXT,
            crawl_exit_code INTEGER,
            import_exit_code INTEGER,
            log_file TEXT,
            summary TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_crawler_task_runs_start ON crawler_task_runs(task_start_time);
        CREATE INDEX IF NOT EXISTS idx_papers_metadata_updated_at ON papers(metadata_updated_at);
        CREATE INDEX IF NOT EXISTS idx_papers_arxiv_id ON papers(arxiv_id);
        """
    )


def _ensure_postgres_crawler_schema(cur: Any) -> None:
    for column in (
        "arxiv_url TEXT",
        "project_url TEXT",
        "source_url TEXT",
        "arxiv_id TEXT",
        "citations INTEGER",
        "tasks TEXT NOT NULL DEFAULT '[]'",
        "methods TEXT NOT NULL DEFAULT '[]'",
        "source_file TEXT",
        "raw_metadata TEXT",
        "metadata_updated_at TEXT",
    ):
        cur.execute(f"ALTER TABLE papers ADD COLUMN IF NOT EXISTS {column}")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS crawler_task_runs (
            id SERIAL PRIMARY KEY,
            task_name TEXT NOT NULL,
            task_start_time TEXT NOT NULL,
            task_end_time TEXT NOT NULL,
            add_paper_count INTEGER NOT NULL DEFAULT 0,
            skip_paper_count INTEGER NOT NULL DEFAULT 0,
            exception_create_time TEXT,
            crawl_exit_code INTEGER,
            import_exit_code INTEGER,
            log_file TEXT,
            summary TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_crawler_task_runs_start ON crawler_task_runs(task_start_time)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_papers_metadata_updated_at ON papers(metadata_updated_at)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_papers_arxiv_id ON papers(arxiv_id)")


def _ensure_sqlite_qa_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SQLITE_QA_SCHEMA)


def _ensure_postgres_qa_schema(cur: Any) -> None:
    for statement in _split_sql_script(POSTGRES_QA_SCHEMA):
        cur.execute(statement)


def _ensure_sqlite_audit_schema(conn: sqlite3.Connection) -> None:
    rows = conn.execute("PRAGMA table_info(audit_logs)").fetchall()
    columns = {row[1] for row in rows}
    required = {
        "log_id",
        "operate_time",
        "operate_user_id",
        "operate_name",
        "user_ip",
        "operate_type",
        "operate_sub_type",
        "target_resource_type",
        "target_resource_id",
        "resource_title",
        "operate_content",
        "is_system_op",
    }
    if required.issubset(columns):
        _ensure_sqlite_audit_indexes(conn)
        return
    if columns:
        conn.execute("ALTER TABLE audit_logs RENAME TO audit_logs_legacy")
    conn.executescript(SQLITE_AUDIT_SCHEMA)
    if columns:
        conn.execute(
            """
            INSERT INTO audit_logs(
                operate_time,
                operate_user_id,
                operate_name,
                user_ip,
                operate_type,
                operate_sub_type,
                target_resource_type,
                target_resource_id,
                resource_title,
                operate_content,
                is_system_op
            )
            SELECT
                COALESCE(created_at, datetime('now')),
                user_id,
                COALESCE(user_id, '未知用户'),
                COALESCE(ip, '-'),
                CASE action
                    WHEN 'INGEST_METADATA' THEN 'PAPER_INGEST'
                    WHEN 'UPLOAD_PDF' THEN 'PAPER_INGEST'
                    WHEN 'UPSERT_USER_PAPER' THEN 'PAPER_INGEST'
                    WHEN 'SLICE' THEN 'CHUNK'
                    WHEN 'MODIFY' THEN 'METADATA_CHANGE'
                    WHEN 'DELETE' THEN 'METADATA_CHANGE'
                    WHEN 'RETRY_PARSE' THEN 'SYSTEM_PERMISSION'
                    WHEN 'INVOKE_AGENT' THEN 'AGENT'
                    ELSE 'SYSTEM_PERMISSION'
                END,
                CASE action
                    WHEN 'INGEST_METADATA' THEN 'CRAWLER_METADATA_INGEST'
                    WHEN 'UPLOAD_PDF' THEN 'PDF_AUTO_PARSE'
                    WHEN 'UPSERT_USER_PAPER' THEN 'PAPER_CREATE'
                    WHEN 'SLICE' THEN 'MANUAL_CHUNK'
                    WHEN 'MODIFY' THEN 'METADATA_UPDATE'
                    WHEN 'DELETE' THEN 'ARCHIVE_DELETE'
                    WHEN 'RETRY_PARSE' THEN 'EXCEPTION_RETRY'
                    WHEN 'INVOKE_AGENT' THEN 'AGENT_INVOKE'
                    ELSE COALESCE(action, 'UNKNOWN')
                END,
                object_type,
                object_id,
                object_id,
                COALESCE(detail, '{}'),
                CASE WHEN user_id = 'system' THEN 1 ELSE 0 END
            FROM audit_logs_legacy
            """
        )
        conn.execute("DROP TABLE audit_logs_legacy")
    _ensure_sqlite_audit_indexes(conn)


def _ensure_postgres_audit_schema(cur: Any) -> None:
    cur.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'audit_logs'
        """
    )
    columns = {row["column_name"] for row in cur.fetchall()}
    required = {
        "log_id",
        "operate_time",
        "operate_user_id",
        "operate_name",
        "user_ip",
        "operate_type",
        "operate_sub_type",
        "target_resource_type",
        "target_resource_id",
        "resource_title",
        "operate_content",
        "is_system_op",
    }
    if required.issubset(columns):
        _ensure_postgres_audit_indexes(cur)
        return
    if columns:
        cur.execute("ALTER TABLE audit_logs RENAME TO audit_logs_legacy")
    for statement in _split_sql_script(POSTGRES_AUDIT_SCHEMA):
        cur.execute(statement)
    if columns:
        cur.execute(
            """
            INSERT INTO audit_logs(
                operate_time,
                operate_user_id,
                operate_name,
                user_ip,
                operate_type,
                operate_sub_type,
                target_resource_type,
                target_resource_id,
                resource_title,
                operate_content,
                is_system_op
            )
            SELECT
                COALESCE(created_at, NOW()::TEXT),
                user_id,
                COALESCE(user_id, '未知用户'),
                COALESCE(ip, '-'),
                CASE action
                    WHEN 'INGEST_METADATA' THEN 'PAPER_INGEST'
                    WHEN 'UPLOAD_PDF' THEN 'PAPER_INGEST'
                    WHEN 'UPSERT_USER_PAPER' THEN 'PAPER_INGEST'
                    WHEN 'SLICE' THEN 'CHUNK'
                    WHEN 'MODIFY' THEN 'METADATA_CHANGE'
                    WHEN 'DELETE' THEN 'METADATA_CHANGE'
                    WHEN 'RETRY_PARSE' THEN 'SYSTEM_PERMISSION'
                    WHEN 'INVOKE_AGENT' THEN 'AGENT'
                    ELSE 'SYSTEM_PERMISSION'
                END,
                CASE action
                    WHEN 'INGEST_METADATA' THEN 'CRAWLER_METADATA_INGEST'
                    WHEN 'UPLOAD_PDF' THEN 'PDF_AUTO_PARSE'
                    WHEN 'UPSERT_USER_PAPER' THEN 'PAPER_CREATE'
                    WHEN 'SLICE' THEN 'MANUAL_CHUNK'
                    WHEN 'MODIFY' THEN 'METADATA_UPDATE'
                    WHEN 'DELETE' THEN 'ARCHIVE_DELETE'
                    WHEN 'RETRY_PARSE' THEN 'EXCEPTION_RETRY'
                    WHEN 'INVOKE_AGENT' THEN 'AGENT_INVOKE'
                    ELSE COALESCE(action, 'UNKNOWN')
                END,
                object_type,
                object_id,
                object_id,
                COALESCE(detail, '{}'),
                CASE WHEN user_id = 'system' THEN TRUE ELSE FALSE END
            FROM audit_logs_legacy
            """
        )
        cur.execute("DROP TABLE audit_logs_legacy")
    _ensure_postgres_audit_indexes(cur)


def _ensure_sqlite_audit_indexes(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_operate_time ON audit_logs(operate_time)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_operate_type ON audit_logs(operate_type)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_target ON audit_logs(target_resource_type, target_resource_id)")


def _ensure_postgres_audit_indexes(cur: Any) -> None:
    cur.execute("CREATE INDEX IF NOT EXISTS idx_audit_operate_time ON audit_logs(operate_time)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_audit_operate_type ON audit_logs(operate_type)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_audit_target ON audit_logs(target_resource_type, target_resource_id)")


SQLITE_SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS papers (
    id TEXT PRIMARY KEY,
    publish_venue TEXT,
    title TEXT NOT NULL,
    publish_year INTEGER,
    abstract TEXT,
    keywords TEXT NOT NULL DEFAULT '[]',
    pdf_url TEXT,
    arxiv_url TEXT,
    github_url TEXT,
    project_url TEXT,
    source_url TEXT,
    arxiv_id TEXT,
    citations INTEGER,
    related_papers TEXT NOT NULL DEFAULT '[]',
    tasks TEXT NOT NULL DEFAULT '[]',
    methods TEXT NOT NULL DEFAULT '[]',
    research_area TEXT,
    subfield TEXT,
    task_name TEXT,
    paper_url TEXT,
    source_page TEXT,
    citation_count INTEGER DEFAULT 0,
    author TEXT NOT NULL DEFAULT '[]',
    minio_pdf_key TEXT,
    parse_status INTEGER DEFAULT 1,
    parse_error TEXT,
    upload_time TEXT,
    parse_finish_time TEXT,
    chunk_gen_time TEXT,
    vector_index_time TEXT,
    last_refresh_time TEXT,
    update_time TEXT,
    last_access_time TEXT,
    delete_time TEXT,
    metadata_updated_at TEXT,
    source_file TEXT,
    raw_metadata TEXT,
    source TEXT DEFAULT 'crawler',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS search_index (
    paper_id TEXT PRIMARY KEY,
    searchable_text TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS paper_chunks (
    chunk_id TEXT PRIMARY KEY,
    paper_id TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    content TEXT NOT NULL,
    page_no INTEGER,
    vector_key TEXT,
    section_path TEXT,
    parent_chunk_id TEXT,
    splitter TEXT,
    cut_method TEXT,
    chunk_create_time TEXT,
    chunk_update_time TEXT,
    chunk_expire_time TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_papers_year ON papers(publish_year);
CREATE INDEX IF NOT EXISTS idx_papers_venue ON papers(publish_venue);
CREATE INDEX IF NOT EXISTS idx_papers_area ON papers(research_area);
CREATE INDEX IF NOT EXISTS idx_chunks_paper ON paper_chunks(paper_id);

CREATE TABLE IF NOT EXISTS user_collections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    collection_name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(user_id, collection_name)
);

CREATE TABLE IF NOT EXISTS collection_papers (
    collection_id INTEGER NOT NULL,
    paper_id TEXT NOT NULL,
    note TEXT,
    added_at TEXT NOT NULL,
    PRIMARY KEY (collection_id, paper_id),
    FOREIGN KEY (collection_id) REFERENCES user_collections(id) ON DELETE CASCADE,
    FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS upload_tasks (
    task_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    status TEXT NOT NULL,
    temp_key TEXT,
    title TEXT,
    authors TEXT NOT NULL DEFAULT '[]',
    paper_id TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_jobs (
    job_id TEXT PRIMARY KEY,
    agent_type TEXT NOT NULL,
    paper_ids TEXT NOT NULL DEFAULT '[]',
    extra_params TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL,
    result TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_logs (
    log_id INTEGER PRIMARY KEY AUTOINCREMENT,
    operate_time TEXT NOT NULL,
    operate_user_id TEXT,
    operate_name TEXT,
    user_ip TEXT,
    operate_type TEXT NOT NULL,
    operate_sub_type TEXT NOT NULL,
    target_resource_type TEXT,
    target_resource_id TEXT,
    resource_title TEXT,
    operate_content TEXT NOT NULL DEFAULT '{}',
    is_system_op INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS crawler_task_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_name TEXT NOT NULL,
    task_start_time TEXT NOT NULL,
    task_end_time TEXT NOT NULL,
    add_paper_count INTEGER NOT NULL DEFAULT 0,
    skip_paper_count INTEGER NOT NULL DEFAULT 0,
    exception_create_time TEXT,
    crawl_exit_code INTEGER,
    import_exit_code INTEGER,
    log_file TEXT,
    summary TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS service_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    role TEXT NOT NULL DEFAULT '普通用户',
    api_key TEXT NOT NULL,
    call_count INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


SQLITE_AUDIT_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_logs (
    log_id INTEGER PRIMARY KEY AUTOINCREMENT,
    operate_time TEXT NOT NULL,
    operate_user_id TEXT,
    operate_name TEXT,
    user_ip TEXT,
    operate_type TEXT NOT NULL,
    operate_sub_type TEXT NOT NULL,
    target_resource_type TEXT,
    target_resource_id TEXT,
    resource_title TEXT,
    operate_content TEXT NOT NULL DEFAULT '{}',
    is_system_op INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_audit_operate_time ON audit_logs(operate_time);
CREATE INDEX IF NOT EXISTS idx_audit_operate_type ON audit_logs(operate_type);
CREATE INDEX IF NOT EXISTS idx_audit_target ON audit_logs(target_resource_type, target_resource_id);
"""


SQLITE_QA_SCHEMA = """
CREATE TABLE IF NOT EXISTS qa_generation_runs (
    run_id TEXT PRIMARY KEY,
    user_id TEXT,
    status TEXT NOT NULL,
    model TEXT,
    batch_config TEXT NOT NULL DEFAULT '{}',
    chunk_ids TEXT NOT NULL DEFAULT '[]',
    qa_count INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    missing_count INTEGER NOT NULL DEFAULT 0,
    errors TEXT NOT NULL DEFAULT '[]',
    missing_chunk_ids TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS qa_candidates (
    candidate_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    chunk_id TEXT NOT NULL,
    paper_id TEXT,
    paper_short_name TEXT,
    paper_title TEXT,
    chunk_index INTEGER,
    section TEXT,
    page INTEGER,
    content_type TEXT,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    evidence_quote TEXT NOT NULL,
    qa_type TEXT,
    generator_model TEXT,
    review_submitted INTEGER NOT NULL DEFAULT 0,
    review_session_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES qa_generation_runs(run_id) ON DELETE CASCADE,
    FOREIGN KEY (chunk_id) REFERENCES paper_chunks(chunk_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS qa_review_sessions (
    review_session_id TEXT PRIMARY KEY,
    source_run_id TEXT,
    user_id TEXT,
    status TEXT NOT NULL,
    total_count INTEGER NOT NULL DEFAULT 0,
    pending_count INTEGER NOT NULL DEFAULT 0,
    decided_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    closed_at TEXT
);

CREATE TABLE IF NOT EXISTS qa_review_items (
    review_item_id TEXT PRIMARY KEY,
    review_session_id TEXT NOT NULL,
    candidate_id TEXT,
    chunk_id TEXT,
    paper_id TEXT,
    paper_short_name TEXT,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    evidence_quote TEXT NOT NULL,
    current_decision TEXT NOT NULL DEFAULT 'PENDING',
    decision_source TEXT,
    reviewer TEXT,
    automatic_triage_policy_version TEXT,
    automatic_routing_reasons TEXT NOT NULL DEFAULT '[]',
    review_comment TEXT,
    reviewed INTEGER NOT NULL DEFAULT 0,
    reviewed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (review_session_id) REFERENCES qa_review_sessions(review_session_id) ON DELETE CASCADE,
    FOREIGN KEY (candidate_id) REFERENCES qa_candidates(candidate_id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_qa_candidates_run ON qa_candidates(run_id);
CREATE INDEX IF NOT EXISTS idx_qa_candidates_chunk ON qa_candidates(chunk_id);
CREATE INDEX IF NOT EXISTS idx_qa_candidates_review ON qa_candidates(review_submitted, created_at);
CREATE INDEX IF NOT EXISTS idx_qa_review_sessions_updated ON qa_review_sessions(updated_at);
CREATE INDEX IF NOT EXISTS idx_qa_review_items_session ON qa_review_items(review_session_id);
CREATE INDEX IF NOT EXISTS idx_qa_review_items_decision ON qa_review_items(current_decision);

CREATE TABLE IF NOT EXISTS dpo_generation_runs (
    run_id TEXT PRIMARY KEY,
    user_id TEXT,
    status TEXT NOT NULL,
    generator_version TEXT NOT NULL,
    chunk_ids TEXT NOT NULL DEFAULT '[]',
    dpo_count INTEGER NOT NULL DEFAULT 0,
    blocked_count INTEGER NOT NULL DEFAULT 0,
    missing_count INTEGER NOT NULL DEFAULT 0,
    blocked_chunk_ids TEXT NOT NULL DEFAULT '[]',
    missing_chunk_ids TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dpo_pairs (
    dpo_pair_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    chunk_id TEXT NOT NULL,
    paper_id TEXT,
    paper_title TEXT,
    prompt TEXT NOT NULL,
    chosen TEXT NOT NULL,
    rejected TEXT NOT NULL,
    generator_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES dpo_generation_runs(run_id) ON DELETE CASCADE,
    FOREIGN KEY (candidate_id) REFERENCES qa_candidates(candidate_id) ON DELETE CASCADE,
    FOREIGN KEY (chunk_id) REFERENCES paper_chunks(chunk_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_dpo_pairs_run ON dpo_pairs(run_id);
CREATE INDEX IF NOT EXISTS idx_dpo_pairs_chunk ON dpo_pairs(chunk_id);
CREATE INDEX IF NOT EXISTS idx_dpo_pairs_candidate ON dpo_pairs(candidate_id);
"""


POSTGRES_SCHEMA = """
CREATE TABLE IF NOT EXISTS papers (
    id TEXT PRIMARY KEY,
    publish_venue TEXT,
    title TEXT NOT NULL,
    publish_year INTEGER,
    abstract TEXT,
    keywords TEXT NOT NULL DEFAULT '[]',
    pdf_url TEXT,
    arxiv_url TEXT,
    github_url TEXT,
    project_url TEXT,
    source_url TEXT,
    arxiv_id TEXT,
    citations INTEGER,
    related_papers TEXT NOT NULL DEFAULT '[]',
    tasks TEXT NOT NULL DEFAULT '[]',
    methods TEXT NOT NULL DEFAULT '[]',
    research_area TEXT,
    subfield TEXT,
    task_name TEXT,
    paper_url TEXT,
    source_page TEXT,
    citation_count INTEGER DEFAULT 0,
    author TEXT NOT NULL DEFAULT '[]',
    minio_pdf_key TEXT,
    parse_status INTEGER DEFAULT 1,
    parse_error TEXT,
    upload_time TEXT,
    parse_finish_time TEXT,
    chunk_gen_time TEXT,
    vector_index_time TEXT,
    last_refresh_time TEXT,
    update_time TEXT,
    last_access_time TEXT,
    delete_time TEXT,
    metadata_updated_at TEXT,
    source_file TEXT,
    raw_metadata TEXT,
    source TEXT DEFAULT 'crawler',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS search_index (
    paper_id TEXT PRIMARY KEY REFERENCES papers(id) ON DELETE CASCADE,
    searchable_text TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_chunks (
    chunk_id TEXT PRIMARY KEY,
    paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    content TEXT NOT NULL,
    page_no INTEGER,
    vector_key TEXT,
    section_path TEXT,
    parent_chunk_id TEXT,
    splitter TEXT,
    cut_method TEXT,
    chunk_create_time TEXT,
    chunk_update_time TEXT,
    chunk_expire_time TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_papers_year ON papers(publish_year);
CREATE INDEX IF NOT EXISTS idx_papers_venue ON papers(publish_venue);
CREATE INDEX IF NOT EXISTS idx_papers_area ON papers(research_area);
CREATE INDEX IF NOT EXISTS idx_chunks_paper ON paper_chunks(paper_id);

CREATE TABLE IF NOT EXISTS user_collections (
    id SERIAL PRIMARY KEY,
    user_id TEXT NOT NULL,
    collection_name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(user_id, collection_name)
);

CREATE TABLE IF NOT EXISTS collection_papers (
    collection_id INTEGER NOT NULL REFERENCES user_collections(id) ON DELETE CASCADE,
    paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    note TEXT,
    added_at TEXT NOT NULL,
    PRIMARY KEY (collection_id, paper_id)
);

CREATE TABLE IF NOT EXISTS upload_tasks (
    task_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    status TEXT NOT NULL,
    temp_key TEXT,
    title TEXT,
    authors TEXT NOT NULL DEFAULT '[]',
    paper_id TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_jobs (
    job_id TEXT PRIMARY KEY,
    agent_type TEXT NOT NULL,
    paper_ids TEXT NOT NULL DEFAULT '[]',
    extra_params TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL,
    result TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_logs (
    log_id SERIAL PRIMARY KEY,
    operate_time TEXT NOT NULL,
    operate_user_id TEXT,
    operate_name TEXT,
    user_ip TEXT,
    operate_type TEXT NOT NULL,
    operate_sub_type TEXT NOT NULL,
    target_resource_type TEXT,
    target_resource_id TEXT,
    resource_title TEXT,
    operate_content TEXT NOT NULL DEFAULT '{}',
    is_system_op BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS crawler_task_runs (
    id SERIAL PRIMARY KEY,
    task_name TEXT NOT NULL,
    task_start_time TEXT NOT NULL,
    task_end_time TEXT NOT NULL,
    add_paper_count INTEGER NOT NULL DEFAULT 0,
    skip_paper_count INTEGER NOT NULL DEFAULT 0,
    exception_create_time TEXT,
    crawl_exit_code INTEGER,
    import_exit_code INTEGER,
    log_file TEXT,
    summary TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS service_accounts (
    id SERIAL PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    role TEXT NOT NULL DEFAULT '普通用户',
    api_key TEXT NOT NULL,
    call_count INTEGER NOT NULL DEFAULT 0,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


POSTGRES_AUDIT_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_logs (
    log_id SERIAL PRIMARY KEY,
    operate_time TEXT NOT NULL,
    operate_user_id TEXT,
    operate_name TEXT,
    user_ip TEXT,
    operate_type TEXT NOT NULL,
    operate_sub_type TEXT NOT NULL,
    target_resource_type TEXT,
    target_resource_id TEXT,
    resource_title TEXT,
    operate_content TEXT NOT NULL DEFAULT '{}',
    is_system_op BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_audit_operate_time ON audit_logs(operate_time);
CREATE INDEX IF NOT EXISTS idx_audit_operate_type ON audit_logs(operate_type);
CREATE INDEX IF NOT EXISTS idx_audit_target ON audit_logs(target_resource_type, target_resource_id);
"""


POSTGRES_QA_SCHEMA = """
CREATE TABLE IF NOT EXISTS qa_generation_runs (
    run_id TEXT PRIMARY KEY,
    user_id TEXT,
    status TEXT NOT NULL,
    model TEXT,
    batch_config TEXT NOT NULL DEFAULT '{}',
    chunk_ids TEXT NOT NULL DEFAULT '[]',
    qa_count INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    missing_count INTEGER NOT NULL DEFAULT 0,
    errors TEXT NOT NULL DEFAULT '[]',
    missing_chunk_ids TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS qa_candidates (
    candidate_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES qa_generation_runs(run_id) ON DELETE CASCADE,
    chunk_id TEXT NOT NULL REFERENCES paper_chunks(chunk_id) ON DELETE CASCADE,
    paper_id TEXT,
    paper_short_name TEXT,
    paper_title TEXT,
    chunk_index INTEGER,
    section TEXT,
    page INTEGER,
    content_type TEXT,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    evidence_quote TEXT NOT NULL,
    qa_type TEXT,
    generator_model TEXT,
    review_submitted BOOLEAN NOT NULL DEFAULT FALSE,
    review_session_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS qa_review_sessions (
    review_session_id TEXT PRIMARY KEY,
    source_run_id TEXT,
    user_id TEXT,
    status TEXT NOT NULL,
    total_count INTEGER NOT NULL DEFAULT 0,
    pending_count INTEGER NOT NULL DEFAULT 0,
    decided_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    closed_at TEXT
);

CREATE TABLE IF NOT EXISTS qa_review_items (
    review_item_id TEXT PRIMARY KEY,
    review_session_id TEXT NOT NULL REFERENCES qa_review_sessions(review_session_id) ON DELETE CASCADE,
    candidate_id TEXT REFERENCES qa_candidates(candidate_id) ON DELETE SET NULL,
    chunk_id TEXT,
    paper_id TEXT,
    paper_short_name TEXT,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    evidence_quote TEXT NOT NULL,
    current_decision TEXT NOT NULL DEFAULT 'PENDING',
    decision_source TEXT,
    reviewer TEXT,
    automatic_triage_policy_version TEXT,
    automatic_routing_reasons TEXT NOT NULL DEFAULT '[]',
    review_comment TEXT,
    reviewed BOOLEAN NOT NULL DEFAULT FALSE,
    reviewed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_qa_candidates_run ON qa_candidates(run_id);
CREATE INDEX IF NOT EXISTS idx_qa_candidates_chunk ON qa_candidates(chunk_id);
CREATE INDEX IF NOT EXISTS idx_qa_candidates_review ON qa_candidates(review_submitted, created_at);
CREATE INDEX IF NOT EXISTS idx_qa_review_sessions_updated ON qa_review_sessions(updated_at);
CREATE INDEX IF NOT EXISTS idx_qa_review_items_session ON qa_review_items(review_session_id);
CREATE INDEX IF NOT EXISTS idx_qa_review_items_decision ON qa_review_items(current_decision);

CREATE TABLE IF NOT EXISTS dpo_generation_runs (
    run_id TEXT PRIMARY KEY,
    user_id TEXT,
    status TEXT NOT NULL,
    generator_version TEXT NOT NULL,
    chunk_ids TEXT NOT NULL DEFAULT '[]',
    dpo_count INTEGER NOT NULL DEFAULT 0,
    blocked_count INTEGER NOT NULL DEFAULT 0,
    missing_count INTEGER NOT NULL DEFAULT 0,
    blocked_chunk_ids TEXT NOT NULL DEFAULT '[]',
    missing_chunk_ids TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dpo_pairs (
    dpo_pair_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES dpo_generation_runs(run_id) ON DELETE CASCADE,
    candidate_id TEXT NOT NULL REFERENCES qa_candidates(candidate_id) ON DELETE CASCADE,
    chunk_id TEXT NOT NULL REFERENCES paper_chunks(chunk_id) ON DELETE CASCADE,
    paper_id TEXT,
    paper_title TEXT,
    prompt TEXT NOT NULL,
    chosen TEXT NOT NULL,
    rejected TEXT NOT NULL,
    generator_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_dpo_pairs_run ON dpo_pairs(run_id);
CREATE INDEX IF NOT EXISTS idx_dpo_pairs_chunk ON dpo_pairs(chunk_id);
CREATE INDEX IF NOT EXISTS idx_dpo_pairs_candidate ON dpo_pairs(candidate_id);
"""
