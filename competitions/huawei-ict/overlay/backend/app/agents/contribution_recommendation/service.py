from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from ...extensions import db
from ...models import Task
from ..task_service import BuiltinAgentTaskService


class ContributionRecommendationService(BuiltinAgentTaskService):
    agent_label = "contribution-recommendation"
    failed_message = "投稿推荐 Agent 工作流执行失败"

    def run(self, task_id: UUID, user_id: UUID) -> None:
        task = db.session.get(Task, task_id)
        if task is None:
            return

        input_json = task.input_json or {}
        prompt = str(input_json.get("prompt") or "").strip()
        output_dir: Path = self.app.config["AGENT_GENERATED_DIR"] / "contribution_recommendation" / str(task.id)
        output_dir.mkdir(parents=True, exist_ok=True)

        task.status = "RUNNING"
        task.started_at = datetime.now(UTC)
        self.emit(task, "task.started", 6, "已启动投稿推荐 Agent")

        parsed_paper = parse_paper_prompt(prompt)
        quality = normalize_quality(input_json.get("contribution_options"))
        preferences = normalize_preferences(input_json.get("contribution_options"))
        self.merge_output(task, submission_request={"paper": parsed_paper, "quality": quality, "preferences": preferences})
        self.emit(task, "submission.features_ready", 18, "已提取论文主题、关键词与质量偏好")

        result = self.run_core(task.id, parsed_paper, quality, preferences)
        self.emit(task, "submission.candidates_ready", 48, "已完成候选会议/期刊召回与匹配评估")

        normalized = normalize_recommendation_result(result, parsed_paper, preferences)
        report_path = output_dir / "submission_report.md"
        result_path = output_dir / "submission_result.json"
        report_path.write_text(normalized["final_report"], encoding="utf-8")
        result_path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

        self.merge_output(
            task,
            recommendations=normalized["recommendations"],
            submission_checklist=normalized["submission_checklist"],
            submission_strategy=normalized["submission_strategy"],
            comparison_matrix=normalized["comparison_matrix"],
            thinking_trace=normalized["thinking_trace"],
            final_report=normalized["final_report"],
            artifacts={"report_markdown": str(report_path), "result_json": str(result_path)},
            errors=normalized.get("errors", []),
        )
        self.emit(task, "submission.ranking_ready", 76, f"已生成 {len(normalized['recommendations'])} 个投稿目标推荐", count=len(normalized["recommendations"]))
        self.emit(task, "submission.report_ready", 92, "已生成投稿策略、准备清单与推荐报告")

        task.status = "SUCCEEDED"
        task.progress = 100
        task.current_step = "投稿推荐完成"
        task.finished_at = datetime.now(UTC)
        task.trace_summary = {
            "agent": "contribution_recommendation",
            "recommendation_count": len(normalized["recommendations"]),
            "top_venue": top_venue(normalized["recommendations"]),
        }
        db.session.commit()
        self.emit(task, "task.completed", 100, "投稿推荐任务已完成")

    def run_core(
        self,
        task_id: UUID,
        parsed_paper: dict[str, Any],
        quality: dict[str, Any],
        preferences: dict[str, Any],
    ) -> dict[str, Any]:
        if not self.app.config.get("PLATFORM_MODEL_ENABLED", False):
            return fallback_recommendations(parsed_paper, quality, preferences, "平台专用模型未启用")
        root = Path(__file__).resolve().parent
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        env_base = str(self.app.config["QWEN_DPO_BASE_URL"]).rstrip("/")
        if env_base.endswith("/v1"):
            env_base = env_base[:-3]
        os.environ.setdefault("ANTHROPIC_BASE_URL", env_base)
        os.environ.setdefault("ANTHROPIC_AUTH_TOKEN", str(self.app.config["QWEN_DPO_API_KEY"]))
        os.environ.setdefault("ANTHROPIC_MODEL", str(self.app.config["QWEN_DPO_MODEL"]))
        try:
            from agent import recommend_submission  # type: ignore

            return asyncio.run(
                recommend_submission(
                    paper_id=f"ZHICY-{str(task_id)[:8]}",
                    parsed_paper=parsed_paper,
                    quality_estimate=quality,
                    user_preferences=preferences,
                )
            )
        except Exception as exc:  # noqa: BLE001
            return fallback_recommendations(parsed_paper, quality, preferences, str(exc))


