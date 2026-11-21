from __future__ import annotations

from types import SimpleNamespace

from flask import g

from app import create_app
from app.api.rag import create_rag_answer
from app.integrations.personal_rag.service import PersonalAcademicRagService, RetrievedChunk


class FakeRepository:
    def __init__(self, authorized=None, chunks=None):
        self.authorized = set(authorized or [])
        self.chunks = list(chunks or [])

    def authorized_document_ids(self, *, user_id: str, role: str):
        assert user_id == "user-1"
        assert role == "normal_user"
        return self.authorized

    def load_chunks(self, document_ids):
        assert set(document_ids).issubset(self.authorized)
        return [item for item in self.chunks if item.document_id in document_ids]


def chunk(chunk_id="chunk-1", document_id="paper-1"):
    return RetrievedChunk(
        chunk_id=chunk_id,
        document_id=document_id,
        title="RAG 研究",
        text="混合检索通过 RRF 融合关键词召回与语义召回。",
        section_path="3 方法 > 3.2 检索",
        page_start=5,
        page_end=5,
    )


def test_answer_returns_no_evidence_for_empty_authorized_library():
    app = create_app({"TESTING": True})
    service = PersonalAcademicRagService(repository=FakeRepository())
    with app.app_context():
        result = service.answer(
            question="RRF 如何工作？", user_id="user-1", role="normal_user"
        )
    assert result["status"] == "NO_EVIDENCE"
    assert result["evidence"] == []
    assert result["warnings"] == ["AUTHORIZED_LIBRARY_EMPTY"]


def test_answer_rejects_document_outside_user_scope():
    app = create_app({"TESTING": True})
    service = PersonalAcademicRagService(
        repository=FakeRepository(authorized={"paper-1"})
    )
    with app.app_context():
        try:
            service.answer(
                question="RRF 如何工作？",
                user_id="user-1",
                role="normal_user",
                document_ids=["paper-2"],
            )
        except PermissionError:
            pass
        else:
            raise AssertionError("out-of-scope document should be rejected")


def test_answer_builds_citations_from_authorized_chunks():
    app = create_app({"TESTING": True, "KNOWLEDGE_BASE_MILVUS_ENABLED": False})

    def generator(**_kwargs):
        return {
            "model": "qwen-test",
            "content": '{"claims":[{"text":"RRF 融合两路召回结果","citation_ids":[1]}]}',
        }

    service = PersonalAcademicRagService(
        repository=FakeRepository(authorized={"paper-1"}, chunks=[chunk()]),
        generator=generator,
    )
    with app.app_context():
        result = service.answer(
            question="混合检索如何融合关键词和语义结果？",
            user_id="user-1",
            role="normal_user",
        )
    assert result["status"] == "DEGRADED"
    assert result["model"] == "qwen-test"
    assert result["answer"].endswith("[1]")
    assert result["evidence"][0]["chunk_id"] == "chunk-1"
    assert result["citations"][0]["evidence_id"] == "evidence_001"
    assert result["documents"] == [
        {"document_id": "paper-1", "title": "RAG 研究"}
    ]
    assert result["warnings"] == ["SEMANTIC_RETRIEVAL_DISABLED_LEXICAL_FALLBACK"]


def test_answer_renders_cohesive_sections_with_paragraph_level_citations():
    app = create_app({"TESTING": True, "KNOWLEDGE_BASE_MILVUS_ENABLED": False})

    def generator(**_kwargs):
        return {
            "model": "qwen-test",
            "content": (
                '{"sections":[{"heading":"方法概述","paragraph":'
                '"RRF 将关键词召回与语义召回统一排序，从而兼顾精确匹配和语义相关性。",'
                '"citation_ids":[1]}]}'
            ),
        }

    service = PersonalAcademicRagService(
        repository=FakeRepository(authorized={"paper-1"}, chunks=[chunk()]),
        generator=generator,
    )
    with app.app_context():
        result = service.answer(
            question="混合检索如何工作？",
            user_id="user-1",
            role="normal_user",
        )

    assert result["answer"] == (
        "## 方法概述\n"
        "RRF 将关键词召回与语义召回统一排序，从而兼顾精确匹配和语义相关性。 [1]"
    )


