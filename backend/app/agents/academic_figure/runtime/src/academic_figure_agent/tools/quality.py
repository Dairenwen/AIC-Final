from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import fitz
from langchain_core.tools import BaseTool
from PIL import Image, ImageChops
from pydantic import BaseModel

from academic_figure_agent.schemas import FigureRequest, FigureSpec, QualityCheck, QualityReport


class QualityInspectionInput(BaseModel):
    spec: dict
    request: dict
    output_dir: str
    revision: int = 0


class QualityInspectionTool(BaseTool):
    name: str = "quality_inspection"
    description: str = "Inspect PNG, SVG, PDF, source code, dimensions, and visual occupancy."
    args_schema: type[BaseModel] = QualityInspectionInput

    def _run(self, spec: dict, request: dict, output_dir: str, revision: int = 0) -> dict:
        return inspect_artifacts(
            FigureSpec.model_validate(spec),
            FigureRequest.model_validate(request),
            Path(output_dir),
            revision,
        ).model_dump(mode="json")


def inspect_artifacts(
    spec: FigureSpec,
    request: FigureRequest,
    output_dir: Path,
    revision: int = 0,
) -> QualityReport:
    checks: list[QualityCheck] = []
    generated_files: list[str] = []
    for language in request.languages:
        for extension in request.export_formats:
            path = output_dir / f"figure_{language}.{extension}"
            if not path.is_file() or path.stat().st_size == 0:
                checks.append(
                    QualityCheck(
                        name=f"{language}_{extension}",
                        status="failed",
                        message="Missing or empty",
                    )
                )
                continue
            generated_files.append(str(path))
            try:
                if extension == "png":
                    checks.extend(_inspect_png(path, spec))
                elif extension == "svg":
                    ET.parse(path)
                    checks.append(
                        QualityCheck(
                            name=f"svg_parse_{language}",
                            status="passed",
                            message="Valid XML",
                        )
                    )
                elif extension == "pdf":
                    with fitz.open(path) as document:
                        valid = document.page_count == 1
                    checks.append(
                        QualityCheck(
                            name=f"pdf_pages_{language}",
                            status="passed" if valid else "failed",
                            message="Single-page PDF" if valid else "PDF must contain exactly one page",
                        )
                    )
            except Exception as exc:  # noqa: BLE001
                checks.append(
                    QualityCheck(
                        name=f"parse_{language}_{extension}",
                        status="failed",
                        message=str(exc),
                    )
                )

    for code_format in request.code_formats:
        filenames = {
            "python": "figure.py",
            "r": "figure.R",
            "latex": "figure.tex",
            "mermaid": "figure.mmd",
        }
        filename = filenames[code_format]
        path = output_dir / filename
        status = "passed" if path.is_file() and path.stat().st_size > 0 else "failed"
        checks.append(QualityCheck(name=f"code_{code_format}", status=status, message=filename))
        if status == "passed":
            generated_files.append(str(path))

    if spec.dpi < 300:
        checks.append(
            QualityCheck(
                name="publication_dpi",
                status="warning",
                message=f"DPI is {spec.dpi}; 300+ recommended",
            )
        )
    else:
        checks.append(QualityCheck(name="publication_dpi", status="passed", message=f"DPI is {spec.dpi}"))
    if not spec.palette:
        checks.append(QualityCheck(name="palette", status="warning", message="No explicit palette"))
    else:
        checks.append(QualityCheck(name="palette", status="passed", message="Explicit palette recorded"))

    passed = not any(check.status == "failed" for check in checks)
    report = QualityReport(passed=passed, checks=checks, generated_files=generated_files, revision=revision)
    (output_dir / "quality_report.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
    return report


def _inspect_png(path: Path, spec: FigureSpec) -> list[QualityCheck]:
    with Image.open(path) as image:
        width, height = image.size
        rgb = image.convert("RGB")
        background = Image.new("RGB", rgb.size, "white")
        bbox = ImageChops.difference(rgb, background).getbbox()
        occupancy = 0.0 if bbox is None else ((bbox[2] - bbox[0]) * (bbox[3] - bbox[1])) / (width * height)
    expected_width = int(spec.width_inches * spec.dpi * 0.75)
    checks = [
        QualityCheck(
            name=f"png_dimensions_{path.stem}",
            status="passed" if width >= expected_width else "warning",
            message=f"{width}x{height}px",
        ),
        QualityCheck(
            name=f"nonblank_{path.stem}",
            status="passed" if occupancy >= 0.03 else "failed",
            message=f"Non-white bounding-box occupancy: {occupancy:.3f}",
        ),
    ]
    return checks
