from __future__ import annotations

import atexit
import os
import threading
from http.server import ThreadingHTTPServer

from flask import Flask


_lock = threading.Lock()
_server: ThreadingHTTPServer | None = None
_thread: threading.Thread | None = None


def start_embedded_knowledge_base(app: Flask) -> str:
    """Start the bundled knowledge-base API and return its local URL."""
    global _server, _thread

    if not app.config["KNOWLEDGE_BASE_EMBEDDED"]:
        return str(app.config["KNOWLEDGE_BASE_SERVICE_URL"])

    with _lock:
        if _server is None:
            _configure_runtime(app)
            from knowledge_base_runtime.backend.controller.http_handler import (
                KnowledgeBaseHandler,
            )
            from knowledge_base_runtime.backend.dao.database import init_db

            init_db()
            _server = ThreadingHTTPServer(("127.0.0.1", 0), KnowledgeBaseHandler)
            _thread = threading.Thread(
                target=_server.serve_forever,
                name="knowledge-base-runtime",
                daemon=True,
            )
            _thread.start()
            atexit.register(_shutdown)

        host, port = _server.server_address[:2]
        return f"http://{host}:{port}"


def _configure_runtime(app: Flask) -> None:
    values = {
        "DATABASE_URL": app.config["SQLALCHEMY_DATABASE_URI"],
        "KB_DB_SCHEMA": app.config["KNOWLEDGE_BASE_DB_SCHEMA"],
        "KB_SHARED_USER_SCHEMA": app.config["KNOWLEDGE_BASE_SHARED_USER_SCHEMA"],
        "KB_DATA_DIR": app.config["KNOWLEDGE_BASE_DATA_DIR"],
        "ELASTICSEARCH_ENABLED": app.config["KNOWLEDGE_BASE_ELASTICSEARCH_ENABLED"],
        "KB_MILVUS_ENABLED": app.config["KNOWLEDGE_BASE_MILVUS_ENABLED"],
        "CHUNK_QA_MODEL_BASE_URL": app.config["CHUNK_QA_MODEL_BASE_URL"],
        "CHUNK_QA_MODEL_API_KEY": app.config["CHUNK_QA_MODEL_API_KEY"],
        "CHUNK_QA_MODEL_NAME": app.config["CHUNK_QA_MODEL_NAME"],
        "CHUNK_QA_MODEL_TIMEOUT": app.config["CHUNK_QA_MODEL_TIMEOUT"],
        "CHUNK_QA_MODEL_TEMPERATURE": app.config["CHUNK_QA_MODEL_TEMPERATURE"],
        "CHUNK_QA_MODEL_RESPONSE_FORMAT": app.config["CHUNK_QA_MODEL_RESPONSE_FORMAT"],
    }
    for name, value in values.items():
        # Empty optional config values must not overwrite the runtime's own
        # defaults (especially when the embedded service is used in tests).
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        os.environ[name] = str(value)


def _shutdown() -> None:
    global _server, _thread
    if _server is not None:
        _server.shutdown()
        _server.server_close()
    _server = None
    _thread = None
