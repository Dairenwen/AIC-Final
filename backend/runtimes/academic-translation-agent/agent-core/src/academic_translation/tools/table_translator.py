from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

import fitz
from PIL import Image

from academic_translation.tools.figure_translator import page_cjk_font_resource


# Deterministic translations avoid altering model names, abbreviations, values, and formulae.
# User glossary entries take precedence and are used when a complete table label matches.
_ACADEMIC_TABLE_TERMS = {
    "model": "模型",
    "baseline": "基线",
    "w/o": "无",
    "broad": "粗粒度",
    "fine": "细粒度",
    "method": "方法",
    "methods": "方法",
    "dataset": "数据集",
    "datasets": "数据集",
    "metric": "指标",
    "metrics": "指标",
    "accuracy": "准确率",
    "rate": "比率",
    "precision": "精确率",
    "recall": "召回率",
    "loss": "损失",
    "average": "平均值",
    "mean": "均值",
    "standard deviation": "标准差",
    "training": "训练",
    "validation": "验证",
    "test": "测试",
    "original": "原始",
    "substructure": "子结构",
    "chromophore": "发色团",
    "absorption": "吸收",
    "emission": "发射",
    "lifetime": "寿命",
}


def _expanded_table_regions(page: fitz.Page) -> list[fitz.Rect]:
    try:
        detected = page.find_tables().tables
    except Exception:
        return []
    regions: list[fitz.Rect] = []
    for table in detected:
        rect = fitz.Rect(table.bbox)
        # PDF table detectors frequently omit header rows directly above the grid.
        rect.y0 = max(page.rect.y0, rect.y0 - 100)
        rect.x0 = max(page.rect.x0, rect.x0 - 2)
        rect.x1 = min(page.rect.x1, rect.x1 + 2)
        if not any(rect.intersects(existing) for existing in regions):
            regions.append(rect)
    return regions


def _plain_white_background(page: fitz.Page, rect: fitz.Rect) -> bool:
    """Only redact text where the small surrounding area is a flat white table cell."""
    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
    image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    sx, sy = pix.width / page.rect.width, pix.height / page.rect.height
    x0, y0 = max(0, int(rect.x0 * sx)), max(0, int(rect.y0 * sy))
    x1, y1 = min(image.width, int(rect.x1 * sx)), min(image.height, int(rect.y1 * sy))
    if x1 <= x0 or y1 <= y0:
        return False
    # Sample narrow strips beside the word. This avoids both the source glyphs and
    # horizontal table rules that may touch the word's top or bottom edge.
    samples = []
    if x0 >= 4:
        samples.extend(image.crop((x0 - 4, y0 + 1, x0 - 1, max(y0 + 2, y1 - 1))).getdata())
    if x1 + 4 <= image.width:
        samples.extend(image.crop((x1 + 1, y0 + 1, x1 + 4, max(y0 + 2, y1 - 1))).getdata())
    if not samples:
        return False
    means = [sum(pixel[index] for pixel in samples) / len(samples) for index in range(3)]
    return min(means) > 235


def _translation_for(text: str, glossary: Mapping[str, str]) -> str | None:
    for source, target in glossary.items():
        if text.casefold() == source.casefold() and target.strip():
            return target.strip()
    return _ACADEMIC_TABLE_TERMS.get(text.casefold())


def translate_pdf_tables(input_pdf: Path, output_pdf: Path, manifest_path: Path, target_lang: str, glossary: Mapping[str, str] | None = None) -> bool:
    """Translate recognised vector-table headers in place while preserving the table grid.

    Only exact, safe academic labels or user-supplied terms are changed. Numbers,
    method names, abbreviations, formulae and uncertain layouts are retained verbatim.
    """
    document = fitz.open(input_pdf)
    records: list[dict] = []
    changed = False
    try:
        if not target_lang.lower().startswith("zh"):
            records.append({"status": "unsupported_target"})
        else:
            for page_number, page in enumerate(document, start=1):
                font_resource = page_cjk_font_resource(page)
                if font_resource is None:
                    records.append({"page": page_number, "status": "cjk_font_not_available"})
                    continue
                font_name, font_file = font_resource
                regions = _expanded_table_regions(page)
                if not regions:
                    continue
                candidates: list[dict] = []
                seen: set[tuple[float, float, float, float]] = set()
                for word in page.get_text("words"):
                    rect = fitz.Rect(word[:4])
                    source = word[4].strip()
                    if not source or not any(rect.intersects(region) for region in regions):
                        continue
                    target = _translation_for(source, glossary or {})
                    record = {"source": source, "box": [round(value, 2) for value in rect], "target": target}
                    key = tuple(round(value, 2) for value in rect)
                    entry = record
                    entry["page"] = page_number
                    if target is None:
                        record["status"] = "original_preserved_unrecognised_or_identifier"
                    elif key in seen:
                        record["status"] = "original_preserved_duplicate"
                    elif not _plain_white_background(page, rect):
                        record["status"] = "original_preserved_nonwhite_background"
                    else:
                        record["status"] = "pending"
                        candidates.append({"rect": rect, "target": target, "fontsize": max(6.0, rect.height * 0.9), "record": entry})
                        seen.add(key)
                    records.append(entry)
                if not candidates:
                    continue
                for item in candidates:
                    page.add_redact_annot(item["rect"], fill=(1, 1, 1))
                page.apply_redactions(images=0, graphics=0, text=0)
                if font_file:
                    page.insert_font(fontname=font_name, fontfile=font_file)
                for item in candidates:
                    baseline = item["rect"].y1 - max(0.3, item["fontsize"] * 0.08)
                    page.insert_text((item["rect"].x0, baseline), item["target"], fontname=font_name, fontsize=item["fontsize"], color=(0, 0, 0))
                    item["record"]["status"] = "translated_in_place"
                    changed = True
        # Keep the page content byte-for-byte semantic while de-duplicating
        # repeated embedded fonts inherited from low-memory PDF chunks.
        document.save(output_pdf, garbage=4, deflate=True, use_objstms=1)
    finally:
        document.close()
    manifest_path.write_text(json.dumps({"input": str(input_pdf), "output": str(output_pdf), "changed": changed, "entries": records}, ensure_ascii=False, indent=2), encoding="utf-8")
    return changed
