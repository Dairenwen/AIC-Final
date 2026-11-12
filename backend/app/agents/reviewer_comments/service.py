from __future__ import annotations

import re
from zipfile import BadZipFile, ZipFile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from ...api.uploads import resolve_manuscript_upload
from ...extensions import db
from ...llm import run_openai_compatible_chat
from ...models import ModelConfig, Task
from ..task_service import BuiltinAgentTaskService
from ...services.system_model_runtime import get_owned_model_runtime, get_system_model_runtime


class ReviewerCommentsService(BuiltinAgentTaskService):
    agent_label = "reviewer-comments"
    failed_message = "审稿意见解析与回复 Agent 工作流执行失败"

    def run(self, task_id: UUID, user_id: UUID) -> None:
        task = db.session.get(Task, task_id)
        if task is None:
            return

        input_json = task.input_json or {}
        prompt = str(input_json.get("prompt") or "").strip()
        options = normalize_reviewer_options(input_json.get("reviewer_options"))
        model_runtime = (
            self._resolve_model_runtime(task, user_id)
            if options["mode"] in {"full", "reply"}
            else None
        )
        model = str(model_runtime["model_name"]) if model_runtime else "not_used"
        attachment_path = resolve_manuscript_upload(user_id, input_json.get("attachment_id"))
        attachment_text = read_review_attachment(attachment_path) if attachment_path else ""
        review_text = combine_review_text(prompt, attachment_text)
        if not review_text:
            raise ValueError("请粘贴审稿意见，或上传包含审稿意见的 TXT、MD、DOCX、PDF 文件")

        task.status = "RUNNING"
        task.started_at = datetime.now(UTC)
        self.emit(task, "task.started", 6, "已启动审稿意见解析与回复 Agent")

        comments = split_review_comments(review_text)
        self.merge_output(
            task,
            reviewer_request={
                "comment_count": len(comments),
                "source": "prompt+attachment" if attachment_text and prompt else ("attachment" if attachment_text else "prompt"),
                "file_name": str(input_json.get("attachment") or attachment_path.name) if attachment_path else None,
                "mode": options["mode"],
                "model": model,
            },
        )
        self.emit(task, "reviewer.comments_split", 18, f"已拆解 {len(comments)} 条审稿意见", count=len(comments))

        analysis = [analyze_comment(index + 1, comment) for index, comment in enumerate(comments)]
        self.merge_output(task, review_items=analysis)
        self.emit(task, "reviewer.analysis_ready", 42, "已完成问题分类、严重程度与证据需求识别")

        strategy = build_reply_strategy(analysis)
        self.merge_output(task, reply_strategy=strategy)
        self.emit(task, "reviewer.strategy_ready", 62, "已生成逐条回复策略与修改优先级")

        response_letter = ""
        checklist: list[dict[str, str]] = []
        if options["mode"] in {"full", "reply"}:
            response_letter = self.generate_response_letter(review_text, analysis, strategy, model_runtime or {})
            checklist = build_revision_checklist(analysis)
            self.emit(task, "reviewer.reply_ready", 88, "已生成回复草稿与修改清单")
        else:
            self.emit(task, "reviewer.reply_ready", 88, "已按仅解析模式跳过回复信生成")

        self.merge_output(
            task,
            response_letter_markdown=response_letter,
            revision_checklist=checklist,
            metrics={
                "comment_count": len(analysis),
                "major_count": sum(1 for item in analysis if item["severity"] == "major"),
                "minor_count": sum(1 for item in analysis if item["severity"] == "minor"),
                "blocking_count": sum(1 for item in analysis if item["severity"] == "blocking"),
            },
        )

        task.status = "SUCCEEDED"
        task.progress = 100
        task.current_step = "审稿意见回复生成完成"
        task.finished_at = datetime.now(UTC)
        task.trace_summary = {
            "agent": "reviewer_comments",
            "comment_count": len(analysis),
            "blocking_count": sum(1 for item in analysis if item["severity"] == "blocking"),
        }
        db.session.commit()
        self.emit(task, "task.completed", 100, "审稿意见解析与回复任务已完成")

    def generate_response_letter(
        self,
        prompt: str,
        analysis: list[dict[str, Any]],
        strategy: dict[str, Any],
        model_runtime: dict[str, Any],
    ) -> str:
        messages = [
            {
                "role": "system",
                "content": (
                    "你是科研论文返修回复助手。请根据审稿意见生成中文回复信草稿，"
                    "风格礼貌、具体、可执行，按 Reviewer Comment / Response / Manuscript Changes 组织。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"原始审稿意见：\n{prompt}\n\n"
                    f"结构化分析：{analysis}\n\n"
                    f"回复策略：{strategy}\n\n"
                    "请输出 Markdown。"
                ),
            },
        ]
        try:
            result = run_openai_compatible_chat(
                messages=messages,
                model=str(model_runtime["model_name"]),
                base_url=str(model_runtime["base_url"]),
                api_key=str(model_runtime["api_key"]),
                timeout_seconds=float(model_runtime["timeout_seconds"]),
            )
            content = str(result.get("content") or "").strip()
            if content:
                return content
        except Exception:  # noqa: BLE001
            pass
        return fallback_response_letter(analysis)

    @staticmethod
    def _resolve_model_runtime(task: Task, user_id: UUID) -> dict[str, Any]:
        selected_config = db.session.get(ModelConfig, task.model_config_id) if task.model_config_id else None
        if selected_config is not None:
            return get_owned_model_runtime(selected_config, user_id)
        return get_system_model_runtime(user_id)