def parse_paper_prompt(prompt: str) -> dict[str, Any]:
    lines = [line.strip() for line in prompt.splitlines() if line.strip()]
    title = lines[0][:180] if lines else prompt[:120]
    keywords = split_keywords(prompt)
    references = extract_references(prompt)
    return {
        "title": title or "Untitled Manuscript",
        "abstract": prompt[:3000],
        "keywords": keywords,
        "references": references,
    }


def normalize_quality(value: object) -> dict[str, Any]:
    options = value if isinstance(value, dict) else {}
    novelty = str(options.get("novelty_level") or "substantial")
    if novelty not in {"incremental", "substantial", "breakthrough"}:
        novelty = "substantial"
    return {
        "experiment_completeness": clamp_float(options.get("experiment_completeness"), 0.72),
        "novelty_level": novelty,
        "theoretical_rigor": clamp_float(options.get("theoretical_rigor"), 0.75),
        "writing_quality": clamp_float(options.get("writing_quality"), 0.75),
    }


def normalize_preferences(value: object) -> dict[str, Any]:
    options = value if isinstance(value, dict) else {}
    levels = options.get("target_ccf_levels")
    if not isinstance(levels, list) or not levels:
        levels = ["CCF-A", "CCF-B"]
    return {
        "target_ccf_levels": [str(item) for item in levels[:3]],
        "target_caai_levels": [],
        "max_review_weeks": int(options.get("max_review_weeks") or 12),
        "prefer_oa": bool(options.get("prefer_oa", True)),
        "sprint_tier_count": 3,
        "match_tier_count": 5,
        "safety_tier_count": 3,
    }


def normalize_recommendation_result(
    result: dict[str, Any],
    parsed_paper: dict[str, Any],
    preferences: dict[str, Any],
) -> dict[str, Any]:
    recommendations = result.get("recommendations") if isinstance(result, dict) else []
    if not recommendations:
        result = fallback_recommendations(parsed_paper, {}, preferences, "empty result")
        recommendations = result["recommendations"]
    return {
        "thinking_trace": result.get("thinking_trace", []),
        "recommendations": recommendations,
        "submission_checklist": result.get("submission_checklist", {}),
        "submission_strategy": result.get("submission_strategy", {}),
        "comparison_matrix": result.get("comparison_matrix", {}),
        "final_report": result.get("final_report") or build_report(parsed_paper, recommendations),
        "errors": result.get("errors", []),
    }


