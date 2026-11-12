import io
from pathlib import Path

import fitz
from PIL import Image, ImageDraw, ImageFont

from academic_translation.tools.figure_translator import translate_pdf_figures, translate_raster_figure


class FakeFigureModel:
    def generate(self, prompt: str) -> str:
        return "核心方法"


def test_safe_figure_overlay_keeps_canvas_size_and_reports_applied_label() -> None:
    image = Image.new("RGB", (640, 240), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 42)
    draw.text((80, 90), "Core Method", fill="black", font=font)
    payload = io.BytesIO()
    image.save(payload, format="PNG")

    translated, manifest = translate_raster_figure(payload.getvalue(), FakeFigureModel(), "zh", min_confidence=50)

    assert translated is not None
    rendered = Image.open(io.BytesIO(translated))
    assert rendered.size == image.size
    assert any(item["status"] == "applied" for item in manifest)


def test_single_word_acronym_is_preserved_to_avoid_ocr_mistranslation() -> None:
    image = Image.new("RGB", (400, 180), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 50)
    draw.text((80, 60), "SOSIB", fill="black", font=font)
    payload = io.BytesIO()
    image.save(payload, format="PNG")

    translated, manifest = translate_raster_figure(payload.getvalue(), FakeFigureModel(), "zh", min_confidence=50)

    assert translated is None
    assert any(item["status"] == "skipped_single_token_or_abbreviation" for item in manifest)


def test_known_single_word_figure_label_is_translated_safely() -> None:
    image = Image.new("RGB", (400, 180), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 50)
    draw.text((80, 60), "Input", fill="black", font=font)
    payload = io.BytesIO()
    image.save(payload, format="PNG")

    translated, manifest = translate_raster_figure(payload.getvalue(), FakeFigureModel(), "zh", min_confidence=50)

    assert translated is not None
    assert any(item["target"] == "输入" and item["status"] == "applied" for item in manifest)


def test_unknown_vector_text_overlap_is_preserved_without_overlay(tmp_path: Path) -> None:
    image = Image.new("RGB", (300, 160), "white")
    image.save(tmp_path / "figure.png")
    document = fitz.open()
    page = document.new_page(width=400, height=300)
    page.insert_image(fitz.Rect(40, 40, 340, 200), filename=str(tmp_path / "figure.png"))
    page.insert_text((80, 110), "Core Method", fontsize=16)
    source = tmp_path / "vector-label.pdf"
    document.save(source)
    document.close()
    output, manifest = tmp_path / "output.pdf", tmp_path / "manifest.json"
    changed = translate_pdf_figures(source, output, manifest, FakeFigureModel(), "zh", 50)
    assert not changed
    assert "original_preserved_unrecognised_or_identifier" in manifest.read_text(encoding="utf-8")


def test_known_vector_figure_label_is_translated_in_place(tmp_path: Path) -> None:
    image = Image.new("RGB", (300, 160), "white")
    image.save(tmp_path / "figure.png")
    document = fitz.open()
    page = document.new_page(width=400, height=300)
    page.insert_image(fitz.Rect(40, 40, 340, 200), filename=str(tmp_path / "figure.png"))
    page.insert_text((80, 110), "Input", fontsize=16)
    source = tmp_path / "known-vector-label.pdf"
    document.save(source)
    document.close()
    output, manifest = tmp_path / "output.pdf", tmp_path / "manifest.json"
    changed = translate_pdf_figures(source, output, manifest, FakeFigureModel(), "zh", 50)
    assert changed
    assert "translated_in_place" in manifest.read_text(encoding="utf-8")
    translated = fitz.open(output)
    assert "输入" in translated[0].get_text()
    translated.close()
