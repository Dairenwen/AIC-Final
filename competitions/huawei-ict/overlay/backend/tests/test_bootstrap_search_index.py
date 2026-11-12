import importlib.util
import os
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "deploy/docker/bootstrap_search_index.py"
spec = importlib.util.spec_from_file_location("bootstrap_search_index", SCRIPT)
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)


def test_script_runs_from_an_unrelated_directory_without_pythonpath(tmp_path):
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    env["ELASTICSEARCH_ENABLED"] = "0"
    result = subprocess.run(
        [sys.executable, str(SCRIPT)], cwd=tmp_path, env=env,
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 1
    assert "Elasticsearch 未启用" in result.stderr
    assert "ModuleNotFoundError" not in result.stderr


def setup_index(monkeypatch, *, documents=("a", "b"), indexed=("a", "b"), count=None):
    calls = []
    service = bootstrap.retrieval_index
    monkeypatch.setattr(service, "ELASTICSEARCH_ENABLED", True)
    monkeypatch.setattr(service, "ELASTICSEARCH_INDEX", "papers_idx")
    monkeypatch.setattr(service, "_fetch_papers", lambda: [{"id": doc} for doc in documents])
    monkeypatch.setattr(service, "_sync_milvus", lambda *args: pytest.fail("must not call a model service"))

    def upsert(papers, *, batch_size, recreate):
        assert batch_size == 500 and recreate is False
        calls.append([paper["id"] for paper in papers])
        return len(papers), 0, []

    class Client:
        def count(self, *, index):
            assert index == "papers_idx"
            return {"count": len(indexed) if count is None else count}

    client = Client()
    monkeypatch.setattr(service, "_bulk_upsert_elasticsearch", upsert)
    monkeypatch.setattr(service, "elasticsearch_client", lambda: client)

    def scan(client_arg, **kwargs):
        assert client_arg is client
        assert kwargs["index"] == "papers_idx"
        assert kwargs["query"]["_source"] is False
        assert kwargs["raise_on_error"] is True
        return iter({"_id": doc} for doc in indexed)

    monkeypatch.setattr(bootstrap, "scan", scan)
    return calls


def test_only_upserts_and_is_safe_to_rerun(monkeypatch):
    calls = setup_index(monkeypatch)
    assert bootstrap.main() == 0
    assert bootstrap.main() == 0
    assert calls == [["a", "b"], ["a", "b"]]


@pytest.mark.parametrize(
    "indexed,count,expected",
    [(("a",), None, "缺失 1 条"), (("a", "b", "old"), None, "过期 1 条"), (("a", "b"), 3, "校验失败")],
)
def test_missing_stale_and_count_mismatch_fail(monkeypatch, capsys, indexed, count, expected):
    setup_index(monkeypatch, indexed=indexed, count=count)
    assert bootstrap.main() == 1
    assert expected in capsys.readouterr().err


@pytest.mark.parametrize("documents", [(), ("a", "a"), ("",)])
def test_empty_or_invalid_database_stops_before_upsert(monkeypatch, documents):
    calls = setup_index(monkeypatch, documents=documents)
    assert bootstrap.main() == 1
    assert calls == []


def test_failed_upsert_does_not_report_success(monkeypatch):
    setup_index(monkeypatch)
    monkeypatch.setattr(bootstrap.retrieval_index, "_bulk_upsert_elasticsearch", lambda *args, **kwargs: (1, 1, []))
    assert bootstrap.main() == 1


def test_backend_errors_do_not_leak_details(monkeypatch, capsys):
    setup_index(monkeypatch)
    def fail():
        raise RuntimeError("secret-in-error")
    monkeypatch.setattr(bootstrap.retrieval_index, "_fetch_papers", fail)
    assert bootstrap.main() == 1
    assert "secret-in-error" not in capsys.readouterr().err
