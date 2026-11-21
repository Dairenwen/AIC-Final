from __future__ import annotations

import threading
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from flask import Blueprint, Response, current_app, g, jsonify, request, send_from_directory

from .auth import require_role
from .responses import error


bp = Blueprint("knowledge_base", __name__)
STATIC_DIR = Path(__file__).resolve().parents[1] / "integrations" / "knowledge_base" / "static"
FORWARDED_RESPONSE_HEADERS = {"content-type", "content-disposition", "cache-control"}
ADMIN_TABS = {
    "dashboard",
    "knowledge",
    "qaGenerate",
    "qaReview",
    "trainingSet",
    "exceptions",
    "audit",
    "permissions",
}
def require_system_admin():
    return require_role("system_admin")


@bp.get("/ui")
def management_ui():
    forbidden = require_system_admin()
    if forbidden:
        return forbidden
    source = _frontend_dir() / "views" / "index.html"
    if not source.is_file():
        return send_from_directory(STATIC_DIR, "index.html")

    html = source.read_text(encoding="utf-8")
    html = html.replace(
        'src="/assets/', 'src="/api/v1/knowledge-base/assets/'
    ).replace(
        'src="/api/kbApi.js"',
        'src="/api/v1/knowledge-base/assets/kbApi.js"',
    )
    if request.args.get("embed") == "1":
        tab = request.args.get("tab", "dashboard")
        if tab not in ADMIN_TABS:
            tab = "dashboard"
        if tab == "trainingSet":
            tab = "qaGenerate"
        html = html.replace("portalMode: 'user'", "portalMode: 'admin'", 1)
        html = html.replace("adminTab: 'dashboard'", f"adminTab: '{tab}'", 1)
        html = html.replace(
            "</head>",
            """<style>
body.embedded .app-header { display: none !important; }
body.embedded .sidebar { display: none !important; }
body.embedded .app-body { height: 100vh; }
body.embedded .main-content { padding: 24px 28px; }
@media (max-width: 720px) { body.embedded .main-content { padding: 18px 16px; } }
</style></head>""",
            1,
        )
        html = html.replace("<body>", '<body class="embedded embedded-admin">', 1)

    html = html.replace(
        '<script src="/api/v1/knowledge-base/assets/kbApi.js"></script>',
        """<script>
const kbNativeFetch = window.fetch.bind(window);
window.fetch = (resource, options = {}) => {
  const method = String(options.method || 'GET').toUpperCase();
  const headers = new Headers(options.headers || {});
  if (['POST', 'PUT', 'PATCH', 'DELETE'].includes(method)) {
    const csrfToken = sessionStorage.getItem('zhiyan.auth.csrf');
    if (csrfToken) headers.set('X-CSRF-Token', csrfToken);
  }
  return kbNativeFetch(resource, { ...options, headers, credentials: 'same-origin' });
};
</script>
<script src="/api/v1/knowledge-base/assets/kbApi.js"></script>""",
        1,
    )
    return Response(html, content_type="text/html; charset=utf-8")


@bp.get("/assets/<path:asset_path>")
def management_asset(asset_path: str):
    forbidden = require_system_admin()
    if forbidden:
        return forbidden
    if asset_path == "kbApi.js":
        api_script = _frontend_dir() / "api" / "kbApi.js"
        if api_script.is_file():
            body = api_script.read_text(encoding="utf-8").replace(
                "baseURL: '/api/v1'",
                "baseURL: '/api/v1/knowledge-base'",
                1,
            )
            return Response(body, content_type="text/javascript; charset=utf-8")
    source_assets = _frontend_dir() / "assets"
    if source_assets.is_dir():
        return send_from_directory(source_assets, asset_path)
    return send_from_directory(STATIC_DIR, asset_path)


_index_text_lock = threading.Lock()


