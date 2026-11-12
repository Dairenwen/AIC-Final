from __future__ import annotations

import math
import os
import re
import shutil
import struct
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Protocol


class PageRenderError(RuntimeError):
    def __init__(self, message: str, code: str = "VISION_RENDER_FAILED") -> None:
        super().__init__(message)
        self.code = code


class PageRenderer(Protocol):
    def render_pages(self, pdf_path: str | Path, page_numbers: list[int]) -> dict[int, bytes]: ...


def _resolve_executable(
    executable: str | Path,
    *,
    label: str,
    required: bool,
) -> str | None:
    raw = os.fspath(executable)
    candidate = Path(raw).expanduser()
    explicit_path = candidate.is_absolute() or candidate.parent != Path(".")
    if explicit_path:
        if candidate.is_file():
            return str(candidate.resolve())
        if required:
            raise PageRenderError(
                f"{label} executable does not exist: {candidate}",
                code="VISION_TOOL_UNAVAILABLE",
            )
        return None
    resolved = shutil.which(raw)
    if resolved is None and required:
        raise PageRenderError(
            f"{label} is required; install it on PATH or provide an explicit executable path",
            code="VISION_TOOL_UNAVAILABLE",
        )
    return resolved


def _default_image_magick() -> str | None:
    resolved = _resolve_executable("magick", label="ImageMagick", required=False)
    if resolved is not None or os.name == "nt":
        return resolved
    return _resolve_executable("convert", label="ImageMagick", required=False)


