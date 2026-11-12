#!/usr/bin/env python3
"""Read-only migration preflight. Never invokes models/SMS or provisions resources."""
from __future__ import annotations
import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / 'backend'
sys.path.insert(0, str(BACKEND))


def inspect_literature_ppt(config, *, required: bool) -> list[dict]:
    paths = (
        ('literature-PPT parser', config.LITERATURE_PPT_RUNTIME_ROOT / 'document_parser.py'),
        ('literature-PPT builder', config.LITERATURE_PPT_RUNTIME_ROOT / 'ppt_builder.py'),
        ('literature-PPT interpreter', config.LITERATURE_PPT_PYTHON),
    )
    return [{'item': name, 'ok': Path(path).is_file(), 'path': str(path),
             'required': required,
             'purpose': 'Required only when the restored tool catalog activates literature_ppt.'}
            for name, path in paths]


def inspect_sources(config) -> list[dict]:
    from app.agents.paper_reading.service import _resolve_uv_executable
    checks = []
    def check(name, path, kind='file', required=True):
        path = Path(path)
        good = path.is_dir() if kind == 'dir' else path.is_file()
        checks.append({'item': name, 'ok': good, 'path': str(path)})
        if not required:
            checks[-1].update(required=False, purpose='Import source; existing skills are restored from PostgreSQL.')
    check('innovation runtime', config.INNOVATION_AGENT_ROOT / 'innovation_agent.py')
    check('compliance runtime', config.COMPLIANCE_AGENT_ROOT / 'app', 'dir')
    check('translation runtime', config.TRANSLATION_AGENT_ROOT / 'agent-core/src/academic_translation/cli.py')
    check('paper-reading runtime', config.PAPER_READING_RUNTIME_ROOT / 'scripts/run_real_pdf_agent.py')
    check('BGE-M3 semantic splitter', BACKEND / 'chunker.py')
    for asset in ('vue.global.prod.js', 'echarts.min.js'):
        check('knowledge-base browser dependency ' + asset,
              config.KNOWLEDGE_BASE_ROOT / 'frontend/assets/vendor' / asset)
    # Source contains this optional tool, but the deployed catalog may not.
    # The read-only database inspection below decides whether it is required.
    checks += inspect_literature_ppt(config, required=False)
    check('formula interpreter', config.FORMULA_RECOGNITION_PYTHON)
    check('formula weights', config.FORMULA_RECOGNITION_ROOT / 'unimernet/models/unimernet_base/pytorch_model.pth')
    check('skill crawl file', config.SKILL_CRAWL_FILE, required=False)
    vendor = config.PATENT_DRAFTING_RUNTIME_ROOT / 'vendor/patent-disclosure-skill'
    for name in ('md_to_docx.py', 'mermaid_render.py', 'cnipa_epub_search.py'):
        check('patent vendor ' + name, vendor / 'tools' / name)
    checks.append({'item': 'paper-reading uv runner', 'ok': _resolve_uv_executable(config.PAPER_READING_UV_EXECUTABLE) is not None})
    for module in ('flask', 'psycopg', 'langgraph', 'langchain_openai', 'docx', 'fitz'):
        checks.append({'item': 'dependency ' + module, 'ok': importlib.util.find_spec(module) is not None})
    return checks


