from __future__ import annotations

import math
import os
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
from matplotlib import font_manager
import numpy as np
import pandas as pd
import seaborn as sns
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch
from PIL import Image

from academic_figure_agent.schemas import FigureSpec
from config.constants import DEFAULT_PALETTE


def render_from_files(
    spec_path: Path,
    data_path: Path | None,
    output_dir: Path,
    formats: list[str],
    languages: list[str],
) -> dict[str, dict[str, str]]:
    spec = FigureSpec.model_validate_json(spec_path.read_text(encoding="utf-8"))
    frame = pd.read_csv(data_path) if data_path and data_path.exists() else pd.DataFrame()
    output_dir.mkdir(parents=True, exist_ok=True)
    artifacts: dict[str, dict[str, str]] = {}
    for language in languages:
        figure = _build_figure(spec, frame, language)
        artifacts[language] = {}
        for extension in formats:
            path = output_dir / f"figure_{language}.{extension}"
            figure.savefig(path, dpi=spec.dpi, bbox_inches="tight", facecolor="white")
            artifacts[language][extension] = str(path)
        plt.close(figure)
    return artifacts


def _build_figure(spec: FigureSpec, frame: pd.DataFrame, language: str):
    _configure_style(spec)
    figure, axis = plt.subplots(figsize=(spec.width_inches, spec.height_inches), constrained_layout=True)
    if spec.figure_type == "flowchart":
        _draw_flowchart(axis, spec, language)
    elif spec.figure_type == "image_panel":
        plt.close(figure)
        figure = _draw_image_panel(spec, language)
        return figure
    else:
        _validate_columns(spec, frame)
        _draw_data_chart(axis, spec, frame)
        axis.set_xlabel(spec.xlabel.get(language) or (spec.x or ""))
        axis.set_ylabel(spec.ylabel.get(language) or (spec.y or ""))
        if spec.grid:
            axis.grid(axis="y", color="#D9DEE3", linewidth=0.6, alpha=0.7)
            axis.set_axisbelow(True)
        sns.despine(ax=axis)
    axis.set_title(spec.title.get(language), fontsize=11, pad=10)
    return figure


def _draw_data_chart(axis, spec: FigureSpec, frame: pd.DataFrame) -> None:
    palette = spec.palette or DEFAULT_PALETTE
    if spec.figure_type == "line":
        _draw_line(axis, spec, frame, palette)
    elif spec.figure_type == "bar":
        sns.barplot(data=frame, x=spec.x, y=spec.y, hue=spec.series, palette=palette, ax=axis, errorbar=None)
    elif spec.figure_type == "scatter":
        sns.scatterplot(data=frame, x=spec.x, y=spec.y, hue=spec.series, palette=palette, ax=axis, s=42)
    elif spec.figure_type == "box":
        sns.boxplot(data=frame, x=spec.x, y=spec.y, hue=spec.series, palette=palette, ax=axis)
    elif spec.figure_type == "heatmap":
        _draw_heatmap(axis, spec, frame)
    else:
        raise ValueError(f"Unsupported figure type: {spec.figure_type}")
    if not spec.legend and axis.get_legend() is not None:
        axis.get_legend().remove()
    elif axis.get_legend() is not None:
        axis.legend(frameon=False, fontsize=8, title_fontsize=8)


def _draw_line(axis, spec: FigureSpec, frame: pd.DataFrame, palette: list[str]) -> None:
    groups = frame.groupby(spec.series, dropna=False) if spec.series else [(None, frame)]
    for index, (name, group) in enumerate(groups):
        ordered = group.sort_values(spec.x)
        color = palette[index % len(palette)]
        axis.plot(
            ordered[spec.x],
            ordered[spec.y],
            marker="o",
            linewidth=1.8,
            markersize=4.5,
            color=color,
            label=str(name) if name is not None else None,
        )
        if spec.error and spec.error in ordered.columns:
            x_values = pd.to_numeric(ordered[spec.x], errors="coerce")
            y_values = pd.to_numeric(ordered[spec.y], errors="coerce")
            errors = pd.to_numeric(ordered[spec.error], errors="coerce")
            if not x_values.isna().any():
                axis.fill_between(x_values, y_values - errors, y_values + errors, color=color, alpha=0.16)
    if spec.series:
        axis.legend(frameon=False, fontsize=8)


def _draw_heatmap(axis, spec: FigureSpec, frame: pd.DataFrame) -> None:
    if spec.series and spec.x and spec.y:
        matrix = frame.pivot_table(index=spec.series, columns=spec.x, values=spec.y, aggfunc="mean")
    else:
        matrix = frame.select_dtypes(include="number").corr()
    sns.heatmap(
        matrix,
        cmap="viridis",
        annot=matrix.size <= 100,
        fmt=".2g",
        ax=axis,
        cbar_kws={"shrink": 0.8},
    )


