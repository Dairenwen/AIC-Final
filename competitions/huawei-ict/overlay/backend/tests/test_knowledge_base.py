from types import SimpleNamespace
from unittest.mock import patch

from flask import g

from app import create_app
from app.api.knowledge_base import management_asset, management_ui, proxy_knowledge_base


QA_DOMAINS = ("vision", "video", "text", "audio", "general", "other")


def test_knowledge_base_ui_rejects_normal_user():
    app = create_app({"TESTING": True})
    with app.test_request_context():
        g.current_user = SimpleNamespace(role_code="normal_user")
        response, status = management_ui()

    assert status == 403
    assert response.get_json()["error"]["code"] == "FORBIDDEN"


def test_knowledge_base_ui_is_available_to_system_admin():
    app = create_app({"TESTING": True})
    with app.test_request_context():
        g.current_user = SimpleNamespace(role_code="system_admin")
        response = management_ui()

    assert response.status_code == 200
    response.direct_passthrough = False
    assert "const loaders = {" in response.get_data(as_text=True)
    assert "智研知识库管理平台" in response.get_data(as_text=True)


def test_training_set_embed_combines_generation_and_review():
    app = create_app({"TESTING": True})
    with app.test_request_context("/?embed=1&tab=trainingSet"):
        g.current_user = SimpleNamespace(role_code="system_admin")
        response = management_ui()

    response.direct_passthrough = False
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "adminTab: 'qaGenerate'" in body
    assert "训练集生成" in body
    assert "质量复核" in body


def test_training_set_embed_contains_inline_six_domain_allocation_card():
    app = create_app({"TESTING": True})
    with app.test_request_context("/?embed=1&tab=trainingSet"):
        g.current_user = SimpleNamespace(role_code="system_admin")
        response = management_ui()

    response.direct_passthrough = False
    body = response.get_data(as_text=True)
    assert "领域分配" in body
    assert "需要展示并生成的 Chunk 总数" in body
    assert 'class="card qa-domain-card"' in body
    assert 'v-show="qaDomainAllocationExpanded"' in body
    assert "qaDomainAllocationExpanded = !qaDomainAllocationExpanded" in body
    assert body.index('class="card qa-domain-card"') < body.index("切片 Chunk 列表")
    assert "showQaDomainAllocation" not in body
    assert 'class="qa-generation-options"' in body
    assert "setQaGenerationStatusFilter('all')\">全选" in body
    assert "setQaGenerationStatusFilter('generated')\">只选已生成" in body
    assert "setQaGenerationStatusFilter('not_generated')\">只选未生成" in body
    assert "generation_status: this.qaGenerationStatusFilter" in body
    for label in ("视觉", "视频", "文本", "音频", "通用", "其他"):
        assert f"label: '{label}'" in body


def test_training_set_and_knowledge_lists_render_compact_pagination_controls():
    app = create_app({"TESTING": True})
    with app.test_request_context("/?embed=1&tab=trainingSet"):
        g.current_user = SimpleNamespace(role_code="system_admin")
        response = management_ui()

    response.direct_passthrough = False
    body = response.get_data(as_text=True)
    assert 'v-model.number="qaChunkPageSize"' in body
    assert 'v-model.number="knowledgePageSize"' in body
    assert "qaChunkDisplayPages" in body
    assert "knowledgeDisplayPages" in body
    assert "buildPaginationItems(currentPage, totalPages)" in body
    assert "type: 'ellipsis'" in body
    assert "第 {{ qaChunkPage }} / {{ Math.max(1, qaChunkTotalPages) }} 页" in body
    assert "第 {{ knowledgePage }} / {{ Math.max(1, knowledgeTotalPages) }} 页" in body


def test_qa_domain_normalization_covers_all_six_domains():
    create_app({"TESTING": True})
    from knowledge_base_runtime.backend.service.qa import _normalize_qa_domain

    assert _normalize_qa_domain("Face Verification") == "vision"
    assert _normalize_qa_domain("Video") == "video"
    assert _normalize_qa_domain("Natural Language Processing") == "text"
    assert _normalize_qa_domain("Audio") == "audio"
    assert _normalize_qa_domain("Machine Learning", "Representation Learning") == "general"
    assert _normalize_qa_domain(None, None) == "other"


def test_domain_allocation_batch_config_requires_total_of_100():
    create_app({"TESTING": True})
    from knowledge_base_runtime.backend.service.qa import _validate_batch_config

    valid = {domain: 0 for domain in QA_DOMAINS}
    valid["general"] = 100
    result = _validate_batch_config({"domain_allocation": {"percentages": valid}})
    assert result["domain_allocation"]["percentages"]["general"] == 100

    invalid = dict(valid)
    invalid["general"] = 99
    try:
        _validate_batch_config({"domain_allocation": {"percentages": invalid}})
    except ValueError as exc:
        assert "total 100" in str(exc)
    else:
        raise AssertionError("invalid allocation should be rejected")


