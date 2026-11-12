from __future__ import annotations

import io
import json
import re
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Mapping

import fitz
from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageStat

from academic_translation.llm.ollama import TextGenerator


_LABEL = re.compile(r"^(?:\([A-Za-z]\)\s*)?[A-Za-z][A-Za-z0-9 ,.'()\-]{0,180}$")
_CJK_FONTS = (
    Path.home() / ".cache/babeldoc/fonts/SourceHanSerifCN-Regular.ttf",
    Path("/System/Library/Fonts/PingFang.ttc"),
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    Path("/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc"),
)
_STANDARD_FIGURE_TERMS = {
    "molecular": "分子",
    "overfitting": "过拟合",
    "sharing": "共享",
    "graph": "图",
    "quantified": "量化",
    "property": "属性",
    "quantified property": "量化属性",
    "substructure": "子结构",
    "core substructure": "核心子结构",
    "core substructures": "核心子结构",
    "are different": "不同",
    "different chemical reactions.": "不同的化学反应。",
    "the substructure.": "该子结构。",
    "causal": "因果",
    "dependency": "依赖关系",
    "causal dependency": "因果依赖",
    "noise injection": "噪声注入",
    "input": "输入",
    "encoder": "编码器",
    "upper": "上界",
    "lower": "下界",
    "upper bound": "上界",
    "lower bound": "下界",
    "original": "原始",
    "prediction target": "预测目标",
    "components": "组件",
    "tasks": "任务",
    "sequence": "序列",
    "pipeline": "流程",
    "metrics": "指标",
    "feature": "特征",
    "features": "特征",
    "interaction": "相互作用",
    "interactions": "相互作用",
    "encoder": "编码器",
    "encoders": "编码器",
    "alignment": "对齐",
    "alignments": "对齐",
    "backbone": "骨干网络",
    "backbones": "骨干网络",
    "prompt": "提示词",
    "prompts": "提示词",
    "regression": "回归",
    "classification": "分类",
    "self attention": "自注意力",
    "cross attention": "交叉注意力",
}


def cjk_font_path() -> Path | None:
    """Return an available local CJK font without introducing another model or asset."""
    return next((font_path for font_path in _CJK_FONTS if font_path.exists()), None)


def page_cjk_font_resource(page: fitz.Page) -> tuple[str, str | None] | None:
    """Reuse PDFMathTranslate's embedded Chinese font when present.

    Adding a new 14 MB CJK program for a few overlay words defeats lossless PDF
    compaction.  Translated PDFs already expose Source Han as ``noto`` on each
    page, so reuse that resource; standalone PDFs fall back to the local font.
    """
    for font in page.get_fonts(full=True):
        if "source han" in font[3].casefold() or "noto" in font[3].casefold():
            return font[4], None
    if font_path := cjk_font_path():
        return "academic-cjk", str(font_path)
    return None


def _known_translation(text: str, glossary: Mapping[str, str]) -> str | None:
    stripped = text.strip()
    prefix = re.match(r"^[([{]+", stripped)
    suffix = re.search(r"[)\]}.,:;]+$", stripped)
    core = stripped[len(prefix.group(0)) if prefix else 0 : len(stripped) - len(suffix.group(0)) if suffix else len(stripped)]
    normalised = core.casefold()
    prefixed = re.fullmatch(r"(\([a-z]\)\s+)(.+)", normalised)
    if prefixed and (translated := _STANDARD_FIGURE_TERMS.get(prefixed.group(2))):
        return f"{prefix.group(0) if prefix else ''}{prefixed.group(1)}{translated}{suffix.group(0) if suffix else ''}"
    for source, target in glossary.items():
        if normalised == source.strip().casefold() and target.strip():
            return f"{prefix.group(0) if prefix else ''}{target.strip()}{suffix.group(0) if suffix else ''}"
    if direct := _STANDARD_FIGURE_TERMS.get(normalised):
        return f"{prefix.group(0) if prefix else ''}{direct}{suffix.group(0) if suffix else ''}"
    group = re.search(r"\bgroup\s*(\d+)", normalised)
    if group:
        return f"{prefix.group(0) if prefix else ''}组{group.group(1)}{suffix.group(0) if suffix else ''}"
    if re.fullmatch(r"group[’']?", normalised):
        return f"{prefix.group(0) if prefix else ''}组1{suffix.group(0) if suffix else ''}"
    return None


