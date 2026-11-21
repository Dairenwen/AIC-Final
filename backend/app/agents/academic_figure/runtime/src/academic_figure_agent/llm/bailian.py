from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from academic_figure_agent.schemas import DatasetSummary, FigureRequest, FigureSpec, LocalizedText
from config.constants import DEFAULT_PALETTE
from config.settings import Settings, get_settings


def build_bailian_chat_model(settings: Settings | None = None) -> ChatOpenAI:
    resolved = settings or get_settings()
    if not resolved.dashscope_api_key or _is_placeholder(resolved.dashscope_api_key):
        raise ValueError("DASHSCOPE_API_KEY is required unless --offline is used")
    return ChatOpenAI(
        api_key=resolved.dashscope_api_key,
        base_url=resolved.bailian_base_url,
        model=resolved.bailian_model,
        temperature=0,
        timeout=resolved.bailian_timeout_seconds,
        max_retries=resolved.bailian_max_retries,
    )


class BailianFigurePlanner:
    def __init__(self, model: ChatOpenAI | None = None, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.model = model
        self._warnings: list[str] = []

    def take_warnings(self) -> list[str]:
        warnings = self._warnings
        self._warnings = []
        return warnings

    def plan(
        self,
        request: FigureRequest,
        dataset: DatasetSummary,
        context: str,
    ) -> FigureSpec:
        if request.offline:
            return heuristic_figure_spec(request, dataset)
        messages = [
            SystemMessage(content=_PLANNER_PROMPT),
            HumanMessage(
                content=json.dumps(
                    {
                        "request": request.model_dump(mode="json"),
                        "dataset": dataset.model_dump(mode="json"),
                        "context_excerpt": context[:12000],
                    },
                    ensure_ascii=False,
                )
            ),
        ]
        try:
            model = self.model or build_bailian_chat_model(self.settings)
            response = model.invoke(messages)
            payload = _parse_json_content(response.content)
            spec = FigureSpec.model_validate(payload)
        except Exception:  # noqa: BLE001 - the model provider exposes several client exception types
            if not self.settings.bailian_allow_offline_fallback:
                raise
            spec = heuristic_figure_spec(request, dataset)
            spec.assumptions.append(
                "Online model planning was unavailable; deterministic fallback planning was used."
            )
            self._warnings.append("模型规划服务暂时不可用，已自动使用离线规则完成图表规划。")
        if not spec.palette:
            spec.palette = DEFAULT_PALETTE
        return spec

    def captions(self, spec: FigureSpec, dataset: DatasetSummary, offline: bool = False) -> dict[str, str]:
        if offline:
            return fallback_captions(spec, dataset)
        try:
            model = self.model or build_bailian_chat_model(self.settings)
            response = model.invoke(
                [
                    SystemMessage(
                        content=(
                            "Write concise academic figure captions in Chinese and English. "
                            "Do not invent numerical conclusions. Return JSON with exactly keys zh and en."
                        )
                    ),
                    HumanMessage(
                        content=json.dumps(
                            {
                                "spec": spec.model_dump(mode="json"),
                                "dataset": dataset.model_dump(mode="json"),
                            },
                            ensure_ascii=False,
                        )
                    ),
                ]
            )
            payload = _parse_json_content(response.content)
            return {"zh": str(payload.get("zh", "")), "en": str(payload.get("en", ""))}
        except Exception:  # noqa: BLE001 - preserve figure delivery when the provider is transiently down
            if not self.settings.bailian_allow_offline_fallback:
                raise
            self._warnings.append("模型图注服务暂时不可用，已自动生成确定性中英文图注。")
            return fallback_captions(spec, dataset)


def heuristic_figure_spec(request: FigureRequest, dataset: DatasetSummary) -> FigureSpec:
    columns = dataset.columns
    numeric = dataset.numeric_columns
    categorical = dataset.categorical_columns
    figure_type = request.figure_type
    if figure_type == "auto":
        lowered = request.prompt.lower()
        if any(token in lowered for token in ("流程", "架构", "flow", "pipeline", "architecture")):
            figure_type = "flowchart"
        elif any(token in lowered for token in ("热力", "heatmap", "matrix")):
            figure_type = "heatmap"
        elif any(token in lowered for token in ("散点", "scatter", "相关")):
            figure_type = "scatter"
        elif any(token in lowered for token in ("箱线", "boxplot", "distribution")):
            figure_type = "box"
        elif any(token in lowered for token in ("柱", "bar", "对比")):
            figure_type = "bar"
        elif request.sketch_files and not dataset.row_count:
            figure_type = "image_panel"
        else:
            figure_type = "line"

    metric_column = _match_column(
        numeric,
        ("accuracy", "acc", "score", "f1", "auc", "precision", "recall", "loss", "metric"),
    )
    progression_column = _match_column(
        numeric,
        ("severity", "step", "epoch", "iteration", "time", "year", "round", "x"),
    )
    error_column = _match_column(
        numeric,
        ("std", "stderr", "standard_error", "error", "ci", "sem"),
    )
    if figure_type in {"line", "scatter"}:
        x = progression_column or next((item for item in numeric if item != metric_column), None)
        y = metric_column or next((item for item in numeric if item != x and item != error_column), None)
        series = categorical[0] if categorical else None
    elif figure_type in {"bar", "box"}:
        x = categorical[0] if categorical else (columns[0] if columns else None)
        y = metric_column or next((item for item in numeric if item != error_column), None)
        series = categorical[1] if len(categorical) > 1 else None
    elif figure_type == "heatmap":
        x = categorical[0] if categorical else progression_column
        y = metric_column or next((item for item in numeric if item != x and item != error_column), None)
        series = categorical[1] if len(categorical) > 1 else None
    else:
        x = y = series = None
    title = _title_from_prompt(request.prompt)
    nodes = []
    edges = []
    if figure_type == "flowchart":
        from academic_figure_agent.schemas import DiagramEdge, DiagramNode

        labels = [part.strip() for part in re.split(r"(?:->|→|，|,|然后|再)", request.prompt) if part.strip()]
        labels = labels[:6] or ["Input", "Processing", "Output"]
        nodes = [
            DiagramNode(id=f"n{idx}", label=LocalizedText(zh=label, en=label))
            for idx, label in enumerate(labels)
        ]
        edges = [DiagramEdge(source=f"n{idx}", target=f"n{idx + 1}") for idx in range(len(nodes) - 1)]
        x = y = series = None

    return FigureSpec(
        figure_type=figure_type,
        title=LocalizedText(zh=title, en=title),
        x=x,
        y=y,
        series=series,
        error=error_column if figure_type == "line" else None,
        xlabel=LocalizedText(zh=x or "", en=x or ""),
        ylabel=LocalizedText(zh=y or "", en=y or ""),
        palette=DEFAULT_PALETTE,
        caption_focus=LocalizedText(zh=request.prompt, en=request.prompt),
        nodes=nodes,
        edges=edges,
        image_paths=[str(path) for path in request.sketch_files],
        assumptions=["Offline heuristic planning was used; review labels before publication."],
    )


def fallback_captions(spec: FigureSpec, dataset: DatasetSummary) -> dict[str, str]:
    source_note = (
        f" Data are drawn from {len(dataset.source_files)} source file(s)." if dataset.source_files else ""
    )
    zh_focus = spec.caption_focus.zh or "所请求的学术图表内容"
    en_focus = spec.caption_focus.en or "the requested academic visualization"
    return {
        "zh": f"{spec.title.zh or spec.title.en}。图中展示了{zh_focus}。",
        "en": f"{spec.title.en or spec.title.zh}. The figure presents {en_focus}.{source_note}",
    }


def _parse_json_content(content: Any) -> dict[str, Any]:
    text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, flags=re.DOTALL)
    candidate = fenced.group(1) if fenced else text[text.find("{") : text.rfind("}") + 1]
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Bailian returned invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("Bailian response must be a JSON object")
    return value


def _title_from_prompt(prompt: str) -> str:
    compact = " ".join(prompt.strip().split())
    return compact[:80] if compact else "Academic Figure"


def _match_column(columns: list[str], aliases: tuple[str, ...]) -> str | None:
    for column in columns:
        normalized = column.casefold().replace(" ", "_").replace("-", "_")
        if any(alias == normalized or alias in normalized for alias in aliases):
            return column
    return None


def _is_placeholder(value: str) -> bool:
    normalized = value.strip().casefold()
    return any(marker in normalized for marker in ("replace", "your_", "example", "placeholder"))


_PLANNER_PROMPT = """
You are the planning module of an academic figure agent. Convert the request into one FigureSpec JSON object.
Allowed figure_type values: line, bar, scatter, box, heatmap, flowchart, image_panel.
Use only columns listed in dataset.columns. Never invent data or statistical conclusions.
Provide Chinese and English title/xlabel/ylabel/caption_focus as objects with zh and en fields.
For flowchart, provide nodes [{id,label:{zh,en},group}] and edges [{source,target,label:{zh,en}}].
For data charts, choose x, y, optional series and optional error. Use a colorblind-safe palette.
Return JSON only. Do not return code or Markdown.
""".strip()
