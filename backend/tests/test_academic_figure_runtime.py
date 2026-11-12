from pathlib import Path
import sys

import pytest


RUNTIME_ROOT = Path(__file__).resolve().parents[1] / "app" / "agents" / "academic_figure" / "runtime"
sys.path.insert(0, str(RUNTIME_ROOT / "src"))
sys.path.insert(0, str(RUNTIME_ROOT))

from academic_figure_agent.llm.bailian import BailianFigurePlanner  # noqa: E402
from academic_figure_agent.schemas import DatasetSummary, FigureRequest  # noqa: E402
from config.settings import Settings  # noqa: E402


class FailingModel:
    def invoke(self, _messages):
        raise RuntimeError("upstream returned 502")


def runtime_settings(*, fallback: bool) -> Settings:
    return Settings.model_validate(
        {
            "DASHSCOPE_API_KEY": "test-key",
            "BAILIAN_ALLOW_OFFLINE_FALLBACK": fallback,
        }
    )


def test_online_planning_falls_back_to_deterministic_spec_and_captions():
    planner = BailianFigurePlanner(
        model=FailingModel(), settings=runtime_settings(fallback=True)
    )
    request = FigureRequest(prompt="对比不同模型的准确率", figure_type="bar")
    dataset = DatasetSummary(
        row_count=2,
        columns=["model", "accuracy"],
        numeric_columns=["accuracy"],
        categorical_columns=["model"],
    )

    spec = planner.plan(request, dataset, "")
    plan_warnings = planner.take_warnings()
    captions = planner.captions(spec, dataset)
    caption_warnings = planner.take_warnings()

    assert spec.figure_type == "bar"
    assert spec.x == "model"
    assert spec.y == "accuracy"
    assert any("deterministic fallback" in item for item in spec.assumptions)
    assert plan_warnings == ["模型规划服务暂时不可用，已自动使用离线规则完成图表规划。"]
    assert captions["zh"] and captions["en"]
    assert caption_warnings == ["模型图注服务暂时不可用，已自动生成确定性中英文图注。"]


def test_online_planning_can_disable_automatic_fallback():
    planner = BailianFigurePlanner(
        model=FailingModel(), settings=runtime_settings(fallback=False)
    )
    request = FigureRequest(prompt="绘制准确率趋势")

    with pytest.raises(RuntimeError, match="502"):
        planner.plan(request, DatasetSummary(), "")