def local_knowledge_checks(environ=None) -> list[dict]:
    """Check local retrieval assets without opening the index or loading weights."""
    environ = os.environ if environ is None else environ
    checks = []
    local_embedding = environ.get('KB_EMBEDDING_BACKEND', 'api').strip().lower() == 'local'
    local_reranker = environ.get('KB_RERANKER_BACKEND', 'api').strip().lower() == 'local'
    if local_embedding or local_reranker:
        for module in ('torch', 'FlagEmbedding', 'transformers', 'sentencepiece'):
            checks.append({'item': 'local BGE dependency ' + module,
                           'ok': importlib.util.find_spec(module) is not None})
    models = []
    if local_embedding:
        models.append(('BGE-M3', 'BGE_M3_MODEL', (
            'config.json', 'tokenizer_config.json', 'tokenizer.json',
            'sentencepiece.bpe.model', 'pytorch_model.bin',
            'colbert_linear.pt', 'sparse_linear.pt',
        )))
    if local_reranker:
        models.append(('BGE reranker', 'BGE_RERANKER_MODEL', (
            'config.json', 'tokenizer_config.json', 'tokenizer.json',
            'sentencepiece.bpe.model', 'model.safetensors',
        )))
    for label, variable, filenames in models:
        configured = environ.get(variable, '').strip()
        directory = Path(configured) if configured else None
        missing = [name for name in filenames if directory is None
                   or not (directory / name).is_file() or (directory / name).stat().st_size == 0]
        checks.append({'item': label + ' local files', 'ok': not missing,
                       'missing_files': missing,
                       'limits': 'Presence only; loading, inference and memory usage still need target-server acceptance.'})
    if environ.get('KB_MILVUS_ENABLED', '0').strip().lower() not in {'0', 'false', ''}:
        for module in ('pymilvus', 'milvus_lite'):
            checks.append({'item': 'local index dependency ' + module,
                           'ok': importlib.util.find_spec(module) is not None})
        uri = environ.get('KB_MILVUS_URI', '').strip()
        parsed = urlparse(uri)
        if parsed.scheme in {'http', 'https', 'tcp'}:
            checks.append({'item': 'configured remote Milvus', 'ok': True,
                           'limits': 'Connection and collection have not been verified.'})
        else:
            index = Path(uri) if uri else None
            collection = environ.get('KB_MILVUS_COLLECTION', 'paper_chunks_vec').strip()
            checks.append({'item': 'restored local Milvus index',
                           'ok': index is not None and (
                               (index.is_file() and index.stat().st_size > 0)
                               or (index.is_dir() and (index / 'collections' / collection / 'schema.json').is_file()
                                   and (index / 'collections' / collection / 'manifest.json').is_file())),
                           'limits': 'Read-only presence check; no lock, conversion or index creation.'})
    return checks


def production_checks(config) -> list[dict]:
    from sqlalchemy.engine import make_url
    api_url = urlparse(str(getattr(config, 'USER_API_MODEL_BASE_URL', '') or '').strip())
    api_configured = (
        api_url.scheme in {'http', 'https'} and bool(api_url.hostname)
        and bool(str(getattr(config, 'USER_API_MODEL_NAME', '') or '').strip())
        and bool(str(getattr(config, 'USER_API_MODEL_API_KEY', '') or '').strip())
    )
    checks = [
        {'item': 'Linux Python 3.12', 'ok': sys.platform == 'linux' and sys.version_info[:2] == (3, 12)},
        {'item': 'non-placeholder SECRET_KEY', 'ok': bool(config.SECRET_KEY) and
            not any(word in config.SECRET_KEY.lower() for word in ('change_me', 'replace', 'dev-only')) and len(config.SECRET_KEY) >= 32},
        {'item': 'secure session cookies', 'ok': config.AUTH_COOKIE_SECURE},
        {'item': 'configured model endpoint', 'ok': bool(config.QWEN_DPO_BASE_URL) or api_configured,
         'limits': 'Configuration only; credentials and default model must still pass runtime verification.'},
        {'item': 'frontend build', 'ok': (ROOT / 'frontend/dist/index.html').is_file()},
        {'item': 'core venv', 'ok': (BACKEND / '.venv/bin/gunicorn').is_file()},
    ]
    try:
        url = make_url(config.SQLALCHEMY_DATABASE_URI)
        checks.append({'item': 'RDS PostgreSQL and verified TLS', 'ok':
            url.drivername == 'postgresql+psycopg' and bool(url.host) and
            url.query.get('sslmode') == 'verify-full' and
            bool(url.query.get('sslrootcert')) and Path(url.query['sslrootcert']).is_file()})
    except Exception:
        checks.append({'item': 'RDS PostgreSQL and verified TLS', 'ok': False})
    return checks


