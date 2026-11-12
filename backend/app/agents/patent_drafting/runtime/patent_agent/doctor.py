from __future__ import annotations

import importlib.util
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from patent_agent.adapters.cnipa import CnipaAdapter
from patent_agent.adapters.fake import FakeModelAdapter
from patent_agent.adapters.qwen import QwenAdapter
from patent_agent.config import AppConfig
from patent_agent.exporter import resolve_docx_font
from patent_agent.platform_support import (
    find_mermaid_cli,
    find_npm_executable,
    find_puppeteer_browser,
    font_available,
    interpreter_kind,
)


SUPPORTED_PYTHON_MIN = (3, 12)
SUPPORTED_PYTHON_MAX_EXCLUSIVE = (3, 15)


def is_supported_python(version_info: tuple[int, ...] | Any) -> bool:
    version = tuple(version_info[:2])
    return SUPPORTED_PYTHON_MIN <= version < SUPPORTED_PYTHON_MAX_EXCLUSIVE


def run_doctor(config: AppConfig, *, skip_qwen: bool = False, live_cnipa: bool = False, cnipa_query: str = "缓存调度") -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def add(name: str, status: str, detail: Any = None) -> None:
        row = {"name": name, "status": status}
        if detail is not None:
            row["detail"] = detail
        checks.append(row)

    add("python", "passed" if is_supported_python(sys.version_info) else "failed", sys.version.split()[0])
    add(
        "platform",
        "passed",
        {
            "system": platform.system(),
            "release": platform.release(),
            "python_platform": sys.platform,
            "interpreter_kind": interpreter_kind(),
        },
    )
    for module in ("yaml", "dotenv", "docx", "mammoth", "pptx", "pypdf", "matplotlib"):
        add(f"dependency:{module}", "passed" if importlib.util.find_spec(module) else "failed")

    node = shutil.which("node")
    add("dependency:node", "passed" if node else "failed", "installed" if node else "not found")
    npm = find_npm_executable()
    add("dependency:npm", "passed" if npm else "failed", npm or "not found")
    tools_dir = config.root / "vendor/patent-disclosure-skill/tools"
    mmdc = find_mermaid_cli(tools_dir)
    expected_mmdc = tools_dir / "node_modules" / ".bin" / ("mmdc.cmd" if os.name == "nt" else "mmdc")
    add(
        "dependency:mermaid-cli",
        "passed" if mmdc else "failed",
        os.path.relpath(mmdc or expected_mmdc, config.root),
    )
    puppeteer_browser = find_puppeteer_browser(tools_dir)
    expected_puppeteer_cache = tools_dir / ".puppeteer-cache"
    add(
        "dependency:puppeteer-browser",
        "passed" if puppeteer_browser else "failed",
        os.path.relpath(puppeteer_browser or expected_puppeteer_cache, config.root),
    )
    md_to_docx = config.root / "vendor/patent-disclosure-skill/tools/md_to_docx.py"
    add("dependency:docx-export", "passed" if md_to_docx.is_file() else "failed", os.path.relpath(md_to_docx, config.root))

    configured_font_available, font_detector = font_available(config.docx_font)
    font_resolution = resolve_docx_font(config.docx_font)
    font_detail: dict[str, Any] = {
        "configured_font": config.docx_font,
        "effective_font": font_resolution.effective_font,
        "fallback_used": font_resolution.fallback_used,
        "detector": font_detector,
    }
    font_detail["repair"] = (
        "Install the configured CJK font or set PATENT_AGENT_DOCX_FONT to an installed family."
        if not configured_font_available and not font_resolution.fallback_used
        else "An installed CJK fallback will be used and recorded."
        if font_resolution.fallback_used
        else "not needed"
    )
    add(
        "configured_docx_font",
        "passed"
        if configured_font_available or font_resolution.fallback_used
        else "not_available",
        font_detail,
    )
    office = shutil.which("libreoffice") or shutil.which("soffice")
    add("optional:libreoffice", "passed" if office else "not_available", "installed" if office else "optional renderer not found")

    for name, path in (("runs_dir", config.runs_dir), ("outputs_dir", config.outputs_dir)):
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".write_probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            add(name, "passed", os.path.relpath(path, config.root))
        except OSError as exc:
            add(name, "failed", type(exc).__name__)

    qwen_detail = {
        "base_url_configured": bool(config.qwen.base_url),
        "model_configured": bool(config.qwen.model),
        "api_key_configured": bool(config.qwen.api_key),
        "api_key_env": config.qwen.api_key_env,
        "recognized_variables": list(config.recognized_qwen_vars),
        "env_file_loaded": bool(config.env_file),
    }
    qwen_config_ok = all((config.qwen.base_url, config.qwen.model, config.qwen.api_key)) or config.fake_mode
    if skip_qwen:
        add("qwen_configuration", "skipped", qwen_detail)
        add("qwen_connectivity", "skipped")
    else:
        add("qwen_configuration", "passed" if qwen_config_ok else "failed", qwen_detail)
    if not skip_qwen and qwen_config_ok:
        try:
            adapter = FakeModelAdapter() if config.fake_mode else QwenAdapter(config.qwen)
            add("qwen_connectivity", "fixture" if config.fake_mode else "passed", adapter.smoke_test())
        except Exception as exc:
            add("qwen_connectivity", "failed", {"error_type": type(exc).__name__, "message": str(exc)})
    elif not skip_qwen:
        add("qwen_connectivity", "failed", "configuration incomplete")

    tool_ok = config.cnipa.tool.is_file()
    add("cnipa_tool", "passed" if tool_ok else "failed", os.path.relpath(config.cnipa.tool, config.root))
    playwright_ok = importlib.util.find_spec("playwright") is not None
    browser_ok = False
    browser_detail = "playwright package missing"
    if playwright_ok:
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                executable = Path(p.chromium.executable_path)
                browser_ok = executable.is_file()
                browser_detail = "installed" if browser_ok else "chromium executable missing"
        except Exception as exc:
            browser_detail = type(exc).__name__
    add("cnipa_playwright_chromium", "passed" if browser_ok else "failed", browser_detail)

    if live_cnipa and tool_ok and playwright_ok and browser_ok:
        try:
            result = CnipaAdapter(config.cnipa).search(cnipa_query)
            status = "passed" if result.status in {"success", "zero_results"} else "failed"
            add("cnipa_live_search", status, result.to_dict())
        except Exception as exc:
            add("cnipa_live_search", "failed", {"query": cnipa_query, "error_type": type(exc).__name__, "message": str(exc)})
    elif live_cnipa:
        add("cnipa_live_search", "failed", "dependencies unavailable")
    else:
        add("cnipa_live_search", "not_run")

    failed = [row for row in checks if row["status"] == "failed"]
    return {
        "status": "passed" if not failed else "failed",
        "fake_mode": config.fake_mode,
        "workflow_mode": config.workflow_mode,
        "checks": checks,
    }