@bp.post("/admin/papers/<paper_id>/index-text")
def index_paper_text(paper_id: str):
    """Chunk and vectorise one paper from text extracted elsewhere.

    Milvus Lite can only be opened by one process, and this process holds it,
    so the background indexing script (scripts/kb_index_daemon.py) hands the
    extracted page text over here instead of writing vectors itself.
    """
    forbidden = require_system_admin()
    if forbidden:
        return forbidden
    payload = request.get_json(silent=True) or dict()
    text = str(payload.get("text") or "")
    if len(text.strip()) < 200:
        return error("论文文本过短，无法切块", code="TEXT_TOO_SHORT", status=400)

    from knowledge_base_runtime.backend.dao.database import get_db
    from knowledge_base_runtime.backend.service.chunking import (
        keep_body_sections,
        normalize_pdf_text,
        replace_chunks,
    )

    with get_db() as kb:
        row = kb.execute(
            "SELECT id FROM papers WHERE id = ? AND delete_time IS NULL", (paper_id,)
        ).fetchone()
    if row is None:
        return error("文献不存在", code="PAPER_NOT_FOUND", status=404)

    # One paper at a time keeps memory flat and leaves the other worker
    # threads free for interactive requests.
    with _index_text_lock:
        cleaned = keep_body_sections(normalize_pdf_text(text))
        count = replace_chunks(paper_id, cleaned)[0]
        with get_db() as kb:
            row = kb.execute(
                "SELECT vector_index_time FROM papers WHERE id = ?", (paper_id,)
            ).fetchone()
    vectorized = bool(row and row["vector_index_time"])
    return jsonify({"paper_id": paper_id, "chunks": count, "vectorized": vectorized})


@bp.route("/<path:legacy_path>", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
def proxy_knowledge_base(legacy_path: str):
    forbidden = require_system_admin()
    if forbidden:
        return forbidden
    if ".." in legacy_path.split("/"):
        return error("无效的知识库服务路径", code="INVALID_PATH", status=400)

    # Keep the model selector available while an older embedded runtime is
    # being restarted. This endpoint is configuration-only and exposes no key.
    if legacy_path == "admin/qa/config":
        model_name = str(current_app.config.get("CHUNK_QA_MODEL_NAME") or "").strip()
        return jsonify(
            {
                "model_identifier": model_name,
                "default_model": model_name,
                "model": model_name,
                "base_url": str(current_app.config.get("CHUNK_QA_MODEL_BASE_URL") or "").strip(),
                "timeout_seconds": current_app.config.get("CHUNK_QA_MODEL_TIMEOUT", 60),
                "temperature": current_app.config.get("CHUNK_QA_MODEL_TEMPERATURE", 0.2),
                "response_format": current_app.config.get(
                    "CHUNK_QA_MODEL_RESPONSE_FORMAT", "json_object"
                ),
            }
        )

    service_url = str(current_app.config["KNOWLEDGE_BASE_SERVICE_URL"]).rstrip("/")
    upstream_url = f"{service_url}/api/v1/{legacy_path}"
    if request.query_string:
        upstream_url = f"{upstream_url}?{request.query_string.decode('latin-1')}"

    headers = {
        "Accept": request.headers.get("Accept", "application/json"),
        "X-User-Id": str(g.current_user.id),
        "X-User-Role": str(g.current_user.role_code),
    }
    if request.content_type:
        headers["Content-Type"] = request.content_type

    upstream_request = Request(
        upstream_url,
        data=request.get_data(cache=False) if request.method not in {"GET", "HEAD"} else None,
        headers=headers,
        method=request.method,
    )
    timeout = float(current_app.config["KNOWLEDGE_BASE_PROXY_TIMEOUT_SECONDS"])
    try:
        with urlopen(upstream_request, timeout=timeout) as upstream:
            return _upstream_response(upstream.read(), upstream.status, upstream.headers.items())
    except HTTPError as exc:
        return _upstream_response(exc.read(), exc.code, exc.headers.items())
    except (URLError, TimeoutError, OSError):
        return error(
            "知识库服务暂不可用，请检查内置运行时状态",
            code="KNOWLEDGE_BASE_UNAVAILABLE",
            status=502,
        )


def _upstream_response(body: bytes, status: int, headers) -> Response:
    response_headers = {
        name: value
        for name, value in headers
        if name.lower() in FORWARDED_RESPONSE_HEADERS
    }
    return Response(body, status=status, headers=response_headers)


def _frontend_dir() -> Path:
    return Path(current_app.config["KNOWLEDGE_BASE_ROOT"]) / "frontend"