def test_generation_failure_degrades_without_dropping_evidence():
    app = create_app({"TESTING": True, "KNOWLEDGE_BASE_MILVUS_ENABLED": False})

    def unavailable_generator(**_kwargs):
        raise RuntimeError("offline")

    service = PersonalAcademicRagService(
        repository=FakeRepository(authorized={"paper-1"}, chunks=[chunk()]),
        generator=unavailable_generator,
    )
    with app.app_context():
        result = service.answer(
            question="混合检索如何融合关键词和语义结果？",
            user_id="user-1",
            role="normal_user",
        )
    assert result["status"] == "DEGRADED"
    assert result["evidence"][0]["document_id"] == "paper-1"
    assert "GENERATION_MODEL_CONNECTION_FAILED" in result["warnings"]
    assert "GENERATION_FAILED_EVIDENCE_PRESERVED" in result["warnings"]


def test_generation_timeout_degrades_without_dropping_evidence():
    app = create_app({"TESTING": True, "KNOWLEDGE_BASE_MILVUS_ENABLED": False})

    def timed_out_generator(**_kwargs):
        raise TimeoutError("model request timed out")

    service = PersonalAcademicRagService(
        repository=FakeRepository(authorized={"paper-1"}, chunks=[chunk()]),
        generator=timed_out_generator,
    )
    with app.app_context():
        result = service.answer(
            question="混合检索如何融合关键词和语义结果？",
            user_id="user-1",
            role="normal_user",
        )
    assert result["status"] == "DEGRADED"
    assert result["evidence"][0]["document_id"] == "paper-1"
    assert "GENERATION_MODEL_TIMEOUT" in result["warnings"]


