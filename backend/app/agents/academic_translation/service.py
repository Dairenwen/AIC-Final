from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable
from uuid import UUID

from ...api.uploads import resolve_translation_upload
from ...extensions import db
from ...models import Task
from ..task_service import BuiltinAgentTaskService

SUPPORTED_TRANSLATION_LANGUAGES = {
    "en": "英语",
    "zh": "简体中文",
    "ja": "日语",
    "de": "德语",
    "fr": "法语",
    "es": "西班牙语",
}
TRANSLATION_LANGUAGE_ALIASES = {
    "en": "en",
    "en-us": "en",
    "en-gb": "en",
    "english": "en",
    "zh": "zh",
    "zh-cn": "zh",
    "zh-hans": "zh",
    "zh_hans": "zh",
    "chinese": "zh",
    "ja": "ja",
    "jp": "ja",
    "japanese": "ja",
    "de": "de",
    "german": "de",
    "fr": "fr",
    "french": "fr",
    "es": "es",
    "spanish": "es",
}


class TranslationConfigError(RuntimeError):
    pass


class TranslationArtifactError(RuntimeError):
    pass


class AcademicTranslationService(BuiltinAgentTaskService):
    agent_label = "academic-translation"
    failed_message = "学术翻译 Agent 工作流执行失败"

    def run(self, task_id: UUID, user_id: UUID) -> None:
        task = db.session.get(Task, task_id)
        if task is None:
            return
        input_json = task.input_json or {}
        source_path = resolve_translation_upload(user_id, input_json.get("attachment_id"))
        if source_path is None:
            raise ValueError("上传的翻译文档不存在或不属于当前用户")
        options = normalize_translation_options(input_json.get("translation_options"))
        output_dir = (
            Path(self.app.config["AGENT_GENERATED_DIR"])
            / "academic_translation"
            / str(task.id)
        )
        output_dir.mkdir(parents=True, exist_ok=True)

        task.status = "RUNNING"
        task.started_at = datetime.now(UTC)
        self.emit(
            task,
            "task.started",
            5,
            "已启动学术翻译 Agent",
            feedback_kind="progress",
            feedback_level="info",
            stage="started",
            task_phase="running",
        )
        request_payload = build_translation_request(
            str(input_json.get("attachment") or source_path.name),
            source_path,
            options,
        )
        self.merge_output(
            task,
            translation_request=request_payload,
        )
        self.emit(
            task,
            "translation.source_ready",
            14,
            "文档来源与用户权限已确认",
            feedback_kind="progress",
            feedback_level="info",
            stage="source_ready",
            task_phase="running",
        )
        self.emit(
            task,
            "translation.parsing",
            26,
            "正在解析文档结构、公式、引用与图表",
            feedback_kind="progress",
            feedback_level="info",
            stage="parsing",
            task_phase="running",
        )
        self.emit(
            task,
            "translation.terms_ready",
            38,
            "正在合并学术术语库与用户术语表",
            feedback_kind="progress",
            feedback_level="info",
            stage="terms_ready",
            task_phase="running",
        )
        self.emit(
            task,
            "translation.translating",
            52,
            "正在执行学术语境翻译与术语一致性约束",
            feedback_kind="progress",
            feedback_level="info",
            stage="translating",
            task_phase="running",
        )

        def heartbeat(elapsed_seconds: float) -> None:
            interval = int(self.app.config["TRANSLATION_HEARTBEAT_SECONDS"])
            progress = min(76, 52 + max(1, int(elapsed_seconds // interval)) * 2)
            self.emit(
                task,
                "translation.heartbeat",
                progress,
                f"学术翻译仍在执行，已用时 {int(elapsed_seconds)} 秒",
                feedback_kind="heartbeat",
                feedback_level="info",
                stage="translating",
                task_phase="running",
                elapsed_seconds=round(elapsed_seconds, 1),
            )

        result = self.run_core(source_path, output_dir, options, progress_callback=heartbeat)
        payload = parse_translation_stdout(result.stdout)
        artifacts = resolve_translation_artifacts(payload.get("outputs"), output_dir)
        segments = compact_segments(payload.get("segments"))
        glossary = payload.get("glossary") if isinstance(payload.get("glossary"), list) else []
        quality = payload.get("quality") if isinstance(payload.get("quality"), dict) else {}
        warning_items = normalize_translation_warning_items(payload.get("warnings"))
        warnings = [item["message"] for item in warning_items]
        elapsed_seconds = max(
            0.0,
            round((datetime.now(UTC) - task.started_at).total_seconds(), 1),
        ) if task.started_at else None

        self.emit(
            task,
            "translation.quality_ready",
            82,
            "译文质量、保护元素和术语一致性检查已完成",
            feedback_kind="progress",
            feedback_level="info",
            stage="quality_ready",
            task_phase="running",
        )
        if warning_items:
            self.emit(
                task,
                "translation.warning",
                88,
                f"翻译已完成，但存在 {len(warning_items)} 条运行提示待复核",
                feedback_kind="warning",
                feedback_level="warning",
                stage="warning",
                task_phase="running",
                warning_count=len(warning_items),
                warnings=warning_items[:20],
            )
        self.merge_output(
            task,
            translation_request=request_payload,
            academic_translation={
                "task_id": payload.get("task_id"),
                "source_lang": payload.get("source_lang"),
                "target_lang": payload.get("target_lang"),
                "precision": payload.get("precision"),
            },
            translation_segments=segments,
            translation_glossary=glossary[:200],
            translation_quality=quality,
            translation_warnings=warnings[:50],
            translation_warning_items=warning_items[:50],
            translation_runtime={
                "status": "SUCCEEDED",
                "outcome": "warning" if warning_items else "success",
                "elapsed_seconds": elapsed_seconds,
                "warning_count": len(warning_items),
                "artifact_count": len(artifacts),
                "stage": "completed",
                "feedback_kind": "warning" if warning_items else "success",
            },
            artifacts=artifacts,
            translation_files=artifact_metadata(artifacts),
            logs={
                "stdout_tail": result.stdout[-2000:],
                "stderr_tail": result.stderr[-2000:],
                "returncode": result.returncode,
            },
        )
        self.emit(
            task,
            "translation.exports_ready",
            94,
            "译文文件与质量报告已生成",
            feedback_kind="progress",
            feedback_level="info",
            stage="exports_ready",
            task_phase="running",
            artifact_count=len(artifacts),
        )

        task.status = "SUCCEEDED"
        task.progress = 100
        task.current_step = "学术翻译完成"
        task.finished_at = datetime.now(UTC)
        task.trace_summary = {
            "agent": "academic_translation",
            "runtime": "academic-translation-agent",
            "source_lang": options["source_lang"],
            "target_lang": options["target_lang"],
            "precision": options["precision"],
            "translated_segments": quality.get("translated_segments", len(segments)),
            "backend": self.app.config["TRANSLATION_LLM_BACKEND"],
            "model": (
                self.app.config["TRANSLATION_API_MODEL"]
                if self.app.config["TRANSLATION_LLM_BACKEND"] in {"api", "openai", "openai-compatible"}
                else self.app.config["TRANSLATION_OLLAMA_MODEL"]
            ),
        }
        db.session.commit()
        self.emit(
            task,
            "task.completed",
            100,
            "学术翻译任务已完成",
            feedback_kind="success",
            feedback_level="info",
            stage="completed",
            task_phase="completed",
            outcome="warning" if warning_items else "success",
            warning_count=len(warning_items),
            elapsed_seconds=elapsed_seconds,
        )

    def run_core(
        self,
        source_path: Path,
        output_dir: Path,
        options: dict[str, Any],
        progress_callback: Callable[[float], None] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        root = Path(self.app.config["TRANSLATION_AGENT_ROOT"])
        core = root / "agent-core"
        source_root = core / "src"
        cli = source_root / "academic_translation" / "cli.py"
        if not root.is_dir() or not cli.is_file():
            raise TranslationConfigError("学术翻译 Agent 运行目录未正确配置")
        if options["preserve_pdf_layout"] and not self.app.config["TRANSLATION_PDF2ZH_COMMAND"]:
            raise TranslationConfigError("保留 PDF 原版式需要配置 TRANSLATION_PDF2ZH_COMMAND")
        backend = str(self.app.config["TRANSLATION_LLM_BACKEND"]).strip().lower()
        if backend in {"api", "openai", "openai-compatible"}:
            if not str(self.app.config["TRANSLATION_API_BASE_URL"]).strip():
                raise TranslationConfigError("API 翻译需要配置 TRANSLATION_API_BASE_URL")
            if not str(self.app.config["TRANSLATION_API_MODEL"]).strip():
                raise TranslationConfigError("API 翻译需要配置 TRANSLATION_API_MODEL")
        elif backend != "ollama":
            raise TranslationConfigError("TRANSLATION_LLM_BACKEND 仅支持 api 或 ollama")

        command = [
            sys.executable,
            "-m",
            "academic_translation.cli",
            str(source_path),
            "--from",
            options["source_lang"],
            "--to",
            options["target_lang"],
            "--precision",
            options["precision"],
            "--output-dir",
            str(output_dir),
            "--parallel",
            str(options["parallel"]),
            "--domain",
            options["domain"],
            "--pdf-timeout-seconds",
            str(options["pdf_timeout_seconds"]),
        ]
        for source, target in options["glossary"].items():
            command.extend(["--glossary", f"{source}={target}"])
        if options["preserve_pdf_layout"]:
            command.extend(["--preserve-pdf-layout", "--pdf-only", "--pdf-layout-mode", options["pdf_layout_mode"]])
        if options["bilingual"]:
            if options["preserve_pdf_layout"]:
                command.append("--pdf-bilingual")
            else:
                command.append("--bilingual-markdown")
        if options["translate_figures"]:
            command.append("--translate-figures")

        env = os.environ.copy()
        previous_pythonpath = env.get("PYTHONPATH", "")
        env.update(
            {
                "PYTHONIOENCODING": "utf-8",
                "PYTHONPATH": os.pathsep.join(
                    item for item in [str(source_root), previous_pythonpath] if item
                ),
                "ACADEMIC_TRANSLATION_PROMPTS_DIR": str(core / "prompts"),
                "TRANSLATION_LLM_BACKEND": backend,
                "OLLAMA_BASE_URL": str(self.app.config["TRANSLATION_OLLAMA_BASE_URL"]),
                "OLLAMA_TRANSLATION_MODEL": str(self.app.config["TRANSLATION_OLLAMA_MODEL"]),
                "TRANSLATION_API_BASE_URL": str(self.app.config["TRANSLATION_API_BASE_URL"]),
                "TRANSLATION_API_KEY": str(self.app.config["TRANSLATION_API_KEY"]),
                "TRANSLATION_API_MODEL": str(self.app.config["TRANSLATION_API_MODEL"]),
                "TRANSLATION_API_TIMEOUT_SECONDS": str(self.app.config["TRANSLATION_API_TIMEOUT_SECONDS"]),
                "TRANSLATION_API_MAX_TOKENS": str(self.app.config["TRANSLATION_API_MAX_TOKENS"]),
                "TRANSLATION_API_RETRIES": str(self.app.config["TRANSLATION_API_RETRIES"]),
                "TRANSLATION_API_RETRY_BACKOFF_SECONDS": str(
                    self.app.config["TRANSLATION_API_RETRY_BACKOFF_SECONDS"]
                ),
                "TRANSLATION_API_DISABLE_THINKING": str(
                    self.app.config["TRANSLATION_API_DISABLE_THINKING"]
                ),
                "TRANSLATION_API_MAX_PARALLEL": str(
                    self.app.config["TRANSLATION_API_MAX_PARALLEL"]
                ),
                "PDF2ZH_COMMAND": str(self.app.config["TRANSLATION_PDF2ZH_COMMAND"]),
                "DEFAULT_OUTPUT_DIR": str(output_dir),
            }
        )
        process_options = {
            "cwd": str(core),
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "env": env,
        }
        timeout = int(self.app.config["TRANSLATION_AGENT_TIMEOUT_SECONDS"])
        if progress_callback is None:
            result = subprocess.run(
                command,
                capture_output=True,
                timeout=timeout,
                **process_options,
            )
        else:
            result = run_with_heartbeat(
                command,
                timeout_seconds=timeout,
                heartbeat_seconds=int(self.app.config["TRANSLATION_HEARTBEAT_SECONDS"]),
                progress_callback=progress_callback,
                **process_options,
            )
        if result.returncode != 0:
            message = (result.stderr or result.stdout or "学术翻译核心工作流执行失败").strip()
            raise RuntimeError(message[-1800:])
        return result


def run_with_heartbeat(
    command: list[str],
    *,
    timeout_seconds: int,
    heartbeat_seconds: int,
    progress_callback: Callable[[float], None],
    **process_options: Any,
) -> subprocess.CompletedProcess[str]:
    started = time.monotonic()
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        **process_options,
    )
    while True:
        elapsed = time.monotonic() - started
        remaining = timeout_seconds - elapsed
        if remaining <= 0:
            process.kill()
            stdout, stderr = process.communicate()
            raise subprocess.TimeoutExpired(command, timeout_seconds, output=stdout, stderr=stderr)
        try:
            stdout, stderr = process.communicate(timeout=min(heartbeat_seconds, remaining))
            return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
        except subprocess.TimeoutExpired:
            progress_callback(time.monotonic() - started)


def normalize_translation_options(value: object) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    source_lang = normalize_translation_language(raw.get("source_lang"), "源语言", "en")
    target_lang = normalize_translation_language(raw.get("target_lang"), "目标语言", "zh")
    if source_lang == target_lang:
        raise ValueError("源语言和目标语言不能相同")
    precision = str(raw.get("precision") or "reading")
    if precision not in {"reading", "submission"}:
        precision = "reading"
    layout_mode = str(raw.get("pdf_layout_mode") or "batch")
    if layout_mode not in {"batch", "pagewise", "low_memory"}:
        layout_mode = "batch"
    try:
        parallel = max(1, min(int(raw.get("parallel") or 2), 5))
    except (TypeError, ValueError):
        parallel = 2
    try:
        pdf_timeout = max(60, min(int(raw.get("pdf_timeout_seconds") or 600), 3600))
    except (TypeError, ValueError):
        pdf_timeout = 600
    clean_glossary = normalize_translation_glossary(raw.get("glossary"))
    return {
        "source_lang": source_lang or "en",
        "target_lang": target_lang or "zh",
        "precision": precision,
        "glossary": clean_glossary,
        "domain": str(raw.get("domain") or "academic").strip()[:64] or "academic",
        "parallel": parallel,
        "preserve_pdf_layout": coerce_bool(raw.get("preserve_pdf_layout", False)),
        "bilingual": coerce_bool(raw.get("bilingual", False)),
        "translate_figures": coerce_bool(raw.get("translate_figures", False)),
        "pdf_layout_mode": layout_mode,
        "pdf_timeout_seconds": pdf_timeout,
    }


def parse_translation_stdout(stdout: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    for start, marker in enumerate(stdout):
        if marker != "{":
            continue
        try:
            payload, _ = decoder.raw_decode(stdout[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and isinstance(payload.get("outputs"), dict):
            return payload
    raise TranslationArtifactError("学术翻译 Agent 未返回有效 JSON 结果")


def resolve_translation_artifacts(value: object, output_dir: Path) -> dict[str, str]:
    outputs = value if isinstance(value, dict) else {}
    output_root = output_dir.resolve()
    artifacts: dict[str, str] = {}
    for key, raw_path in outputs.items():
        if not isinstance(raw_path, str) or key.endswith("warning") or key in {"pdf_layout_mode", "pdf_layout_pages", "pdf_layout_elapsed_seconds"}:
            continue
        path = Path(raw_path).resolve()
        if path.is_file() and path.is_relative_to(output_root):
            artifacts[str(key)] = str(path)
    if not artifacts:
        raise TranslationArtifactError("学术翻译 Agent 未生成可用译文文件")
    return artifacts


def compact_segments(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    compact: list[dict[str, Any]] = []
    for item in value[:120]:
        if not isinstance(item, dict):
            continue
        compact.append(
            {
                "segment_id": item.get("segment_id"),
                "kind": item.get("kind"),
                "page": item.get("page"),
                "source_text": str(item.get("source_text") or "")[:3000],
                "translated_text": str(item.get("translated_text") or "")[:3000],
            }
        )
    return compact


def artifact_metadata(artifacts: dict[str, str]) -> list[dict[str, Any]]:
    labels = {
        "monolingual_markdown": "单语译文 Markdown",
        "bilingual_markdown": "双语对照 Markdown",
        "monolingual_docx": "译文 Word",
        "translation_report": "翻译质量报告",
        "pdf_monolingual": "保留版式译文 PDF",
        "pdf_bilingual": "双语对照 PDF",
        "figure_translation_manifest": "图像翻译清单",
        "table_translation_manifest": "表格翻译清单",
    }
    return [
        {
            "kind": key,
            "label": labels.get(key, key),
            "file_name": Path(path).name,
            "size": Path(path).stat().st_size,
        }
        for key, path in artifacts.items()
    ]


def normalize_translation_language(value: object, label: str, default: str) -> str:
    raw = str(value or default).strip().lower().replace("_", "-")[:32]
    normalized = TRANSLATION_LANGUAGE_ALIASES.get(raw, raw)
    if normalized not in SUPPORTED_TRANSLATION_LANGUAGES:
        supported = "、".join(SUPPORTED_TRANSLATION_LANGUAGES)
        raise ValueError(f"{label}仅支持 {supported}")
    return normalized


def normalize_translation_glossary(value: object) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("术语表必须是源术语到目标术语的 JSON 对象")
    if len(value) > 100:
        raise ValueError("术语表最多包含 100 条术语")
    clean_glossary: dict[str, str] = {}
    for source, target in value.items():
        source_text = str(source).strip()
        if not source_text or not isinstance(target, str) or not target.strip():
            raise ValueError("术语表中的源术语和目标术语都必须是非空文本")
        clean_glossary[source_text[:120]] = target.strip()[:120]
    return clean_glossary


def normalize_translation_warning_items(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    warning_items: list[dict[str, str]] = []
    for index, item in enumerate(value[:50], start=1):
        if isinstance(item, dict):
            message = str(item.get("message") or item.get("warning") or "").strip()
            code = str(item.get("code") or f"TRANSLATION_WARNING_{index}").strip()[:80]
        else:
            message = str(item or "").strip()
            code = f"TRANSLATION_WARNING_{index}"
        if not message:
            continue
        warning_items.append(
            {
                "code": code or f"TRANSLATION_WARNING_{index}",
                "message": message[:500],
                "level": "warning",
            }
        )
    return warning_items


def build_translation_request(file_name: str, source_path: Path, options: dict[str, Any]) -> dict[str, Any]:
    return {
        "file_name": file_name,
        "file_type": source_path.suffix.removeprefix(".").lower(),
        **options,
    }


def coerce_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off", ""}:
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    return False
