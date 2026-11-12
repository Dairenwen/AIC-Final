import importlib.util
from pathlib import Path

import pytest
from sqlalchemy.engine import make_url


def restore_module():
    path = Path(__file__).resolve().parents[2] / 'deploy/huawei/restore_database.py'
    spec = importlib.util.spec_from_file_location('restore_database_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_unreviewed_sql_cannot_be_restored():
    with pytest.raises(ValueError, match='differs'):
        restore_module().prepare_dump(b'DROP DATABASE production;')


@pytest.mark.parametrize('url', [
    'postgresql+psycopg://user:password@remote/zhiyan_migration_test',
    'postgresql+psycopg://user:password@127.0.0.1/production',
    'postgresql+psycopg://user:password@zhiyan-migration-db/production',
])
def test_local_restore_exception_cannot_target_other_databases(url):
    with pytest.raises(ValueError):
        restore_module().pg_environment(make_url(url), local_test=True)


def test_production_restore_requires_verified_tls():
    with pytest.raises(ValueError, match='TLS'):
        restore_module().pg_environment(make_url('postgresql+psycopg://u:p@rds/zhiyan?sslmode=disable'))


def test_psql_environment_does_not_inherit_other_database_settings(monkeypatch):
    monkeypatch.setenv('PGSERVICE', 'production')
    result = restore_module().pg_environment(
        make_url('postgresql+psycopg://u:p%40ss@zhiyan-migration-db/zhiyan_migration_test'), local_test=True)
    assert 'PGSERVICE' not in result
    assert result['PGPASSWORD'] == 'p@ss'
    assert result['PGDATABASE'] == 'zhiyan_migration_test'
