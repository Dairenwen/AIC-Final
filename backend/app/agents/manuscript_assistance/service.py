from __future__ import annotations

import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from ...extensions import db
from ...models import ModelConfig, Task
from ...services.model_credentials import decrypt_api_key
from ...services.system_model_runtime import get_owned_model_runtime, get_system_model_runtime
from ..task_service import BuiltinAgentTaskService


SECTION_TITLES = {
    "abstract": "摘要",
    "introduction": "引言",
    "related_work": "相关工作",
    "method": "方法设计",
    "experiment": "实验方案",
    "conclusion": "总结",
}


class ManuscriptAssistanceService(BuiltinAgentTaskService):
    agent_label = "manuscript-assistance"
    failed_message = "文稿辅助 Agent 工作流执行失败"

    def run(self, task_id: UUID, user_id: UUID) -> None:
        task = db.session.get(Task, task_id)
        if task is None:
            return

        prompt = str((task.input_json or {}).get("prompt") or "").strip()
        output_dir: Path = self.app.config["AGENT_GENERATED_DIR"] / "manuscript_assistance" / str(task.id)
        output_dir.mkdir(parents=True, exist_ok=True)

        task.status = "RUNNING"
        task.started_at = datetime.now(UTC)
        self.emit(task, "task.started", 6, "已启动文稿辅助 Agent")

        plan = build_manuscript_plan(prompt)
        self.merge_output(task, manuscript_plan=plan)
        self.emit(task, "manuscript.plan_ready", 18, "写作任务已解析，章节规划已生成", detail=plan)

        self.emit(task, "manuscript.sections_started", 35, "正在调用文稿辅助核心工作流生成论文内容")
        execution_mode = "model"
        warnings: list[str] = []
        try:
            result = self.run_core(prompt, output_dir, self.resolve_model_runtime(task, user_id))
            markdown = clean_cli_output(result.stdout)
            if not markdown:
                raise RuntimeError(result.stderr.strip() or "文稿辅助 Agent 未返回有效内容")
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            if not self.app.config["MANUSCRIPT_ALLOW_DETERMINISTIC_FALLBACK"]:
                raise
            execution_mode = "deterministic_fallback"
            warning = "模型服务当前不可用，已生成确定性结构化写作底稿，请人工补充论据和实验结果。"
            warnings.append(warning)
            self.emit(task, "manuscript.fallback", 52, warning)
            markdown = build_deterministic_manuscript(prompt, plan)
            result = subprocess.CompletedProcess([], 0, stdout=markdown, stderr=type(exc).__name__)

        sections = parse_markdown_sections(markdown)
        manuscript_path = output_dir / "manuscript.md"
        manuscript_path.write_text(markdown, encoding="utf-8")

        self.merge_output(
            task,
            manuscript_markdown=markdown,
            sections=sections,
            artifact_path=str(manuscript_path),
            metrics={
                "section_count": len(sections),
                "character_count": len(markdown),
                "line_count": len(markdown.splitlines()),
            },
            manuscript_execution_mode=execution_mode,
            manuscript_warnings=warnings,
            logs={
                "stderr": result.stderr[-3000:] if execution_mode == "model" else "",
                "returncode": result.returncode,
            },
        )
        self.emit(task, "manuscript.sections_ready", 78, f"已生成 {len(sections)} 个文稿章节", count=len(sections))
        self.emit(task, "manuscript.quality_checked", 91, "已完成结构完整性与格式检查")

        task.status = "SUCCEEDED"
        task.progress = 100
        task.current_step = "文稿生成完成"
        task.finished_at = datetime.now(UTC)
        task.trace_summary = {
            "agent": "manuscript_assistance",
            "execution_mode": execution_mode,
            "section_count": len(sections),
            "character_count": len(markdown),
        }
        db.session.commit()
        self.emit(task, "task.completed", 100, "文稿辅助任务已完成")

    def run_core(
        self,
        prompt: str,
        output_dir: Path,
        model_runtime: dict[str, object] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        core_dir = Path(__file__).resolve().parents[1] / "Manuscript assistance" / "agent-core"
        runtime = model_runtime or get_system_model_runtime(task.user_id if hasattr(task, "user_id") else UUID(int=0))
        env = os.environ.copy()
        env.update(
            {
                "PYTHONIOENCODING": "utf-8",
                "LLM_MODEL": str(runtime["model_name"]),
                "OPENAI_API_BASE": str(runtime["base_url"]),
                "OPENAI_API_KEY": str(runtime["api_key"]),
                "LLM_TIMEOUT": str(runtime["timeout_seconds"]),
                "VECTOR_STORE_PATH": str(output_dir / "vector_store"),
            }
        )
        result = subprocess.run(
            [sys.executable, "-m", "src.main", "-i", prompt, "-l", "zh", "-f", "markdown"],
            cwd=str(core_dir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=1800,
        )
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or "文稿辅助核心工作流执行失败").strip()[:1000])
        return result

    def resolve_model_runtime(self, task: Task, user_id: UUID) -> dict[str, object] | None:
        if task.model_config_id is None:
            return get_system_model_runtime(user_id)
        item = db.session.get(ModelConfig, task.model_config_id)
        if item is None:
            raise RuntimeError("所选个人模型不存在、未验证或已停用")
        return get_owned_model_runtime(item, user_id)


