import pytest

from knowledge_base_runtime.backend.service import es_rebuild


class FakeIndices:
    def __init__(self):
        self.alias = {"papers_idx--initial"}
        self.indices = {"papers_idx--initial": {"old"}}

    def exists_alias(self, *, name):
        return bool(self.alias)

    def exists(self, *, index):
        return index in self.indices or (index == "papers_idx" and bool(self.alias))

    def get_alias(self, *, name):
        return {index: {} for index in self.alias}

    def create(self, *, index, **kwargs):
        self.indices[index] = set()

    def refresh(self, *, index):
        return None

    def update_aliases(self, *, actions):
        for action in actions:
            if "remove" in action:
                self.alias.discard(action["remove"]["index"])
            else:
                self.alias.add(action["add"]["index"])
        return {"acknowledged": True, "errors": False}


class FakeClient:
    def __init__(self, *, incomplete=False):
        self.indices = FakeIndices()
        self.incomplete = incomplete

    def bulk(self, *, operations):
        ids = [operations[i]["index"]["_id"] for i in range(0, len(operations), 2)]
        index = operations[0]["index"]["_index"]
        self.indices.indices[index].update(ids)
        items = [{"index": {"status": 201}} for _ in ids]
        return {"errors": False, "items": items[:-1] if self.incomplete else items}

    def count(self, *, index):
        return {"count": len(self.indices.indices[index])}


@pytest.fixture
def setup_rebuild(monkeypatch, tmp_path):
    monkeypatch.setattr(es_rebuild, "ELASTICSEARCH_INDEX", "papers_idx")
    monkeypatch.setattr(es_rebuild, "DATA_DIR", tmp_path)
    monkeypatch.setattr(es_rebuild, "maintenance_held", lambda: True)
    monkeypatch.setattr(es_rebuild, "ensure_elasticsearch_index", lambda _client: None)
    monkeypatch.setattr(es_rebuild, "build_elasticsearch_document", lambda paper: paper)
    monkeypatch.setattr(es_rebuild, "scan", lambda client, *, index, **kwargs:
                        iter({"_id": value} for value in client.indices.indices[index]))
    return tmp_path


def test_requires_maintenance_before_touching_es(monkeypatch, setup_rebuild):
    monkeypatch.setattr(es_rebuild, "maintenance_held", lambda: False)
    with pytest.raises(RuntimeError, match="维护锁"):
        es_rebuild.rebuild_keyword_index([{"id": "p1"}], client=FakeClient())


def test_incomplete_write_preserves_original_alias(setup_rebuild):
    client = FakeClient(incomplete=True)
    with pytest.raises(RuntimeError, match="不完整"):
        es_rebuild.rebuild_keyword_index([{"id": "p1"}], client=client)
    assert client.indices.alias == {"papers_idx--initial"}
    assert client.indices.indices["papers_idx--initial"] == {"old"}


def test_success_switches_after_verification_and_keeps_old_index(setup_rebuild):
    client = FakeClient()
    result = es_rebuild.rebuild_keyword_index([{"id": "p1"}, {"id": "p2"}], client=client)
    assert client.indices.alias == {result["new_index"]}
    assert client.indices.indices[result["old_index"]] == {"old"}
    assert client.indices.indices[result["new_index"]] == {"p1", "p2"}
    assert not (setup_rebuild / ".state/elasticsearch-rebuild.json").exists()


def test_interrupted_switch_restores_missing_alias(setup_rebuild):
    client = FakeClient()
    journal = es_rebuild._journal_path()
    es_rebuild._write_journal(journal, "papers_idx--initial", "papers_idx--generation-123")
    client.indices.alias.clear()
    es_rebuild.recover_pending_switch(client, journal)
    assert client.indices.alias == {"papers_idx--initial"}
    assert not journal.exists()


def test_partial_alias_switch_restores_old_index(setup_rebuild, monkeypatch):
    client = FakeClient()
    real_update = client.indices.update_aliases
    def partial_once(*, actions):
        if len(actions) == 2:
            client.indices.alias.clear()
            return {"acknowledged": True, "errors": True}
        return real_update(actions=actions)
    monkeypatch.setattr(client.indices, "update_aliases", partial_once)
    with pytest.raises(RuntimeError, match="切换未确认"):
        es_rebuild.rebuild_keyword_index([{"id": "p1"}], client=client)
    assert client.indices.alias == {"papers_idx--initial"}
    assert client.indices.indices["papers_idx--initial"] == {"old"}


def test_ambiguous_alias_keeps_journal_and_all_indices(setup_rebuild):
    client = FakeClient()
    journal = es_rebuild._journal_path()
    es_rebuild._write_journal(journal, "papers_idx--initial", "papers_idx--generation-123")
    client.indices.alias.add("another-index")
    with pytest.raises(RuntimeError, match="状态不明确"):
        es_rebuild.recover_pending_switch(client, journal)
    assert journal.exists()
    assert client.indices.indices["papers_idx--initial"] == {"old"}


def test_physical_index_with_same_name_is_never_deleted(setup_rebuild):
    client = FakeClient()
    client.indices.alias.clear()
    client.indices.indices["papers_idx"] = {"historical"}
    with pytest.raises(RuntimeError, match="同名物理索引"):
        es_rebuild.rebuild_keyword_index([{"id": "p1"}], client=client)
    assert client.indices.indices["papers_idx"] == {"historical"}


def test_combined_rebuild_reports_partial_failure(monkeypatch):
    from knowledge_base_runtime.backend.service import maintenance, retrieval_index
    monkeypatch.setattr(maintenance, "maintenance_held", lambda: True)
    monkeypatch.setattr(retrieval_index, "_fetch_papers", lambda: [{"id": "p1"}])
    monkeypatch.setattr(retrieval_index, "_fetch_chunks_by_paper", lambda: {"p1": [{"chunk_id": "c1"}]})
    monkeypatch.setattr(retrieval_index, "_elasticsearch_count", lambda: 1)
    monkeypatch.setattr(es_rebuild, "rebuild_keyword_index", lambda papers, **kwargs: {"papers": 1})
    def fail_vector(_chunks):
        raise RuntimeError("test-only error")
    monkeypatch.setattr(retrieval_index, "rebuild_milvus_safely", fail_vector)
    with pytest.raises(RuntimeError, match="关键词索引可能已更新"):
        retrieval_index.rebuild_retrieval_indexes({"target": "all", "mode": "recreate", "confirm_embedding_calls": True})
    assert retrieval_index.LAST_REINDEX["ok"] is False
    assert retrieval_index.LAST_REINDEX["elasticsearch_synced"] == 1