def test_generation_retries_once_after_a_transient_failure():
    app = create_app({"TESTING": True, "KNOWLEDGE_BASE_MILVUS_ENABLED": False})
    attempts = 0

    def flaky_generator(**_kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporary connection failure")
        return {
            "model": "qwen-test",
            "content": '{"claims":[{"text":"RRF 融合两路召回结果","citation_ids":[1]}]}',
        }

    service = PersonalAcademicRagService(
        repository=FakeRepository(authorized={"paper-1"}, chunks=[chunk()]),
        generator=flaky_generator,
    )
    with app.app_context():
        result = service.answer(
            question="混合检索如何融合关键词和语义结果？",
            user_id="user-1",
            role="normal_user",
        )

    assert attempts == 2
    assert result["model"] == "qwen-test"
    assert "GENERATION_FAILED_EVIDENCE_PRESERVED" not in result["warnings"]


def test_invalid_citations_are_repaired_with_valid_range_prompt():
    app = create_app({"TESTING": True, "KNOWLEDGE_BASE_MILVUS_ENABLED": False})
    prompts = []

    def generator(**kwargs):
        prompts.append(kwargs["messages"][-1]["content"])
        if len(prompts) == 1:
            return {
                "model": "qwen-test",
                "content": '{"sections":[{"heading":"结论","paragraph":"错误引用",' + '"citation_ids":[9]}]}',
            }
        return {
            "model": "qwen-test",
            "content": '{"sections":[{"heading":"结论","paragraph":"RRF 融合两路召回结果",' + '"citation_ids":[1]}]}',
        }

    service = PersonalAcademicRagService(
        repository=FakeRepository(authorized={"paper-1"}, chunks=[chunk()]),
        generator=generator,
    )
    with app.app_context():
        result = service.answer(
            question="混合检索如何工作？", user_id="user-1", role="normal_user"
        )

    assert result["answer"].endswith("[1]")
    assert "合法 citation_ids 只能是 1 到 1" in prompts[1]


def test_inline_citations_from_model_are_normalized():
    app = create_app({"TESTING": True, "KNOWLEDGE_BASE_MILVUS_ENABLED": False})

    def generator(**_kwargs):
        return {
            "model": "minimax-test",
            "content": (
                '{"sections":[{"heading":"结论","paragraph":'
                '"RRF 可以融合关键词与语义召回。[1]","citation_ids":[1]}]}'
            ),
        }

    service = PersonalAcademicRagService(
        repository=FakeRepository(authorized={"paper-1"}, chunks=[chunk()]),
        generator=generator,
    )
    with app.app_context():
        result = service.answer(
            question="混合检索如何工作？", user_id="user-1", role="normal_user"
        )

    assert result["model"] == "minimax-test"
    assert result["answer"] == "## 结论\nRRF 可以融合关键词与语义召回。 [1]"
    assert "GENERATION_MODEL_OUTPUT_INVALID" not in result["warnings"]


def test_inline_only_and_string_citation_ids_are_supported():
    app = create_app({"TESTING": True, "KNOWLEDGE_BASE_MILVUS_ENABLED": False})
    responses = iter(
        [
            '{"sections":[{"heading":"结论","paragraph":"结论来自证据【1】"}]}',
            '{"sections":[{"heading":"结论","paragraph":"结论来自证据",'
            '"citation_ids":"evidence_001"}]}',
        ]
    )

    for expected in ("结论来自证据 [1]", "结论来自证据 [1]"):
        service = PersonalAcademicRagService(
            repository=FakeRepository(authorized={"paper-1"}, chunks=[chunk()]),
            generator=lambda **_kwargs: {"model": "minimax-test", "content": next(responses)},
        )
        with app.app_context():
            result = service.answer(
                question="混合检索如何工作？", user_id="user-1", role="normal_user"
            )
        assert result["answer"].endswith(expected)


def test_invalid_generation_falls_back_to_cited_evidence_answer():
    app = create_app({"TESTING": True, "KNOWLEDGE_BASE_MILVUS_ENABLED": False})

    def generator(**_kwargs):
        return {
            "model": "qwen-test",
            "content": '{"sections":[{"heading":"结论","paragraph":"错误引用","citation_ids":[99]}]}',
        }

    service = PersonalAcademicRagService(
        repository=FakeRepository(authorized={"paper-1"}, chunks=[chunk()]),
        generator=generator,
    )
    with app.app_context():
        result = service.answer(
            question="混合检索如何工作？", user_id="user-1", role="normal_user"
        )

    assert result["status"] == "DEGRADED"
    assert result["answer"].endswith("[1]")
    assert "生成模型暂时不可用" not in result["answer"]
    assert "GENERATION_MODEL_OUTPUT_INVALID" in result["warnings"]


def test_reranker_reorders_retrieved_evidence():
    app = create_app({"TESTING": True, "KNOWLEDGE_BASE_MILVUS_ENABLED": False})

    class FakeReranker:
        def score(self, _query, documents):
            assert len(documents) == 2
            return [0.1, 0.9]

    first = chunk("chunk-1")
    second = RetrievedChunk(
        **{
            **chunk("chunk-2").__dict__,
            "text": "语义召回和关键词召回可通过倒数排名融合进行组合。",
        }
    )
    service = PersonalAcademicRagService(
        repository=FakeRepository(authorized={"paper-1"}, chunks=[first, second]),
        reranker=FakeReranker(),
        generator=lambda **_kwargs: {
            "model": "qwen-test",
            "content": '{"claims":[{"text":"融合检索结论","citation_ids":[1]}]}',
        },
    )
    with app.app_context():
        result = service.answer(
            question="关键词召回与语义召回如何融合？", user_id="user-1", role="normal_user"
        )

    assert result["evidence"][0]["chunk_id"] == "chunk-2"
    assert "rerank" in result["retrieval"]["stages"]


def test_rag_api_maps_forbidden_scope_to_403():
    app = create_app({"TESTING": True})

    class ForbiddenService:
        def answer(self, **_kwargs):
            raise PermissionError

    app.extensions["personal_academic_rag"] = ForbiddenService()
    with app.test_request_context(
        "/api/v1/rag/answers",
        method="POST",
        json={"question": "问题", "document_ids": ["paper-2"], "stream": False},
    ):
        g.current_user = SimpleNamespace(id="user-1", role_code="normal_user")
        response, status = create_rag_answer()
    assert status == 403
    assert response.get_json()["error"]["code"] == "RAG_FORBIDDEN_SCOPE"


class PersonalRepository(FakeRepository):
    """Platform library plus the person's own uploads."""

    def __init__(self, personal, vectors, **kwargs):
        super().__init__(**kwargs)
        self.personal = personal
        self.vectors = vectors
        self.authorized |= {item.document_id for item in personal}

    def load_chunks(self, document_ids):
        assert not any(item.startswith("personal:") for item in document_ids)
        return super().load_chunks(document_ids)

    def load_personal_chunks(self, user_id):
        assert user_id == "user-1"
        return self.personal, self.vectors


def personal_chunk(chunk_id, text, page=3):
    return RetrievedChunk(
        chunk_id=f"personal:{chunk_id}",
        document_id="personal:paper-9",
        title="我上传的论文",
        text=text,
        section_path=f"第 {page} 页",
        page_start=page,
        page_end=page,
    )


def test_answer_searches_the_persons_own_uploads(monkeypatch):
    app = create_app({"TESTING": True, "KNOWLEDGE_BASE_MILVUS_ENABLED": False})
    own = personal_chunk("c1", "我们提出的分层检索把长文档问答的召回率提升到 0.86。")
    unrelated = personal_chunk("c2", "附录：实验所用硬件为单张 A100。", page=12)
    # The query embedding points at the first own chunk only.
    monkeypatch.setattr("app.integrations.personal_rag.service.embed_query", lambda question: [1.0, 0.0])
    service = PersonalAcademicRagService(
        repository=PersonalRepository(
            [own, unrelated],
            {own.chunk_id: [1.0, 0.0], unrelated.chunk_id: [0.0, 1.0]},
            authorized={"paper-1"},
            chunks=[chunk()],
        ),
        generator=lambda **_kwargs: {
            "model": "qwen-test",
            "content": '{"claims":[{"text":"分层检索提升了召回率","citation_ids":[1]}]}',
        },
    )
    with app.app_context():
        result = service.answer(question="分层检索的召回率是多少？", user_id="user-1", role="normal_user")

    documents = {item["document_id"]: item["title"] for item in result["documents"]}
    assert documents["personal:paper-9"] == "我上传的论文"
    top = result["evidence"][0]
    assert top["chunk_id"] == "personal:c1"
    assert (top["section_path"], top["page_start"]) == ("第 3 页", 3)
    assert "semantic" in result["retrieval"]["stages"]


def test_answer_can_be_limited_to_one_own_upload():
    app = create_app({"TESTING": True, "KNOWLEDGE_BASE_MILVUS_ENABLED": False})
    own = personal_chunk("c1", "混合检索通过 RRF 融合关键词召回与语义召回。")
    service = PersonalAcademicRagService(
        repository=PersonalRepository([own], {}, authorized={"paper-1"}, chunks=[chunk()]),
        generator=lambda **_kwargs: {
            "model": "qwen-test",
            "content": '{"claims":[{"text":"融合检索","citation_ids":[1]}]}',
        },
    )
    with app.app_context():
        result = service.answer(
            question="混合检索如何融合？",
            user_id="user-1",
            role="normal_user",
            document_ids=["personal:paper-9"],
        )
    assert {item["document_id"] for item in result["evidence"]} == {"personal:paper-9"}
