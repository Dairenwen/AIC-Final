from __future__ import annotations

import os
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")

import matplotlib.font_manager as font_manager
import matplotlib.pyplot as plt
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from src.schemas import LiteratureListItem
from src.tools.literature_list import normalize_paper


class AnnualPublicationFishboneInput(BaseModel):
    literature_list: list[dict[str, Any]] = Field(description="Step-5 literature display list")
    output_path: str = Field(default="output/annual_publication_fishbone.png")
    title: str = Field(default="年度文献发表脉络", min_length=1)
    stream_delay_seconds: float = Field(default=0.0, ge=0.0, le=10.0)


class AnnualPublicationFishboneTool(BaseTool):
    name: str = "draw_annual_publication_fishbone"
    description: str = (
        "Draw a dark annual literature timeline fishbone. Papers are inserted by sequence number, "
        "and each streamed event is emitted after the PNG is atomically refreshed."
    )
    args_schema: type[BaseModel] = AnnualPublicationFishboneInput

    def _run(
        self,
        literature_list: list[dict[str, Any]],
        output_path: str = "output/annual_publication_fishbone.png",
        title: str = "年度文献发表脉络",
        stream_delay_seconds: float = 0.0,
    ) -> dict[str, Any]:
        final_event: dict[str, Any] | None = None
        for event in self._stream_events(literature_list, output_path, title, stream_delay_seconds):
            final_event = event
        return final_event or empty_result(output_path)

    def stream(
        self,
        input: dict[str, Any],
        config: RunnableConfig | None = None,
        **kwargs: Any,
    ) -> Iterator[dict[str, Any]]:
        del config, kwargs
        values = AnnualPublicationFishboneInput.model_validate(input)
        yield from self._stream_events(
            values.literature_list,
            values.output_path,
            values.title,
            values.stream_delay_seconds,
        )

    def _stream_events(
        self,
        literature_list: list[dict[str, Any]],
        output_path: str,
        title: str,
        stream_delay_seconds: float,
    ) -> Iterator[dict[str, Any]]:
        items = normalize_literature_list(literature_list)
        path = Path(output_path).expanduser().resolve()
        if path.suffix.lower() != ".png":
            raise ValueError("output_path must use the .png extension")
        path.parent.mkdir(parents=True, exist_ok=True)

        for inserted_count, item in enumerate(items, start=1):
            render_annual_fishbone(items, inserted_count, path, title)
            yield {
                "event": "paper_inserted",
                "sequence": item.sequence,
                "year": item.year,
                "inserted": inserted_count,
                "total": len(items),
                "progress": inserted_count / len(items) if items else 1.0,
                "path": str(path),
            }
            if stream_delay_seconds:
                time.sleep(stream_delay_seconds)

        if not items:
            render_annual_fishbone([], 0, path, title)
        yield {
            "event": "completed",
            "inserted": len(items),
            "total": len(items),
            "progress": 1.0,
            "path": str(path),
            "years": sorted({item.year for item in items if item.year is not None}),
        }


def normalize_literature_list(values: list[dict[str, Any]]) -> list[LiteratureListItem]:
    items: list[LiteratureListItem] = []
    for index, value in enumerate(values, start=1):
        item = normalize_paper(value, index)
        raw_sequence = value.get("序号", value.get("sequence", index))
        try:
            item.sequence = int(raw_sequence)
        except (TypeError, ValueError):
            item.sequence = index
        items.append(item)
    return sorted(items, key=lambda item: item.sequence)


