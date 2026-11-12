from __future__ import annotations

import os
import re
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
BASE_DIR = Path(__file__).resolve().parents[2]
PROJECT_DIR = BASE_DIR
INFRASTRUCTURE_DIR = BASE_DIR / "infrastructure"
FILE_STORAGE_DIR = INFRASTRUCTURE_DIR / "file-storage"
EXTERNAL_DEP_DIR = INFRASTRUCTURE_DIR / "external-dep"
RUNTIME_DIR = EXTERNAL_DEP_DIR / "runtime"
FRONTEND_DIR = BASE_DIR / "frontend"
FRONTEND_INDEX = FRONTEND_DIR / "views" / "index.html"
FRONTEND_ASSETS_DIR = FRONTEND_DIR / "assets"
FRONTEND_API_DIR = FRONTEND_DIR / "api"


def _project_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else BASE_DIR / path


def _load_dotenv_file(env_path: Path, *, override_file_values: bool = False, file_loaded_keys: set[str] | None = None) -> set[str]:
    loaded = file_loaded_keys or set()
    if not env_path.exists():
        return loaded
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key not in os.environ or (override_file_values and key in loaded):
            os.environ[key] = value
            loaded.add(key)
    return loaded


def _load_dotenv() -> None:
    loaded: set[str] = set()
    # The embedded runtime is launched by the main Flask service, while a
    # standalone deployment may launch app_server.py directly. In both cases
    # the canonical deployment configuration lives in backend/.env.
    loaded = _load_dotenv_file(BASE_DIR.parent / ".env", file_loaded_keys=loaded)
    loaded = _load_dotenv_file(BACKEND_DIR / ".env", file_loaded_keys=loaded)
    _load_dotenv_file(BACKEND_DIR / ".env.qwen.local", override_file_values=True, file_loaded_keys=loaded)


_load_dotenv()

DATA_DIR = _project_path(os.getenv("KB_DATA_DIR", FILE_STORAGE_DIR / "data"))
PDF_DIR = DATA_DIR / "pdfs"
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = _project_path(os.getenv("KB_DB_PATH", DATA_DIR / "knowledge_base.sqlite3"))
DATABASE_URL = os.getenv("DATABASE_URL", "").strip().replace(
    "postgresql+psycopg://", "postgresql://", 1
)
DB_BACKEND = "postgresql" if DATABASE_URL.startswith(("postgresql://", "postgres://")) else "sqlite"


def _sql_identifier(value: str, default: str) -> str:
    candidate = (value or default).strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", candidate):
        raise ValueError(f"invalid SQL identifier: {candidate!r}")
    return candidate


# PostgreSQL integration keeps the knowledge domain in the same database as
# demov1.5 while preserving a clear ownership boundary between table groups.
KB_DB_SCHEMA = _sql_identifier(os.getenv("KB_DB_SCHEMA", "knowledge_base"), "knowledge_base")
KB_SHARED_USER_SCHEMA = _sql_identifier(
    os.getenv("KB_SHARED_USER_SCHEMA", "zhiyan"), "zhiyan"
)

DEFAULT_USER_ID = os.getenv("KB_DEFAULT_USER_ID", "demo-user")

ELASTICSEARCH_URL = os.getenv("ELASTICSEARCH_URL", "http://127.0.0.1:9200").strip()
ELASTICSEARCH_INDEX = os.getenv("ELASTICSEARCH_INDEX", "papers_idx").strip()
ELASTICSEARCH_ENABLED = os.getenv("ELASTICSEARCH_ENABLED", "1").strip() not in {"0", "false", "False"}
ELASTICSEARCH_USERNAME = os.getenv("ELASTICSEARCH_USERNAME", "").strip()
ELASTICSEARCH_PASSWORD = os.getenv("ELASTICSEARCH_PASSWORD", "").strip()
ELASTICSEARCH_CA_CERT = os.getenv("ELASTICSEARCH_CA_CERT", "").strip()

