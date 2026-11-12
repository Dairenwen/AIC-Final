#!/usr/bin/env python3
"""Verify the restored DB and bind its identity to the application data volume.

This does not change the database. The identity file detects a subsequently
swapped/recreated PostgreSQL cluster or database, including an empty database
paired with a data volume that already claims restoration is complete.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import tempfile

BACKUP_SHA256 = "3324d6374bc80a71e5e1eee62485ecb2d3af058f13e5e072252c61da265c5a1e"
COPY = re.compile(rb"^COPY ([a-z_][a-z_0-9]*)\.([a-z_][a-z_0-9]*) \(")
JOURNAL = "db-restore-journal.json"


def read_journal(path: Path, identity: dict[str, str]) -> dict[str, str]:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink not in (1, 2) or info.st_mode & 0o077:
        raise ValueError("恢复凭据类型或权限不安全")
    pending = None
    if info.st_nlink == 2:
        # A crash after link() but before unlink() leaves precisely the
        # published file and its private staging name pointing to one inode.
        # Do not accept other hard links or search outside the state directory.
        matches = []
        for candidate in path.parent.iterdir():
            if not re.fullmatch(r"\.db-restore-journal-[A-Za-z0-9_]{8}", candidate.name):
                continue
            candidate_info = candidate.lstat()
            if (stat.S_ISREG(candidate_info.st_mode) and candidate_info.st_dev == info.st_dev
                    and candidate_info.st_ino == info.st_ino):
                matches.append(candidate)
        if len(matches) != 1:
            raise ValueError("恢复凭据存在无法识别的硬链接")
        pending = matches[0]
    record = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(record, dict) or set(record) != {"cluster", "database", "backup", "token"}
            or record.get("cluster") != identity["cluster"] or record.get("database") != identity["database"]
            or record.get("backup") != BACKUP_SHA256 or not isinstance(record.get("token"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", record["token"])):
        raise ValueError("恢复凭据与目标数据库或备份不匹配")
    if pending is not None:
        try:
            pending.unlink()
        except FileNotFoundError:
            pass  # another legitimate reader may already have removed it
    return record


def prepare_journal(path: Path, identity: dict[str, str]) -> dict[str, str]:
    if path.exists() or path.is_symlink():
        return read_journal(path, identity)
    record = {**identity, "backup": BACKUP_SHA256, "token": secrets.token_hex(32)}
    fd, temp = tempfile.mkstemp(prefix=".db-restore-journal-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(record, output, sort_keys=True)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        try:
            os.link(temp, path, follow_symlinks=False)
        except FileExistsError:
            return read_journal(path, identity)
        dir_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
        return record
    finally:
        try:
            os.unlink(temp)
        except FileNotFoundError:
            pass


def verify_committed_restore(connection, text, journal: dict[str, str]) -> None:
    exists = connection.execute(text("SELECT to_regclass('__zhiyan_deploy.restore_proof') IS NOT NULL")).scalar_one()
    if not exists:
        raise ValueError("数据库缺少匹配的恢复凭据，不会自动接管")
    proof = connection.execute(text("SELECT token, backup FROM __zhiyan_deploy.restore_proof")).one()
    if tuple(proof) != (journal["token"], journal["backup"]):
        raise ValueError("数据库恢复凭据不匹配，不会自动接管")


def expected_tables(path: Path) -> set[tuple[str, str]]:
    digest = hashlib.sha256()
    expected = set()
    with path.open("rb") as source:
        for line in source:
            digest.update(line)
            match = COPY.match(line)
            if match:
                expected.add(tuple(part.decode("ascii") for part in match.groups()))
    if digest.hexdigest() != BACKUP_SHA256 or len(expected) != 100:
        raise ValueError("数据库备份与已核验版本不一致，停止启动")
    return expected


def check_complete(expected, actual):
    missing = expected - actual
    if missing:
        raise ValueError(f"数据库缺少 {len(missing)} 张历史表；停止启动，未修改数据库")


def check_binding(path: Path, identity: dict[str, str]):
    if not path.is_file():
        raise ValueError("数据卷缺少数据库身份记录；请先核对备份和当前数据库，再显式绑定")
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("数据库身份记录无法读取；停止启动") from exc
    if stored != identity:
        raise ValueError("数据库与数据卷身份不一致；停止启动，未修改数据")


def write_binding(path: Path, identity: dict[str, str]):
    # Never expose a partially written binding if the process stops. Publish
    # the fully synced file only if no binding exists, then sync the directory.
    fd, temp = tempfile.mkstemp(prefix=".db-identity-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(identity, output, sort_keys=True)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        try:
            os.link(temp, path, follow_symlinks=False)
        except FileExistsError:
            check_binding(path, identity)
            return
        dir_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        os.unlink(temp)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("before", "prepare", "after", "adopt-existing"))
    parser.add_argument("--dump", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        expected = expected_tables(args.dump)
        from sqlalchemy import create_engine, text
        engine = create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
        with engine.connect() as connection:
            identity_row = connection.execute(text(
                "SELECT system_identifier::text, d.oid::text "
                "FROM pg_control_system(), pg_database d WHERE d.datname=current_database()"
            )).one()
            identity = {"cluster": identity_row[0], "database": identity_row[1]}
            actual = set(connection.execute(text(
                "SELECT n.nspname, c.relname FROM pg_class c "
                "JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE c.relkind IN ('r','p') AND n.nspname IN ('zhiyan','zhiyan1','knowledge_base')"
            )).all())
            existing_schemas = connection.execute(text(
                "SELECT count(*) FROM pg_namespace "
                "WHERE nspname IN ('zhiyan','zhiyan1','knowledge_base')"
            )).scalar_one()
        marker = args.state_dir / "db-initialised"
        binding = args.state_dir / "db-identity.json"
        journal_path = args.state_dir / JOURNAL
        if args.stage == "prepare":
            if marker.exists() or binding.exists() or existing_schemas:
                raise ValueError("只有无业务结构、无恢复标记的新库才能准备恢复凭据")
            prepare_journal(journal_path, identity)
            return 0
        if args.stage == "before" and not marker.exists() and not binding.exists():
            if not existing_schemas:
                return 0
        check_complete(expected, actual)
        if args.stage == "before":
            if not binding.exists() and not binding.is_symlink():
                # The database transaction may have committed immediately before
                # the container or host stopped. Only a proof written in that
                # very transaction and a matching durable private journal allow
                # an automatic binding; arbitrary pre-existing DBs fail closed.
                journal = read_journal(journal_path, identity)
                with engine.connect() as connection:
                    verify_committed_restore(connection, text, journal)
                write_binding(binding, identity)
            check_binding(binding, identity)
        elif args.stage == "after":
            if marker.exists():
                check_binding(binding, identity)
            else:
                write_binding(binding, identity)
        else:
            write_binding(binding, identity)
        return 0
    except (OSError, ValueError, KeyError) as exc:
        print(f"[zhiyan] 数据库状态检查失败：{exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        # Database diagnostics may include connection details; do not print them.
        print(f"[zhiyan] 数据库状态检查失败：{type(exc).__name__}；检查数据库访问权限", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
