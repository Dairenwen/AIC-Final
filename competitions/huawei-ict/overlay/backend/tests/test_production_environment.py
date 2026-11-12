"""Inherited variables cannot silently override production configuration."""
import importlib.util
from pathlib import Path
import os
import subprocess
import sys

import pytest
from sqlalchemy.engine import make_url


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy/huawei/validate_production_environment.py"
spec = importlib.util.spec_from_file_location("production_environment", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
COMPOSE = [(ROOT / name).read_text(encoding="utf-8") for name in
           ("deploy/docker/docker-compose.yml", "deploy/huawei/docker-production.yml")]


@pytest.mark.parametrize("name", ["ELASTICSEARCH_PASSWORD", "PUBLIC_ORIGIN", "POSTGRES_PASSWORD"])
def test_conflicting_production_shell_value_is_rejected_without_exposing_it(name):
    result = module.conflicts({name: "file-secret"}, {name: "shell-secret"}, COMPOSE)
    assert result == [name]
    assert "secret" not in str(result)


def test_missing_optional_file_value_is_not_filled_from_shell():
    assert module.conflicts({}, {"POSTGRES_PORT": "9999"}, COMPOSE) == ["POSTGRES_PORT"]
    assert module.conflicts({}, {"POSTGRES_PORT": ""}, COMPOSE) == ["POSTGRES_PORT"]


def test_equal_values_and_docker_connection_settings_are_preserved():
    values = {"ELASTICSEARCH_PASSWORD": "same", "PUBLIC_ORIGIN": "https://example.org"}
    env = {**values, "DOCKER_HOST": "unix:///tmp/example.sock", "DOCKER_CONTEXT": "example",
           "WEB_PORT": "100", "ZHIYAN_ENV_FILE": "/untrusted"}
    assert module.conflicts(values, env, COMPOSE) == []


@pytest.mark.parametrize("name", ["COMPOSE_PROFILES", "COMPOSE_ENV_FILES", "COMPOSE_REMOVE_ORPHANS", "DOCKER_DEFAULT_PLATFORM"])
def test_inherited_compose_and_platform_controls_are_rejected(name):
    assert module.conflicts({}, {name: "enabled"}, COMPOSE) == [name]


def test_dynamic_compose_reference_is_also_checked():
    assert module.conflicts({}, {"FUTURE_SETTING": "value"}, ["environment: ${FUTURE_SETTING:-default}"]) == ["FUTURE_SETTING"]
    assert module.conflicts({}, {"ESCAPED": "value"}, ["command: $${ESCAPED}"]) == []


def test_env_file_variable_expansion_rejected_but_single_quoted_literal_allowed(tmp_path):
    env = tmp_path / "config"
    env.write_text('USER_API_MODEL_API_KEY="prefix-${FOREIGN}"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="USER_API_MODEL_API_KEY"):
        module.read_env(env)
    env.write_text("USER_API_MODEL_API_KEY='prefix-${FOREIGN}'\n", encoding="utf-8")
    assert module.read_env(env)["USER_API_MODEL_API_KEY"] == "prefix-${FOREIGN}"


def test_cli_error_reports_only_variable_name(tmp_path):
    env = tmp_path / "config"
    env.write_text("ELASTICSEARCH_PASSWORD=file-secret-value\n", encoding="utf-8")
    caller = {**os.environ, "ELASTICSEARCH_PASSWORD": "shell-secret-value"}
    command = [sys.executable, str(SCRIPT), str(env), str(ROOT / "deploy/docker/docker-compose.yml"),
               str(ROOT / "deploy/huawei/docker-production.yml")]
    result = subprocess.run(command, env=caller, capture_output=True, text=True)
    assert result.returncode == 1
    assert "ELASTICSEARCH_PASSWORD" in result.stderr
    assert "file-secret-value" not in result.stderr and "shell-secret-value" not in result.stderr


@pytest.mark.parametrize("values", [
    {},
    {"POSTGRES_USER": "", "POSTGRES_DB": ""},
    {"POSTGRES_USER": "zhiyan", "POSTGRES_DB": "zhiyan"},
    {"POSTGRES_USER": "user_1.dev-test", "POSTGRES_DB": "research_2026.v1"},
    {"POSTGRES_USER": "u" * 63, "POSTGRES_DB": "d" * 63},
])
def test_database_names_use_compose_defaults_and_round_trip_through_sqlalchemy(values):
    module.validate_database_identity(values)
    user = values.get("POSTGRES_USER") or "zhiyan"
    database = values.get("POSTGRES_DB") or "zhiyan"
    parsed = make_url(f"postgresql+psycopg://{user}:example_password@db:5432/{database}")
    assert (parsed.username, parsed.host, parsed.database) == (user, "db", database)


@pytest.mark.parametrize("name", ["POSTGRES_USER", "POSTGRES_DB"])
@pytest.mark.parametrize("value", ["demo:name", "name@host", "name/path", "two words", "line\nbreak", "name%40host", "a" * 64])
def test_database_names_reject_url_delimiters_and_oversized_identifiers(name, value):
    with pytest.raises(ValueError, match=name):
        module.validate_database_identity({name: value})


def test_production_compose_uses_managed_database_and_cache():
    """Production data lives in RDS and DCS Redis, not in host containers."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    production = (root / "deploy/huawei/docker-production.yml").read_text(encoding="utf-8")
    base = (root / "deploy/docker/docker-compose.yml").read_text(encoding="utf-8")
    template = (root / "deploy/huawei/production.env.example").read_text(encoding="utf-8")

    assert "${POSTGRES_HOST:?" in production and "${REDIS_URL:?" in production
    assert 'profiles: ["bundled-services"]' in production
    assert "depends_on: !override" in production
    # Local runs keep working without any of the managed-service settings.
    assert "${POSTGRES_HOST:-db}" in base and "${REDIS_URL:-redis://redis:6379/0}" in base
    for name in ("POSTGRES_HOST=", "POSTGRES_SERVER_PORT=", "REDIS_URL="):
        assert name in template