def render_annual_fishbone(
    all_items: list[LiteratureListItem],
    inserted_count: int,
    output_path: Path,
    title: str,
) -> None:
    font_name = configure_chinese_font()
    positions, ticks = calculate_positions(all_items)
    visible = all_items[:inserted_count]
    width = max(12.0, 2.3 * max(len(ticks), 3))
    fig, ax = plt.subplots(figsize=(width, 7.2), dpi=160)
    background = "#232428"
    fig.patch.set_facecolor(background)
    ax.set_facecolor(background)

    x_values = list(positions.values()) or [0.0]
    x_min, x_max = min(x_values) - 0.5, max(x_values) + 0.5
    ax.set_xlim(x_min, x_max)
    ax.set_ylim(-0.18, 1.08)
    ax.spines[["top", "left", "right"]].set_visible(False)
    ax.spines["bottom"].set_color("#777A84")
    ax.spines["bottom"].set_linewidth(1.1)
    ax.tick_params(axis="x", colors="#AEB2BE", labelsize=9, pad=8)
    ax.tick_params(axis="y", left=False, labelleft=False)
    ax.set_xticks([position for position, _ in ticks], [label for _, label in ticks])
    ax.grid(axis="x", color="#3C3E45", linestyle=(0, (5, 7)), linewidth=0.8, alpha=0.7)

    ax.text(
        x_min,
        1.02,
        title,
        color="#F0F1F5",
        fontsize=18,
        fontweight="bold",
        ha="left",
        va="top",
        fontname=font_name,
    )
    ax.text(
        x_min,
        0.94,
        f"按排序序号实时插入 · 已展示 {inserted_count}/{len(all_items)} 篇",
        color="#AEB2BE",
        fontsize=10,
        ha="left",
        va="top",
        fontname=font_name,
    )

    for item in visible:
        x = positions[item.sequence]
        lane = (item.sequence - 1) % 4
        y = 0.24 + lane * 0.13
        bubble_size = 430 if item.sequence <= 9 else 350
        ax.vlines(x, 0, y, color="#555862", linestyle=(0, (4, 5)), linewidth=1.0, zorder=1)
        ax.scatter(
            [x],
            [y],
            s=bubble_size,
            color="#5A5D68",
            edgecolors="#777B87",
            linewidths=1.0,
            zorder=3,
        )
        ax.text(
            x,
            y,
            str(item.sequence),
            color="#F5F6F8",
            fontsize=9,
            fontweight="bold",
            ha="center",
            va="center",
            zorder=4,
        )

    ax.text(
        x_min,
        -0.13,
        "节点序号与文献列表一一对应",
        color="#858A96",
        fontsize=8.5,
        ha="left",
        va="top",
        fontname=font_name,
    )
    fig.tight_layout(pad=1.5)
    atomic_save(fig, output_path)
    plt.close(fig)


def calculate_positions(items: list[LiteratureListItem]) -> tuple[dict[int, float], list[tuple[float, str]]]:
    known_years = sorted({item.year for item in items if item.year is not None})
    labels = [str(year) for year in known_years]
    if any(item.year is None for item in items):
        labels.append("未知")
    if not labels:
        labels = ["暂无年份"]
    label_index = {label: index for index, label in enumerate(labels)}
    grouped: dict[str, list[LiteratureListItem]] = {label: [] for label in labels}
    for item in items:
        label = str(item.year) if item.year is not None else "未知"
        grouped.setdefault(label, []).append(item)

    positions: dict[int, float] = {}
    for label, group in grouped.items():
        base = float(label_index.get(label, len(label_index)))
        for offset, item in enumerate(group, start=1):
            positions[item.sequence] = base - 0.36 + 0.72 * offset / (len(group) + 1)
    ticks = [(float(index), label) for index, label in enumerate(labels)]
    return positions, ticks


def configure_chinese_font() -> str | None:
    preferred = ("Noto Sans CJK SC", "Noto Sans CJK JP", "Microsoft YaHei", "SimHei", "Arial Unicode MS")
    installed = {font.name for font in font_manager.fontManager.ttflist}
    selected = next((name for name in preferred if name in installed), None)
    if selected:
        plt.rcParams["font.family"] = "sans-serif"
        plt.rcParams["font.sans-serif"] = [selected, "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    return selected


def atomic_save(fig: Any, output_path: Path) -> None:
    temporary_path = output_path.with_name(f".{output_path.stem}.tmp.png")
    fig.savefig(temporary_path, format="png", bbox_inches="tight", facecolor=fig.get_facecolor())
    os.replace(temporary_path, output_path)


def empty_result(output_path: str) -> dict[str, Any]:
    return {
        "event": "completed",
        "inserted": 0,
        "total": 0,
        "progress": 1.0,
        "path": str(Path(output_path).expanduser().resolve()),
        "years": [],
    }
