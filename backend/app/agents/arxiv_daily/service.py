from __future__ import annotations

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select

from ...extensions import db
from ...llm import run_openai_compatible_chat
from ...models import ArxivDailyRun, Task
from ...services.user_model_config import API_MODEL_VALUE
from ..task_service import BuiltinAgentTaskService, public_error_message


ARXIV_CS_CATEGORIES = [
    ("cs.AI", "人工智能"),
    ("cs.AR", "硬件架构"),
    ("cs.CC", "计算复杂性"),
    ("cs.CE", "计算工程、金融与科学"),
    ("cs.CG", "计算几何"),
    ("cs.CL", "计算与语言"),
    ("cs.CR", "密码学与安全"),
    ("cs.CV", "计算机视觉与模式识别"),
    ("cs.CY", "计算机与社会"),
    ("cs.DB", "数据库"),
    ("cs.DC", "分布式、并行与集群计算"),
    ("cs.DL", "数字图书馆"),
    ("cs.DM", "离散数学"),
    ("cs.DS", "数据结构与算法"),
    ("cs.ET", "新兴技术"),
    ("cs.FL", "形式语言与自动机"),
    ("cs.GL", "综合文献"),
    ("cs.GR", "图形学"),
    ("cs.GT", "计算机科学与博弈论"),
    ("cs.HC", "人机交互"),
    ("cs.IR", "信息检索"),
    ("cs.IT", "信息论"),
    ("cs.LG", "机器学习"),
    ("cs.LO", "计算机逻辑"),
    ("cs.MA", "多智能体系统"),
    ("cs.MM", "多媒体"),
    ("cs.MS", "数学软件"),
    ("cs.NA", "数值分析"),
    ("cs.NE", "神经与进化计算"),
    ("cs.NI", "网络与互联网架构"),
    ("cs.OH", "其他计算机科学"),
    ("cs.OS", "操作系统"),
    ("cs.PF", "性能分析"),
    ("cs.PL", "编程语言"),
    ("cs.RO", "机器人学"),
    ("cs.SC", "符号计算"),
    ("cs.SD", "声音技术"),
    ("cs.SE", "软件工程"),
    ("cs.SI", "社会与信息网络"),
    ("cs.SY", "系统与控制"),
]
ARXIV_CATEGORY_NAMES = dict(ARXIV_CS_CATEGORIES)


def normalize_arxiv_daily_options(payload: dict[str, Any]) -> dict[str, Any]:
    category = str(payload.get("arxiv_category") or "cs.AI").strip()
    if category not in ARXIV_CATEGORY_NAMES:
        raise ValueError("请选择有效的 arXiv 计算机科学分类")
    search_query = str(payload.get("arxiv_search") or "").strip()[:200]
    refresh_value = payload.get("arxiv_refresh", False)
    refresh = refresh_value is True or str(refresh_value).strip().lower() in {"1", "true", "yes", "on"}
    return {
        "category": category,
        "category_name": ARXIV_CATEGORY_NAMES[category],
        "search_query": search_query,
        "refresh": refresh,
    }


