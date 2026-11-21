from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field, field_validator


class FishboneDiagramInput(BaseModel):
    problem: str = Field(min_length=1, description="Problem or effect displayed at the fish head")
    causes: dict[str, list[str]] = Field(min_length=1, description="Major cause categories and detailed causes")
    output_path: str = Field(default="output/fishbone.png")
    title: str | None = None

    @field_validator("causes")
    @classmethod
    def validate_categories(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        if any(not name.strip() for name in value):
            raise ValueError("cause category names cannot be blank")
        return value


class FishboneDiagramTool(BaseTool):
    name: str = "draw_fishbone_diagram"
    description: str = "Draw an Ishikawa cause-and-effect fishbone diagram and save it as a PNG file."
    args_schema: type[BaseModel] = FishboneDiagramInput

    def _run(
        self,
        problem: str,
        causes: dict[str, list[str]],
        output_path: str = "output/fishbone.png",
        title: str | None = None,
    ) -> dict[str, Any]:
        path = draw_fishbone(problem, causes, Path(output_path), title=title)
        return {
            "path": str(path),
            "format": "png",
            "problem": problem,
            "category_count": len(causes),
            "cause_count": sum(len(items) for items in causes.values()),
        }


def draw_fishbone(
    problem: str,
    causes: dict[str, list[str]],
    output_path: Path,
    *,
    title: str | None = None,
) -> Path:
    output_path = output_path.expanduser().resolve()
    if output_path.suffix.lower() != ".png":
        raise ValueError("output_path must use the .png extension")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    count = len(causes)
    fig, ax = plt.subplots(figsize=(max(12.0, count * 2.2), 7.5), dpi=160)
    ax.set_xlim(0, 12)
    ax.set_ylim(-5, 5)
    ax.axis("off")
    ax.annotate("", xy=(10, 0), xytext=(0.7, 0), arrowprops={"arrowstyle": "->", "lw": 2.4})
    ax.text(
        10.15,
        0,
        problem,
        ha="left",
        va="center",
        fontsize=12,
        fontweight="bold",
        bbox={"boxstyle": "round,pad=0.4", "facecolor": "#F4C95D", "edgecolor": "#333333"},
        wrap=True,
    )

    anchors = spaced_positions(count, 1.8, 8.9)
    for index, ((category, details), anchor_x) in enumerate(zip(causes.items(), anchors, strict=True)):
        direction = 1 if index % 2 == 0 else -1
        outer_x, outer_y = anchor_x - 1.25, 3.5 * direction
        ax.plot([outer_x, anchor_x], [outer_y, 0], color="#315A73", linewidth=2)
        ax.text(
            outer_x,
            outer_y + 0.3 * direction,
            category,
            ha="center",
            va="center",
            fontsize=11,
            fontweight="bold",
            color="#173B4F",
        )
        draw_details(ax, details, outer_x, outer_y, anchor_x, direction)

    if title:
        ax.set_title(title, fontsize=16, fontweight="bold", pad=16)
    fig.tight_layout()
    fig.savefig(output_path, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return output_path


def spaced_positions(count: int, start: float, end: float) -> list[float]:
    if count == 1:
        return [(start + end) / 2]
    step = (end - start) / (count - 1)
    return [start + index * step for index in range(count)]


def draw_details(ax: Any, details: list[str], x0: float, y0: float, x1: float, direction: int) -> None:
    for index, detail in enumerate(details):
        ratio = (index + 1) / (len(details) + 1)
        x = x0 + (x1 - x0) * ratio
        y = y0 * (1 - ratio)
        ax.plot([x - 0.7, x], [y + 0.65 * direction, y], color="#6A8797", linewidth=1.2)
        ax.text(x - 0.75, y + 0.65 * direction, detail, ha="right", va="center", fontsize=8.5, wrap=True)