def split_review_comments(prompt: str) -> list[str]:
    text = prompt.strip()
    if not text:
        return []
    text = re.sub(r"\n\s*(major|minor)\s+comments?\s*[:：]?", "\n", text, flags=re.I)
    text = re.sub(r"\n\s*(weaknesses|questions|suggestions)\s*[:：]?", "\n", text, flags=re.I)
    parts = re.split(
        r"(?:\n\s*(?:\d+[\).、]|[-*•])\s+)|(?:\n\s*(?:Reviewer|Referee|审稿人)\s*\d*\s*[:：])|(?:\n\s*(?:Comment|Issue|问题)\s*\d+\s*[:：])",
        text,
        flags=re.I,
    )
    comments = [clean_space(part) for part in parts if len(clean_space(part)) >= 12]
    return comments[:20] or [text[:2000]]


def analyze_comment(index: int, comment: str) -> dict[str, Any]:
    lower = comment.lower()
    category = "clarification"
    if any(word in lower for word in ["experiment", "ablation", "baseline", "dataset", "实验", "消融", "基线", "数据集"]):
        category = "experiment"
    elif any(word in lower for word in ["method", "algorithm", "model", "方法", "算法", "模型"]):
        category = "method"
    elif any(word in lower for word in ["related", "citation", "reference", "literature", "相关工作", "引用", "文献"]):
        category = "literature"
    elif any(word in lower for word in ["writing", "format", "typo", "clarity", "表达", "格式", "拼写"]):
        category = "writing"

    severity = "minor"
    if any(word in lower for word in ["fatal", "reject", "invalid", "major concern", "serious", "严重", "无法", "缺失", "不足"]):
        severity = "major"
    if any(word in lower for word in ["cannot be accepted", "not acceptable", "fundamental flaw", "fatal flaw", "核心问题", "致命"]):
        severity = "blocking"

    return {
        "id": f"RC-{index:02d}",
        "comment": comment,
        "category": category,
        "severity": severity,
        "intent": infer_intent(category),
        "evidence_needed": evidence_needed(category),
        "reply_angle": reply_angle(category, severity),
    }