class ArxivDailyService(BuiltinAgentTaskService):
    agent_label = "arxiv-daily"
    failed_message = "学术速递 Agent 同步失败"

    def _run_with_context(self, task_id: UUID, user_id: UUID) -> None:
        with self.app.app_context():
            try:
                self.run(task_id, user_id)
            except Exception as exc:  # noqa: BLE001
                db.session.rollback()
                task = db.session.get(Task, task_id)
                record = self._get_record(task_id, user_id)
                if task:
                    safe_error = public_error_message(exc, self.failed_message)
                    task.status = "FAILED"
                    task.progress = 100
                    task.error_code = type(exc).__name__
                    task.safe_error_message = safe_error
                    task.finished_at = datetime.now(UTC)
                    if record:
                        record.status = "FAILED"
                    self.emit(task, "task.failed", 100, self.failed_message, error=safe_error)
                self.app.logger.exception("Academic daily task %s failed", task_id)
            finally:
                db.session.remove()

    def run(self, task_id: UUID, user_id: UUID) -> None:
        task = db.session.get(Task, task_id)
        if task is None:
            return
        options = (task.input_json or {}).get("arxiv_daily_options") or {}
        category = str(options.get("category") or "cs.AI")
        record = self._record_for(task, user_id, options)
        task.status = "RUNNING"
        task.started_at = datetime.now(UTC)
        record.status = "RUNNING"
        self.emit(task, "task.started", 2, "已启动学术速递")

        recent = self._latest_snapshot(category, exclude_task_id=task.id, fresh_only=True)
        stale = recent or self._latest_snapshot(category, exclude_task_id=task.id, fresh_only=False)
        warnings: list[str] = []
        if recent is not None and not record.refresh_requested:
            categories = recent.categories
            papers = recent.papers
            fetched_at = recent.fetched_at or recent.completed_at or datetime.now(UTC)
            source_url = recent.source_url
            warnings.append("一小时内已同步过该分类，直接使用上次结果；点“重新同步”可强制刷新。")
            self.emit(task, "daily.cache_hit", 90, "已读取一小时内同步过的论文")
        else:
            self.emit(task, "daily.fetching", 5, f"正在从 arXiv 获取 {category} 最新论文")
            try:
                payload = self._fetch_snapshot(category)
                categories = payload["categories"]
                papers = payload["papers"]
                fetched_at = _parse_datetime(payload.get("fetched_at"))
                source_url = str(payload.get("source") or f"https://arxiv.org/list/{category}/new")
            except Exception:
                if stale is None:
                    raise
                self.app.logger.warning(
                    "arXiv refresh failed for %s; serving stale database snapshot",
                    category,
                    exc_info=True,
                )
                categories = stale.categories
                papers = stale.papers
                fetched_at = stale.fetched_at or stale.completed_at or datetime.now(UTC)
                source_url = stale.source_url
                warnings.append("arXiv 暂时不可用，已返回数据库中的最近一次成功快照。")
            else:
                papers, missing = self._translate_papers(task, papers)
                if missing:
                    warnings.append(f"有 {missing} 篇论文的中文内容暂未生成，可稍后重新同步。")

        category_name = next(
            (
                str(item.get("name_cn") or ARXIV_CATEGORY_NAMES.get(category, category))
                for item in categories
                if isinstance(item, dict) and item.get("code") == category
            ),
            ARXIV_CATEGORY_NAMES.get(category, category),
        )
        record.status = "SUCCEEDED"
        record.category_name = category_name
        record.source_url = source_url
        record.paper_count = len(papers)
        record.categories = categories
        record.papers = papers
        record.warnings = warnings
        record.fetched_at = fetched_at
        record.completed_at = datetime.now(UTC)
        task.status = "SUCCEEDED"
        task.finished_at = datetime.now(UTC)
        task.trace_summary = {
            "agent": "arxiv_daily",
            "category": category,
            "paper_count": len(papers),
            "source": source_url,
            "used_stale_snapshot": any("最近一次" in item for item in warnings),
        }
        self.merge_output(
            task,
            daily_request={
                "category": category,
                "category_name": category_name,
                "search_query": record.search_query or "",
                "refresh": record.refresh_requested,
            },
            daily_categories=categories,
            daily_papers=papers,
            daily_summary={
                "source": source_url,
                "paper_count": len(papers),
                "fetched_at": fetched_at.isoformat(),
                "cached": recent is not None and not record.refresh_requested,
            },
            daily_warnings=warnings,
        )
        self.emit(task, "task.completed", 100, f"已完成 {category} 学术速递，共 {len(papers)} 篇")

    def _translate_papers(self, task: Task, papers: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
        """Fill in Chinese title, one-line summary and abstract for each paper.

        Papers translated in an earlier run are reused; the rest are sent to the
        platform chat model in parallel. Progress is reported per finished paper,
        so the bar reflects real work rather than fixed checkpoints.
        """
        total = len(papers)
        if total == 0:
            return papers, 0
        cached = self._cached_translations(set(str(item.get("arxiv_id") or "") for item in papers))
        results: list[dict[str, Any]] = [dict(item) for item in papers]
        pending: list[int] = []
        for index, paper in enumerate(results):
            known = cached.get(str(paper.get("arxiv_id") or ""))
            if known:
                paper.update(known)
            else:
                pending.append(index)
        done = total - len(pending)

        def report() -> None:
            progress = 15 + round(80 * done / total)
            self.emit(
                task,
                "daily.translating",
                progress,
                f"已整理 {done} / {total} 篇论文的中文摘要",
                done=done,
                total=total,
            )

        report()
        if not pending:
            return results, 0

        app = self.app

        def work(index: int) -> tuple[int, dict[str, str] | None]:
            with app.app_context():
                try:
                    return index, _translate_one(results[index])
                except Exception:  # noqa: BLE001
                    app.logger.warning("arXiv daily translation failed for %s", results[index].get("arxiv_id"), exc_info=True)
                    return index, None

        missing = 0
        workers = max(1, min(int(app.config.get("ARXIV_DAILY_TRANSLATE_WORKERS", 12)), len(pending)))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="arxiv-daily-cn") as pool:
            for future in as_completed([pool.submit(work, index) for index in pending]):
                index, translated = future.result()
                if translated:
                    results[index].update(translated)
                else:
                    missing += 1
                done += 1
                report()
        return results, missing

    def _cached_translations(self, arxiv_ids: set[str]) -> dict[str, dict[str, str]]:
        """Chinese fields already produced for these papers in earlier runs."""
        found: dict[str, dict[str, str]] = dict()
        rows = db.session.scalars(
            select(ArxivDailyRun)
            .where(ArxivDailyRun.status == "SUCCEEDED", ArxivDailyRun.paper_count > 0)
            .order_by(ArxivDailyRun.completed_at.desc())
            .limit(60)
        ).all()
        for row in rows:
            for paper in row.papers or []:
                if not isinstance(paper, dict) or not paper.get("cn_generated"):
                    continue
                arxiv_id = str(paper.get("arxiv_id") or "")
                if arxiv_id in arxiv_ids and arxiv_id not in found:
                    found[arxiv_id] = dict(
                        title_cn=str(paper.get("title_cn") or ""),
                        summary_cn=str(paper.get("summary_cn") or ""),
                        abstract_cn=str(paper.get("abstract_cn") or ""),
                        cn_generated=True,
                    )
        return found

    def _record_for(self, task: Task, user_id: UUID, options: dict[str, Any]) -> ArxivDailyRun:
        record = self._get_record(task.id, user_id)
        if record is not None:
            return record
        record = ArxivDailyRun(
            task_id=task.id,
            user_id=user_id,
            status="QUEUED",
            category=str(options.get("category") or "cs.AI"),
            category_name=str(options.get("category_name") or "人工智能"),
            search_query=str(options.get("search_query") or "") or None,
            refresh_requested=bool(options.get("refresh")),
            paper_count=0,
            categories=[],
            papers=[],
            warnings=[],
        )
        db.session.add(record)
        db.session.flush()
        return record

    @staticmethod
    def _get_record(task_id: UUID, user_id: UUID) -> ArxivDailyRun | None:
        return db.session.scalar(
            select(ArxivDailyRun).where(
                ArxivDailyRun.task_id == task_id,
                ArxivDailyRun.user_id == user_id,
            )
        )

    def _latest_snapshot(
        self, category: str, *, exclude_task_id: UUID, fresh_only: bool
    ) -> ArxivDailyRun | None:
        query = select(ArxivDailyRun).where(
            ArxivDailyRun.category == category,
            ArxivDailyRun.status == "SUCCEEDED",
            ArxivDailyRun.task_id != exclude_task_id,
            ArxivDailyRun.paper_count > 0,
        )
        if fresh_only:
            cutoff = datetime.now(UTC) - timedelta(
                seconds=max(0, int(self.app.config["ARXIV_DAILY_CACHE_TTL_SECONDS"]))
            )
            query = query.where(ArxivDailyRun.completed_at >= cutoff)
        return db.session.scalar(query.order_by(ArxivDailyRun.completed_at.desc()).limit(1))

    def _fetch_snapshot(self, category: str) -> dict[str, Any]:
        root = self._runtime_root()
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        result = subprocess.run(
            [sys.executable, str(root / "main.py"), "--category", category],
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=int(self.app.config["ARXIV_DAILY_TIMEOUT_SECONDS"]),
        )
        payload = _parse_cli_payload(result.stdout)
        if result.returncode != 0:
            message = (result.stderr or result.stdout or "arXivDaily 抓取失败").strip()[-1500:]
            raise RuntimeError(message)
        categories = payload.get("categories")
        papers = payload.get("papers")
        if not isinstance(categories, list) or not isinstance(papers, list):
            raise RuntimeError("学术速递 Agent 返回结果不完整")
        return payload

    def _runtime_root(self) -> Path:
        root = Path(self.app.config["ARXIV_DAILY_RUNTIME_ROOT"]).resolve()
        if not (root / "main.py").is_file() or not (
            root / "agent-core" / "src" / "tools" / "arxivdaily.py"
        ).is_file():
            raise RuntimeError(f"学术速递 Agent 运行时不完整: {root}")
        return root