def fallback_recommendations(
    parsed_paper: dict[str, Any],
    quality: dict[str, Any],
    preferences: dict[str, Any],
    reason: str,
) -> dict[str, Any]:
    venues = [
        ("AAAI", "AAAI Conference on Artificial Intelligence", "CCF-A", "sprint", 0.83),
        ("IJCAI", "International Joint Conference on Artificial Intelligence", "CCF-A", "sprint", 0.80),
        ("ACL", "Annual Meeting of the ACL", "CCF-A", "match", 0.76),
        ("EMNLP", "Conference on Empirical Methods in Natural Language Processing", "CCF-B", "match", 0.72),
        ("COLING", "International Conference on Computational Linguistics", "CCF-B", "safety", 0.66),
    ]
    recommendations = [
        {
            "venue": {
                "abbreviation": abbrev,
                "full_name": name,
                "ccf_level": ccf,
                "type": "conference",
                "avg_review_weeks": 12,
                "is_oa": preferences.get("prefer_oa", True),
            },
            "tier": tier,
            "match_score": {"overall": score, "topic_similarity": score - 0.05, "methodology_alignment": score - 0.08},
            "estimated_acceptance_prob": "中等",
            "confidence": 0.62,
            "rank_score": score,
            "strengths": ["主题与 AI/科研智能体方向匹配", "适合突出方法贡献和实验完整性"],
            "risks": ["需要补强实验对比和消融", "需确认最新截稿时间"],
            "differentiation": "建议在投稿前强化问题定义、方法边界和实验可信度。",
        }


        for abbrev, name, ccf, tier, score in venues
    ]
    return {
        "thinking_trace": [
            {"step": "fallback", "label": "本地推荐", "summary": f"核心 Agent 未完整运行，已使用内置 venue 知识兜底：{reason}", "details": {}}
        ],
        "recommendations": recommendations,
        "submission_checklist": {
            "format_checks": ["核对目标会议模板", "检查匿名要求", "统一参考文献格式"],
            "experiment_supplements": ["补充强基线", "补充消融实验", "报告统计显著性"],
            "cover_letter_points": ["突出核心贡献", "说明与目标 venue 的主题契合度"],
        },
        "submission_strategy": {
            "primary_target": recommendations[0],
            "timeline": [{"phase": f"投稿: {item['venue']['abbreviation']}", "deadline": "待确认", "tier": item["tier"]} for item in recommendations[:3]],
            "fallback_plan": "若冲刺档风险过高，优先选择匹配档并保留保底档并行准备。",
        },
        "comparison_matrix": {"total_candidates": len(recommendations)},
        "final_report": build_report(parsed_paper, recommendations),
        "errors": [reason],
    }


def build_deterministic_recommendation(prompt: str, options: object = None) -> dict[str, Any]:
    """Public offline contract used by smoke checks and degraded deployments."""
    parsed = parse_paper_prompt(prompt)
    preferences = normalize_preferences(options)
    quality = normalize_quality(options)
    return normalize_recommendation_result(
        fallback_recommendations(parsed, quality, preferences, "offline deterministic mode"),
        parsed,
        preferences,
    )


def build_report(parsed_paper: dict[str, Any], recommendations: list[dict[str, Any]]) -> str:
    lines = ["# 投稿推荐报告", "", f"论文题目：{parsed_paper.get('title', '')}", "", "## 推荐排序"]
    for index, item in enumerate(recommendations, 1):
        venue = item.get("venue", {})
        lines.append(
            f"{index}. **{venue.get('abbreviation', '')}** ({venue.get('ccf_level', '')}, {item.get('tier', '')}) "
            f"- 匹配度 {float((item.get('match_score') or {}).get('overall', 0)):.0%}"
        )
    lines.extend(["", "## 投稿建议", "先准备冲刺档，同时保留匹配档和保底档；投稿前重点补强实验完整性、格式要求和差异化贡献表述。"])
    return "\n".join(lines)


def split_keywords(prompt: str) -> list[str]:
    words = [
        item
        for item in re.split(r"[\s,;，；、。()\[\]{}]+", prompt)
        if 2 <= len(item) <= 32 and not item.isdigit()
    ]
    return list(dict.fromkeys(words))[:10]


def extract_references(prompt: str) -> list[dict[str, Any]]:
    refs = []
    for match in re.finditer(r"(NeurIPS|ICML|ICLR|ACL|EMNLP|AAAI|IJCAI|CVPR|KDD|WWW)\D*(20\d{2})?", prompt, flags=re.I):
        refs.append({"title": match.group(0), "venue": match.group(1).upper(), "year": int(match.group(2) or 2024)})
    return refs[:20]


def clamp_float(value: object, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, number))


def top_venue(recommendations: list[dict[str, Any]]) -> str | None:
    if not recommendations:
        return None
    return str((recommendations[0].get("venue") or {}).get("abbreviation") or "")
