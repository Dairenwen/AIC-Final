from pathlib import Path
from types import SimpleNamespace
import shutil

import fitz

from academic_translation.schemas.models import ElementPolicy, TranslationRequest
from academic_translation.tools import pdfmathtranslate_adapter as adapter


def test_layout_adapter_uses_one_batch_process_and_checks_output_counts(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    document = fitz.open()
    document.new_page()
    document.new_page()
    document.save(source)
    document.close()
    captured: dict[str, list[str]] = {}
    monkeypatch.setattr(adapter.settings, "pdf2zh_command", "pdf2zh")
    def fake_run(command, **kwargs):
        captured.setdefault("commands", []).append(command)
        page_dir = Path(command[command.index("--output") + 1])
        page_dir.mkdir(parents=True, exist_ok=True)
        rendered = fitz.open()
        rendered.new_page()
        rendered.new_page()
        rendered.save(page_dir / "source-mono.pdf")
        rendered.close()
        return SimpleNamespace(returncode=0, stderr="")
    monkeypatch.setattr(adapter.subprocess, "run", fake_run)
    result = adapter.render_pdf_with_pdf2zh(TranslationRequest(input_path=source, preserve_pdf_layout=True, output_dir=tmp_path))
    assert len(captured["commands"]) == 1
    assert "--pages" not in captured["commands"][0]
    assert captured["commands"][0][captured["commands"][0].index("--thread") + 1] == "1"
    assert "--skip-subset-fonts" not in captured["commands"][0]
    assert result["pdf_layout_pages"] == "2"
    assert result["pdf_layout_mode"] == "batch"


def test_layout_adapter_integrates_images_and_tables_into_one_visual_output(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    document = fitz.open()
    document.new_page()
    document.new_page()
    document.save(source)
    document.close()
    monkeypatch.setattr(adapter.settings, "pdf2zh_command", "pdf2zh")

    def fake_run(command, **kwargs):
        page_dir = Path(command[command.index("--output") + 1])
        page_dir.mkdir(parents=True, exist_ok=True)
        rendered = fitz.open()
        rendered.new_page()
        rendered.new_page()
        rendered.save(page_dir / "source-mono.pdf")
        rendered.close()
        return SimpleNamespace(returncode=0, stderr="")

    def fake_figures(input_pdf, output_pdf, manifest_path, *args):
        shutil.copyfile(input_pdf, output_pdf)
        manifest_path.write_text("{}")
        return True

    def fake_tables(input_pdf, output_pdf, manifest_path, *args):
        shutil.copyfile(input_pdf, output_pdf)
        manifest_path.write_text("{}")
        return True

    monkeypatch.setattr(adapter.subprocess, "run", fake_run)
    monkeypatch.setattr(adapter, "translate_pdf_figures", fake_figures)
    monkeypatch.setattr(adapter, "translate_pdf_tables", fake_tables)
    result = adapter.render_pdf_with_pdf2zh(TranslationRequest(input_path=source, preserve_pdf_layout=True, pdf_bilingual=True, output_dir=tmp_path, element_policy=ElementPolicy(translate_figures=True)))

    assert result["pdf_monolingual"].endswith("source-mono-visuals.pdf")
    assert result["pdf_bilingual"].endswith("source-dual-visuals.pdf")
    assert len(fitz.open(result["pdf_monolingual"])) == 2
    assert len(fitz.open(result["pdf_bilingual"])) == 4
    assert "figure_translation_manifest" in result
    assert "table_translation_manifest" in result


def test_pagewise_mode_remains_available_as_a_compatibility_fallback(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    document = fitz.open()
    document.new_page()
    document.new_page()
    document.save(source)
    document.close()
    monkeypatch.setattr(adapter.settings, "pdf2zh_command", "pdf2zh")
    pages: list[str] = []

    def fake_run(command, **kwargs):
        pages.append(command[command.index("--pages") + 1])
        page_dir = Path(command[command.index("--output") + 1])
        page_dir.mkdir(parents=True, exist_ok=True)
        rendered = fitz.open()
        rendered.new_page()
        rendered.save(page_dir / "source-mono.pdf")
        rendered.close()
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(adapter.subprocess, "run", fake_run)
    result = adapter.render_pdf_with_pdf2zh(TranslationRequest(input_path=source, preserve_pdf_layout=True, pdf_layout_mode="pagewise", output_dir=tmp_path))
    assert pages == ["1", "2"]
    assert result["pdf_layout_mode"] == "pagewise"


def test_pdf_only_batch_mode_skips_bilingual_pdf(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    document = fitz.open()
    document.new_page()
    document.save(source)
    document.close()
    monkeypatch.setattr(adapter.settings, "pdf2zh_command", "pdf2zh")
    captured: dict[str, list[str]] = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        page_dir = Path(command[command.index("--output") + 1])
        page_dir.mkdir(parents=True, exist_ok=True)
        rendered = fitz.open()
        rendered.new_page()
        rendered.save(page_dir / "source-mono.pdf")
        rendered.close()
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(adapter.subprocess, "run", fake_run)
    result = adapter.render_pdf_with_pdf2zh(TranslationRequest(input_path=source, preserve_pdf_layout=True, pdf_only=True, output_dir=tmp_path))
    assert "pdf_bilingual" not in result
    assert Path(result["pdf_monolingual"]).exists()
    assert "--skip-subset-fonts" not in captured["command"]


def test_pdf_only_can_return_upstream_bilingual_pdf_without_graph(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    document = fitz.open()
    document.new_page()
    document.save(source)
    document.close()
    monkeypatch.setattr(adapter.settings, "pdf2zh_command", "pdf2zh")

    def fake_run(command, **kwargs):
        page_dir = Path(command[command.index("--output") + 1])
        page_dir.mkdir(parents=True, exist_ok=True)
        mono, dual = fitz.open(), fitz.open()
        mono.new_page()
        dual.new_page()
        dual.new_page()
        mono.save(page_dir / "source-mono.pdf")
        dual.save(page_dir / "source-dual.pdf")
        mono.close()
        dual.close()
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(adapter.subprocess, "run", fake_run)
    result = adapter.render_pdf_with_pdf2zh(TranslationRequest(input_path=source, preserve_pdf_layout=True, pdf_only=True, pdf_bilingual=True, output_dir=tmp_path))
    assert Path(result["pdf_monolingual"]).exists()
    assert Path(result["pdf_bilingual"]).exists()
    assert len(fitz.open(result["pdf_bilingual"])) == 2


def test_low_memory_mode_merges_isolated_pages_and_builds_bilingual_pdf(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    document = fitz.open()
    document.new_page()
    document.new_page()
    document.save(source)
    document.close()
    monkeypatch.setattr(adapter.settings, "pdf2zh_command", "pdf2zh")
    translated_sources: list[str] = []

    def fake_run(command, **kwargs):
        translated_sources.append(command[1])
        page_dir = Path(command[command.index("--output") + 1])
        page_dir.mkdir(parents=True, exist_ok=True)
        translated = fitz.open()
        input_pages = fitz.open(command[1])
        for _ in input_pages:
            translated.new_page()
        input_pages.close()
        translated.save(page_dir / f"{Path(command[1]).stem}-mono.pdf")
        translated.close()
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(adapter.subprocess, "run", fake_run)
    result = adapter.render_pdf_with_pdf2zh(
        TranslationRequest(
            input_path=source,
            preserve_pdf_layout=True,
            pdf_only=True,
            pdf_bilingual=True,
            pdf_layout_mode="low_memory",
            output_dir=tmp_path,
        )
    )
    assert [Path(item).name for item in translated_sources] == ["pages-0001-0002-source.pdf"]
    assert len(fitz.open(result["pdf_monolingual"])) == 2
    assert len(fitz.open(result["pdf_bilingual"])) == 4
