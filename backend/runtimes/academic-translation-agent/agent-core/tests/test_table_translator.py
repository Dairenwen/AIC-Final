from pathlib import Path

import fitz

from academic_translation.tools.table_translator import translate_pdf_tables


def test_vector_table_headers_are_translated_without_changing_table_page_count(tmp_path: Path) -> None:
    source = tmp_path / "table.pdf"
    document = fitz.open()
    page = document.new_page(width=360, height=200)
    for x in (40, 150, 260, 340):
        page.draw_line((x, 40), (x, 150), color=(0, 0, 0))
    for y in (40, 90, 150):
        page.draw_line((40, y), (340, y), color=(0, 0, 0))
    page.insert_text((55, 72), "Model", fontsize=12)
    page.insert_text((165, 72), "Accuracy", fontsize=12)
    page.insert_text((55, 122), "Method-A", fontsize=12)
    page.insert_text((165, 122), "0.91", fontsize=12)
    document.save(source)
    document.close()

    output, manifest = tmp_path / "table-zh.pdf", tmp_path / "table-manifest.json"
    changed = translate_pdf_tables(source, output, manifest, "zh")

    assert changed
    rendered = fitz.open(output)
    assert len(rendered) == 1
    text = rendered[0].get_text()
    rendered.close()
    assert "模型" in text
    assert "准确率" in text
    assert "Method-A" in text
    assert '"status": "translated_in_place"' in manifest.read_text(encoding="utf-8")
