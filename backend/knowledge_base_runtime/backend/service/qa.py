from __future__ import annotations

import json
import urllib.error
import urllib.request
import uuid
from typing import Any

from knowledge_base_runtime.backend.config.settings import (
    CHUNK_QA_MODEL_API_KEY,
    CHUNK_QA_MODEL_BASE_URL,
    CHUNK_QA_MODEL_NAME,
    CHUNK_QA_MODEL_RESPONSE_FORMAT,
    CHUNK_QA_MODEL_TEMPERATURE,
    CHUNK_QA_MODEL_TIMEOUT,
)
from knowledge_base_runtime.backend.dao.database import get_db, utc_now
from knowledge_base_runtime.backend.service.audit import record_audit_log
from knowledge_base_runtime.backend.utils.common import dumps, preview


DPO_GENERATOR_VERSION = f"{CHUNK_QA_MODEL_NAME.replace('-', '_')}_dpo_v1"

QA_DOMAINS = ("vision", "video", "text", "audio", "general", "other")
QA_GENERATION_STATUSES = ("all", "generated", "not_generated")
QA_DOMAIN_TERMS = {
    "video": ("video", "temporal", "action recognition", "activity recognition", "motion generation"),
    "audio": ("audio", "speech", "voice", "acoustic", "music"),
    "text": (
        "language",
        "nlp",
        "text",
        "linguistic",
        "translation",
        "large language",
        "llm",
        "information retrieval",
        "scientific search",
        "question answering",
        "dialogue",
    ),
    "vision": (
        "vision",
        "visual",
        "image",
        "detection",
        "segmentation",
        "3d",
        "face",
        "pose",
        "depth",
    ),
    "general": (
        "general",
        "machine learning",
        "representation learning",
        "multimodal",
        "artificial intelligence",
    ),
}