KB_MILVUS_URI = str(
    _project_path(
        os.getenv(
            "KB_MILVUS_URI",
            str(RUNTIME_DIR / "milvus_lite_runtime" / "milvus_lite_ascii.db"),
        ).strip()
    )
)
KB_MILVUS_COLLECTION = os.getenv("KB_MILVUS_COLLECTION", "paper_chunks_vec").strip()
KB_MILVUS_DIM = int(os.getenv("KB_MILVUS_DIM", "1024"))
KB_MILVUS_ENABLED = os.getenv("KB_MILVUS_ENABLED", "1").strip() not in {"0", "false", "False"}
KB_EMBEDDING_BACKEND = os.getenv("KB_EMBEDDING_BACKEND", "api").strip().lower()
KB_EMBEDDING_API_BASE_URL = os.getenv("KB_EMBEDDING_API_BASE_URL", "").strip().rstrip("/")
KB_EMBEDDING_API_KEY = os.getenv("KB_EMBEDDING_API_KEY", "").strip()
KB_EMBEDDING_API_MODEL = os.getenv("KB_EMBEDDING_API_MODEL", "BAAI/bge-m3").strip()
KB_EMBEDDING_API_ENDPOINT = os.getenv("KB_EMBEDDING_API_ENDPOINT", "/embeddings").strip()
KB_EMBEDDING_API_TIMEOUT_SECONDS = float(os.getenv("KB_EMBEDDING_API_TIMEOUT_SECONDS", "120") or 120)
KB_EMBEDDING_API_MAX_RETRIES = max(0, int(os.getenv("KB_EMBEDDING_API_MAX_RETRIES", "3") or 3))
OLLAMA_EMBED_BASE_URL = os.getenv("OLLAMA_EMBED_BASE_URL", "http://127.0.0.1:11434").strip().rstrip("/")
OLLAMA_EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "bge-m3:latest").strip()
OLLAMA_EMBED_TIMEOUT_SECONDS = float(os.getenv("OLLAMA_EMBED_TIMEOUT_SECONDS", "120") or 120)
BGE_M3_MODEL = str(
    _project_path(
        os.getenv("BGE_M3_MODEL", str(RUNTIME_DIR / "models" / "BAAI_bge-m3")).strip()
    )
)
BGE_EMBED_BATCH_SIZE = int(os.getenv("BGE_EMBED_BATCH_SIZE", "16") or 16)
BGE_USE_FP16 = os.getenv("BGE_USE_FP16", "1").strip() not in {"0", "false", "False"}
BGE_RERANKER_MODEL = os.getenv("BGE_RERANKER_MODEL", "").strip()
KB_RERANKER_BACKEND = os.getenv("KB_RERANKER_BACKEND", "api").strip().lower()
KB_RERANKER_API_BASE_URL = os.getenv("KB_RERANKER_API_BASE_URL", "").strip().rstrip("/")
KB_RERANKER_API_KEY = os.getenv("KB_RERANKER_API_KEY", "").strip()
KB_RERANKER_API_MODEL = os.getenv("KB_RERANKER_API_MODEL", "BAAI/bge-reranker-v2-m3").strip()
KB_RERANKER_API_ENDPOINT = os.getenv("KB_RERANKER_API_ENDPOINT", "/rerank").strip()
KB_RERANKER_API_TIMEOUT_SECONDS = float(os.getenv("KB_RERANKER_API_TIMEOUT_SECONDS", "120") or 120)
KB_RERANKER_API_MAX_RETRIES = max(0, int(os.getenv("KB_RERANKER_API_MAX_RETRIES", "3") or 3))

# The splitter runs as a separate service.  Keeping the address configurable
# lets the same package be used on a teammate's LAN without changing code.
SPLITTER_API_BASE_URL = os.getenv("SPLITTER_API_BASE_URL", "http://10.82.148.6:8000").rstrip("/")

CHUNK_QA_MODEL_BASE_URL = os.getenv(
    "CHUNK_QA_MODEL_BASE_URL",
    "https://k9n4n0vf52f6239t-9001.js01-webservice.gpuhome.cc:50000/v1",
).strip().rstrip("/")
CHUNK_QA_MODEL_API_KEY = os.getenv("CHUNK_QA_MODEL_API_KEY", "sk-unsloth-YOUR_KEY").strip()
CHUNK_QA_MODEL_NAME = os.getenv(
    "CHUNK_QA_MODEL_NAME", "Qwen3.8-27B-UD-Q4_K_XL"
).strip()
CHUNK_QA_MODEL_TIMEOUT = float(os.getenv("CHUNK_QA_MODEL_TIMEOUT", "60") or 60)
CHUNK_QA_MODEL_TEMPERATURE = float(os.getenv("CHUNK_QA_MODEL_TEMPERATURE", "0.2") or 0.2)
CHUNK_QA_MODEL_RESPONSE_FORMAT = os.getenv("CHUNK_QA_MODEL_RESPONSE_FORMAT", "json_object").strip()


def ensure_data_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
