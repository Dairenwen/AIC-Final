from __future__ import annotations

import argparse
import base64
import cgi
import json
import sys
from pathlib import Path
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from urllib.parse import parse_qs, unquote, urlparse

ROOT_DIR = Path(__file__).resolve().parents[2]
LOCAL_PACKAGES = ROOT_DIR / "backend" / ".local_packages"
if LOCAL_PACKAGES.exists():
    sys.path.insert(0, str(LOCAL_PACKAGES))

from knowledge_base_runtime.backend.config.settings import DEFAULT_USER_ID, FRONTEND_API_DIR, FRONTEND_ASSETS_DIR, FRONTEND_INDEX
from knowledge_base_runtime.backend.dao.database import db_label, init_db
from knowledge_base_runtime.backend.service.agent import get_agent_status, invoke_agent
from knowledge_base_runtime.backend.service.audit import get_audit_stats, list_audit_logs
from knowledge_base_runtime.backend.service.collections import add_paper, create_collection, get_collection_papers, list_collections, remove_paper
from knowledge_base_runtime.backend.service.knowledge import list_knowledge_papers, list_paper_chunks, slice_papers
from knowledge_base_runtime.backend.service.metadata import (
    create_paper,
    delete_paper,
    get_paper,
    get_stats,
    ingest_papers,
    list_exceptions,
    list_papers,
    retry_parse,
    update_paper,
)
from knowledge_base_runtime.backend.service.pwc_crawler import import_crawled_papers, list_task_runs
from knowledge_base_runtime.backend.service.qa import export_dpo_jsonl, generate_dpo, generate_qa, list_qa_chunks, manual_review, submit_review
from knowledge_base_runtime.backend.service.retrieval_backends import external_status
from knowledge_base_runtime.backend.service.retrieval_index import get_retrieval_index_status, rebuild_retrieval_indexes
from knowledge_base_runtime.backend.service.maintenance import guard_knowledge_write
from knowledge_base_runtime.backend.service.search import search_papers
from knowledge_base_runtime.backend.service.uploads import confirm_upload, create_upload_task, get_upload_task, list_upload_tasks
from knowledge_base_runtime.backend.service.users import (
    create_user,
    delete_user,
    list_users,
    reset_api_key,
    update_user_role,
    update_user_status,
)


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400):
        self.message = message
        self.status = status
        super().__init__(message)


