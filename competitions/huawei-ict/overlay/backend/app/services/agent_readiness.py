from __future__ import annotations

import shutil
import socket
from pathlib import Path
from urllib.parse import urlparse

from flask import Flask

from . import shared_cache


def agent_readiness(app: Flask, code: str, *, has_personal_model: bool = False) -> dict[str, str]:
    # Kept in Redis when it is configured, so every backend instance answers
    # from one probe instead of each probing the model endpoints itself.
    cache_seconds = int(app.config["AGENT_READINESS_CACHE_SECONDS"])
    cache_key = f"readiness:{app.root_path}:{code}:{int(has_personal_model)}"
    cached = shared_cache.get_json(cache_key)
    if isinstance(cached, dict) and cached.get("readiness"):
        return dict(cached)

    result = _calculate_readiness(app, code, has_personal_model=has_personal_model)
    shared_cache.set_json(cache_key, result, cache_seconds)
    return dict(result)


def _calculate_readiness(app: Flask, code: str, *, has_personal_model: bool) -> dict[str, str]:
    agents_root = Path(app.root_path) / "agents"
    required_paths: dict[str, list[Path]] = {
        "literature_search": [agents_root / "literature_search" / "core" / "src"],
        "manuscript_assistance": [agents_root / "Manuscript assistance" / "agent-core" / "src" / "main.py"],
        "innovation_point_generation": [
            Path(app.config["INNOVATION_AGENT_ROOT"]) / "innovation_agent.py",
            Path(app.config["INNOVATION_AGENT_ROOT"]) / "chuangx",
        ],
        "paper_reading": [
            Path(app.config["PAPER_READING_RUNTIME_ROOT"]) / "scripts" / "run_real_pdf_agent.py",
            Path(app.config["PAPER_READING_RUNTIME_ROOT"]) / "VERSION",
        ],
        "patent_drafting": [Path(app.config["PATENT_DRAFTING_RUNTIME_ROOT"]) / "patent_agent"],
        "academic_compliance": [Path(app.config["COMPLIANCE_AGENT_ROOT"]) / "app"],
        "arxiv_daily": [Path(app.config["ARXIV_DAILY_RUNTIME_ROOT"]) / "main.py"],
        "academic_figure": [Path(app.config["ACADEMIC_FIGURE_RUNTIME_ROOT"]) / "src"],
        "academic_translation": [
            Path(app.config["TRANSLATION_AGENT_ROOT"])
            / "agent-core"
            / "src"
            / "academic_translation"
            / "cli.py"
        ],
        "reviewer_comments": [agents_root / "reviewer_comments" / "src"],
        "contribution_recommendation": [agents_root / "contribution_recommendation" / "agent.py"],
    }
    missing = [path for path in required_paths.get(code, []) if not path.exists()]
    if missing:
        return _result("UNAVAILABLE", "运行时文件不完整")
    if code == "paper_reading" and shutil.which(str(app.config["PAPER_READING_UV_EXECUTABLE"])) is None:
        return _result("UNAVAILABLE", "缺少论文精读运行器 uv")

    timeout = float(app.config["AGENT_READINESS_CONNECT_TIMEOUT_SECONDS"])
    if code in {"manuscript_assistance", "paper_reading", "academic_figure", "academic_compliance"}:
        model_ready = bool(app.config.get("PLATFORM_MODEL_ENABLED", False)) and endpoint_reachable(
            str(app.config["QWEN_DPO_BASE_URL"]), timeout
        )
        if not model_ready and has_personal_model:
            return _result("READY", "平台模型不可用，将使用已验证个人模型")
        if not model_ready and code in {"manuscript_assistance", "academic_figure", "academic_compliance"}:
            return _result("DEGRADED", "平台模型不可用，将使用确定性降级能力")
        if not model_ready:
            return _result("UNAVAILABLE", "平台模型服务不可达，可配置已验证个人模型")
    if code == "academic_translation":
        backend = str(app.config["TRANSLATION_LLM_BACKEND"]).strip().lower()
        if backend in {"api", "openai", "openai-compatible"}:
            base_url = str(app.config["TRANSLATION_API_BASE_URL"]).strip()
            model = str(app.config["TRANSLATION_API_MODEL"]).strip()
            if not base_url or not model:
                return _result("UNAVAILABLE", "学术翻译 API 未配置地址或模型")
        elif backend == "ollama":
            base_url = str(app.config["TRANSLATION_OLLAMA_BASE_URL"])
        else:
            return _result("UNAVAILABLE", "学术翻译后端配置无效")
        if not endpoint_reachable(base_url, timeout):
            return _result("UNAVAILABLE", "学术翻译模型服务不可达")
    return _result("READY", "运行依赖已就绪")


def endpoint_reachable(base_url: str, timeout_seconds: float) -> bool:
    try:
        parsed = urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return False
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        with socket.create_connection((parsed.hostname, port), timeout=timeout_seconds):
            return True
    except (OSError, ValueError):
        return False


def _result(readiness: str, detail: str) -> dict[str, str]:
    labels = {"READY": "可用", "DEGRADED": "降级可用", "UNAVAILABLE": "依赖异常"}
    return {"readiness": readiness, "status": labels[readiness], "readiness_detail": detail}


def clear_readiness_cache() -> None:
    shared_cache.clear_prefix("readiness:")