def _parse_cli_payload(stdout: str) -> dict[str, Any]:
    text = stdout.strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return {}
        try:
            payload = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return {}
    return payload if isinstance(payload, dict) else {}


def _parse_datetime(value: object) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(UTC)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


TRANSLATE_PROMPT = (
    "你是科研论文编辑。根据给出的英文标题和摘要，输出一个 JSON 对象，包含三个字段："
    "title_cn（准确的中文标题）、summary_cn（一句话总结，60 字以内，说明做了什么、结论是什么）、"
    "abstract_cn（完整、忠实的中文摘要，专业术语保留英文缩写）。只输出 JSON，不要解释。"
)


def _translate_one(paper: dict[str, Any]) -> dict[str, str] | None:
    title = str(paper.get("title") or "").strip()
    abstract = str(paper.get("abstract") or "").strip()
    if not title or not abstract:
        return None
    response = run_openai_compatible_chat(
        messages=[
            dict(role="system", content=TRANSLATE_PROMPT),
            dict(role="user", content=f"标题：{title}\n\n摘要：{abstract[:6000]}"),
        ],
        model=API_MODEL_VALUE,
        temperature=0.1,
        max_tokens=1800,
        timeout_seconds=90,
    )
    data = _parse_cli_payload(str(response.get("content") or ""))
    title_cn = str(data.get("title_cn") or "").strip()
    abstract_cn = str(data.get("abstract_cn") or "").strip()
    if not title_cn or not abstract_cn:
        return None
    return dict(
        title_cn=title_cn,
        summary_cn=str(data.get("summary_cn") or "").strip(),
        abstract_cn=abstract_cn,
        cn_generated=True,
    )