class PopplerPageRenderer:
    """Render selected pages and, when possible, crop around supplied target labels."""

    def __init__(
        self,
        executable: str | Path = "pdftoppm",
        *,
        pdftotext_executable: str | Path | None = None,
        image_magick_executable: str | Path | None = None,
        dpi: int = 144,
        timeout_seconds: float = 30.0,
    ) -> None:
        if dpi < 72 or timeout_seconds <= 0:
            raise ValueError("renderer limits must be positive")
        resolved = _resolve_executable(executable, label="pdftoppm", required=True)
        assert resolved is not None
        if os.name == "nt" and Path(resolved).suffix.lower() in {".bat", ".cmd"}:
            raise PageRenderError(
                "pdftoppm resolved to a Windows batch wrapper; provide the actual "
                "pdftoppm.exe path",
                code="VISION_TOOL_UNAVAILABLE",
            )
        self.executable = resolved
        self.pdftotext = (
            _resolve_executable(
                pdftotext_executable,
                label="pdftotext",
                required=True,
            )
            if pdftotext_executable is not None
            else _resolve_executable("pdftotext", label="pdftotext", required=False)
        )
        self.image_magick = (
            _resolve_executable(
                image_magick_executable,
                label="ImageMagick",
                required=True,
            )
            if image_magick_executable is not None
            else _default_image_magick()
        )
        self.dpi = dpi
        self.timeout_seconds = timeout_seconds

    def render_pages(self, pdf_path: str | Path, page_numbers: list[int]) -> dict[int, bytes]:
        path = Path(pdf_path)
        if not path.is_file():
            raise PageRenderError("PDF path does not exist")
        pages = sorted(set(page_numbers))
        if not pages or any(page < 1 for page in pages):
            raise PageRenderError("at least one positive page number is required")

        rendered: dict[int, bytes] = {}
        with tempfile.TemporaryDirectory(prefix="paper-reading-pages-") as directory:
            root = Path(directory)
            for page in pages:
                prefix = root / f"page-{page:04d}"
                command = [
                    self.executable,
                    "-f",
                    str(page),
                    "-l",
                    str(page),
                    "-r",
                    str(self.dpi),
                    "-png",
                    "-singlefile",
                    str(path),
                    str(prefix),
                ]
                try:
                    completed = subprocess.run(
                        command,
                        check=False,
                        capture_output=True,
                        timeout=self.timeout_seconds,
                    )
                except (OSError, subprocess.TimeoutExpired) as exc:
                    raise PageRenderError(f"failed to render PDF page {page}") from exc
                output_path = prefix.with_suffix(".png")
                if completed.returncode != 0 or not output_path.is_file():
                    raise PageRenderError(f"pdftoppm could not render PDF page {page}")
                image = output_path.read_bytes()
                if not image.startswith(b"\x89PNG\r\n\x1a\n"):
                    raise PageRenderError(f"rendered page {page} is not a PNG image")
                rendered[page] = image
        return rendered

    def render_target_pages(
        self,
        pdf_path: str | Path,
        targets_by_page: dict[int, list[str]],
    ) -> dict[int, bytes]:
        """Return target-focused page images, with full-page fallback on uncertain locations."""

        return self._render_focused_pages(pdf_path, targets_by_page, {})

    def render_target_regions(
        self,
        pdf_path: str | Path,
        targets_by_page: dict[int, list[str]],
        regions_by_page: dict[int, list[tuple[float, float, float, float]]],
    ) -> dict[int, bytes]:
        """Prefer exact PDF-coordinate target boxes, with label and full-page fallback."""

        return self._render_focused_pages(
            pdf_path,
            targets_by_page,
            regions_by_page,
        )

    def _render_focused_pages(
        self,
        pdf_path: str | Path,
        targets_by_page: dict[int, list[str]],
        regions_by_page: dict[int, list[tuple[float, float, float, float]]],
    ) -> dict[int, bytes]:
        pages = sorted(targets_by_page)
        rendered = self.render_pages(pdf_path, pages)
        if self.pdftotext is None or self.image_magick is None:
            return rendered
        path = Path(pdf_path)
        with tempfile.TemporaryDirectory(prefix="paper-reading-targets-") as directory:
            root = Path(directory)
            for page in pages:
                region = self._find_explicit_target_region(
                    path,
                    page,
                    regions_by_page.get(page, []),
                    root,
                )
                if region is None:
                    region = self._find_target_region(
                        path,
                        page,
                        targets_by_page[page],
                        root,
                    )
                if region is None:
                    continue
                page_width, page_height, left, top, right, bottom = region
                image_width, image_height = self._png_size(rendered[page])
                left_px = max(0, round(left / page_width * image_width))
                right_px = min(image_width, round(right / page_width * image_width))
                top_px = max(0, round(top / page_height * image_height))
                bottom_px = min(image_height, round(bottom / page_height * image_height))
                crop_width = right_px - left_px
                crop_height = bottom_px - top_px
                if (
                    crop_width < 32
                    or crop_height < 32
                    or (
                        crop_width >= image_width * 0.9
                        and crop_height >= image_height * 0.9
                    )
                ):
                    continue
                source = root / f"page-{page:04d}.png"
                crop = root / f"crop-{page:04d}.png"
                overview = root / f"overview-{page:04d}.png"
                target = root / f"target-{page:04d}.png"
                source.write_bytes(rendered[page])
                command = [
                    self.image_magick,
                    str(source),
                    "-crop",
                    f"{crop_width}x{crop_height}+{left_px}+{top_px}",
                    "+repage",
                    str(crop),
                ]
                try:
                    completed = subprocess.run(
                        command,
                        check=False,
                        capture_output=True,
                        timeout=self.timeout_seconds,
                    )
                except (OSError, subprocess.TimeoutExpired):
                    continue
                if completed.returncode != 0 or not crop.is_file():
                    continue
                overview_command = [
                    self.image_magick,
                    str(source),
                    "-resize",
                    "35%",
                    str(overview),
                ]
                combine_command = [
                    self.image_magick,
                    str(crop),
                    str(overview),
                    "-background",
                    "white",
                    "-gravity",
                    "center",
                    "-append",
                    str(target),
                ]
                try:
                    overview_result = subprocess.run(
                        overview_command,
                        check=False,
                        capture_output=True,
                        timeout=self.timeout_seconds,
                    )
                    combine_result = subprocess.run(
                        combine_command,
                        check=False,
                        capture_output=True,
                        timeout=self.timeout_seconds,
                    )
                except (OSError, subprocess.TimeoutExpired):
                    continue
                if (
                    overview_result.returncode == 0
                    and combine_result.returncode == 0
                    and target.is_file()
                ):
                    focused_with_context = target.read_bytes()
                    if focused_with_context.startswith(b"\x89PNG\r\n\x1a\n"):
                        rendered[page] = focused_with_context
        return rendered

    def _find_explicit_target_region(
        self,
        pdf_path: Path,
        page_number: int,
        regions: list[tuple[float, float, float, float]],
        directory: Path,
    ) -> tuple[float, float, float, float, float, float] | None:
        if not regions:
            return None
        page_content = self._page_words(pdf_path, page_number, directory)
        if page_content is None:
            return None
        width, height, _words = page_content
        valid = [
            (x0, y0, x1, y1)
            for x0, y0, x1, y1 in regions
            if all(math.isfinite(value) for value in (x0, y0, x1, y1))
            and 0 <= x0 < x1 <= width + 1
            and 0 <= y0 < y1 <= height + 1
        ]
        if not valid:
            return None
        left = max(0.0, min(item[0] for item in valid) - width * 0.02)
        top = max(0.0, min(item[1] for item in valid) - height * 0.08)
        right = min(width, max(item[2] for item in valid) + width * 0.02)
        bottom = min(height, max(item[3] for item in valid) + height * 0.04)
        return width, height, left, top, right, bottom

    def _find_target_region(
        self,
        pdf_path: Path,
        page_number: int,
        labels: list[str],
        directory: Path,
    ) -> tuple[float, float, float, float, float, float] | None:
        page_content = self._page_words(pdf_path, page_number, directory)
        if page_content is None:
            return None
        width, height, words = page_content
        regions: list[tuple[float, float]] = []
        for label in labels:
            match = re.search(r"\b(Equation|Figure|Table)\s+(\d+[A-Za-z]?)\b", label, re.I)
            if match is None:
                continue
            element_type, number = match.group(1).upper(), match.group(2).lower()
            anchor = self._find_label_anchor(words, element_type, number)
            if anchor is None:
                continue
            y_min, y_max = anchor
            if element_type == "FIGURE":
                regions.append((y_min - height * 0.45, y_max + height * 0.10))
            elif element_type == "TABLE":
                regions.append((y_min - height * 0.06, y_max + height * 0.45))
            else:
                regions.append((y_min - height * 0.10, y_max + height * 0.10))
        if not regions:
            return None
        top = max(0.0, min(item[0] for item in regions))
        bottom = min(height, max(item[1] for item in regions))
        return width, height, 0.0, top, width, bottom

    def _page_words(
        self,
        pdf_path: Path,
        page_number: int,
        directory: Path,
    ) -> tuple[float, float, list[tuple[str, float, float]]] | None:
        assert self.pdftotext is not None
        output = directory / f"words-{page_number:04d}.xml"
        try:
            completed = subprocess.run(
                [
                    self.pdftotext,
                    "-f",
                    str(page_number),
                    "-l",
                    str(page_number),
                    "-bbox",
                    "-enc",
                    "UTF-8",
                    str(pdf_path),
                    str(output),
                ],
                check=False,
                capture_output=True,
                timeout=self.timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if completed.returncode != 0 or not output.is_file():
            return None
        try:
            root = ET.parse(output).getroot()
            page = next(iter(root.findall(".//{*}page")))
            width = float(page.attrib["width"])
            height = float(page.attrib["height"])
            words = [
                (
                    (word.text or "").strip(),
                    float(word.attrib["yMin"]),
                    float(word.attrib["yMax"]),
                )
                for word in page.findall(".//{*}word")
            ]
        except (ET.ParseError, KeyError, StopIteration, TypeError, ValueError):
            return None
        return width, height, words

    @staticmethod
    def _find_label_anchor(
        words: list[tuple[str, float, float]],
        element_type: str,
        number: str,
    ) -> tuple[float, float] | None:
        def clean(value: str) -> str:
            return value.strip().strip(":.,;()[]").lower()

        if element_type == "EQUATION":
            expected = f"({number})"
            for text, y_min, y_max in words:
                if text.lower() == expected:
                    return y_min, y_max
            return None
        expected_types = {element_type.lower()}
        if element_type == "FIGURE":
            expected_types.add("fig")
        for index, (text, y_min, y_max) in enumerate(words[:-1]):
            next_text, next_y_min, next_y_max = words[index + 1]
            if clean(text) in expected_types and clean(next_text) == number:
                return min(y_min, next_y_min), max(y_max, next_y_max)
        return None

    @staticmethod
    def _png_size(image: bytes) -> tuple[int, int]:
        if not image.startswith(b"\x89PNG\r\n\x1a\n") or len(image) < 24:
            raise PageRenderError("rendered image is not a valid PNG")
        return struct.unpack(">II", image[16:24])