def list_qa_chunks(
    search: str | None = None,
    page: int = 1,
    size: int = 100,
    domain: str | None = None,
    generation_status: str | None = None,
) -> dict[str, Any]:
    page = max(page, 1)
    size = min(max(size, 1), 200)
    offset = (page - 1) * size
    base_params: list[Any] = []
    base_where = ["1=1"]
    if search:
        like = f"%{search.strip()}%"
        base_where.append("(c.chunk_id LIKE ? OR c.paper_id LIKE ? OR p.title LIKE ? OR c.content LIKE ?)")
        base_params.extend([like, like, like, like])
    normalized_domain = str(domain or "").strip().lower()
    if normalized_domain and normalized_domain not in QA_DOMAINS:
        raise ValueError(f"unsupported QA domain: {domain}")
    normalized_generation_status = str(generation_status or "all").strip().lower()
    if normalized_generation_status not in QA_GENERATION_STATUSES:
        raise ValueError(f"unsupported QA generation status: {generation_status}")
    base_where_sql = " AND ".join(base_where)
    with get_db() as db:
        metadata_rows = db.execute(
            f"""
            SELECT
                c.chunk_id,
                p.research_area,
                p.subfield,
                EXISTS(
                    SELECT 1
                    FROM qa_candidates q
                    WHERE q.chunk_id = c.chunk_id
                ) AS has_qa
            FROM paper_chunks c
            LEFT JOIN papers p ON p.id = c.paper_id
            WHERE {base_where_sql}
            ORDER BY c.paper_id, c.chunk_index
            """,
            base_params,
        ).fetchall()
        domain_counts = {key: 0 for key in QA_DOMAINS}
        domain_by_chunk_id: dict[str, str] = {}
        filtered_ids: list[str] = []
        for metadata_row in metadata_rows:
            has_qa = bool(metadata_row["has_qa"])
            if normalized_generation_status == "generated" and not has_qa:
                continue
            if normalized_generation_status == "not_generated" and has_qa:
                continue
            item_domain = _normalize_qa_domain(metadata_row["research_area"], metadata_row["subfield"])
            chunk_id = str(metadata_row["chunk_id"])
            domain_by_chunk_id[chunk_id] = item_domain
            domain_counts[item_domain] += 1
            if not normalized_domain or item_domain == normalized_domain:
                filtered_ids.append(chunk_id)
        total = len(filtered_ids)
        page_ids = filtered_ids[offset:offset + size]
        if not page_ids:
            rows = []
        else:
            page_where_sql = f"c.chunk_id IN ({', '.join('?' for _ in page_ids)})"
            rows = db.execute(
                f"""
                SELECT
                    c.chunk_id,
                    c.paper_id,
                    c.chunk_index,
                    c.content,
                    c.page_no,
                    p.title AS paper_title,
                    p.publish_venue,
                    p.source AS paper_source,
                    p.source_url,
                    p.source_page,
                    p.paper_url,
                    p.pdf_url,
                    p.research_area,
                    p.subfield,
                    (
                        SELECT COUNT(*)
                        FROM qa_candidates q
                        WHERE q.chunk_id = c.chunk_id
                    ) AS qa_count,
                    (
                        SELECT COUNT(*)
                        FROM dpo_pairs d
                        WHERE d.chunk_id = c.chunk_id
                    ) AS dpo_count
                FROM paper_chunks c
                LEFT JOIN papers p ON p.id = c.paper_id
                WHERE {page_where_sql}
                ORDER BY c.paper_id, c.chunk_index
                """,
                page_ids,
            ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item["paper_short_name"] = _paper_source_label(item)
        item["domain"] = domain_by_chunk_id.get(
            str(item.get("chunk_id")),
            _normalize_qa_domain(item.get("research_area"), item.get("subfield")),
        )
        item["source"] = item["paper_short_name"]
        item["source_url"] = item.get("source_url") or item.get("source_page") or item.get("paper_url") or item.get("pdf_url")
        item["content_preview"] = preview(item.get("content"), 260)
        item["has_qa"] = int(item.get("qa_count") or 0) > 0
        item["has_dpo"] = int(item.get("dpo_count") or 0) > 0
        if item["has_dpo"]:
            item["qa_status"] = "DPO_GENERATED"
        elif item["has_qa"]:
            item["qa_status"] = "SFT_GENERATED"
        else:
            item["qa_status"] = "NOT_GENERATED"
        items.append(item)
    return {
        "total": total,
        "available_total": sum(domain_counts.values()),
        "page": page,
        "size": size,
        "domain": normalized_domain or None,
        "generation_status": normalized_generation_status,
        "domain_counts": domain_counts,
        "list": items,
    }


def _normalize_qa_domain(research_area: Any, subfield: Any = None) -> str:
    searchable = " ".join(str(value or "").strip().lower() for value in (research_area, subfield)).strip()
    for domain in ("video", "audio", "text", "vision", "general"):
        if any(term in searchable for term in QA_DOMAIN_TERMS[domain]):
            return domain
    return "other"


def _paper_source_label(item: dict[str, Any]) -> str:
    for key in ("publish_venue", "paper_source", "paper_id"):
        value = str(item.get(key) or "").strip()
        if value:
            return value
    return ""


def generate_qa(payload: dict[str, Any], user_id: str = "system", ip: str | None = None) -> dict[str, Any]:
    chunk_ids = [str(item).strip() for item in payload.get("chunk_ids") or [] if str(item).strip()]
    batch_config = _validate_batch_config(payload.get("batch_config") or {})
    run_id = f"qa-run-{uuid.uuid4().hex[:12]}"
    now = utc_now()
    candidates: list[dict[str, Any]] = []
    missing: list[str] = []
    errors: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    model_name = CHUNK_QA_MODEL_NAME
    _require_model_config()
    with get_db() as db:
        chunks_by_id = {str(chunk["chunk_id"]): chunk for chunk in _load_chunks_by_ids(db, chunk_ids)}
        allocation = batch_config.get("domain_allocation")
        if allocation:
            actual_counts = {key: 0 for key in QA_DOMAINS}
            for chunk_id in chunk_ids:
                chunk = chunks_by_id.get(chunk_id)
                if chunk is not None:
                    actual_counts[str(chunk.get("domain") or "other")] += 1
            allocation["selected_total"] = sum(actual_counts.values())
            allocation["actual_counts"] = actual_counts
        _db_execute(
            db,
            """
            INSERT INTO qa_generation_runs(
                run_id, user_id, status, model, batch_config, chunk_ids,
                qa_count, error_count, missing_count, errors, missing_chunk_ids,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                user_id,
                "RUNNING",
                model_name,
                dumps(batch_config),
                dumps(chunk_ids),
                0,
                0,
                0,
                "[]",
                "[]",
                now,
                now,
            ),
        )
        for chunk_id in chunk_ids:
            chunk = chunks_by_id.get(chunk_id)
            if chunk is None:
                missing.append(chunk_id)
                continue
            try:
                candidate = _candidate_from_model(run_id, chunk)
            except ModelSkip as exc:
                skipped.append({"chunk_id": chunk_id, "reason": str(exc)})
                continue
            except ModelGenerationError as exc:
                errors.append({"chunk_id": chunk_id, "error": str(exc)})
                continue
            _db_execute(
                db,
                """
                INSERT INTO qa_candidates(
                    candidate_id, run_id, chunk_id, paper_id, paper_title, chunk_index,
                    page, question, answer, evidence_quote, qa_type, generator_model,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    candidate["candidate_id"],
                    run_id,
                    candidate["chunk_id"],
                    candidate.get("paper_id"),
                    candidate.get("paper_title"),
                    candidate.get("chunk_index"),
                    candidate.get("page"),
                    candidate["question"],
                    candidate["answer"],
                    candidate["evidence_quote"],
                    candidate["qa_type"],
                    candidate["generator_model"],
                    now,
                    now,
                ),
            )
            candidates.append(candidate)
        _db_execute(
            db,
            """
            UPDATE qa_generation_runs
            SET status = ?, qa_count = ?, error_count = ?, missing_count = ?, errors = ?,
                missing_chunk_ids = ?, updated_at = ?
            WHERE run_id = ?
            """,
            (
                _generation_status(len(candidates), len(errors), len(missing), len(skipped)),
                len(candidates),
                len(errors) + len(skipped),
                len(missing),
                dumps([*errors, *skipped]),
                dumps(missing),
                utc_now(),
                run_id,
            ),
        )
    return {
        "task_id": run_id,
        "run_id": run_id,
        "status": "completed" if candidates and not errors and not skipped else ("failed" if not candidates else "completed_with_errors"),
        "total": len(candidates),
        "list": candidates,
        "missing_chunk_ids": missing,
        "errors": errors,
        "skipped": skipped,
    }


def submit_review(payload: dict[str, Any], user_id: str = "system", ip: str | None = None) -> dict[str, Any]:
    candidate_ids = [str(item).strip() for item in payload.get("candidate_ids") or [] if str(item).strip()]
    review_session_id = f"qa-review-{uuid.uuid4().hex[:12]}"
    now = utc_now()
    created = 0
    with get_db() as db:
        db.execute(
            """
            INSERT INTO qa_review_sessions(
                review_session_id, source_run_id, user_id, status, total_count,
                pending_count, decided_count, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (review_session_id, payload.get("run_id"), user_id, "OPEN", 0, 0, 0, now, now),
        )
        for candidate_id in candidate_ids:
            row = db.execute("SELECT * FROM qa_candidates WHERE candidate_id = ?", (candidate_id,)).fetchone()
            if row is None:
                continue
            candidate = dict(row)
            review_item_id = f"qa-review-item-{uuid.uuid4().hex[:12]}"
            db.execute(
                """
                INSERT INTO qa_review_items(
                    review_item_id, review_session_id, candidate_id, chunk_id, paper_id,
                    paper_short_name, question, answer, evidence_quote, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    review_item_id,
                    review_session_id,
                    candidate_id,
                    candidate.get("chunk_id"),
                    candidate.get("paper_id"),
                    candidate.get("paper_short_name"),
                    candidate.get("question"),
                    candidate.get("answer"),
                    candidate.get("evidence_quote"),
                    now,
                    now,
                ),
            )
            db.execute(
                "UPDATE qa_candidates SET review_submitted = ?, review_session_id = ?, updated_at = ? WHERE candidate_id = ?",
                (True, review_session_id, now, candidate_id),
            )
            created += 1
        db.execute(
            """
            UPDATE qa_review_sessions
            SET total_count = ?, pending_count = ?, updated_at = ?
            WHERE review_session_id = ?
            """,
            (created, created, utc_now(), review_session_id),
        )
    return {"review_session_id": review_session_id, "created": created, "status": "OPEN"}


def manual_review(payload: dict[str, Any], user_id: str = "system", ip: str | None = None) -> dict[str, Any]:
    review_item_id = str(payload.get("review_item_id") or payload.get("item_id") or "").strip()
    decision = str(payload.get("decision") or payload.get("current_decision") or "APPROVED").strip().upper()
    comment = payload.get("comment") or payload.get("review_comment")
    if not review_item_id:
        return {"updated": 0, "error": "review_item_id is required"}
    now = utc_now()
    with get_db() as db:
        cur = db.execute(
            """
            UPDATE qa_review_items
            SET current_decision = ?, decision_source = ?, reviewer = ?, review_comment = ?,
                reviewed = ?, reviewed_at = ?, updated_at = ?
            WHERE review_item_id = ?
            """,
            (decision, "MANUAL", user_id, comment, True, now, now, review_item_id),
        )
    return {"updated": getattr(cur, "rowcount", 0), "review_item_id": review_item_id, "decision": decision}


def generate_dpo(payload: dict[str, Any], user_id: str = "system", ip: str | None = None) -> dict[str, Any]:
    chunk_ids = [str(item).strip() for item in payload.get("chunk_ids") or [] if str(item).strip()]
    if not chunk_ids:
        raise ValueError("chunk_ids is required")

    run_id = f"dpo-run-{uuid.uuid4().hex[:12]}"
    now = utc_now()
    pairs: list[dict[str, Any]] = []
    blocked: list[str] = []
    missing: list[str] = []
    seen: set[str] = set()
    normalized_chunk_ids: list[str] = []
    for chunk_id in chunk_ids:
        if chunk_id not in seen:
            normalized_chunk_ids.append(chunk_id)
            seen.add(chunk_id)

    with get_db() as db:
        db.execute(
            """
            INSERT INTO dpo_generation_runs(
                run_id, user_id, status, generator_version, chunk_ids,
                dpo_count, blocked_count, missing_count, blocked_chunk_ids,
                missing_chunk_ids, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                user_id,
                "RUNNING",
                DPO_GENERATOR_VERSION,
                dumps(normalized_chunk_ids),
                0,
                0,
                0,
                "[]",
                "[]",
                now,
                now,
            ),
        )
        for chunk_id in normalized_chunk_ids:
            chunk_row = db.execute(
                """
                SELECT c.chunk_id, c.paper_id, p.title AS paper_title
                FROM paper_chunks c
                LEFT JOIN papers p ON p.id = c.paper_id
                WHERE c.chunk_id = ?
                """,
                (chunk_id,),
            ).fetchone()
            if chunk_row is None:
                missing.append(chunk_id)
                continue

            candidate_row = db.execute(
                """
                SELECT candidate_id, chunk_id, paper_id, paper_title, question, answer, evidence_quote, created_at
                FROM qa_candidates
                WHERE chunk_id = ?
                ORDER BY created_at DESC, candidate_id DESC
                LIMIT 1
                """,
                (chunk_id,),
            ).fetchone()
            if candidate_row is None:
                blocked.append(chunk_id)
                continue

            candidate = dict(candidate_row)
            try:
                dpo_pair = _dpo_pair_from_candidate(run_id, candidate)
            except ModelGenerationError:
                blocked.append(chunk_id)
                continue
            db.execute(
                """
                INSERT INTO dpo_pairs(
                    dpo_pair_id, run_id, candidate_id, chunk_id, paper_id, paper_title,
                    prompt, chosen, rejected, generator_version, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    dpo_pair["dpo_pair_id"],
                    run_id,
                    dpo_pair["candidate_id"],
                    dpo_pair["chunk_id"],
                    dpo_pair.get("paper_id"),
                    dpo_pair.get("paper_title"),
                    dpo_pair["prompt"],
                    dpo_pair["chosen"],
                    dpo_pair["rejected"],
                    dpo_pair["generator_version"],
                    now,
                    now,
                ),
            )
            pairs.append(dpo_pair)

        status = "SUCCESS" if pairs else "BLOCKED"
        db.execute(
            """
            UPDATE dpo_generation_runs
            SET status = ?, dpo_count = ?, blocked_count = ?, missing_count = ?,
                blocked_chunk_ids = ?, missing_chunk_ids = ?, updated_at = ?
            WHERE run_id = ?
            """,
            (
                status,
                len(pairs),
                len(blocked),
                len(missing),
                dumps(blocked),
                dumps(missing),
                utc_now(),
                run_id,
            ),
        )

    return {
        "task_id": run_id,
        "run_id": run_id,
        "status": "completed" if pairs else "blocked",
        "generator_version": DPO_GENERATOR_VERSION,
        "total": len(pairs),
        "list": pairs,
        "blocked_chunk_ids": blocked,
        "missing_chunk_ids": missing,
    }


def export_dpo_jsonl(run_id: str) -> tuple[str, str]:
    run_id = str(run_id or "").strip()
    if not run_id:
        raise ValueError("run_id is required")
    with get_db() as db:
        rows = db.execute(
            """
            SELECT dpo_pair_id, prompt, chosen, rejected, chunk_id
            FROM dpo_pairs
            WHERE run_id = ?
            ORDER BY created_at, dpo_pair_id
            """,
            (run_id,),
        ).fetchall()
    if not rows:
        raise ValueError("no dpo pairs found for run_id")
    lines = []
    for row in rows:
        item = dict(row)
        lines.append(
            dumps(
                {
                    "id": item["dpo_pair_id"],
                    "prompt": item["prompt"],
                    "chosen": item["chosen"],
                    "rejected": item["rejected"],
                    "chunk_id": item["chunk_id"],
                }
            )
        )
    return "\n".join(lines) + "\n", f"dpo-pairs-{run_id}.jsonl"


def _candidate_from_model(run_id: str, chunk: dict[str, Any]) -> dict[str, Any]:
    content = chunk.get("content") or chunk.get("text") or ""
    paper_title = chunk.get("paper_title") or chunk.get("title") or chunk.get("paper_id") or "this paper"
    messages = [
        {
            "role": "system",
            "content": (
                "You generate high-quality SFT question-answer data from scientific paper chunks. "
                "Return one strict JSON object only."
            ),
        },
        {
            "role": "user",
            "content": (
                "Generate exactly one grounded QA pair from the chunk below. "
                "Use the chunk as evidence. If the chunk is unusable, return {\"status\":\"SKIP\",\"reason\":\"...\"}. "
                "Otherwise return JSON with keys: status='GENERATED', question, answer, evidence_quote, qa_type. "
                "The evidence_quote must be copied from the chunk and the answer must be supported by it.\n\n"
                f"Paper title: {paper_title}\n"
                f"Chunk ID: {chunk.get('chunk_id')}\n"
                f"Chunk text:\n{content}"
            ),
        },
    ]
    data = _parse_model_json(_call_model(messages))
    status = str(data.get("status") or "GENERATED").upper()
    if status == "SKIP":
        retry_messages = [
            {
                "role": "system",
                "content": (
                    "Do not return SKIP. Generate one conservative QA pair grounded only in the provided chunk. "
                    "If evidence is thin, ask about the clearest factual statement in the chunk."
                ),
            },
            *messages,
        ]
        retry_data = _parse_model_json(_call_model(retry_messages))
        retry_status = str(retry_data.get("status") or "GENERATED").upper()
        if retry_status == "SKIP":
            raise ModelGenerationError(f"force retry failed: {retry_data.get('reason') or data.get('reason') or 'model skipped chunk'}")
        if retry_status != "GENERATED":
            raise ModelGenerationError(f"unexpected model status: {retry_status}")
        data = retry_data
        status = retry_status
    if status != "GENERATED":
        raise ModelGenerationError(f"unexpected model status: {status}")
    question = _required_text(data, "question")
    answer = _required_text(data, "answer")
    evidence = _required_text(data, "evidence_quote")
    return {
        "candidate_id": f"qa-{uuid.uuid4().hex[:12]}",
        "run_id": run_id,
        "chunk_id": chunk["chunk_id"],
        "paper_id": chunk.get("paper_id"),
        "paper_title": paper_title,
        "chunk_index": chunk.get("chunk_index"),
        "page": chunk.get("page_no") if chunk.get("page_no") is not None else chunk.get("page"),
        "question": question,
        "answer": answer,
        "evidence_quote": evidence,
        "qa_type": str(data.get("qa_type") or "model_generated"),
        "generator_model": CHUNK_QA_MODEL_NAME,
    }


def _load_chunks_by_ids(db: Any, chunk_ids: list[str]) -> list[dict[str, Any]]:
    chunks = []
    for chunk_id in chunk_ids:
        row = db.execute(
            """
            SELECT c.chunk_id, c.paper_id, c.chunk_index, c.content, c.page_no,
                   p.title AS paper_title, p.research_area, p.subfield
            FROM paper_chunks c
            LEFT JOIN papers p ON p.id = c.paper_id
            WHERE c.chunk_id = ?
            """,
            (chunk_id,),
        ).fetchone()
        if row is not None:
            chunk = dict(row)
            chunk["domain"] = _normalize_qa_domain(chunk.get("research_area"), chunk.get("subfield"))
            chunks.append(chunk)
    return chunks


def _validate_batch_config(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("batch_config must be an object")
    batch_config = dict(value)
    allocation = batch_config.get("domain_allocation")
    if allocation is None:
        return batch_config
    if not isinstance(allocation, dict):
        raise ValueError("domain_allocation must be an object")
    percentages = allocation.get("percentages")
    if not isinstance(percentages, dict) or any(domain not in percentages for domain in QA_DOMAINS):
        raise ValueError("domain_allocation.percentages must contain all six domains")
    normalized_percentages: dict[str, float] = {}
    for domain in QA_DOMAINS:
        try:
            percentage = float(percentages[domain])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid percentage for domain: {domain}") from exc
        if percentage < 0 or percentage > 100:
            raise ValueError(f"percentage out of range for domain: {domain}")
        normalized_percentages[domain] = percentage
    if abs(sum(normalized_percentages.values()) - 100) > 0.01:
        raise ValueError("domain allocation percentages must total 100")
    normalized_allocation = dict(allocation)
    normalized_allocation["percentages"] = normalized_percentages
    normalized_allocation["version"] = str(allocation.get("version") or "paper-metadata-v1")
    batch_config["domain_allocation"] = normalized_allocation
    return batch_config


def _db_execute(db: Any, sql: str, params: tuple | list | None = None) -> Any:
    if not hasattr(db, "execute"):
        return None
    return db.execute(sql, params or [])


def _dpo_pair_from_candidate(run_id: str, candidate: dict[str, Any]) -> dict[str, Any]:
    chosen = str(candidate.get("answer") or "").strip()
    prompt = str(candidate.get("question") or "").strip()
    evidence = str(candidate.get("evidence_quote") or chosen).strip()
    rejected = _generate_rejected_answer(prompt, chosen, evidence)
    return {
        "dpo_pair_id": f"dpo-{uuid.uuid4().hex[:12]}",
        "run_id": run_id,
        "candidate_id": candidate["candidate_id"],
        "chunk_id": candidate["chunk_id"],
        "paper_id": candidate.get("paper_id"),
        "paper_title": candidate.get("paper_title"),
        "prompt": prompt,
        "chosen": chosen,
        "rejected": rejected,
        "generator_version": DPO_GENERATOR_VERSION,
    }


def _weaken_answer(chosen: str, evidence: str) -> str:
    base = " ".join((chosen or evidence or "").split())
    if not base:
        return "The selected evidence is not sufficient to answer the question."
    shortened = preview(base, 120)
    return (
        "The passage only gives a partial clue, so a cautious but incomplete answer is: "
        f"{shortened}"
    )


def _generate_rejected_answer(prompt: str, chosen: str, evidence: str) -> str:
    _require_model_config()
    messages = [
        {
            "role": "system",
            "content": (
                "You create DPO rejected answers for preference training. "
                "Return one strict JSON object only."
            ),
        },
        {
            "role": "user",
            "content": (
                "Given a prompt, a high-quality chosen answer, and evidence, write a rejected answer. "
                "The rejected answer must be relevant and plausible but clearly worse than the chosen answer: "
                "less complete, less precise, or partially unsupported. Do not include harmful content. "
                "Return JSON with key rejected only.\n\n"
                f"Prompt: {prompt}\n"
                f"Chosen answer: {chosen}\n"
                f"Evidence: {evidence}"
            ),
        },
    ]
    data = _parse_model_json(_call_model(messages))
    rejected = _required_text(data, "rejected")
    if rejected.strip() == chosen.strip():
        raise ModelGenerationError("model returned rejected answer identical to chosen")
    return rejected


def _call_model(messages: list[dict[str, str]]) -> str:
    _require_model_config()
    payload: dict[str, Any] = {
        "model": CHUNK_QA_MODEL_NAME,
        "messages": messages,
        "temperature": CHUNK_QA_MODEL_TEMPERATURE,
    }
    if CHUNK_QA_MODEL_RESPONSE_FORMAT:
        payload["response_format"] = {"type": CHUNK_QA_MODEL_RESPONSE_FORMAT}
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"{CHUNK_QA_MODEL_BASE_URL}/chat/completions",
        data=data,
        headers={
            "Authorization": f"Bearer {CHUNK_QA_MODEL_API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=CHUNK_QA_MODEL_TIMEOUT) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise ModelGenerationError(f"model provider returned HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise ModelGenerationError(f"model provider unavailable: {exc.reason}") from exc
    except TimeoutError as exc:
        raise ModelGenerationError("model provider timeout") from exc
    try:
        parsed = json.loads(body)
        return str(parsed["choices"][0]["message"]["content"])
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise ModelGenerationError("model provider response schema invalid") from exc


def _parse_model_json(content: str) -> dict[str, Any]:
    text = (content or "").strip()
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text.lower().startswith("json"):
            text = text[4:].strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ModelGenerationError("model output is not valid JSON") from exc
    if not isinstance(data, dict):
        raise ModelGenerationError("model output must be a JSON object")
    return data


def _required_text(data: dict[str, Any], key: str) -> str:
    value = str(data.get(key) or "").strip()
    if not value:
        raise ModelGenerationError(f"model output missing {key}")
    return value


def _require_model_config() -> None:
    if not CHUNK_QA_MODEL_BASE_URL or not CHUNK_QA_MODEL_API_KEY or not CHUNK_QA_MODEL_NAME:
        raise ModelGenerationError("真实大模型配置缺失，请检查 CHUNK_QA_MODEL_BASE_URL/API_KEY/NAME")


def _generation_status(generated: int, errors: int, missing: int, skipped: int) -> str:
    if generated and not errors and not missing and not skipped:
        return "SUCCESS"
    if generated:
        return "COMPLETED_WITH_ERRORS"
    return "FAILED"


class ModelGenerationError(Exception):
    pass


class ModelSkip(Exception):
    pass
