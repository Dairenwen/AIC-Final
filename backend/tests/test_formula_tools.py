from io import BytesIO
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from flask import g
from PIL import Image

from app import create_app
from app.api.formula_tools import formula_runtime_status, recognize_formula
from app.services.catalog_setup import FORMULA_IMAGE_TO_LATEX_TOOL
from app.tools.formula_recognition import FormulaRecognitionNotReady, FormulaRecognitionService


def png_bytes(size=(12, 8)) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", size, "white").save(buffer, format="PNG")
    return buffer.getvalue()


class FakeFormulaService:
    def __init__(self, *, ready=True):
        self.ready = ready
        self.received_path: Path | None = None

    def status(self):
        return {
            "ready": self.ready,
            "checks": {"runtime": True, "pythonEnvironment": self.ready, "model": self.ready},
            "device": "cpu",
        }

    def recognize(self, image_path: Path):
        self.received_path = image_path
        assert image_path.is_file()
        if not self.ready:
            raise FormulaRecognitionNotReady("UniMERNet 识别环境尚未完成配置")
        return {"latex": r"x^2 + y^2", "device": "cpu", "durationMs": 25}


def test_formula_upload_is_validated_recognized_and_deleted(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "FORMULA_UPLOAD_DIR": tmp_path,
            "FORMULA_UPLOAD_MAX_BYTES": 1024 * 1024,
            "FORMULA_MAX_IMAGE_PIXELS": 1000,
        }
    )
    service = FakeFormulaService()
    app.extensions["formula_recognition_service"] = service
    with app.test_request_context(
        method="POST",
        data={"file": (BytesIO(png_bytes()), "../equation.png")},
        content_type="multipart/form-data",
    ):
        g.current_user = SimpleNamespace(id=uuid4())
        response, status = recognize_formula()

    assert status == 200
    assert response.get_json()["data"] == {
        "latex": r"x^2 + y^2",
        "device": "cpu",
        "durationMs": 25,
        "fileName": "equation.png",
    }
    assert service.received_path is not None
    assert not service.received_path.exists()


def test_formula_upload_rejects_disguised_or_oversized_images(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "FORMULA_UPLOAD_DIR": tmp_path,
            "FORMULA_UPLOAD_MAX_BYTES": 32,
            "FORMULA_MAX_IMAGE_PIXELS": 1000,
        }
    )
    for content, expected_status, expected_code in (
        (b"not an image", 415, "FORMULA_IMAGE_INVALID"),
        (png_bytes(), 413, "FORMULA_IMAGE_TOO_LARGE"),
    ):
        with app.test_request_context(
            method="POST",
            data={"file": (BytesIO(content), "formula.png")},
            content_type="multipart/form-data",
        ):
            g.current_user = SimpleNamespace(id=uuid4())
            response, status = recognize_formula()
        assert status == expected_status
        assert response.get_json()["error"]["code"] == expected_code


def test_formula_runtime_not_ready_returns_service_unavailable_and_cleans_file(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "FORMULA_UPLOAD_DIR": tmp_path,
            "FORMULA_UPLOAD_MAX_BYTES": 1024 * 1024,
            "FORMULA_MAX_IMAGE_PIXELS": 1000,
        }
    )
    service = FakeFormulaService(ready=False)
    app.extensions["formula_recognition_service"] = service
    with app.test_request_context(
        method="POST",
        data={"file": (BytesIO(png_bytes()), "formula.png")},
        content_type="multipart/form-data",
    ):
        g.current_user = SimpleNamespace(id=uuid4())
        response, status = recognize_formula()

    assert status == 503
    assert response.get_json()["error"]["code"] == "FORMULA_RUNTIME_NOT_READY"
    assert service.received_path is not None and not service.received_path.exists()


