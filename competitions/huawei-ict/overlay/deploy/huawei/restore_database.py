#!/usr/bin/env python3
"""Restore the supplied, verified PostgreSQL 18 backup into an empty database.

Default is offline inspection. --apply is target-only, atomic, and preserves
the original backup. The approved historical-task correction runs inside the
same transaction, before any application process starts.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import os
from pathlib import Path
import re
import subprocess
import sys

BACKUP_SHA256 = '3324d6374bc80a71e5e1eee62485ecb2d3af058f13e5e072252c61da265c5a1e'
ROOT = Path(__file__).resolve().parents[2]
GUARD = """
SELECT pg_advisory_xact_lock(20261025, 1);
DO $$ BEGIN
 IF current_setting('server_version_num')::int < 180000 THEN
  RAISE EXCEPTION 'PostgreSQL 18 or later required';
 END IF;
 IF EXISTS (SELECT 1 FROM pg_namespace WHERE nspname IN ('zhiyan','zhiyan1','knowledge_base'))
 OR EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE c.relkind IN ('r','p','v','m','S','f')
            AND n.nspname NOT LIKE 'pg_%' AND n.nspname <> 'information_schema') THEN
  RAISE EXCEPTION 'Restore requires an empty target database';
 END IF;
END $$;
"""
FINALIZE = """
-- User approved only the unfinished tasks imported from this backup.
-- Keep input/output/progress/history and all successful/failed task records.
WITH interrupted AS (
 UPDATE zhiyan.tasks SET status='FAILED', error_code='MIGRATION_INTERRUPTED',
 safe_error_message='迁移前任务未完成', current_step='迁移前任务未完成', finished_at=now()
 WHERE status IN ('QUEUED','RUNNING') RETURNING id
)
INSERT INTO zhiyan.task_events(id, task_id, sequence, event_type, payload, created_at)
SELECT gen_random_uuid(), i.id,
 COALESCE((SELECT MAX(sequence) FROM zhiyan.task_events e WHERE e.task_id=i.id),0)+1,
 'task.failed', '{"message":"迁移前任务未完成","reason":"migration_interrupted","auto_retry":false}'::jsonb, now()
FROM interrupted i;
UPDATE zhiyan.paper_reading_runs SET status='FAILED', completed_at=now()
WHERE task_id IN (SELECT id FROM zhiyan.tasks WHERE error_code='MIGRATION_INTERRUPTED')
AND status IN ('QUEUED','RUNNING');
"""


def prepare_dump(raw: bytes) -> tuple[str, dict[str, int]]:
    if hashlib.sha256(raw).hexdigest() != BACKUP_SHA256:
        raise ValueError('Backup differs from the reviewed handoff; review it before restoring.')
    source = raw.decode('utf-8')
    # Only this fixed, reviewed dump is accepted. Never apply a generic regex
    # rewrite to arbitrary SQL or to COPY payloads.
    begin = source.index('CREATE SCHEMA knowledge_base;')
    preamble_end = source.index('ALTER TABLE ONLY ')
    source = source[:preamble_end] + source[begin:]
    lines, in_copy, tables, rows, owners = [], False, 0, 0, 0
    for line in io.StringIO(source):
        if in_copy:
            lines.append(line)
            if line.strip() == r'\.':
                in_copy = False
            else:
                rows += 1
            continue
        if line.startswith(('\\restrict ', '\\unrestrict ')):
            continue
        if line.strip() == 'GRANT ALL ON SCHEMA public TO zhiyan;':
            # The new database owner already owns restored objects. Do not
            # recreate the old server login or grant rights to another account.
            continue
        if re.fullmatch(r'ALTER (SCHEMA|TABLE|SEQUENCE|FUNCTION|VIEW) .+ OWNER TO postgres;\s*', line):
            owners += 1
            continue
        if line.startswith('COPY '):
            tables += 1
            in_copy = True
        lines.append(line)
    if in_copy or tables != 100 or owners != 111:
        raise ValueError('Unexpected backup structure.')
    return GUARD + ''.join(lines) + FINALIZE, {'tables': tables, 'rows': rows, 'owners_rebound': owners}


def pg_environment(url, *, local_test=False):
    if url.drivername != 'postgresql+psycopg' or not url.database or not url.username:
        raise ValueError('Configure a complete PostgreSQL DATABASE_URL.')
    if local_test:
        if url.host not in {'localhost', '127.0.0.1', 'zhiyan-migration-db'} or url.database != 'zhiyan_migration_test':
            raise ValueError('Local test is restricted to the isolated migration-test database.')
    elif url.query.get('sslmode') != 'verify-full' or not Path(url.query.get('sslrootcert', '')).is_file():
        raise ValueError('Production restore requires verified TLS and the RDS CA file.')
    # No inherited PG* options, client startup file, credential argv, or query output.
    env = {k: v for k, v in os.environ.items() if not k.startswith('PG')}
    env.update(PGHOST=url.host or '', PGPORT=str(url.port or 5432), PGDATABASE=url.database,
               PGUSER=url.username, PGPASSWORD=url.password or '', PGCONNECT_TIMEOUT='10',
               PGAPPNAME='zhiyan-migration-restore', PGCLIENTENCODING='UTF8')
    for key, name in [('sslmode', 'PGSSLMODE'), ('sslrootcert', 'PGSSLROOTCERT')]:
        if url.query.get(key):
            env[name] = url.query[key]
    return env


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dump', type=Path, required=True)
    parser.add_argument('--env-file', type=Path)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--local-test', action='store_true')
    args = parser.parse_args()
    payload, counts = prepare_dump(args.dump.read_bytes())
    print(f'Backup verified: {counts}; historical unfinished tasks will be marked failed only on restore.')
    if not args.apply:
        print('Offline inspection only. No database connection or writes.')
        return 0
    if sys.platform != 'linux' or not args.env_file:
        parser.error('--apply requires Linux and an explicit --env-file.')
    from dotenv import dotenv_values
    from sqlalchemy.engine import make_url
    try:
        env = pg_environment(make_url(dotenv_values(args.env_file).get('DATABASE_URL') or ''), local_test=args.local_test)
        result = subprocess.run(['psql', '-X', '--no-password', '--single-transaction',
                                 '--set=ON_ERROR_STOP=1', '--quiet', '--file=-'],
                                input=payload, text=True, env=env, capture_output=True)
    except (ValueError, OSError):
        print('Invalid target configuration or missing PostgreSQL client.', file=sys.stderr)
        return 1
    if result.returncode:
        # psql diagnostics may contain private COPY row contents. Never print them.
        print('Restore failed and transaction rolled back. Check empty target, PG18+, TLS and privileges.', file=sys.stderr)
        return 1
    print('Restore committed. No model credentials rewritten, seeds imported or model calls made.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
