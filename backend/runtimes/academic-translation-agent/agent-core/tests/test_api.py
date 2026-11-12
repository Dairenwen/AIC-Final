import re
import sys
from pathlib import Path

from docx import Document
from fastapi.testclient import TestClient

from academic_translation.agent.service import AcademicTranslationAgent


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agent-system" / "backend"))
import app as backend_app  # noqa: E402


class FakeModel:
    def generate(self, prompt: str) -> str:
        if "terminology curator" in prompt:
            return '[{"source":"model","target":"模型","confidence":1.0}]'
        if "academic copy editor" in prompt:
            return "润色后的译文 " + " ".join(re.findall(r"\[\[KEEP_\d+\]\]", prompt))
        return "译文 模型 " + " ".join(re.findall(r"\[\[KEEP_\d+\]\]", prompt))


def test_api_upload_configuration_and_online_segment_edit(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(backend_app, "agent", AcademicTranslationAgent(FakeModel()))
    backend_app.tasks.clear()
    client = TestClient(backend_app.app)
    assert client.get("/health").status_code == 200
    translated = client.post("/translate/text?text=A%20model%20uses%20%24x%3Dy%24.&source_lang=en&target_lang=zh&max_parallel_segments=2")
    assert translated.status_code == 200
    payload = translated.json()
    task_id = payload["task_id"]
    segment_id = payload["segments"][0]["segment_id"]
    edited = client.patch(f"/tasks/{task_id}/segments/{segment_id}", json={"translated_text": "人工修订模型 $x=y$。"})
    assert edited.status_code == 200
    assert edited.json()["quality"]["protected_token_violations"] == []

    source = tmp_path / "upload.docx"
    document = Document()
    document.add_paragraph("A model.")
    document.save(source)
    with source.open("rb") as handle:
        uploaded = client.post("/translate/document", files={"file": ("upload.docx", handle, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")}, data={"source_lang": "en", "target_lang": "zh", "precision": "reading", "preserve_formulas": "true", "preserve_figures": "true", "preserve_references": "true", "preserve_headers_footers": "true", "glossary_json": '{"A model":"模型"}', "max_parallel_segments": "2"})
    assert uploaded.status_code == 200
    assert uploaded.json()["quality"]["untranslated_segment_ids"] == []
    assert any(item["source"] == "A model" and item["target"] == "模型" for item in uploaded.json()["glossary"])
