from __future__ import annotations

import json
import os
import re
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from flask import Flask
from sqlalchemy import func, select, text

from ...extensions import db
from ...llm import build_personal_chat_model, build_qwen_dpo_chat_model
from ...models import Task, TaskEvent
from ...services.task_model_runtime import resolve_task_model_runtime
from ..task_service import public_error_message
from .adapter import LiteratureCoreAdapter
from .offline_model import OfflineResearchModel


SOURCE_NODES = {
    "retrieve_local": ("local_knowledge", "本地文献库"),
    "retrieve_personal": ("personal_knowledge", "个人收藏"),
    "retrieve_google_scholar": ("google_scholar", "Google Scholar"),
    "retrieve_arxiv": ("arxiv", "arXiv"),
}


class LiteratureSearchService:
    """Runs the vendored six-stage workflow and persists every state transition."""

    def __init__(self, app: Flask) -> None:
        self.app = app

    def start(self, task_id: UUID, user_id: UUID) -> None:
        thread = threading.Thread(
            target=self._run_with_context,
            args=(task_id, user_id),
            name=f"literature-search-{task_id}",
            daemon=True,
        )
        thread.start()

    def _run_with_context(self, task_id: UUID, user_id: UUID) -> None:
        with self.app.app_context():
            try:
                self.run(task_id, user_id)
            except Exception as exc:  # noqa: BLE001
                db.session.rollback()
                task = db.session.get(Task, task_id)
                if task:
                    safe_error = public_error_message(exc, "文献检索工作流执行失败")
                    task.status = "FAILED"
                    task.progress = 100
                    task.error_code = type(exc).__name__
                    task.safe_error_message = safe_error
                    task.finished_at = datetime.now(UTC)
                    self.emit(task, "task.failed", 100, "文献检索工作流执行失败", error=safe_error)
                self.app.logger.exception("Literature search task %s failed", task_id)
            finally:
                db.session.remove()

    def run(self, task_id: UUID, user_id: UUID) -> None:
        task = db.session.get(Task, task_id)
        if task is None:
            return
        prompt = str((task.input_json or {}).get("prompt") or "").strip()
        output_dir: Path = self.app.config["AGENT_GENERATED_DIR"] / str(task.id)
        output_dir.mkdir(parents=True, exist_ok=True)
        fishbone_path = output_dir / "annual_publication_timeline.png"

        adapter = LiteratureCoreAdapter()
        engine = db.engine
        arxiv_tool, scholar_tool = adapter.external_tools(
            bool(self.app.config["LITERATURE_EXTERNAL_SEARCH"])
        )
        use_offline_model = bool(self.app.config["LITERATURE_FORCE_OFFLINE_MODEL"])
        # The task's own model when it has one; the platform model otherwise.
        personal_runtime = None if use_offline_model else resolve_task_model_runtime(task, user_id)
        if use_offline_model:
            chat_model = OfflineResearchModel()
        elif personal_runtime:
            chat_model = build_personal_chat_model(personal_runtime)
        else:
            chat_model = build_qwen_dpo_chat_model()
        graph = adapter.build_graph(
            chat_model=chat_model,
            local_retriever=DatabasePaperRetriever(engine=engine, user_id=None),
            personal_retriever=DatabasePaperRetriever(engine=engine, user_id=user_id),
            arxiv_tool=arxiv_tool,
            scholar_tool=scholar_tool,
            top_n=int(self.app.config["LITERATURE_REPORT_LIMIT"]),
            output_path=str(fishbone_path),
            output_title=f"{prompt[:80]} · 年度文献脉络",
            allow_report_fallback=True,
        )

        task.status = "RUNNING"
        task.started_at = datetime.now(UTC)
        self.emit(task, "task.started", 5, "已启动文献检索 Agent 六阶段工作流")
        final_state: dict[str, Any] = {}
        source_progress: dict[str, dict[str, Any]] = {}
        persistence_warnings: list[str] = []
        completed_sources = 0
        context = adapter.runtime_context(user_id=str(user_id), thread_id=str(task.id))

        for mode, payload in graph.stream(
            {
                "user_text": prompt,
                "output_path": str(fishbone_path),
                "output_title": f"{prompt[:80]} · 年度文献脉络",
            },
            stream_mode=["updates", "custom", "values"],
            context=context,
        ):
            if mode == "values":
                final_state = payload
                continue
            if mode == "custom":
                if payload.get("event") == "paper_inserted":
                    progress = 84 + int(float(payload.get("progress", 0)) * 14)
                    self.emit(
                        task,
                        "timeline.inserted",
                        progress,
                        f"年度脉络已插入第 {payload.get('sequence')} 篇文献",
                        detail=to_jsonable(payload),
                    )
                continue
            if mode != "updates":
                continue
            for node_name, update in payload.items():
                if not isinstance(update, dict):
                    continue
                if node_name == "rewrite_query":
                    query_plan = to_jsonable(update.get("query_plan"))
                    self.merge_output(task, query_plan=query_plan)
                    self.emit(task, "query.rewritten", 12, "查询意图与检索式已生成", detail=query_plan)
                elif node_name in SOURCE_NODES:
                    completed_sources += 1
                    source, label = SOURCE_NODES[node_name]
                    batches = next(
                        (value for key, value in update.items() if key.endswith("_retrieval_batches")), []
                    )
                    count = sum(len(getattr(batch, "papers", [])) for batch in batches)
                    errors = [to_jsonable(item) for item in update.get("errors", [])]
                    source_progress[source] = {
                        "label": label,
                        "count": count,
                        "status": "completed" if count or not errors else "failed",
                        "errors": errors,
                    }
                    self.merge_output(task, source_progress=source_progress)
                    self.emit(
                        task,
                        "source.completed",
                        14 + completed_sources * 10,
                        f"{label}检索完成，获得 {count} 条结果",
                        source=source,
                        detail=source_progress[source],
                    )
                elif node_name == "aggregate_and_rank":
                    papers = [core_paper_to_dict(item) for item in update.get("all_ranked_papers", [])]
                    try:
                        self.persist_papers(papers)
                    except Exception as exc:  # noqa: BLE001
                        # Search results are useful even when the optional
                        # platform-paper cache is unavailable or has an older
                        # schema on a newly deployed server.
                        warning = f"本地文献缓存写入失败：{type(exc).__name__}"
                        persistence_warnings.append(warning)
                        db.session.rollback()
                        self.app.logger.warning(
                            "Could not persist literature search papers for task %s: %s",
                            task.id,
                            exc,
                        )
                    self.merge_output(task, papers=papers)
                    self.emit(task, "papers.ranked", 66, f"文献去重排序完成，共 {len(papers)} 篇", count=len(papers))
                elif node_name == "generate_report":
                    report = to_jsonable(update.get("report"))
                    self.merge_output(task, report_markdown=report.get("markdown", ""))
                    self.emit(task, "report.ready", 78, "研究报告生成完成")
                elif node_name == "format_literature_list":
                    literature_list = to_jsonable(update.get("literature_list", []))
                    self.merge_output(task, literature_list=literature_list)
                    self.emit(task, "literature.list_ready", 83, "文献列表整理完成", count=len(literature_list))

        if not final_state:
            raise RuntimeError("Agent 工作流结束但未返回最终状态")
        if fishbone_path.exists():
            try:
                self.persist_artifact(task, fishbone_path)
            except Exception as exc:  # noqa: BLE001
                warning = f"年度脉络图索引写入失败：{type(exc).__name__}"
                persistence_warnings.append(warning)
                db.session.rollback()
                self.app.logger.warning(
                    "Could not persist literature artifact for task %s: %s",
                    task.id,
                    exc,
                )
                fishbone_path = None
        final_warnings = [*to_jsonable(final_state.get("warnings", [])), *persistence_warnings]
        result = {
            "query_plan": to_jsonable(final_state.get("query_plan")),
            "source_progress": source_progress,
            "papers": [core_paper_to_dict(item) for item in final_state.get("all_ranked_papers", [])],
            "literature_list": to_jsonable(final_state.get("literature_list", [])),
            "report_markdown": getattr(final_state.get("report"), "markdown", ""),
            "fishbone_url": f"/api/v1/tasks/{task.id}/fishbone" if fishbone_path and fishbone_path.exists() else None,
            "warnings": final_warnings,
            "errors": to_jsonable(final_state.get("errors", [])),
        }
        task.output_json = result
        task.trace_summary = {
            "sources": source_progress,
            "paper_count": len(result["papers"]),
            "has_report": bool(result["report_markdown"]),
        }
        task.status = "SUCCEEDED"
        task.progress = 100
        task.current_step = "检索完成"
        task.finished_at = datetime.now(UTC)
        self.emit(task, "task.completed", 100, "文献检索、报告和年度脉络生成完成")

    def emit(
        self,
        task: Task,
        event_type: str,
        progress: int,
        message: str,
        **payload: Any,
    ) -> None:
        sequence = db.session.scalar(
            select(func.coalesce(func.max(TaskEvent.sequence), 0)).where(TaskEvent.task_id == task.id)
        )
        task.progress = min(progress, 100)
        task.current_step = message[:150]
        db.session.add(
            TaskEvent(
                task_id=task.id,
                sequence=int(sequence or 0) + 1,
                event_type=event_type,
                payload={"progress": task.progress, "message": message, **to_jsonable(payload)},
            )
        )
        db.session.commit()

    @staticmethod
    def merge_output(task: Task, **updates: Any) -> None:
        task.output_json = {**(task.output_json or {}), **to_jsonable(updates)}

    @staticmethod
    def persist_papers(papers: list[dict[str, Any]]) -> None:
        for paper in papers:
            title = str(paper.get("title") or "").strip()
            if not title:
                continue
            year = paper.get("year")
            normalized = normalize_title(title)
            exists = db.session.execute(
                text(
                    """
                    SELECT id FROM zhiyan.papers
                    WHERE normalized_title = :normalized
                      AND publish_year IS NOT DISTINCT FROM :year
                    LIMIT 1
                    """
                ),
                {"normalized": normalized, "year": year},
            ).scalar_one_or_none()
            source_json = {
                "sources": paper.get("sources") or [paper.get("source")],
                "url": paper.get("url"),
                "raw": paper.get("raw") or {},
            }
            if exists:
                db.session.execute(
                    text(
                        """
                        UPDATE zhiyan.papers
                        SET citation_count = COALESCE(:citation_count, citation_count),
                            pdf_url = COALESCE(:pdf_url, pdf_url),
                            source_json = CAST(:source_json AS jsonb),
                            updated_at = now()
                        WHERE id = :id
                        """
                    ),
                    {
                        "id": exists,
                        "citation_count": paper.get("citation_count"),
                        "pdf_url": paper.get("pdf_url"),
                        "source_json": json.dumps(source_json, ensure_ascii=False),
                    },
                )
                continue
            db.session.execute(
                text(
                    """
                    INSERT INTO zhiyan.papers (
                        title, normalized_title, abstract, authors, venue, publish_year,
                        keywords, research_areas, pdf_url, source_json, citation_count
                    ) VALUES (
                        :title, :normalized, :abstract, CAST(:authors AS jsonb), :venue, :year,
                        '[]'::jsonb, '[]'::jsonb, :pdf_url, CAST(:source_json AS jsonb), :citation_count
                    )
                    """
                ),
                {
                    "title": title,
                    "normalized": normalized,
                    "abstract": paper.get("abstract"),
                    "authors": json.dumps(paper.get("authors") or [], ensure_ascii=False),
                    "venue": paper.get("venue"),
                    "year": year,
                    "pdf_url": paper.get("pdf_url"),
                    "source_json": json.dumps(source_json, ensure_ascii=False),
                    "citation_count": paper.get("citation_count"),
                },
            )

    @staticmethod
    def persist_artifact(task: Task, path: Path) -> None:
        db.session.execute(
            text(
                """
                INSERT INTO zhiyan.artifacts (
                    id, owner_user_id, task_id, artifact_type, name, object_key,
                    content_json, metadata, status
                ) VALUES (
                    :id, :owner_user_id, :task_id, 'IMAGE', :name, :object_key,
                    '{}'::jsonb, CAST(:metadata AS jsonb), 'READY'
                )
                """
            ),
            {
                "id": uuid4(),
                "owner_user_id": task.user_id,
                "task_id": task.id,
                "name": "年度文献发表脉络.png",
                "object_key": f"literature_search/{task.id}/{path.name}",
                "metadata": json.dumps({"mime_type": "image/png", "size_bytes": path.stat().st_size}),
            },
        )


