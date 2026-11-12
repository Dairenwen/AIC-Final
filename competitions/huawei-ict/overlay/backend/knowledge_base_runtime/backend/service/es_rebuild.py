"""Build a new keyword index and switch its alias after complete verification.

The caller must hold the knowledge-base maintenance lock for the entire call.
Old indices are never deleted. A small journal makes an interrupted switch
identifiable and allows restoring an absent alias on the next maintenance run.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat
import tempfile
from uuid import uuid4

from elasticsearch.helpers import scan

from knowledge_base_runtime.backend.config.settings import DATA_DIR, ELASTICSEARCH_INDEX
from knowledge_base_runtime.backend.service.maintenance import maintenance_held
from knowledge_base_runtime.backend.service.retrieval_backends import (
    build_elasticsearch_document,
    create_elasticsearch_index,
    elasticsearch_client,
    ensure_elasticsearch_index,
)


def _body(response):
    return getattr(response, "body", response)


def _alias_targets(client, alias: str) -> set[str]:
    if not client.indices.exists_alias(name=alias):
        return set()
    return set(_body(client.indices.get_alias(name=alias)))


def _journal_path() -> Path:
    folder = Path(DATA_DIR) / ".state"
    folder.mkdir(parents=True, exist_ok=True)
    if folder.is_symlink() or not folder.is_dir():
        raise RuntimeError("搜索索引状态目录不是普通目录；停止重建")
    return folder / "elasticsearch-rebuild.json"


def _write_journal(path: Path, old: str, new: str) -> None:
    if path.exists() or path.is_symlink():
        raise RuntimeError("存在未处理的搜索索引切换记录；请先核验")
    fd, tmp = tempfile.mkstemp(prefix=".es-rebuild-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as target:
            json.dump({"version": 1, "alias": ELASTICSEARCH_INDEX, "old": old, "new": new}, target)
            target.flush()
            os.fsync(target.fileno())
        # Hard-linking makes publication exclusive, without replacing an
        # earlier journal that another deployment may need for recovery.
        os.link(tmp, path)
        directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        os.unlink(tmp)


def recover_pending_switch(client, path: Path | None = None) -> None:
    """Resolve a prior switch only when its actual alias state is unambiguous."""
    path = path or _journal_path()
    if not path.exists() and not path.is_symlink():
        return
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_mode & 0o077:
        raise RuntimeError("搜索索引切换记录类型异常；保留数据并停止")
    try:
        journal = json.loads(path.read_text(encoding="utf-8"))
        old, new = journal["old"], journal["new"]
        valid_name = re.compile(re.escape(ELASTICSEARCH_INDEX) + r"--[a-z0-9-]+\Z")
        if (journal.get("version") != 1 or journal.get("alias") != ELASTICSEARCH_INDEX
                or not isinstance(old, str) or not isinstance(new, str)
                or not valid_name.fullmatch(old) or not valid_name.fullmatch(new) or old == new):
            raise ValueError("invalid journal")
    except (ValueError, TypeError, KeyError, OSError) as exc:
        raise RuntimeError("搜索索引切换记录无法核验；保留数据并停止") from exc
    targets = _alias_targets(client, ELASTICSEARCH_INDEX)
    if not targets:
        if not client.indices.exists(index=old):
            raise RuntimeError("旧搜索索引不存在，无法自动恢复别名")
        client.indices.update_aliases(actions=[{"add": {"index": old, "alias": ELASTICSEARCH_INDEX, "is_write_index": True}}])
        targets = _alias_targets(client, ELASTICSEARCH_INDEX)
    if targets not in ({old}, {new}):
        raise RuntimeError("搜索索引别名状态不明确；保留所有索引并停止")
    path.unlink()


def rebuild_keyword_index(papers: list[dict], *, client=None, batch_size: int = 500) -> dict:
    """Rebuild from an already captured DB snapshot while writes are stopped."""
    if not maintenance_held():
        raise RuntimeError("关键词索引重建必须在知识库维护锁内执行")
    if not papers:
        raise RuntimeError("活动论文为空，拒绝用空索引替换现有索引")
    identifiers = [str(paper.get("id") or "").strip() for paper in papers]
    if not all(identifiers) or len(set(identifiers)) != len(identifiers):
        raise RuntimeError("活动论文 ID 为空或重复")
    client = client or elasticsearch_client()
    recover_pending_switch(client)
    if client.indices.exists(index=ELASTICSEARCH_INDEX) and not client.indices.exists_alias(name=ELASTICSEARCH_INDEX):
        raise RuntimeError("旧搜索索引是同名物理索引；不能自动转换，请先备份并人工迁移")
    ensure_elasticsearch_index(client)
    old_targets = _alias_targets(client, ELASTICSEARCH_INDEX)
    if len(old_targets) != 1:
        raise RuntimeError("搜索索引别名未唯一指向一个索引；停止重建")
    old = next(iter(old_targets))
    if not re.fullmatch(re.escape(ELASTICSEARCH_INDEX) + r"--[a-z0-9-]+", old):
        raise RuntimeError("现有搜索索引不属于可安全切换的版本；请先人工核验")
    new = f"{ELASTICSEARCH_INDEX}--generation-{uuid4().hex}"
    create_elasticsearch_index(client, new)
    for start in range(0, len(papers), max(1, min(batch_size, 2000))):
        batch = papers[start:start + max(1, min(batch_size, 2000))]
        operations = []
        for paper in batch:
            document = build_elasticsearch_document(paper)
            operations.extend([{"index": {"_index": new, "_id": str(document["id"])}}, document])
        result = _body(client.bulk(operations=operations))
        items = result.get("items", [])
        if result.get("errors") or len(items) != len(batch) or any(
                not 200 <= item.get("index", {}).get("status", 0) < 300 for item in items):
            raise RuntimeError("新关键词索引写入不完整；旧索引保持不变")
    client.indices.refresh(index=new)
    indexed_count = int(_body(client.count(index=new))["count"])
    indexed_ids = {str(hit["_id"]) for hit in scan(
        client, index=new, query={"query": {"match_all": {}}, "_source": False},
        size=500, raise_on_error=True,
    )}
    if indexed_count != len(identifiers) or indexed_ids != set(identifiers):
        raise RuntimeError("新关键词索引校验不一致；旧索引保持不变")
    journal = _journal_path()
    _write_journal(journal, old, new)
    try:
        response = _body(client.indices.update_aliases(actions=[
            {"remove": {"index": old, "alias": ELASTICSEARCH_INDEX, "must_exist": True}},
            {"add": {"index": new, "alias": ELASTICSEARCH_INDEX, "is_write_index": True}},
        ]))
        if not response.get("acknowledged") or response.get("errors"):
            raise RuntimeError("搜索索引切换未完整确认")
        if _alias_targets(client, ELASTICSEARCH_INDEX) != {new}:
            raise RuntimeError("搜索索引切换结果不明确")
    except Exception as exc:
        try:
            recover_pending_switch(client, journal)
        except Exception as recovery_error:
            raise RuntimeError("搜索索引切换或恢复未确认；已保留切换记录与所有索引，请人工检查") from recovery_error
        raise RuntimeError("搜索索引切换未确认；已保留所有索引，请检查当前状态") from exc
    journal.unlink()
    return {"papers": len(identifiers), "old_index": old, "new_index": new}
