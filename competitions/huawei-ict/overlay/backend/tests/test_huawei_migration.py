from __future__ import annotations

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace
from uuid import uuid4
from zipfile import ZipFile

import pytest
from flask import Flask
from sqlalchemy import func, select

from app import create_app
from app.agents.reviewer_comments.service import (
    ReviewerCommentsService, analyze_comment, fallback_response_letter,
    read_docx_text, split_review_comments,
)
from app.config import Config
from app.extensions import db
from app.models import Agent, ModelConfig, Role, Tool
from app.services.task_model_runtime import resolve_task_model_runtime
from app.services.user_model_config import ensure_user_api_config
from scripts.init_database import initialize_database


def test_relative_paths_ignore_process_working_directory(tmp_path):
    backend = Path(__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": str(backend), "PAPER_UPLOAD_DIR": "portable/papers",
           "KB_DATA_DIR": "portable/kb", "ZHIYAN_DATA_ROOT": "portable/data"}
    output = subprocess.check_output([
        sys.executable, "-c",
        "from app.config import Config; print(Config.PAPER_UPLOAD_DIR); "
        "print(Config.KNOWLEDGE_BASE_DATA_DIR); print(Config.PATENT_DRAFTING_DATA_DIR)",
    ], cwd=tmp_path, env=env, text=True).splitlines()
    assert output == [str(backend / "portable/papers"), str(backend / "portable/kb"),
                      str(backend / "portable/data/generated/patent_drafting")]


def test_config_preserves_venv_symlink_and_python_override(tmp_path):
    from app.config import interpreter_path
    python = tmp_path / "python"
    python.symlink_to(sys.executable)
    assert interpreter_path(python) == python
    backend = Path(__file__).resolve().parents[1]
    output = subprocess.check_output([
        sys.executable, "-c", "from app.config import Config; print(Config.FORMULA_RECOGNITION_PYTHON)",
    ], cwd=tmp_path, env={**os.environ, "PYTHONPATH": str(backend),
                          "FORMULA_RECOGNITION_PYTHON": str(python)}, text=True)
    assert output.strip() == str(python)


@pytest.mark.parametrize("review", [
    "1.请补实验\n2.引用不足\n3.修正拼写",
    "Major comments:\n1. 请补实验\n2. 引用不足\nMinor comments:\n3. 修正拼写",
])
def test_chinese_numbered_comments_keep_short_items(review):
    assert split_review_comments(review) == ["请补实验", "引用不足", "修正拼写"]
    assert split_review_comments("测量值为 1.5，不应拆分。") == ["测量值为 1.5，不应拆分。"]


def test_docx_preserves_paragraphs_entities_and_automatic_numbering(tmp_path):
    path = tmp_path / "review.docx"
    xml = '''<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
    <w:p><w:pPr><w:numPr><w:numId w:val="1"/></w:numPr></w:pPr><w:r><w:t>请比较 A&amp;B</w:t></w:r></w:p>
    <w:p><w:pPr><w:numPr><w:numId w:val="1"/></w:numPr></w:pPr><w:r><w:t>补实验</w:t></w:r></w:p>
    <w:p><w:pPr><w:numPr><w:numId w:val="1"/></w:numPr></w:pPr><w:r><w:t>补引用</w:t></w:r></w:p>
    </w:body></w:document>'''
    with ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", xml)
    text = read_docx_text(path)
    assert text.count("\n") == 2
    assert split_review_comments(text) == ["请比较 A&B", "补实验", "补引用"]


def test_reviewer_fallback_does_not_claim_completed_revisions():
    letter = fallback_response_letter([analyze_comment(1, "请补实验")])
    assert "We have" not in letter
    assert "proposed" in letter and "待作者确认" in letter


def test_reviewer_passes_selected_provider_model_and_credentials(monkeypatch):
    captured = {}
    def fake_chat(**kwargs):
        captured.update(kwargs)
        return {"content": "reply draft"}
    monkeypatch.setattr("app.agents.reviewer_comments.service.run_openai_compatible_chat", fake_chat)
    app = Flask("reviewer-test")
    with app.app_context():
        result = ReviewerCommentsService(app).generate_response_letter(
            "意见", [], {}, "模型显示名称",
            {"model_name": "actual-model-id", "base_url": "http://127.0.0.1:9/v1",
             "api_key": "fake-test-key", "timeout_seconds": 50},
        )
    assert result == "reply draft"
    assert captured["model"] == "actual-model-id"
    assert captured["api_key"] == "fake-test-key"
    assert captured["base_url"] == "http://127.0.0.1:9/v1"


def test_task_model_rejects_another_users_configuration(monkeypatch):
    owner = uuid4()
    monkeypatch.setattr(db.session, "get", lambda *_: SimpleNamespace(owner_user_id=owner))
    with pytest.raises(RuntimeError, match="不存在"):
        resolve_task_model_runtime(SimpleNamespace(model_config_id=uuid4()), uuid4())


database_test = pytest.mark.skipif(os.getenv("ZHIYAN_RUN_DATABASE_TESTS") != "1",
                                 reason="requires an explicitly selected isolated PostgreSQL test database")


def database_app(overrides=None):
    # Never run these tests against a cloud/production database accidentally.
    from sqlalchemy.engine import make_url
    url = make_url(Config.SQLALCHEMY_DATABASE_URI)
    assert url.host in {"localhost", "127.0.0.1", "zhiyan-migration-db"} and url.database.endswith("_test")
    app = Flask("migration-database-test")
    app.config.from_object(Config)
    app.config.update(overrides or {})
    db.init_app(app)
    return app


@database_test
def test_bootstrap_is_idempotent_and_preserves_custom_data():
    app = database_app()
    initialize_database(app)
    with app.app_context():
        code = f"test_{uuid4().hex}"
        db.session.add(Role(code=code, name="保留原数据"))
        db.session.commit()
        before = {model.__tablename__: db.session.scalar(select(func.count()).select_from(model))
                  for model in (Agent, Tool, Role)}
    initialize_database(app)
    with app.app_context():
        after = {model.__tablename__: db.session.scalar(select(func.count()).select_from(model))
                 for model in (Agent, Tool, Role)}
        assert before == after
        assert after["agents"] == 11 and after["tools"] == 6
        assert db.session.get(Role, code).name == "保留原数据"


@database_test
def test_concurrent_account_model_provisioning_creates_one_slot():
    app = database_app()
    owner = uuid4()
    barrier = Barrier(6)
    def provision(_):
        with app.app_context():
            barrier.wait(timeout=10)
            item = ensure_user_api_config(owner)
            assert item is not None
            return item.id
    with ThreadPoolExecutor(max_workers=6) as pool:
        ids = list(pool.map(provision, range(6)))
    assert len(set(ids)) == 1
    with app.app_context():
        count = db.session.scalar(select(func.count()).select_from(ModelConfig).where(
            ModelConfig.owner_user_id == owner, ModelConfig.is_account_api.is_(True)))
        assert count == 1


def test_health_verifier_rejects_http_200_with_degraded_components():
    import importlib.util
    source = Path(__file__).resolve().parents[2] / "deploy/huawei/verify.py"
    spec = importlib.util.spec_from_file_location("huawei_verify", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert not module.assess({"data": {"status": "up"}},
                             {"data": {"status": "degraded", "components": {"database": "up"}}})
    assert module.assess({"data": {"status": "up"}},
                         {"data": {"status": "ready", "components": {"database": "up"}}})