def database_checks(config) -> list[dict]:
    from sqlalchemy import create_engine, inspect, select, func, text
    from app.extensions import db
    from app import model_registry  # noqa: F401
    from app.models import Task, Skill, Tool
    engine = None
    checks = []
    try:
        engine = create_engine(config.SQLALCHEMY_DATABASE_URI, connect_args={'connect_timeout': 5})
        inspector = inspect(engine)
        for table in db.metadata.sorted_tables:
            if not inspector.has_table(table.name, schema=table.schema):
                checks.append({'item': f'schema {table.fullname}', 'ok': False, 'missing_table': True})
                continue
            existing = {column['name'] for column in inspector.get_columns(table.name, schema=table.schema)}
            missing = sorted({column.name for column in table.columns} - existing)
            checks.append({'item': f'schema {table.fullname}', 'ok': not missing, 'missing_columns': missing})
        for table in ('papers', 'paper_chunks', 'qa_candidates', 'dpo_pairs', 'service_accounts'):
            checks.append({'item': 'knowledge-base schema ' + table,
                'ok': inspector.has_table(table, schema=config.KNOWLEDGE_BASE_DB_SCHEMA)})
        checks.append({'item': 'platform settings table', 'ok': inspector.has_table('system_settings', schema='zhiyan')})
        if inspector.has_table(Tool.__tablename__, schema='zhiyan'):
            with engine.connect() as connection:
                ppt_count = connection.scalar(select(func.count()).select_from(Tool).where(
                    Tool.code == 'literature_ppt', Tool.status == 'ACTIVE'))
            checks.append({'item': 'restored literature-PPT activation', 'ok': True,
                           'active': bool(ppt_count)})
            if ppt_count:
                checks += inspect_literature_ppt(config, required=True)
        if inspector.has_table(Skill.__tablename__, schema='zhiyan'):
            with engine.connect() as connection:
                skill_count = connection.scalar(select(func.count()).select_from(Skill))
                checks.append({'item': 'restored skill catalog', 'ok': bool(skill_count), 'rows': skill_count,
                               'hint': 'Restore existing skills, or import the original skill JSON.'})
        else:
            checks.append({'item': 'restored skill catalog', 'ok': False, 'missing_table': True})
        if inspector.has_table(Task.__tablename__, schema='zhiyan'):
            with engine.connect() as connection:
                active = connection.scalar(select(func.count()).select_from(Task).where(Task.status.in_(['QUEUED', 'RUNNING'])))
                checks.append({'item': 'tasks drained before release', 'ok': active == 0, 'active_tasks': active})
        else:
            checks.append({'item': 'tasks drained before release', 'ok': False, 'missing_table': True})
    except Exception as exc:
        # Do not print exception strings: driver errors can include credentials.
        checks.append({'item': 'database inspection', 'ok': False, 'error_type': type(exc).__name__})
    finally:
        if engine is not None:
            engine.dispose()
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', type=Path)
    parser.add_argument('--production', action='store_true')
    parser.add_argument('--database', action='store_true', help='read-only DB connection; no model/SMS calls')
    parser.add_argument('--allow-incomplete', action='store_true', help='source review only; forbidden with --production')
    args = parser.parse_args()
    if args.production and args.allow_incomplete:
        parser.error('--production cannot allow incomplete resources')
    from dotenv import load_dotenv
    if args.env_file is not None and not args.env_file.is_file():
        parser.error('The explicitly selected environment file does not exist.')
    # An explicit deployment file must win over stale variables in the shell.
    load_dotenv(args.env_file or BACKEND / '.env', override=args.env_file is not None)
    from app.config import Config
    checks = inspect_sources(Config)
    if args.production:
        checks += production_checks(Config)
        checks += local_knowledge_checks()
    if args.database:
        checks += database_checks(Config)
    ready = all(item['ok'] for item in checks if item.get('required', True))
    print(json.dumps({'status': 'ready' if ready else 'blocked', 'checks': checks,
        'limits': 'No real inference, retrieval quality, original-data completeness or Linux deployment verified by source checks.'},
        ensure_ascii=False, indent=2))
    return 0 if ready or args.allow_incomplete else 1


if __name__ == '__main__':
    raise SystemExit(main())