def test_user_mode_cannot_bypass_admin_ui_authorization():
    app = create_app({"TESTING": True})
    with app.test_request_context("/?embed=1&mode=user&tab=collections"):
        g.current_user = SimpleNamespace(role_code="normal_user")
        response, status = management_ui()

    assert status == 403
    assert response.get_json()["error"]["code"] == "FORBIDDEN"


def test_knowledge_base_api_script_uses_the_demov15_proxy():
    app = create_app({"TESTING": True})
    with app.test_request_context():
        g.current_user = SimpleNamespace(role_code="system_admin")
        response = management_asset("kbApi.js")

    assert "baseURL: '/api/v1/knowledge-base'" in response.get_data(as_text=True)


def test_normal_user_cannot_load_knowledge_base_assets():
    app = create_app({"TESTING": True})
    with app.test_request_context():
        g.current_user = SimpleNamespace(role_code="normal_user")
        response, status = management_asset("kbApi.js")

    assert status == 403
    assert response.get_json()["error"]["code"] == "FORBIDDEN"


def test_knowledge_base_browser_dependencies_are_served_as_javascript():
    app = create_app({"TESTING": True})
    for asset in ("vendor/vue.global.prod.js", "vendor/echarts.min.js"):
        with app.test_request_context():
            g.current_user = SimpleNamespace(role_code="system_admin")
            response = management_asset(asset)
            response.direct_passthrough = False
            assert response.status_code == 200
            assert "javascript" in response.content_type
            assert len(response.get_data()) > 10000
            assert not response.get_data().lstrip().startswith(b"<!DOCTYPE")


def test_normal_user_cannot_proxy_any_knowledge_operation():
    app = create_app({"TESTING": True})
    with app.test_request_context():
        g.current_user = SimpleNamespace(role_code="normal_user")
        response, status = proxy_knowledge_base("papers")

    assert status == 403
    assert response.get_json()["error"]["code"] == "FORBIDDEN"


def test_proxy_forwards_the_authenticated_main_user():
    class Upstream:
        status = 200
        headers = {"Content-Type": "application/json"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"ok": true}'

    app = create_app({"TESTING": True})
    user = SimpleNamespace(
        id="d45d13a0-80ab-4d99-ac5f-29211e34dede",
        role_code="system_admin",
        display_name="中文用户",
    )
    with app.test_request_context("/?page=2"):
        g.current_user = user
        with patch("app.api.knowledge_base.urlopen", return_value=Upstream()) as mocked:
            response = proxy_knowledge_base("collections")

    upstream_request = mocked.call_args.args[0]
    assert upstream_request.full_url.endswith("/api/v1/collections?page=2")
    assert upstream_request.get_header("X-user-id") == user.id
    assert response.get_json()["ok"] is True


def test_embedded_runtime_reads_the_demov15_knowledge_schema():
    app = create_app({"TESTING": True})
    user = SimpleNamespace(
        id="d45d13a0-80ab-4d99-ac5f-29211e34dede",
        role_code="system_admin",
    )
    with app.test_request_context():
        g.current_user = user
        response = proxy_knowledge_base("admin/dashboard")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["total_papers"] >= 0
    assert payload["vector_chunks"] >= 0


def test_qa_chunk_api_returns_domain_counts_and_filterable_domains():
    app = create_app({"TESTING": True})
    user = SimpleNamespace(
        id="d45d13a0-80ab-4d99-ac5f-29211e34dede",
        role_code="system_admin",
    )
    with app.test_request_context("/?size=5"):
        g.current_user = user
        response = proxy_knowledge_base("admin/qa/chunks")

    assert response.status_code == 200
    payload = response.get_json()
    assert set(payload["domain_counts"]) == set(QA_DOMAINS)
    assert payload["available_total"] == sum(payload["domain_counts"].values())
    assert all(item["domain"] in QA_DOMAINS for item in payload["list"])

    with app.test_request_context("/?domain=general&size=3"):
        g.current_user = user
        filtered_response = proxy_knowledge_base("admin/qa/chunks")

    assert filtered_response.status_code == 200
    assert all(item["domain"] == "general" for item in filtered_response.get_json()["list"])

    status_payloads = {}
    for generation_status in ("generated", "not_generated"):
        with app.test_request_context(f"/?generation_status={generation_status}&size=200"):
            g.current_user = user
            status_response = proxy_knowledge_base("admin/qa/chunks")

        assert status_response.status_code == 200
        status_payload = status_response.get_json()
        status_payloads[generation_status] = status_payload
        assert status_payload["generation_status"] == generation_status
        assert status_payload["available_total"] == sum(status_payload["domain_counts"].values())
        expected_has_qa = generation_status == "generated"
        assert all(item["has_qa"] is expected_has_qa for item in status_payload["list"])

    assert payload["total"] == status_payloads["generated"]["total"] + status_payloads["not_generated"]["total"]


def test_qa_chunk_service_rejects_unknown_generation_status():
    create_app({"TESTING": True})
    from knowledge_base_runtime.backend.service.qa import list_qa_chunks

    try:
        list_qa_chunks(generation_status="unknown")
    except ValueError as exc:
        assert "unsupported QA generation status" in str(exc)
    else:
        raise AssertionError("unknown generation status should be rejected")
