from __future__ import annotations

import warnings
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from flask import Blueprint, current_app, g, request
from PIL import Image, UnidentifiedImageError

from ..tools.formula_recognition import (
    FormulaRecognitionFailed,
    FormulaRecognitionNotReady,
    FormulaRecognitionService,
)
from .responses import error, ok


bp = Blueprint("formula_tools", __name__)
IMAGE_FORMAT_SUFFIXES = {
    "PNG": ".png",
    "JPEG": ".jpg",
    "WEBP": ".webp",
    "BMP": ".bmp",
    "TIFF": ".tiff",
}


@bp.get("/tools/formula-to-latex/status")
def formula_runtime_status():
    return ok(_service().status())


@bp.post("/tools/formula-to-latex/recognize")
def recognize_formula():
    file = request.files.get("file")
    if file is None or not file.filename:
        return error("请选择公式图片", code="FORMULA_IMAGE_REQUIRED", status=400)

    max_bytes = int(current_app.config["FORMULA_UPLOAD_MAX_BYTES"])
    content = file.read(max_bytes + 1)
    if len(content) > max_bytes:
        return error("公式图片超过大小限制", code="FORMULA_IMAGE_TOO_LARGE", status=413)
    suffix = _validated_image_suffix(content)
    if suffix is None:
        return error(
            "仅支持有效的 PNG、JPG、WEBP、BMP 或 TIFF 图片",
            code="FORMULA_IMAGE_INVALID",
            status=415,
        )

    user_dir = Path(current_app.config["FORMULA_UPLOAD_DIR"]) / str(g.current_user.id)
    user_dir.mkdir(parents=True, exist_ok=True)
    image_path = user_dir / f"{uuid4()}{suffix}"
    image_path.write_bytes(content)
    try:
        result = _service().recognize(image_path)
    except FormulaRecognitionNotReady as exc:
        return error(str(exc), code="FORMULA_RUNTIME_NOT_READY", status=503)
    except FormulaRecognitionFailed as exc:
        if exc.detail:
            current_app.logger.error("Formula recognition failed: %s", exc.detail)
        return error(str(exc), code="FORMULA_RECOGNITION_FAILED", status=502)
    finally:
        image_path.unlink(missing_ok=True)

    return ok({**result, "fileName": Path(file.filename).name})


def _service() -> FormulaRecognitionService:
    return current_app.extensions["formula_recognition_service"]


def _validated_image_suffix(content: bytes) -> str | None:
    if not content:
        return None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(content)) as image:
                suffix = IMAGE_FORMAT_SUFFIXES.get(str(image.format or "").upper())
                width, height = image.size
                if suffix is None or width < 1 or height < 1:
                    return None
                if width * height > int(current_app.config["FORMULA_MAX_IMAGE_PIXELS"]):
                    return None
                image.verify()
        return suffix
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        return None