def _draw_flowchart(axis, spec: FigureSpec, language: str) -> None:
    axis.set_xlim(0, 1)
    axis.set_ylim(0, 1)
    axis.axis("off")
    count = max(1, len(spec.nodes))
    columns = min(4, count)
    rows = math.ceil(count / columns)
    positions: dict[str, tuple[float, float]] = {}
    for index, node in enumerate(spec.nodes):
        row, column = divmod(index, columns)
        x = (column + 0.5) / columns
        y = 1 - (row + 0.5) / rows
        positions[node.id] = (x, y)
    for edge in spec.edges:
        if edge.source not in positions or edge.target not in positions:
            continue
        start = positions[edge.source]
        end = positions[edge.target]
        arrow = FancyArrowPatch(
            start,
            end,
            arrowstyle="-|>",
            mutation_scale=12,
            linewidth=1.2,
            color="#6B7280",
        )
        axis.add_patch(arrow)
    box_width = min(0.19, 0.72 / columns)
    box_height = min(0.18, 0.55 / rows)
    for index, node in enumerate(spec.nodes):
        x, y = positions[node.id]
        color = (spec.palette or DEFAULT_PALETTE)[index % len(spec.palette or DEFAULT_PALETTE)]
        box = FancyBboxPatch(
            (x - box_width / 2, y - box_height / 2),
            box_width,
            box_height,
            boxstyle="round,pad=0.015,rounding_size=0.018",
            linewidth=1.3,
            edgecolor=color,
            facecolor="white",
            zorder=2,
        )
        axis.add_patch(box)
        axis.text(x, y, node.label.get(language), ha="center", va="center", fontsize=8.5, wrap=True, zorder=3)


def _draw_image_panel(spec: FigureSpec, language: str):
    paths = [Path(path) for path in spec.image_paths if Path(path).is_file()]
    if not paths:
        raise ValueError("image_panel requires at least one valid sketch/image path")
    columns = min(3, len(paths))
    rows = math.ceil(len(paths) / columns)
    figure, axes = plt.subplots(
        rows,
        columns,
        figsize=(spec.width_inches, max(spec.height_inches, rows * 2.4)),
        squeeze=False,
        constrained_layout=True,
    )
    for index, axis in enumerate(axes.flat):
        axis.axis("off")
        if index >= len(paths):
            continue
        with Image.open(paths[index]) as image:
            axis.imshow(np.asarray(image.convert("RGB")))
        axis.set_title(f"({chr(97 + index)}) {paths[index].stem}", fontsize=8)
    figure.suptitle(spec.title.get(language), fontsize=11)
    return figure


def _validate_columns(spec: FigureSpec, frame: pd.DataFrame) -> None:
    if frame.empty:
        raise ValueError(f"{spec.figure_type} requires structured experiment data")
    required = [column for column in (spec.x, spec.y, spec.series, spec.error) if column]
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise ValueError(f"FigureSpec references missing columns: {missing}")
    if spec.figure_type != "heatmap" and (not spec.x or not spec.y):
        raise ValueError(f"{spec.figure_type} requires x and y columns")


def _configure_style(spec: FigureSpec) -> None:
    sns.set_theme(style="white", context="paper")
    font_name = _configure_cjk_font()
    plt.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": [font_name, "Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "DejaVu Sans"] if font_name else ["Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "DejaVu Sans"],
            "font.size": 9,
            "axes.titlesize": 11,
            "axes.labelsize": 9,
            "xtick.labelsize": 8,
            "ytick.labelsize": 8,
            "legend.fontsize": 8,
            "svg.fonttype": "none",
            "pdf.fonttype": 42,
            "axes.unicode_minus": False,
        }
    )


def _configure_cjk_font() -> str | None:
    """Register a real CJK font for Linux and Windows server rendering."""
    candidates: list[Path] = []
    configured = os.getenv("ACADEMIC_FIGURE_FONT_PATH", "").strip()
    if configured:
        candidates.append(Path(configured))
    candidates.extend(
        Path(path)
        for path in (
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
            r"C:\\Windows\\Fonts\\msyh.ttc",
            r"C:\\Windows\\Fonts\\simhei.ttf",
        )
    )
    for path in candidates:
        if not path.is_file():
            continue
        try:
            font_manager.fontManager.addfont(str(path))
            return font_manager.FontProperties(fname=str(path)).get_name()
        except (OSError, RuntimeError):
            continue
    return None
