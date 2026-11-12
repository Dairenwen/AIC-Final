from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID
from zipfile import BadZipFile, ZipFile

from flask import Flask

from ..agents.task_service import BuiltinAgentTaskService
from ..extensions import db
from ..models import Task


class LiteraturePptService(BuiltinAgentTaskService):
    agent_label = "literature-ppt"
    failed_message = "文献 PPT 生成失败"

    def run(self, task_id: UUID, user_id: UUID) -> None:
        task = db.session.get(Task, task_id)
        if task is None:
            return
        runtime_root = Path(self.app.config["LITERATURE_PPT_RUNTIME_ROOT"]).resolve()
        python = Path(self.app.config["LITERATURE_PPT_PYTHON"]).resolve()
        parser = runtime_root / "document_parser.py"
        builder = runtime_root / "ppt_builder.py"
        source_path = Path(str((task.input_json or {}).get("source_path") or "")).resolve()
        if not python.is_file() or not parser.is_file() or not builder.is_file():
            raise RuntimeError("文献 PPT 运行环境未正确配置")
        upload_root = Path(self.app.config["LITERATURE_PPT_UPLOAD_DIR"]).resolve()
        if not source_path.is_file() or not source_path.is_relative_to(upload_root):
            raise ValueError("上传文献不存在或不属于当前任务")

        output_dir = Path(self.app.config["LITERATURE_PPT_DATA_DIR"]) / str(task.id)
        output_dir.mkdir(parents=True, exist_ok=True)
        evidence_path = output_dir / "literature.evidence.json"
        ppt_path = output_dir / "literature-presentation.pptx"

        task.status = "RUNNING"
        task.started_at = datetime.now(UTC)
        self.emit(task, "task.started", 5, "已启动文献解析与 PPT 生成工具")
        self.emit(task, "literature_ppt.parsing", 15, "正在解析文献正文、表格和插图")
        self._run_command(
            [str(python), str(parser), "--input", str(source_path), "--output", str(evidence_path)],
            runtime_root,
        )
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        blocks = evidence.get("evidence") if isinstance(evidence, dict) else []
        blocks = blocks if isinstance(blocks, list) else []
        visual_count = sum(
            1 for item in blocks if isinstance(item, dict) and item.get("kind") in {"table", "picture"}
        )
        self.merge_output(task, evidence_count=len(blocks), visual_count=visual_count)
        self.emit(
            task,
            "literature_ppt.parsed",
            55,
            f"已解析 {len(blocks)} 个证据块，其中包含 {visual_count} 个图表项",
            evidence_count=len(blocks),
            visual_count=visual_count,
        )

        options = (task.input_json or {}).get("ppt_options") or {}
        command = [str(python), str(builder), "--evidence", str(evidence_path), "--output", str(ppt_path)]
        for key, flag in (
            ("audience", "--audience"),
            ("language", "--language"),
            ("tone", "--tone"),
            ("focus", "--focus"),
            ("requirements", "--requirements"),
        ):
            value = str(options.get(key) or "").strip()
            if value:
                command.extend([flag, value])
        if options.get("slides") is not None:
            command.extend(["--slides", str(int(options["slides"]))])
        self.emit(task, "literature_ppt.building", 68, "正在规划汇报结构并生成可编辑 PPT")
        self._run_command(command, runtime_root)
        if not valid_pptx(ppt_path):
            raise RuntimeError("PPT 工具未生成有效的 PPTX 文件")
        slide_count = pptx_slide_count(ppt_path)
        self.merge_output(
            task,
            slide_count=slide_count,
            source_file=(task.input_json or {}).get("source_file"),
            request_options=options,
            artifacts={"literature-ppt": str(ppt_path), "literature-evidence": str(evidence_path)},
        )
        self.emit(task, "literature_ppt.ready", 94, f"已生成 {slide_count} 页可编辑 PPT")
        task.status = "SUCCEEDED"
        task.progress = 100
        task.current_step = "文献 PPT 生成完成"
        task.finished_at = datetime.now(UTC)
        task.trace_summary = {
            "tool": "literature_ppt",
            "slide_count": slide_count,
            "evidence_count": len(blocks),
            "visual_count": visual_count,
        }
        db.session.commit()
        self.emit(task, "task.completed", 100, "文献 PPT 已生成，可下载编辑")

    def _run_command(self, command: list[str], cwd: Path) -> None:
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        result = subprocess.run(
            command,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=int(self.app.config["LITERATURE_PPT_TIMEOUT_SECONDS"]),
            env=env,
        )
        if result.returncode != 0:
            message = (result.stderr or result.stdout or "文献 PPT 子进程执行失败").strip()
            raise RuntimeError(message[-1200:])


def valid_pptx(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        with ZipFile(path) as archive:
            names = set(archive.namelist())
            return "[Content_Types].xml" in names and any(
                name.startswith("ppt/slides/slide") and name.endswith(".xml") for name in names
            )
    except BadZipFile:
        return False


def pptx_slide_count(path: Path) -> int:
    with ZipFile(path) as archive:
        return sum(
            1
            for name in archive.namelist()
            if name.startswith("ppt/slides/slide") and name.endswith(".xml")
        )
