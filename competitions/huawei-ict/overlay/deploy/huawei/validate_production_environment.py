#!/usr/bin/env python3
"""Prevent inherited shell variables from silently changing production Compose."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import sys


# These variables are assigned explicitly by deploy-docker.sh / the deployment
# transaction after this check. They are never taken from the caller for Compose.
INTERNAL = {"WEB_BIND_ADDRESS", "WEB_PORT", "ZHIYAN_ENV_FILE",
            "ZHIYAN_DEPLOY_ROOT", "ZHIYAN_RELEASE_DIR"}
# These change which services are run, which file is used, or image architecture;
# they are not application settings and have no role in this deployment.
CONTROL = {"COMPOSE_PROFILES", "COMPOSE_ENV_FILES", "COMPOSE_REMOVE_ORPHANS",
           "DOCKER_DEFAULT_PLATFORM"}
REFERENCE = re.compile(r"(?<!\$)\$\{([A-Za-z_][A-Za-z_0-9]*)")
EXPANSION = re.compile(r"(?<!\$)\$(?:\{|[A-Za-z_])")
DATABASE_IDENTIFIER = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,62}\Z")


def validate_database_identity(values: dict[str, str]) -> None:
    # Compose uses ${POSTGRES_USER:-zhiyan} / ${POSTGRES_DB:-zhiyan} in
    # both database initialization and the backend connection URL. Restrict
    # names to URL-safe characters so delimiters cannot change URL parsing.
    for name in ("POSTGRES_USER", "POSTGRES_DB"):
        value = values.get(name) or "zhiyan"
        if not DATABASE_IDENTIFIER.fullmatch(value):
            raise ValueError(f"{name} 必须为 1–63 位字母、数字、下划线、点或连字符，且首位为字母、数字或下划线")


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name, value = name.strip(), value.strip()
        if name in values:
            raise ValueError(f"生产配置存在重复项：{name}")
        # Compose expands $VAR/${VAR} in unquoted and double-quoted .env values.
        # A single-quoted value is literal and does not inherit a caller variable.
        if not (len(value) >= 2 and value.startswith("'") and value.endswith("'")) and EXPANSION.search(value):
            raise ValueError(f"生产配置项 {name} 引用了其他环境变量；请填写固定值或使用单引号")
        values[name] = value.strip("\"'")
    return values


def conflicts(values: dict[str, str], environ: dict[str, str], compose_texts: list[str]) -> list[str]:
    names = set().union(*(set(REFERENCE.findall(text)) for text in compose_texts)) - INTERNAL
    # Also include keys from the env file: a future Compose edit must not make an
    # inherited production setting silently authoritative before this is reviewed.
    names.update(values.keys())
    names.difference_update(INTERNAL)
    result = {name for name in names if name in environ and
              (name not in values or environ[name] != values[name])}
    result.update(name for name in CONTROL if environ.get(name))
    return sorted(result)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("env_file", type=Path)
    parser.add_argument("compose_files", nargs="+", type=Path)
    args = parser.parse_args()
    try:
        values = read_env(args.env_file)
        validate_database_identity(values)
        bad = conflicts(values, dict(os.environ), [p.read_text(encoding="utf-8") for p in args.compose_files])
        if bad:
            raise ValueError("继承的环境变量会覆盖或改变生产配置，请取消后重试：" + ", ".join(bad))
    except (OSError, ValueError) as exc:
        print(f"生产环境预检失败：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
