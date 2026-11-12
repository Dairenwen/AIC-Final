from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import text

from ...extensions import db
from ...models import Task
from ..task_service import BuiltinAgentTaskService


class InnovationPointGenerationService(BuiltinAgentTaskService):
    agent_label = "innovation-point-generation"
    failed_message = "创新点生成 Agent 工作流执行失败"

    def run(self, task_id: UUID, user_id: UUID) -> None:
        task = db.session.get(Task, task_id)
        if task is None:
            return

        input_json = task.input_json or {}
        prompt = str(input_json.get("prompt") or "").strip()
        options = normalized_options(input_json.get("innovation_options"))
        # Keep this path deliberately short: the external runtime appends a timestamp
        # and an 80-character slug, which otherwise crosses MAX_PATH on Windows.
        output_dir: Path = self.app.config["INNOVATION_DATA_DIR"] / str(task.id)
        corpus_dir = output_dir / "corpus"
        runs_dir = output_dir / "runs"
        corpus_dir.mkdir(parents=True, exist_ok=True)
        runs_dir.mkdir(parents=True, exist_ok=True)

        task.status = "RUNNING"
        task.started_at = datetime.now(UTC)
        self.emit(task, "task.started", 6, "已启动创新点生成 Agent")

        keywords = options["keywords"] or split_keywords(prompt)
        self.merge_output(
            task,
            request_plan={
                "domain": prompt,
                "keywords": keywords,
                "top_k": options["top_k"],
                "mode": options["mode"],
                "time_range": options["time_range"],
                "seed_ideas": options["seed_ideas"],
            },
        )
        self.emit(task, "innovation.domain_ready", 14, "研究领域与关键词已解析", detail={"keywords": keywords})

        corpus_count = export_platform_corpus(corpus_dir)
        self.emit(task, "innovation.corpus_ready", 28, f"已从本地文献库构建 {corpus_count} 篇语料", count=corpus_count)
        self.emit(task, "innovation.workflow_started", 42, "正在执行趋势分析、空白识别、创新点生成与评估")

        result = self.run_core(prompt, keywords, corpus_dir, runs_dir, options)
        payload, result_path = read_result_payload(result.stdout, runs_dir)
        innovations = payload.get("innovations") if isinstance(payload, dict) else []
        trends = payload.get("research_trends") if isinstance(payload, dict) else []
        gaps = payload.get("research_gaps") if isinstance(payload, dict) else []

        self.merge_output(
            task,
            research_domain=payload.get("research_domain", prompt),
            research_trends=trends or [],
            research_gaps=gaps or [],
            innovations=innovations or [],
            candidate_innovations=payload.get("candidate_innovations", []),
            evaluated_innovations=payload.get("evaluated_innovations", []),
            evidence_map=payload.get("evidence_map", {}),
            literature_corpus=payload.get("literature_corpus", []),
            knowledge_graph_summary=payload.get("knowledge_graph_summary", ""),
            citation_network_summary=payload.get("citation_network_summary", ""),
            workflow_trace=payload.get("workflow_trace", []),
            metadata=payload.get("metadata", {}),
            artifacts={"result_json": str(result_path)},
            logs={
                "stdout": result.stdout[-3000:],
                "stderr": result.stderr[-3000:],
                "returncode": result.returncode,
            },
        )
        self.emit(task, "innovation.trends_ready", 62, f"已识别 {len(trends or [])} 条研究趋势", count=len(trends or []))
        self.emit(task, "innovation.gaps_ready", 74, f"已识别 {len(gaps or [])} 个研究空白", count=len(gaps or []))
        self.emit(task, "innovation.proposals_ready", 92, f"已生成 {len(innovations or [])} 个创新点方案", count=len(innovations or []))

        task.status = "SUCCEEDED"
        task.progress = 100
        task.current_step = "创新点生成完成"
        task.finished_at = datetime.now(UTC)
        task.trace_summary = {
            "agent": "innovation_point_generation",
            "trend_count": len(trends or []),
            "gap_count": len(gaps or []),
            "proposal_count": len(innovations or []),
            "corpus_count": corpus_count,
            "runtime": "paper-insight-generate",
            "mode": options["mode"],
        }
        db.session.commit()
        self.emit(task, "task.completed", 100, "创新点生成任务已完成")

    def run_core(
        self,
        prompt: str,
        keywords: list[str],
        corpus_dir: Path,
        runs_dir: Path,
        options: dict[str, Any] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        options = normalized_options(options)
        root = Path(self.app.config["INNOVATION_AGENT_ROOT"])
        core_entry = root / "innovation_agent.py"
        if not root.is_dir() or not core_entry.is_file() or not (root / "chuangx").is_dir():
            raise RuntimeError("创新点生成 Agent 运行目录未正确配置")
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        cmd = [
            sys.executable,
            str(core_entry),
            "--domain",
            prompt,
            "--mode",
            str(options["mode"]),
            "--top-k",
            str(options["top_k"]),
            "--corpus",
            str(corpus_dir),
            "--out",
            str(runs_dir),
            "--additional-context",
            str(options["additional_context"] or prompt),
            "--max-documents",
            str(self.app.config["INNOVATION_AGENT_MAX_DOCUMENTS"]),
        ]
        for keyword in keywords[:8]:
            cmd.extend(["--keyword", keyword])
        for seed_idea in options["seed_ideas"]:
            cmd.extend(["--seed-idea", seed_idea])
        if options["time_range"]:
            cmd.extend(["--time-range", str(options["time_range"])])
        if options["constraints"]:
            cmd.extend(["--constraints-json", json.dumps(options["constraints"], ensure_ascii=False)])
        result = subprocess.run(
            cmd,
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=int(self.app.config["INNOVATION_AGENT_TIMEOUT_SECONDS"]),
        )
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or "创新点生成核心工作流执行失败").strip()[:1000])
        return result


