from __future__ import annotations

import shutil
import socket
import time
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID

from flask import Flask

from .system_model_runtime import get_system_model_runtime


_CACHE: dict[tuple[str, str, bool, str], tuple[float, dict[str, str]]] = {}


def agent_readiness(
    app: Flask,
    code: str,
    *,
    has_personal_model: bool = False,
    user_id: UUID | str | None = None,
) -> dict[str, str]:
    cache_seconds = int(app.config["AGENT_READINESS_CACHE_SECONDS"])
    cache_key = (str(app.root_path), code, has_personal_model, str(user_id or ""))
    cached = _CACHE.get(cache_key)
    now = time.monotonic()
    if cached and cached[0] > now:
        return dict(cached[1])

    result = _calculate_readiness(app, code, has_personal_model=has_personal_model, user_id=user_id)
    _CACHE[cache_key] = (now + cache_seconds, result)
    return dict(result)


def _calculate_readiness(
    app: Flask,
    code: str,
    *,
    has_personal_model: bool,
    user_id: UUID | str | None,
) -> dict[str, str]:
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
        if user_id is None:
            api_base_url = str(app.config.get("USER_API_MODEL_BASE_URL") or "").strip()
            api_model_name = str(app.config.get("USER_API_MODEL_NAME") or "").strip()
            if not api_base_url or not api_model_name:
                return _result("UNAVAILABLE", "系统默认模型配置不可用")
            system_runtime = {
                "selector": "api_model",
                "base_url": api_base_url,
                "model_name": api_model_name,
            }
        else:
            try:
                system_runtime = get_system_model_runtime(user_id)
            except Exception:
                # Readiness must remain useful during a transient database issue;
                # the deployment API configuration is still a valid probe target.
                fallback_base_url = str(app.config.get("USER_API_MODEL_BASE_URL") or "").strip()
                if not fallback_base_url:
                    return _result("UNAVAILABLE", "系统默认模型配置不可用")
                system_runtime = {
                    "selector": "api_model",
                    "base_url": fallback_base_url,
                    "model_name": str(app.config.get("USER_API_MODEL_NAME") or ""),
                }
        model_base_url = str(system_runtime["base_url"])
        model_label = "API 模型" if system_runtime["selector"] == "api_model" else "平台专用模型"
        model_ready = endpoint_reachable(model_base_url, timeout)
        if not model_ready and code in {"manuscript_assistance", "academic_figure", "academic_compliance"}:
            return _result("DEGRADED", f"{model_label}服务不可用，将使用确定性降级能力")
        if not model_ready:
            return _result("UNAVAILABLE", f"{model_label}服务不可达")
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
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((parsed.hostname, port), timeout=timeout_seconds):
            return True
    except OSError:
        return False


def _result(readiness: str, detail: str) -> dict[str, str]:
    labels = {"READY": "可用", "DEGRADED": "降级可用", "UNAVAILABLE": "依赖异常"}
    return {"readiness": readiness, "status": labels[readiness], "readiness_detail": detail}


def clear_readiness_cache() -> None:
    _CACHE.clear()