def build_reply_strategy(items: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(items, key=lambda item: {"blocking": 0, "major": 1, "minor": 2}.get(item["severity"], 3))
    return {
        "overall_tone": "感谢审稿人指出问题，先承认合理性，再说明已完成或计划完成的修改。",
        "priority_order": [item["id"] for item in ordered],
        "principles": [
            "逐条对应，不遗漏问题",
            "回复中给出修改位置或新增实验",
            "对无法完全采纳的建议给出边界和理由",
            "最后汇总主要改动",
        ],
    }


def build_revision_checklist(items: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [
        {
            "id": item["id"],
            "action": action_for(item["category"]),
            "evidence": "需要在论文正文、附录或回复信中明确标注修改位置",
            "priority": item["severity"],
        }
        for item in items
    ]


def fallback_response_letter(items: list[dict[str, Any]]) -> str:
    lines = ["# Response to Reviewers", "", "Dear reviewers,", "", "Thank you for the constructive comments. We have carefully revised the manuscript and respond point by point below.", ""]
    for item in items:
        lines.extend(
            [
                f"## {item['id']}",
                "",
                f"**Reviewer Comment.** {item['comment']}",
                "",
                f"**Response.** Thank you for this suggestion. We agree that this issue is important. We have addressed it from the perspective of {item['intent']}.",
                "",
                f"**Manuscript Changes.** {action_for(item['category'])}",
                "",
            ]
        )
    return "\n".join(lines).strip()


def infer_intent(category: str) -> str:
    return {
        "experiment": "补充实验可信度与对比充分性",
        "method": "澄清方法设计、假设和算法细节",
        "literature": "补全相关研究定位与引用脉络",
        "writing": "提升表达清晰度和格式规范性",
    }.get(category, "澄清审稿人关切并补充解释")


def evidence_needed(category: str) -> list[str]:
    return {
        "experiment": ["新增实验结果", "消融或基线对比", "统计显著性或误差分析"],
        "method": ["算法流程", "参数设置", "复杂度或适用边界"],
        "literature": ["新增引用", "与已有工作的差异表述"],
        "writing": ["修改段落位置", "术语统一说明"],
    }.get(category, ["正文修改位置", "回复信解释"])


def reply_angle(category: str, severity: str) -> str:
    prefix = "优先处理核心阻塞问题，" if severity == "blocking" else ""
    return prefix + {
        "experiment": "用新增结果和对照实验回应。",
        "method": "用结构化说明和公式/流程补齐细节。",
        "literature": "补充引用并明确本文贡献边界。",
        "writing": "说明已润色并列出具体修改位置。",
    }.get(category, "先感谢，再解释修改动作。")


def action_for(category: str) -> str:
    return {
        "experiment": "补充实验设置、对比结果和必要的消融分析。",
        "method": "在方法章节补充算法流程、变量定义和适用条件。",
        "literature": "在相关工作中新增引用并重写差异化贡献说明。",
        "writing": "通读全文，统一术语、格式和图表引用。",
    }.get(category, "在正文中补充解释，并在回复信中给出对应修改位置。")


def clean_space(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def normalize_reviewer_options(value: object) -> dict[str, str]:
    options = value if isinstance(value, dict) else {}
    mode = str(options.get("mode") or "full").strip()
    if mode not in {"full", "analysis", "reply"}:
        mode = "full"
    return {"mode": mode, "target_language": str(options.get("target_language") or "zh")}


def combine_review_text(prompt: str, attachment_text: str) -> str:
    parts = [part.strip() for part in [prompt, attachment_text] if part and part.strip()]
    return "\n\n".join(parts)


def read_review_attachment(path: Path | None) -> str:
    if path is None:
        return ""
    suffix = path.suffix.lower()
    if suffix in {".txt", ".md"}:
        return path.read_text(encoding="utf-8-sig", errors="replace")[:30000]
    if suffix == ".docx":
        return read_docx_text(path)[:30000]
    if suffix == ".pdf":
        return read_pdf_text(path)[:30000]
    return ""


def read_docx_text(path: Path) -> str:
    try:
        with ZipFile(path) as archive:
            xml = archive.read("word/document.xml").decode("utf-8", errors="replace")
    except (KeyError, BadZipFile):
        return ""
    text = re.sub(r"<w:tab\s*/>", "\t", xml)
    text = re.sub(r"</w:p>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    return clean_space(text)


def read_pdf_text(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        return ""
    try:
        reader = PdfReader(str(path))
        pages = [(page.extract_text() or "") for page in reader.pages[:20]]
    except Exception:  # noqa: BLE001
        return ""
    return "\n".join(pages)