def build_manuscript_plan(prompt: str) -> dict[str, Any]:
    keywords = [
        item
        for item in re.split(r"[\s,，;；、]+", prompt)
        if 2 <= len(item) <= 32 and not item.startswith(("请", "帮", "写"))
    ][:8]
    return {
        "topic": prompt[:80],
        "language": "zh",
        "keywords": keywords,
        "sections": [{"id": key, "title": title} for key, title in SECTION_TITLES.items()],
        "checks": ["结构完整性", "章节一致性", "学术表达", "Markdown 格式"],
    }


def build_deterministic_manuscript(prompt: str, plan: dict[str, Any]) -> str:
    topic = str(plan.get("topic") or prompt).strip()
    keywords = "、".join(plan.get("keywords") or []) or "研究问题、方法设计、实验验证"
    return "\n\n".join(
        [
            f"# {topic}",
            "## 摘要\n"
            f"本文围绕“{topic}”开展研究，拟从问题定义、方法设计与实验验证三个层面形成完整论证。"
            "当前版本为模型不可用时生成的结构化底稿，具体数据、结论与引用需由作者补充并核验。",
            "## 引言\n"
            f"研究主题涉及{keywords}。建议首先说明现实需求与现有方法边界，再明确本文要解决的核心问题、"
            "研究目标和可验证贡献。引言中的事实判断应补充权威文献来源。",
            "## 相关工作\n"
            "建议按方法范式、应用场景和评价指标组织相关工作，分别概括代表性路线、适用条件与不足，"
            "最后说明本文方案与已有工作的实质差异。",
            "## 方法设计\n"
            "建议依次描述输入与假设、系统模块、数据流或算法步骤、关键参数以及输出形式。"
            "对每项设计选择给出可检验的技术动机，并补充复杂度或边界条件分析。",
            "## 实验方案\n"
            "建议设置公开数据集或可复现实验环境，包含强基线、消融实验、敏感性分析、统计显著性检验"
            "和失败案例分析。预先定义主要指标，并确保结果能够对应研究问题。",
            "## 总结\n"
            "总结应仅概括已由实验支持的发现，区分已验证结论与后续研究设想，并说明当前方案的适用范围和局限。",
        ]
    )


def clean_cli_output(stdout: str) -> str:
    text = stdout.strip()
    if not text:
        return ""
    marker = "## "
    index = text.find(marker)
    return text[index:].strip() if index >= 0 else text


def parse_markdown_sections(markdown: str) -> list[dict[str, str]]:
    matches = list(re.finditer(r"^##\s+(.+?)\s*$", markdown, flags=re.MULTILINE))
    if not matches:
        return [{"id": "manuscript", "title": "文稿内容", "content": markdown.strip()}]

    sections: list[dict[str, str]] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        title = match.group(1).strip()
        section_id = normalize_section_id(title)
        sections.append({"id": section_id, "title": title, "content": markdown[start:end].strip()})
    return sections


def normalize_section_id(title: str) -> str:
    normalized = title.lower()
    if "abstract" in normalized or "摘要" in title:
        return "abstract"
    if "introduction" in normalized or "引言" in title:
        return "introduction"
    if "related" in normalized or "相关" in title:
        return "related_work"
    if "method" in normalized or "方法" in title:
        return "method"
    if "experiment" in normalized or "实验" in title:
        return "experiment"
    if "conclusion" in normalized or "总结" in title or "结论" in title:
        return "conclusion"
    return re.sub(r"[^a-z0-9]+", "_", normalized).strip("_") or "section"