def export_platform_corpus(corpus_dir: Path) -> int:
    try:
        rows = db.session.execute(
            text(
                """
                SELECT title, abstract, authors, venue, publish_year, keywords, research_areas, pdf_url, source_json
                FROM zhiyan.papers
                WHERE status = 'ACTIVE'
                ORDER BY publish_year DESC NULLS LAST, citation_count DESC NULLS LAST
                LIMIT 500
                """
            )
        ).mappings().all()
    except Exception:
        # The innovation workflow is deterministic and can still produce
        # explicitly evidence-limited proposals without the optional platform
        # corpus.  A missing/old papers table must not turn a completed task
        # into an opaque empty result on a fresh deployment.
        db.session.rollback()
        rows = []
    records = []
    for row in rows:
        source_json = row["source_json"] if isinstance(row["source_json"], dict) else {}
        records.append(
            {
                "title": row["title"],
                "abstract": row["abstract"] or "",
                "authors": row["authors"] or [],
                "conference": row["venue"] or "",
                "publish_year": row["publish_year"],
                "keywords": row["keywords"] or row["research_areas"] or [],
                "pdf_url": row["pdf_url"] or "",
                "url": source_json.get("url") or source_json.get("source_url") or "",
                "source_url": source_json.get("url") or source_json.get("source_url") or "",
            }
        )
    (corpus_dir / "platform_papers.json").write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(records)


def read_result_payload(stdout: str, runs_dir: Path) -> tuple[dict[str, Any], Path]:
    result_path: Path | None = None
    for line in stdout.splitlines():
        if line.startswith("RESULT_JSON="):
            candidate = Path(line.split("=", 1)[1].strip())
            if candidate.is_file():
                result_path = candidate
                break
    if result_path is None:
        files = sorted(runs_dir.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)
        if files:
            result_path = files[0]
    if result_path is None:
        raise RuntimeError("创新点生成 Agent 未输出结果 JSON")
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("创新点生成 Agent 输出格式异常")
    required_lists = ("research_trends", "research_gaps", "innovations")
    if any(not isinstance(payload.get(key), list) for key in required_lists):
        raise RuntimeError("创新点生成 Agent 输出缺少趋势、空白或创新方案")
    return payload, result_path


def split_keywords(prompt: str) -> list[str]:
    return [
        item
        for item in re.split(r"[\s,，;；、]+", prompt)
        if 2 <= len(item) <= 32 and item not in {"请", "帮我", "生成", "创新点"}
    ][:10]


def normalized_options(value: object) -> dict[str, Any]:
    options = value if isinstance(value, dict) else {}
    mode = str(options.get("mode") or "full")
    if mode not in {"full", "expand", "evaluate"}:
        mode = "full"
    try:
        top_k = max(1, min(int(options.get("top_k") or 5), 10))
    except (TypeError, ValueError):
        top_k = 5
    return {
        "mode": mode,
        "top_k": top_k,
        "time_range": str(options.get("time_range") or "").strip() or None,
        "keywords": [str(item) for item in options.get("keywords", []) if str(item).strip()][:12],
        "seed_ideas": [str(item) for item in options.get("seed_ideas", []) if str(item).strip()][:10],
        "constraints": options.get("constraints") if isinstance(options.get("constraints"), dict) else {},
        "additional_context": str(options.get("additional_context") or "").strip(),
    }