class DatabasePaperRetriever:
    """Reads platform papers from PostgreSQL; a user id restricts results to favorites."""

    def __init__(self, engine: Any, user_id: UUID | None) -> None:
        self.engine = engine
        self.user_id = user_id

    def invoke(self, input: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
        query = str(input.get("query") or "").strip()
        limit = min(max(int(input.get("max_results", 10)), 1), 100)
        terms = [item for item in re.split(r"\s+", query) if len(item) > 1][:6]
        conditions = ["p.status = 'ACTIVE'"]
        params: dict[str, Any] = {"limit": limit}
        if input.get("start_year") is not None:
            conditions.append("p.publish_year >= :start_year")
            params["start_year"] = input["start_year"]
        if input.get("end_year") is not None:
            conditions.append("p.publish_year <= :end_year")
            params["end_year"] = input["end_year"]
        if terms:
            matches = []
            for index, term in enumerate(terms):
                key = f"term_{index}"
                params[key] = f"%{term}%"
                matches.append(f"(p.title ILIKE :{key} OR COALESCE(p.abstract, '') ILIKE :{key})")
            conditions.append("(" + " OR ".join(matches) + ")")
        join = ""
        if self.user_id is not None:
            join = "JOIN zhiyan.paper_favorites pf ON pf.paper_id = p.id AND pf.user_id = :user_id"
            params["user_id"] = self.user_id
        statement = text(
            f"""
            SELECT p.id, p.title, p.abstract, p.authors, p.venue, p.publish_year,
                   p.pdf_url, p.citation_count, p.source_json
            FROM zhiyan.papers p
            {join}
            WHERE {' AND '.join(conditions)}
            ORDER BY p.citation_count DESC NULLS LAST, p.publish_year DESC NULLS LAST
            LIMIT :limit
            """
        )
        # LangGraph runs retrieval nodes in worker threads. A captured Engine is
        # thread-safe and avoids Flask's request-scoped db.session in those workers.
        with self.engine.connect() as connection:
            rows = connection.execute(statement, params).mappings().all()
        source = "personal_knowledge" if self.user_id is not None else "local_knowledge"
        return {
            "papers": [
                {
                    "id": f"database:{row['id']}",
                    "title": row["title"],
                    "authors": normalize_authors(row["authors"]),
                    "abstract": row["abstract"] or "",
                    "year": row["publish_year"],
                    "published_year": row["publish_year"],
                    "venue": row["venue"],
                    "source": source,
                    "sources": [source],
                    "url": normalize_source_json(row["source_json"]).get("url"),
                    "pdf_url": row["pdf_url"],
                    "citation_count": row["citation_count"],
                    "raw": normalize_source_json(row["source_json"]),
                }
                for row in rows
            ]
        }


def to_jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {key: to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    if isinstance(value, (UUID, datetime)):
        return str(value)
    return value


def core_paper_to_dict(paper: Any) -> dict[str, Any]:
    data = to_jsonable(paper)
    return {
        "id": data.get("id"),
        "title": data.get("title") or "",
        "authors": data.get("authors") or [],
        "abstract": data.get("abstract") or "",
        "year": data.get("published_year"),
        "venue": data.get("venue"),
        "source": data.get("source") or "unknown",
        "sources": data.get("sources") or [],
        "url": data.get("url"),
        "pdf_url": data.get("pdf_url"),
        "citation_count": data.get("citation_count"),
        "score": data.get("retrieval_score") or 0.0,
        "raw": data.get("raw") or {},
    }


def normalize_title(title: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", title.casefold())


def normalize_source_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def normalize_authors(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
        return [item.strip() for item in re.split(r"[,;，；]", value) if item.strip()]
    return []