def _is_safe_label_text(text: str, known_translation: str | None) -> tuple[bool, str | None]:
    """Accept only complete-looking, multi-word English labels.

    OCR fragments, one-token labels, abbreviations, and lower-case fragments are
    especially easy to mistranslate.  Keeping them untouched is safer than
    guessing inside an academic figure.
    """
    if known_translation:
        return True, None
    caption_text = re.sub(r"^\([A-Za-z]\)\s*", "", text)
    words = re.findall(r"[A-Za-z]+", caption_text)
    if len(words) < 2:
        return False, "skipped_single_token_or_abbreviation"
    if not (text[0].isupper() or re.match(r"^\([A-Za-z]\)\s+[A-Z]", text)):
        return False, "skipped_partial_or_fragmented_label"
    if any(len(word) == 1 and word.lower() not in {"x", "y", "z"} for word in words):
        return False, "skipped_ambiguous_short_token"
    return True, None


def _combine_caption_lines(lines: list[dict]) -> list[dict]:
    """Merge wrapped OCR caption lines before they are translated and overlaid."""
    ordered = sorted(lines, key=lambda line: (line["box"][1], line["box"][0]))
    combined: list[dict] = []
    index = 0
    while index < len(ordered):
        current = dict(ordered[index])
        is_caption = bool(re.match(r"^\([A-Za-z]\)\s+", current["source"]))
        while is_caption and index + 1 < len(ordered):
            following = ordered[index + 1]
            gap = following["box"][1] - current["box"][3]
            if gap > max(30, (current["box"][3] - current["box"][1]) * 1.5) or not following["source"][:1].islower():
                break
            current["source"] = f"{current['source']} {following['source']}"
            current["box"] = (
                min(current["box"][0], following["box"][0]),
                min(current["box"][1], following["box"][1]),
                max(current["box"][2], following["box"][2]),
                max(current["box"][3], following["box"][3]),
            )
            current["confidence"] = min(current["confidence"], following["confidence"])
            index += 1
        combined.append(current)
        index += 1
    return combined


def _repair_bound_labels(lines: list[dict]) -> list[dict]:
    """Repair common OCR splits of two-line 'Upper/Lower bound' diagram labels."""
    repaired: list[dict] = []
    consumed: set[int] = set()
    for index, line in enumerate(lines):
        if index in consumed:
            continue
        source = line["source"].casefold()
        if source == "bound":
            left, top, right, bottom = line["box"]
            repaired.append({**line, "source": "Upper bound", "box": (left, max(0, top - int((bottom - top) * 1.45)), right, bottom)})
            continue
        if source == "lower":
            for candidate_index, candidate in enumerate(lines[index + 1 :], start=index + 1):
                candidate_source = candidate["source"].casefold()
                close_vertically = 0 <= candidate["box"][1] - line["box"][3] <= 90
                same_column = abs((candidate["box"][0] + candidate["box"][2]) - (line["box"][0] + line["box"][2])) < 100
                if close_vertically and same_column and candidate_source in {"bound", "oun"}:
                    repaired.append({**line, "source": "Lower bound", "box": (min(line["box"][0], candidate["box"][0]), line["box"][1], max(line["box"][2], candidate["box"][2]), candidate["box"][3])})
                    consumed.add(candidate_index)
                    break
            else:
                repaired.append(line)
            continue
        repaired.append(line)
    return repaired


