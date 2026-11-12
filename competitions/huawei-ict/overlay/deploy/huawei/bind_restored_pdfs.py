#!/usr/bin/env python3
"""Bind restored PDF filenames to existing paper IDs; preserve all other data."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def existing_pdf_bindings(paper_rows, pdf_names):
    """Use exact IDs only. Never replace an existing object key or create papers."""
    names = {name for name in pdf_names if Path(name).name == name and name.endswith('.pdf')}
    return [(f'/pdfs/{paper_id}.pdf', paper_id)
            for paper_id, object_key in paper_rows
            if not str(object_key or '').strip() and f'{paper_id}.pdf' in names]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', type=Path, required=True)
    parser.add_argument('--apply', action='store_true', help='Update empty PDF keys in the restored target database.')
    args = parser.parse_args()
    if args.apply and sys.platform != 'linux':
        parser.error('Apply on the prepared Linux target only; leave the handoff backup unchanged.')
    from dotenv import dotenv_values
    from sqlalchemy.engine import make_url
    import psycopg
    from psycopg import sql

    values = dotenv_values(args.env_file)
    data_dir = values.get('KB_DATA_DIR')
    if not data_dir or not Path(data_dir).is_absolute():
        parser.error('KB_DATA_DIR must be the configured absolute target data directory.')
    pdf_dir = Path(data_dir) / 'pdfs'
    if not pdf_dir.is_dir():
        parser.error('Restore the downloaded arxiv/*.pdf directly into KB_DATA_DIR/pdfs first.')
    database_url = values.get('DATABASE_URL')
    if not database_url:
        parser.error('Configure the restored target DATABASE_URL first.')
    url = make_url(database_url)
    if url.drivername != 'postgresql+psycopg':
        parser.error('Keep the original PostgreSQL database engine.')
    connection_url = url.set(drivername='postgresql').render_as_string(hide_password=False)
    table = sql.Identifier(values.get('KB_DB_SCHEMA') or 'knowledge_base', 'papers')
    try:
        with psycopg.connect(connection_url, connect_timeout=5) as connection:
            with connection.cursor() as cursor:
                if not args.apply:
                    cursor.execute('SET TRANSACTION READ ONLY')
                cursor.execute(sql.SQL('SELECT id, minio_pdf_key FROM {}').format(table))
                rows = cursor.fetchall()
                files = {path.name for path in pdf_dir.glob('*.pdf') if path.is_file()}
                bindings = existing_pdf_bindings(rows, files)
                if args.apply and bindings:
                    cursor.executemany(sql.SQL(
                        "UPDATE {} SET minio_pdf_key = %s WHERE id = %s "
                        "AND COALESCE(BTRIM(minio_pdf_key), '') = ''"
                    ).format(table), bindings)
                print(f'PDFs: {len(files)}; existing papers: {len(rows)}; '
                      f'{"bound" if args.apply else "eligible"}: {len(bindings)}.')
                print('No paper deletion, task changes, model calls, chunking or index rebuild.')
    except psycopg.Error as exc:
        # Connection errors can include private connection information.
        print(f'Target database operation failed: {type(exc).__name__}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
