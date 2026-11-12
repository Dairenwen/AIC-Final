import json
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app import create_app
from app.agents.Innovation_point_generation.service import (
    InnovationPointGenerationService,
    read_result_payload,
)
from app.api.tasks import normalize_innovation_options
from app.services.catalog_setup import INNOVATION_POINT_AGENT


def test_innovation_options_are_normalized_and_bounded():
    options = normalize_innovation_options(
        {
            "innovation_mode": "expand",
            "innovation_top_k": 7,
            "innovation_time_range": "2021-2026",
            "innovation_keywords": ["RAG", "可靠性"],
            "innovation_seed_ideas": ["动态证据更新"],
            "innovation_constraints": {"min_score": 0.6},
        }
    )
    assert options == {
        "mode": "expand",
        "top_k": 7,
        "time_range": "2021-2026",
        "keywords": ["RAG", "可靠性"],
        "seed_ideas": ["动态证据更新"],
        "constraints": {"min_score": 0.6},
        "additional_context": "",
    }
    with pytest.raises(ValueError, match="1 到 10"):
        normalize_innovation_options({"innovation_top_k": 11})


def test_innovation_service_uses_configured_external_agent(monkeypatch, tmp_path):
    root = tmp_path / "paper-insight-generate"
    (root / "chuangx").mkdir(parents=True)
    (root / "innovation_agent.py").write_text("", encoding="utf-8")
    app = create_app(
        {
            "TESTING": True,
            "INNOVATION_AGENT_ROOT": root,
            "INNOVATION_AGENT_TIMEOUT_SECONDS": 321,
            "INNOVATION_AGENT_MAX_DOCUMENTS": 64,
        }
    )
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return SimpleNamespace(returncode=0, stdout="RESULT_JSON=C:\\result.json\n", stderr="")

    monkeypatch.setattr(
        "app.agents.Innovation_point_generation.service.subprocess.run",
        fake_run,
    )
    service = InnovationPointGenerationService(app)
    result = service.run_core(
        "动态 RAG 可靠性",
        ["RAG", "reliability"],
        tmp_path / "corpus",
        tmp_path / "runs",
        {
            "mode": "expand",
            "top_k": 3,
            "time_range": "2022-2026",
            "seed_ideas": ["按证据置信度动态更新"],
            "constraints": {"min_score": 0.6},
            "additional_context": "优先考虑可复现实验",
        },
    )

    command = captured["command"]
    assert result.returncode == 0
    assert command[0] == sys.executable
    assert command[1] == str(root / "innovation_agent.py")
    assert captured["cwd"] == str(root)
    assert captured["timeout"] == 321
    assert captured["env"]["PYTHONIOENCODING"] == "utf-8"
    assert command[command.index("--mode") + 1] == "expand"
    assert command[command.index("--top-k") + 1] == "3"
    assert command[command.index("--max-documents") + 1] == "64"
    assert command[command.index("--time-range") + 1] == "2022-2026"
    assert "按证据置信度动态更新" in command
    assert "qwen3.6-dpo" not in " ".join(command)


def test_innovation_result_contract_is_validated(tmp_path):
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    result_path = runs_dir / "result.json"
    payload = {"research_trends": [], "research_gaps": [], "innovations": []}
    result_path.write_text(json.dumps(payload), encoding="utf-8")

    parsed, resolved_path = read_result_payload(f"RESULT_JSON={result_path}\n", runs_dir)
    assert parsed == payload
    assert resolved_path == result_path

    result_path.write_text(json.dumps({"innovations": []}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="缺少趋势、空白或创新方案"):
        read_result_payload(f"RESULT_JSON={result_path}\n", runs_dir)


def test_innovation_catalog_points_to_external_runtime():
    assert INNOVATION_POINT_AGENT["config_json"]["runtime"] == "paper-insight-generate"
    assert INNOVATION_POINT_AGENT["config_json"]["route"] == "/agents/innovation-point-generation"


def test_innovation_default_output_root_stays_short_on_windows():
    app = create_app({"TESTING": True})
    runs_dir = Path(app.config["INNOVATION_DATA_DIR"]) / str(uuid4()) / "runs"
    worst_case_file = runs_dir / ("20260729T160000_" + "x" * 80 + ".json")
    assert "literature_search" not in str(runs_dir)
    assert len(str(worst_case_file)) < 260