class KnowledgeBaseHandler(BaseHTTPRequestHandler):
    server_version = "KnowledgeBaseBackend/0.1"

    def do_OPTIONS(self) -> None:
        self._send_json({"ok": True})

    def do_GET(self) -> None:
        try:
            path, query = self._path_query()
            if path == "/":
                if FRONTEND_INDEX.exists():
                    self._send_file(FRONTEND_INDEX, "text/html; charset=utf-8")
                    return
            if path.startswith("/assets/"):
                asset = (FRONTEND_ASSETS_DIR / path.removeprefix("/assets/")).resolve()
                if FRONTEND_ASSETS_DIR in asset.parents and asset.exists():
                    content_type = "application/javascript; charset=utf-8" if asset.suffix == ".js" else "text/css; charset=utf-8"
                    self._send_file(asset, content_type)
                    return
            if path == "/api/kbApi.js":
                api_asset = FRONTEND_API_DIR / "kbApi.js"
                if api_asset.exists():
                    self._send_file(api_asset, "application/javascript; charset=utf-8")
                    return
            if path in {"/vendor/vue.global.prod.js", "/vendor/echarts.min.js"}:
                asset = FRONTEND_ASSETS_DIR / "vendor" / Path(path).name
                if asset.exists():
                    self._send_file(asset, "application/javascript; charset=utf-8")
                    return
            if path == "/favicon.ico":
                self.send_response(204)
                self.end_headers()
                return
            if path in {"/api/v1", "/api/v1/health"}:
                self._send_json(
                    {
                        "status": "ok",
                        "service": "knowledge-base-backend",
                        "endpoints": [
                            "POST /api/v1/ingest/metadata",
                            "POST /api/v1/search",
                            "GET /api/v1/papers",
                            "GET /api/v1/collections",
                            "POST /api/v1/upload/pdf",
                            "POST /api/v1/upload/confirm",
                            "POST /api/v1/agent/invoke",
                            "GET /api/v1/admin/knowledge",
                            "POST /api/v1/admin/knowledge/slice",
                            "GET /api/v1/admin/retrieval/status",
                            "POST /api/v1/admin/retrieval/reindex",
                        ],
                    }
                )
                return
            if path == "/api/v1/admin/stats":
                self._send_json(get_stats())
                return
            if path == "/api/v1/admin/dashboard":
                self._send_json(get_stats())
                return
            if path == "/api/v1/admin/service-health":
                self._send_json(
                    [
                        {
                            "name": "API",
                            "healthy": True,
                            "latency": "本机 HTTP 服务",
                            "endpoint": "本机 HTTP 服务",
                            "uptime": "running",
                        },
                        {
                            "name": db_label(),
                            "healthy": True,
                            "latency": "数据库连接",
                            "endpoint": "数据库连接",
                            "uptime": "running",
                        },
                        *external_status(),
                    ]
                )
                return
            if path == "/api/v1/admin/retrieval/status":
                self._send_json(get_retrieval_index_status())
                return
            if path == "/api/v1/admin/qa/chunks":
                self._send_json(
                    list_qa_chunks(
                        search=query.get("search", [None])[0],
                        page=_int_query(query, "page", 1),
                        size=_int_query(query, "size", 100),
                        domain=query.get("domain", [None])[0],
                        generation_status=query.get("generation_status", [None])[0],
                    )
                )
                return
            if path == "/api/v1/admin/qa/dpo/export":
                data, filename = export_dpo_jsonl(query.get("run_id", [""])[0])
                self._send_bytes(
                    data.encode("utf-8"),
                    "application/x-ndjson; charset=utf-8",
                    filename=filename,
                )
                return
            if path == "/api/v1/admin/venues":
                self._send_json(self._list_venues())
                return
            if path == "/api/v1/admin/papers":
                status = query.get("parse_status", [None])[0]
                self._send_json(
                    list_papers(
                        _int_query(query, "page", 1),
                        _int_query(query, "size", 20),
                        search=query.get("search", [None])[0],
                        parse_status=int(status) if status not in (None, "") else None,
                    )
                )
                return
            if path == "/api/v1/admin/knowledge":
                self._send_json(
                    list_knowledge_papers(
                        domain=query.get("domain", ["General"])[0],
                        search=query.get("search", [None])[0],
                        ccf_level=query.get("ccf_level", [None])[0],
                        sliced=query.get("sliced", [None])[0],
                        page=_int_query(query, "page", 1),
                        size=_int_query(query, "size", 20),
                    )
                )
                return
            if path.startswith("/api/v1/admin/knowledge/") and path.endswith("/chunks"):
                parts = path.strip("/").split("/")
                paper_id = unquote(parts[4])
                self._send_json(
                    list_paper_chunks(
                        paper_id,
                        page=_int_query(query, "page", 1),
                        size=_int_query(query, "size", 20),
                    )
                )
                return
            if path == "/api/v1/admin/exceptions":
                self._send_json(list_exceptions())
                return
            if path == "/api/v1/admin/audit-stats":
                self._send_json(get_audit_stats())
                return
            if path == "/api/v1/admin/audit-logs":
                self._send_json(
                    list_audit_logs(
                        operate_type=query.get("operate_type", query.get("action", [None]))[0],
                        operate_sub_type=query.get("operate_sub_type", [None])[0],
                        target_resource_type=query.get("target_resource_type", query.get("object_type", [None]))[0],
                        operate_user_id=query.get("operate_user_id", query.get("user_id", [None]))[0],
                        keyword=query.get("keyword", [None])[0],
                        start_time=query.get("start_time", [None])[0],
                        end_time=query.get("end_time", [None])[0],
                        page=_int_query(query, "page", 1),
                        size=_int_query(query, "size", 20),
                    )
                )
                return
            if path == "/api/v1/admin/crawler/runs":
                self._send_json(
                    list_task_runs(
                        page=_int_query(query, "page", 1),
                        size=_int_query(query, "size", 20),
                    )
                )
                return
            if path == "/api/v1/admin/users":
                self._send_json(list_users(_int_query(query, "page", 1), _int_query(query, "size", 50)))
                return
            if path == "/api/v1/papers":
                self._send_json(
                    list_papers(
                        _int_query(query, "page", 1),
                        _int_query(query, "size", 20),
                        search=query.get("search", [None])[0],
                    )
                )
                return
            if path.startswith("/api/v1/papers/"):
                paper_id = path.rsplit("/", 1)[-1]
                paper = get_paper(paper_id)
                if paper is None:
                    raise ApiError("paper not found", 404)
                self._send_json(paper)
                return
            if path == "/api/v1/collections":
                self._send_json(list_collections(self._user_id()))
                return
            if path.startswith("/api/v1/collections/") and path.endswith("/papers"):
                parts = path.strip("/").split("/")
                self._send_json(get_collection_papers(int(parts[3]), self._user_id()))
                return
            if path == "/api/v1/upload/tasks":
                self._send_json({"list": list_upload_tasks(self._user_id())})
                return
            if path.startswith("/api/v1/upload/status/"):
                task_id = path.rsplit("/", 1)[-1]
                task = get_upload_task(task_id, self._user_id())
                if task is None:
                    raise ApiError("upload task not found", 404)
                self._send_json(task)
                return
            if path.startswith("/api/v1/agent/status/"):
                job_id = path.rsplit("/", 1)[-1]
                job = get_agent_status(job_id)
                if job is None:
                    raise ApiError("agent job not found", 404)
                self._send_json(job)
                return
            raise ApiError("endpoint not found", 404)
        except Exception as exc:
            self._handle_error(exc)

    @guard_knowledge_write
    def do_POST(self) -> None:
        try:
            path, _ = self._path_query()
            if path == "/api/v1/ingest/metadata":
                payload = self._read_json()
                items = payload if isinstance(payload, list) else payload.get("items", [])
                if not isinstance(items, list):
                    raise ApiError("metadata payload must be an array or {items: [...]}")
                self._send_json(ingest_papers(items, user_id=self._user_id(), ip=self._client_ip()))
                return
            if path == "/api/v1/admin/crawler/import":
                payload = self._read_json()
                input_text = str(payload.get("input") or "").strip()
                if not input_text:
                    raise ApiError("input is required")
                input_path = Path(input_text).expanduser()
                if not input_path.exists():
                    raise ApiError("input path not found", 404)
                self._send_json(
                    import_crawled_papers(
                        input_path,
                        task_name=str(payload.get("task_name") or "pwc_manual_import"),
                        user_id=self._user_id(),
                        log_file=payload.get("log_file"),
                    ),
                    202,
                )
                return
            if path == "/api/v1/search":
                self._send_json(search_papers(self._read_json()))
                return
            if path == "/api/v1/collections":
                payload = self._read_json()
                name = str(payload.get("collection_name") or payload.get("name") or "").strip()
                if not name:
                    raise ApiError("collection_name is required")
                self._send_json(create_collection(self._user_id(), name), 201)
                return
            if path.startswith("/api/v1/collections/") and path.endswith("/papers"):
                parts = path.strip("/").split("/")
                collection_id = int(parts[3])
                payload = self._read_json()
                paper_id = str(payload.get("paper_id") or "").strip()
                if not paper_id:
                    raise ApiError("paper_id is required")
                self._send_json(add_paper(collection_id, paper_id, payload.get("note"), self._user_id()))
                return
            if path == "/api/v1/admin/papers":
                self._send_json(create_paper(self._read_json(), self._user_id(), ip=self._client_ip()), 201)
                return
            if path == "/api/v1/admin/knowledge/slice":
                self._send_json(slice_papers(self._read_json(), self._user_id(), ip=self._client_ip()), 202)
                return
            if path == "/api/v1/admin/retrieval/reindex":
                self._send_json(rebuild_retrieval_indexes(self._read_json()), 202)
                return
            if path == "/api/v1/admin/venues":
                self._send_json({"ok": True}, 201)
                return
            if path.startswith("/api/v1/admin/exceptions/") and path.endswith("/retry"):
                parts = path.strip("/").split("/")
                self._send_json(retry_parse(parts[4], self._user_id(), ip=self._client_ip()))
                return
            if path.startswith("/api/v1/admin/users/") and path.endswith("/reset-api-key"):
                parts = path.strip("/").split("/")
                self._send_json(reset_api_key(int(parts[4]), self._user_id(), ip=self._client_ip()))
                return
            if path == "/api/v1/admin/users":
                self._send_json(create_user(self._read_json(), self._user_id(), ip=self._client_ip()), 201)
                return
            if path == "/api/v1/admin/qa/generate":
                self._send_json(generate_qa(self._read_json(), self._user_id(), ip=self._client_ip()), 202)
                return
            if path == "/api/v1/admin/qa/generate-dpo":
                self._send_json(generate_dpo(self._read_json(), self._user_id(), ip=self._client_ip()), 202)
                return
            if path == "/api/v1/admin/qa/submit-review":
                self._send_json(submit_review(self._read_json(), self._user_id(), ip=self._client_ip()), 202)
                return
            if path == "/api/v1/admin/qa/manual-review":
                self._send_json(manual_review(self._read_json(), self._user_id(), ip=self._client_ip()))
                return
            if path == "/api/v1/upload/pdf":
                filename, data = self._read_upload()
                if not data:
                    raise ApiError("empty upload")
                self._send_json(create_upload_task(filename, data, self._user_id(), ip=self._client_ip()), 201)
                return
            if path == "/api/v1/upload/confirm":
                self._send_json(confirm_upload(self._read_json(), self._user_id(), ip=self._client_ip()))
                return
            if path == "/api/v1/agent/invoke":
                self._send_json(invoke_agent(self._read_json(), self._user_id(), ip=self._client_ip()), 202)
                return
            raise ApiError("endpoint not found", 404)
        except Exception as exc:
            self._handle_error(exc)

    @guard_knowledge_write
    def do_PUT(self) -> None:
        try:
            path, _ = self._path_query()
            if path.startswith("/api/v1/admin/papers/"):
                paper_id = path.rsplit("/", 1)[-1]
                self._send_json(update_paper(paper_id, self._read_json(), self._user_id(), ip=self._client_ip()))
                return
            if path.startswith("/api/v1/admin/venues/"):
                self._send_json({"ok": True})
                return
            if path.startswith("/api/v1/admin/users/"):
                parts = path.strip("/").split("/")
                user_id = int(parts[4])
                payload = self._read_json()
                if path.endswith("/role"):
                    self._send_json(update_user_role(user_id, payload.get("role"), self._user_id(), ip=self._client_ip()))
                    return
                if path.endswith("/status"):
                    self._send_json(update_user_status(user_id, payload.get("active"), self._user_id(), ip=self._client_ip()))
                    return
            raise ApiError("endpoint not found", 404)
        except Exception as exc:
            self._handle_error(exc)

    @guard_knowledge_write
    def do_DELETE(self) -> None:
        try:
            path, _ = self._path_query()
            if path.startswith("/api/v1/collections/") and "/papers/" in path:
                parts = path.strip("/").split("/")
                self._send_json(remove_paper(int(parts[3]), parts[5], self._user_id()))
                return
            if path.startswith("/api/v1/admin/papers/"):
                paper_id = path.rsplit("/", 1)[-1]
                self._send_json(delete_paper(paper_id, self._user_id(), ip=self._client_ip()))
                return
            if path.startswith("/api/v1/admin/venues/"):
                self._send_json({"deleted": 0})
                return
            if path.startswith("/api/v1/admin/users/"):
                user_id = int(path.rsplit("/", 1)[-1])
                self._send_json(delete_user(user_id, self._user_id(), ip=self._client_ip()))
                return
            raise ApiError("endpoint not found", 404)
        except Exception as exc:
            self._handle_error(exc)

    def log_message(self, fmt: str, *args) -> None:
        print("%s - - %s" % (self.address_string(), fmt % args))

    def _path_query(self) -> tuple[str, dict[str, list[str]]]:
        parsed = urlparse(self.path)
        return parsed.path.rstrip("/") or "/", parse_qs(parsed.query)

    def _user_id(self) -> str:
        return self.headers.get("X-User-Id") or DEFAULT_USER_ID

    def _client_ip(self) -> str:
        forwarded = self.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",", 1)[0].strip()
        return self.client_address[0] if self.client_address else "-"

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b""

    def _read_json(self) -> dict | list:
        raw = self._read_body()
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ApiError(f"invalid JSON: {exc}") from exc

    def _read_upload(self) -> tuple[str, bytes]:
        content_type = self.headers.get("Content-Type", "")
        raw = self._read_body()
        if content_type.startswith("multipart/form-data"):
            environ = {
                "REQUEST_METHOD": "POST",
                "CONTENT_TYPE": content_type,
                "CONTENT_LENGTH": str(len(raw)),
            }
            form = cgi.FieldStorage(fp=BytesIO(raw), headers=self.headers, environ=environ)
            file_item = form["file"] if "file" in form else None
            if file_item is None or not getattr(file_item, "file", None):
                raise ApiError("multipart field 'file' is required")
            filename = file_item.filename or "upload.pdf"
            return filename, file_item.file.read()
        payload = json.loads(raw.decode("utf-8")) if raw else {}
        filename = payload.get("filename") or "upload.pdf"
        content_b64 = payload.get("content_base64")
        if not content_b64:
            raise ApiError("use multipart file field or JSON content_base64")
        return filename, base64.b64decode(content_b64)

    def _send_json(self, payload, status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,PUT,DELETE,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type,X-User-Id")
        self.end_headers()
        self.wfile.write(data)

    def _send_file(self, path: Path, content_type: str) -> None:
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_bytes(self, data: bytes, content_type: str, filename: str | None = None) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(data)

    def _list_venues(self) -> dict:
        from knowledge_base_runtime.backend.dao.database import get_db

        with get_db() as db:
            rows = db.execute(
                """
                SELECT publish_venue AS short_name, publish_venue AS full_name, COUNT(*) AS paper_count
                FROM papers
                WHERE publish_venue IS NOT NULL AND publish_venue != ''
                GROUP BY publish_venue
                ORDER BY publish_venue
                """
            ).fetchall()
        items = []
        for index, row in enumerate(rows, start=1):
            item = dict(row)
            item.update({"id": index, "ccf_level": "-", "type": "会议/期刊", "website": ""})
            items.append(item)
        return {"total": len(items), "list": items}

    def _handle_error(self, exc: Exception) -> None:
        if isinstance(exc, ApiError):
            self._send_json({"error": exc.message}, exc.status)
            return
        if isinstance(exc, ValueError):
            self._send_json({"error": str(exc)}, 400)
            return
        self._send_json({"error": "internal server error", "detail": str(exc)}, 500)


def _int_query(query: dict[str, list[str]], name: str, default: int) -> int:
    try:
        return int(query.get(name, [default])[0])
    except (TypeError, ValueError):
        return default


def main() -> None:
    parser = argparse.ArgumentParser(description="Knowledge base backend service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    init_db()
    server = ThreadingHTTPServer((args.host, args.port), KnowledgeBaseHandler)
    print(f"Knowledge base backend running at http://{args.host}:{args.port}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