def _recover_repeated_bound_labels(image: Image.Image, lines: list[dict]) -> list[dict]:
    """Recover repeated *Upper/Lower bound* labels missed by sparse OCR.

    Architecture diagrams often repeat these exact two-line labels on aligned
    coloured panels.  Tesseract can recognise one occurrence while missing a
    visually identical later occurrence.  Rather than use a broad, unsafe OCR
    fallback, compare the already-recognised label crop only within the same
    image column.  A candidate is accepted only when it is a close visual
    match and does not overlap another recognised label.
    """
    recovered = list(lines)
    grayscale = image.convert("L")
    for line in lines:
        if line["source"].casefold() not in {"upper bound", "lower bound"}:
            continue
        left, top, right, bottom = line["box"]
        height = bottom - top
        width = right - left
        if height < 20 or width < 30:
            continue
        template = grayscale.crop((left, top, right, bottom))
        candidates: list[tuple[float, int]] = []
        for candidate_top in range(0, grayscale.height - height + 1):
            candidate_box = (left, candidate_top, right, candidate_top + height)
            if any(
                candidate_box[1] < existing["box"][3]
                and candidate_box[3] > existing["box"][1]
                and candidate_box[0] < existing["box"][2]
                and candidate_box[2] > existing["box"][0]
                for existing in recovered
            ):
                continue
            difference = ImageChops.difference(grayscale.crop(candidate_box), template)
            candidates.append((ImageStat.Stat(difference).mean[0], candidate_top))
        selected_y: list[int] = []
        for score, candidate_top in sorted(candidates):
            # Empirically, exact repeated label panels are below 30; unrelated
            # panel content is materially farther away.  Keep candidates
            # separated so a single label cannot be emitted more than once.
            if score > 30 or any(abs(candidate_top - previous) < height // 2 for previous in selected_y):
                continue
            recovered.append(
                {
                    "source": line["source"],
                    "box": (left, candidate_top, right, candidate_top + height),
                    "confidence": line["confidence"],
                    "recovery_only": True,
                    "template_recovery": True,
                }
            )
            selected_y.append(candidate_top)
    return recovered


def _lines_from_tsv(raw: str, min_confidence: float, recovery_only: bool = False) -> list[dict]:
    """Build line boxes from a Tesseract TSV stream."""
    grouped: dict[tuple[str, str, str], list[dict]] = {}
    for row in raw.splitlines()[1:]:
        parts = row.split("\t")
        if len(parts) != 12 or not parts[11].strip():
            continue
        try:
            confidence = float(parts[10])
        except ValueError:
            continue
        # Keep a narrow low-confidence band only so known labels such as Group 1
        # can be recovered. Unknown low-confidence OCR is still rejected later.
        if confidence < max(1.0, min_confidence - 35):
            continue
        key = (parts[2], parts[3], parts[4])
        grouped.setdefault(key, []).append({"text": parts[11].strip(), "left": int(parts[6]), "top": int(parts[7]), "width": int(parts[8]), "height": int(parts[9]), "confidence": confidence})
    lines: list[dict] = []
    for words in grouped.values():
        text = " ".join(word["text"] for word in words).replace("’", "'")
        if not _LABEL.fullmatch(text) or sum(char.isalpha() for char in text) < 3:
            continue
        # The block-layout OCR is only a recovery channel for Group N labels.
        # Allowing it to emit all known terms would redraw labels already found
        # by sparse OCR and produce visible double text.
        if recovery_only and not re.search(r"\bgroup", text, re.I):
            continue
        left = min(word["left"] for word in words)
        top = min(word["top"] for word in words)
        right = max(word["left"] + word["width"] for word in words)
        bottom = max(word["top"] + word["height"] for word in words)
        lines.append({"source": text, "box": (left, top, right, bottom), "confidence": round(min(word["confidence"] for word in words), 1), "recovery_only": recovery_only})
    return lines


def _ocr_lines(image: Image.Image, min_confidence: float) -> list[dict]:
    # PDF figures commonly store a 2x / 3x downscaled raster while retaining
    # tiny legend labels.  Upscale only the temporary OCR bitmap; all returned
    # boxes are mapped back to the original image before any overlay occurs.
    scale = 3 if max(image.size) < 1400 else 1
    ocr_image = image.resize((image.width * scale, image.height * scale), Image.Resampling.LANCZOS) if scale > 1 else image
    with tempfile.NamedTemporaryFile(suffix=".png") as handle:
        ocr_image.save(handle.name)
        sparse = subprocess.run(["tesseract", handle.name, "stdout", "-l", "eng", "--psm", "11", "tsv"], text=True, capture_output=True, check=False)
        block = subprocess.run(["tesseract", handle.name, "stdout", "-l", "eng", "--psm", "6", "tsv"], text=True, capture_output=True, check=False)
    if sparse.returncode:
        return []
    lines = _lines_from_tsv(sparse.stdout, min_confidence)
    if not block.returncode:
        for recovered in _lines_from_tsv(block.stdout, min_confidence, recovery_only=True):
            if not any(recovered["source"].casefold() == existing["source"].casefold() and abs(recovered["box"][0] - existing["box"][0]) < 20 and abs(recovered["box"][1] - existing["box"][1]) < 20 for existing in lines):
                lines.append(recovered)
    if scale > 1:
        for line in lines:
            left, top, right, bottom = line["box"]
            line["box"] = (round(left / scale), round(top / scale), round(right / scale), round(bottom / scale))
    return _recover_repeated_bound_labels(image, _repair_bound_labels(_combine_caption_lines(lines)))


def _flat_background(image: Image.Image, box: tuple[int, int, int, int]) -> tuple[int, int, int] | None:
    left, top, right, bottom = box
    # Inspect only a thin outer ring, not the black source glyphs inside the text box.
    rgb = image.convert("RGB")
    x0, y0, x1, y1 = max(0, left - 3), max(0, top - 3), min(image.width, right + 3), min(image.height, bottom + 3)
    samples = []
    samples.extend(rgb.crop((x0, y0, x1, min(y1, y0 + 3))).getdata())
    samples.extend(rgb.crop((x0, max(y0, y1 - 3), x1, y1)).getdata())
    samples.extend(rgb.crop((x0, y0, min(x1, x0 + 3), y1)).getdata())
    samples.extend(rgb.crop((max(x0, x1 - 3), y0, x1, y1)).getdata())
    if not samples:
        return None
    means = [sum(pixel[channel] for pixel in samples) / len(samples) for channel in range(3)]
    variances = [sum((pixel[channel] - means[channel]) ** 2 for pixel in samples) / len(samples) for channel in range(3)]
    if max(variances) <= 180:
        return tuple(int(value) for value in means)
    # Some labels sit on a mostly uniform panel next to arrows or shadows.  A
    # dominant colour is safe enough for the text box, while a genuinely mixed
    # background still remains untouched.
    buckets: dict[tuple[int, int, int], list[tuple[int, int, int]]] = {}
    for pixel in samples:
        bucket = tuple(channel // 32 for channel in pixel)
        buckets.setdefault(bucket, []).append(pixel)
    dominant = max(buckets.values(), key=len)
    if len(dominant) / len(samples) < 0.62:
        return None
    return tuple(int(sum(pixel[channel] for pixel in dominant) / len(dominant)) for channel in range(3))


def _fit_font(text: str, box: tuple[int, int, int, int]) -> ImageFont.FreeTypeFont | None:
    width, height = box[2] - box[0], box[3] - box[1]
    for font_path in filter(None, (cjk_font_path(),)):
        for size in range(max(8, height), 5, -1):
            try:
                font = ImageFont.truetype(font_path, size=size)
            except OSError:
                break
            bounds = font.getbbox(text)
            if bounds[2] - bounds[0] <= width and bounds[3] - bounds[1] <= height:
                return font
    return None


def _boxes_intersect(left: tuple[int, int, int, int], right: tuple[int, int, int, int]) -> bool:
    return left[0] < right[2] and left[2] > right[0] and left[1] < right[3] and left[3] > right[1]


def _blocked_image_boxes(page: fitz.Page, image_rects: list[fitz.Rect], image_width: int, image_height: int) -> list[tuple[int, int, int, int]]:
    """Map vector-text rectangles into the coordinate space of an image xref."""
    blocks: list[tuple[int, int, int, int]] = []
    for image_rect in image_rects:
        if image_rect.width <= 0 or image_rect.height <= 0:
            continue
        for word in page.get_text("words"):
            word_rect = fitz.Rect(word[:4])
            intersection = word_rect & image_rect
            if intersection.is_empty:
                continue
            blocks.append(
                (
                    max(0, round((intersection.x0 - image_rect.x0) / image_rect.width * image_width)),
                    max(0, round((intersection.y0 - image_rect.y0) / image_rect.height * image_height)),
                    min(image_width, round((intersection.x1 - image_rect.x0) / image_rect.width * image_width)),
                    min(image_height, round((intersection.y1 - image_rect.y0) / image_rect.height * image_height)),
                )
            )
    return blocks


def _page_background(page_image: Image.Image, page_rect: fitz.Rect, rect: fitz.Rect) -> tuple[float, float, float] | None:
    sx, sy = page_image.width / page_rect.width, page_image.height / page_rect.height
    box = (round(rect.x0 * sx), round(rect.y0 * sy), round(rect.x1 * sx), round(rect.y1 * sy))
    colour = _flat_background(page_image, box)
    return tuple(value / 255 for value in colour) if colour is not None else None


def _vector_label_overlays(page: fitz.Page, image_rects: list[fitz.Rect], glossary: Mapping[str, str]) -> tuple[bool, list[dict]]:
    """Safely translate recognised vector text placed on top of figures.

    PDF papers often draw labels as vector glyphs over a raster illustration.
    Replacing the raster alone would leave English text doubled on top, which is
    why the earlier implementation skipped the entire image.  Here we only
    replace deterministic terms and user glossary entries in a flat local box.
    """
    font_resource = page_cjk_font_resource(page)
    if font_resource is None:
        return False, [{"status": "cjk_font_not_available"}]
    font_name, font_file = font_resource
    page_image_pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
    page_image = Image.frombytes("RGB", (page_image_pix.width, page_image_pix.height), page_image_pix.samples)
    metric_font_path = font_file or str(cjk_font_path())
    font = fitz.Font(fontfile=metric_font_path)
    planned: list[dict] = []
    records: list[dict] = []
    seen: set[tuple[float, float, float, float]] = set()
    for word in page.get_text("words"):
        rect = fitz.Rect(word[:4])
        source = word[4].strip()
        key = tuple(round(value, 2) for value in rect)
        if not source or key in seen or not any(rect.intersects(image_rect) for image_rect in image_rects):
            continue
        seen.add(key)
        target = _known_translation(source, glossary)
        record = {"source": source, "box": [round(value, 2) for value in rect], "target": target}
        if target is None:
            record["status"] = "original_preserved_unrecognised_or_identifier"
            records.append(record)
            continue
        background = _page_background(page_image, page.rect, rect)
        if background is None:
            record["status"] = "original_preserved_complex_background"
            records.append(record)
            continue
        size = max(4.0, min(rect.height * 0.92, 10.0))
        while size >= 4.0 and (font.text_length(target, fontsize=size) > rect.width or size > rect.height * 1.15):
            size -= 0.5
        if size < 4.0:
            record["status"] = "original_preserved_text_does_not_fit"
            records.append(record)
            continue
        planned.append({"rect": rect, "target": target, "fontsize": size, "background": background, "record": record})
        records.append(record)
    if not planned:
        return False, records
    for item in planned:
        page.add_redact_annot(item["rect"], fill=item["background"])
    page.apply_redactions(images=0, graphics=0, text=0)
    if font_file:
        page.insert_font(fontname=font_name, fontfile=font_file)
    for item in planned:
        # ``insert_textbox`` rejects tiny diagram labels because CJK font ascent
        # can exceed the original Latin glyph box even when the rendered text
        # fits.  A baseline insertion uses the same bounded font size computed
        # above and avoids silently dropping otherwise safe labels.
        baseline = item["rect"].y1 - max(0.25, item["fontsize"] * 0.10)
        page.insert_text((item["rect"].x0, baseline), item["target"], fontname=font_name, fontsize=item["fontsize"], color=(0, 0, 0))
        item["record"]["status"] = "translated_in_place"
    return True, records


def translate_raster_figure(
    image_bytes: bytes,
    model: TextGenerator,
    target_lang: str,
    min_confidence: float,
    glossary: Mapping[str, str] | None = None,
    blocked_boxes: list[tuple[int, int, int, int]] | None = None,
) -> tuple[bytes | None, list[dict]]:
    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    manifest: list[dict] = []
    changed = False
    canvas = image.copy()
    drawer = ImageDraw.Draw(canvas)
    for label in _ocr_lines(image, min_confidence):
        record = dict(label)
        if any(_boxes_intersect(label["box"], blocked) for blocked in blocked_boxes or []):
            record["status"] = "skipped_vector_text_overlay"
            manifest.append(record)
            continue
        known_translation = _known_translation(label["source"], glossary or {})
        is_caption = bool(re.match(r"^\([A-Za-z]\)\s+", label["source"]))
        if label["confidence"] < min_confidence and known_translation is None and not is_caption:
            record["status"] = "skipped_low_confidence_unknown_label"
            manifest.append(record)
            continue
        safe_text, skip_reason = _is_safe_label_text(label["source"], known_translation)
        if not safe_text:
            record["status"] = skip_reason
            manifest.append(record)
            continue
        background = _flat_background(image, label["box"])
        if background is None:
            record["status"] = "skipped_complex_background"
            manifest.append(record)
            continue
        if known_translation:
            translated = known_translation
        else:
            matching_terms = {source: target for source, target in (glossary or {}).items() if source.casefold() in label["source"].casefold()}
            term_constraint = ""
            if matching_terms:
                term_constraint = " Required terminology: " + "; ".join(f"{source} -> {target}" for source, target in matching_terms.items()) + "."
            translated = model.generate(
                f"Translate this complete academic figure label from English to {target_lang}. "
                "Return only a concise, faithful translation. Preserve method names, variable names, "
                f"and scientific symbols exactly; never invent meaning.{term_constraint}\n\n"
                f"{label['source']}"
            ).strip()
        if target_lang.startswith("zh") and not re.search(r"[\u4e00-\u9fff]", translated):
            record["status"] = "skipped_unverified_translation"
            manifest.append(record)
            continue
        font = _fit_font(translated, label["box"])
        if font is None:
            record["status"] = "skipped_text_does_not_fit"
            manifest.append(record)
            continue
        drawer.rectangle(label["box"], fill=background)
        drawer.text((label["box"][0], label["box"][1]), translated, fill=(0, 0, 0), font=font)
        record.update({"target": translated, "status": "applied"})
        manifest.append(record)
        changed = True
    if not changed:
        return None, manifest
    output = io.BytesIO()
    canvas.save(output, format="PNG")
    return output.getvalue(), manifest


def translate_pdf_figures(
    input_pdf: Path,
    output_pdf: Path,
    manifest_path: Path,
    model: TextGenerator,
    target_lang: str,
    min_confidence: float,
    glossary: Mapping[str, str] | None = None,
) -> bool:
    source = fitz.open(input_pdf)
    records: list[dict] = []
    processed: set[int] = set()
    changed = False
    try:
        for page_index, page in enumerate(source, start=1):
            page_image_rects: list[fitz.Rect] = []
            for image_info in page.get_images(full=True):
                xref = image_info[0]
                image_rects = page.get_image_rects(xref)
                page_image_rects.extend(image_rects)
                if xref in processed:
                    continue
                processed.add(xref)
                vector_words = [word[4] for word in page.get_text("words") if any(fitz.Rect(word[:4]).intersects(rect) for rect in image_rects)]
                extracted = source.extract_image(xref)
                blocked_boxes = _blocked_image_boxes(page, image_rects, extracted["width"], extracted["height"])
                translated, labels = translate_raster_figure(extracted["image"], model, target_lang, min_confidence, glossary, blocked_boxes)
                record = {"page": page_index, "xref": xref, "width": extracted["width"], "height": extracted["height"], "labels": labels, "vector_words": vector_words[:30]}
                if translated is None:
                    record["status"] = "original_preserved" if not vector_words else "raster_original_preserved_vector_overlay_handled_separately"
                else:
                    page.replace_image(xref, stream=translated)
                    record["status"] = "translated_in_place"
                    changed = True
                records.append(record)
            # Keep vector replacement limited to the exact embedded-image
            # rectangles.  Wider figure-cluster replacement can cross vector
            # masks in complex architecture diagrams and is therefore not a
            # safe automatic operation.
            vector_changed, vector_records = _vector_label_overlays(page, page_image_rects, glossary or {})
            if vector_records:
                records.append({"page": page_index, "status": "vector_figure_labels", "labels": vector_records})
            changed = changed or vector_changed
        source.save(output_pdf, garbage=4, deflate=True, use_objstms=1)
    finally:
        source.close()
    manifest_path.write_text(json.dumps({"input": str(input_pdf), "output": str(output_pdf), "changed": changed, "images": records}, ensure_ascii=False, indent=2), encoding="utf-8")
    return changed


def translate_docx_figures(
    document_path: Path,
    manifest_path: Path,
    model: TextGenerator,
    target_lang: str,
    min_confidence: float,
    glossary: Mapping[str, str] | None = None,
) -> bool:
    """Translate embedded Word images in-place, without adding duplicate media.

    Word packages reference files under ``word/media``. Replacing only a safely
    translated image keeps every drawing anchor, caption, relationship, and
    image count unchanged. This closes the old PDF-only visual-translation gap
    while avoiding an image extraction/reinsertion pass that can bloat DOCX.
    """
    records: list[dict] = []
    changed = False
    temporary = document_path.with_name(f".{document_path.stem}-visuals.tmp.docx")
    with zipfile.ZipFile(document_path, "r") as source, zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as target:
        for entry in source.infolist():
            payload = source.read(entry.filename)
            is_raster = entry.filename.startswith("word/media/") and Path(entry.filename).suffix.lower() in {".png", ".jpg", ".jpeg"}
            if not is_raster:
                target.writestr(entry, payload)
                continue
            record: dict = {"media": entry.filename, "status": "original_preserved", "labels": []}
            try:
                translated, labels = translate_raster_figure(payload, model, target_lang, min_confidence, glossary)
                record["labels"] = labels
                if translated is not None:
                    # Keep the file extension/content type valid for Word. A
                    # PNG overlay is lossless; JPEG is re-encoded only when the
                    # original package used JPEG.
                    rendered = Image.open(io.BytesIO(translated)).convert("RGB")
                    encoded = io.BytesIO()
                    suffix = Path(entry.filename).suffix.lower()
                    if suffix in {".jpg", ".jpeg"}:
                        rendered.save(encoded, format="JPEG", quality=90, optimize=True)
                    else:
                        rendered.save(encoded, format="PNG", optimize=True)
                    payload = encoded.getvalue()
                    record["status"] = "translated_in_place"
                    changed = True
            except Exception as exc:  # Visual translation must never corrupt a deliverable DOCX.
                record["status"] = "original_preserved_error"
                record["error"] = str(exc)
            records.append(record)
            target.writestr(entry, payload)
    temporary.replace(document_path)
    manifest_path.write_text(json.dumps({"input": str(document_path), "changed": changed, "images": records}, ensure_ascii=False, indent=2), encoding="utf-8")
    return changed