def test_formula_service_invokes_vendored_cli_without_shell(monkeypatch, tmp_path):
    root = tmp_path / "formula-runtime"
    python = root / ".venv" / "Scripts" / "python.exe"
    model = root / "unimernet" / "models" / "unimernet_base" / "pytorch_model.pth"
    script = root / "recognize.py"
    python.parent.mkdir(parents=True)
    model.parent.mkdir(parents=True)
    (root / ".venv" / "Lib" / "site-packages" / "huggingface_hub").mkdir(parents=True)
    (
        root / ".venv" / "Lib" / "site-packages" / "huggingface_hub" / "__init__.py"
    ).write_text(
        "", encoding="utf-8"
    )
    python.write_bytes(b"")
    model.write_bytes(b"model")
    script.write_text("", encoding="utf-8")
    image_path = tmp_path / "formula.png"
    image_path.write_bytes(png_bytes())
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return SimpleNamespace(returncode=0, stdout="\\frac{a}{b}\n", stderr="")

    monkeypatch.setattr("app.tools.formula_recognition.service.subprocess.run", fake_run)
    app = create_app(
        {
            "TESTING": True,
            "FORMULA_RECOGNITION_ROOT": root,
            "FORMULA_RECOGNITION_PYTHON": python,
            "FORMULA_RECOGNITION_DEVICE": "cpu",
            "FORMULA_RECOGNITION_TIMEOUT_SECONDS": 30,
        }
    )
    result = FormulaRecognitionService(app).recognize(image_path)

    assert captured["command"] == [
        str(python),
        str(script),
        str(image_path.resolve()),
        "--device",
        "cpu",
    ]
    assert "shell" not in captured
    assert captured["timeout"] == 30
    assert captured["env"]["PYTHONNOUSERSITE"] == "1"
    assert captured["env"]["PYTHONUTF8"] == "1"
    assert result["latex"] == r"\frac{a}{b}"


def test_formula_status_and_catalog_contract(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "FORMULA_RECOGNITION_ROOT": tmp_path,
            "FORMULA_RECOGNITION_PYTHON": tmp_path / "missing-python",
        }
    )
    with app.test_request_context():
        response, status = formula_runtime_status()
    assert status == 200
    assert response.get_json()["data"]["ready"] is False
    assert FORMULA_IMAGE_TO_LATEX_TOOL["config_json"]["route"] == "/tools/formula-to-latex"


def test_formula_runtime_defaults_to_vendored_system_directory():
    app = create_app({"TESTING": True})
    runtime_root = Path(app.root_path) / "tools" / "formula_recognition" / "runtime"

    assert Path(app.config["FORMULA_RECOGNITION_ROOT"]).resolve() == runtime_root.resolve()
    assert Path(app.config["FORMULA_RECOGNITION_PYTHON"]) == (
        runtime_root
        / ".venv"
        / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    )


def test_formula_status_reports_missing_inference_dependencies(monkeypatch, tmp_path):
    root = tmp_path / "formula-runtime"
    python = root / ".venv" / ("Scripts" if os.name == "nt" else "bin") / (
        "python.exe" if os.name == "nt" else "python"
    )
    python.parent.mkdir(parents=True)
    python.write_bytes(b"")
    (root / "unimernet" / "models" / "unimernet_base").mkdir(parents=True)
    (root / "unimernet" / "models" / "unimernet_base" / "pytorch_model.pth").write_bytes(b"model")
    (root / "recognize.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(
        "app.tools.formula_recognition.service.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout="", stderr="ModuleNotFoundError: No module named 'torch'"),
    )
    app = create_app(
        {
            "TESTING": True,
            "FORMULA_RECOGNITION_ROOT": root,
            "FORMULA_RECOGNITION_PYTHON": python,
        }
    )

    with app.test_request_context():
        response, status = formula_runtime_status()

    data = response.get_json()["data"]
    assert status == 200
    assert data["ready"] is False
    assert data["checks"]["inferenceDependencies"] is False
    assert "torch" in data["dependencyDetail"]


def test_formula_status_caches_expensive_dependency_probe(monkeypatch, tmp_path):
    root = tmp_path / "formula-runtime"
    python = root / ".venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"")
    (root / "unimernet" / "models" / "unimernet_base").mkdir(parents=True)
    (root / "unimernet" / "models" / "unimernet_base" / "pytorch_model.pth").write_bytes(b"model")
    (root / "recognize.py").write_text("", encoding="utf-8")
    calls = []

    def fake_run(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("app.tools.formula_recognition.service.subprocess.run", fake_run)
    app = create_app(
        {
            "TESTING": True,
            "FORMULA_RECOGNITION_ROOT": root,
            "FORMULA_RECOGNITION_PYTHON": python,
            "FORMULA_RUNTIME_STATUS_CACHE_SECONDS": 60,
        }
    )
    service = FormulaRecognitionService(app)
    service.status()
    service.status()

    assert len(calls) == 1
