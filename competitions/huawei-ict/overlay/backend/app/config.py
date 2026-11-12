import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]


def as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def project_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    return (path if path.is_absolute() else BASE_DIR / path).resolve()


def interpreter_path(value: str | Path) -> Path:
    # Preserve venv interpreter symlinks: resolving them bypasses the venv.
    path = Path(value).expanduser()
    return Path(os.path.abspath(path if path.is_absolute() else BASE_DIR / path))


DATA_ROOT = project_path(os.getenv("ZHIYAN_DATA_ROOT", str(BASE_DIR)))
UPLOAD_ROOT = project_path(os.getenv("UPLOAD_FOLDER", str(DATA_ROOT / "uploads")))
GENERATED_ROOT = project_path(os.getenv("GENERATED_FOLDER", str(DATA_ROOT / "generated")))


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-only-change-me")
    MODEL_CONFIG_ENCRYPTION_KEY = os.getenv("MODEL_CONFIG_ENCRYPTION_KEY", "")
    AUTH_COOKIE_NAME = os.getenv("AUTH_COOKIE_NAME", "zhiyan_session")
    AUTH_TOKEN_MAX_AGE = int(os.getenv("AUTH_TOKEN_MAX_AGE", str(7 * 24 * 60 * 60)))
    AUTH_COOKIE_SECURE = as_bool(os.getenv("AUTH_COOKIE_SECURE"), False)
    SMS_PROVIDER = os.getenv("SMS_PROVIDER", "disabled").strip().lower()
    ALIYUN_SMS_ACCESS_KEY_ID = os.getenv("ALIYUN_SMS_ACCESS_KEY_ID", os.getenv("ALIBABA_CLOUD_ACCESS_KEY_ID", ""))
    ALIYUN_SMS_ACCESS_KEY_SECRET = os.getenv("ALIYUN_SMS_ACCESS_KEY_SECRET", os.getenv("ALIBABA_CLOUD_ACCESS_KEY_SECRET", ""))
    ALIYUN_SMS_SIGN_NAME = os.getenv("ALIYUN_SMS_SIGN_NAME", "")
    ALIYUN_SMS_TEMPLATE_CODE = os.getenv("ALIYUN_SMS_TEMPLATE_CODE", "")
    ALIYUN_SMS_TEMPLATE_MINUTES = int(os.getenv("ALIYUN_SMS_TEMPLATE_MINUTES", "5"))
    ALIYUN_SMS_SCHEME_NAME = os.getenv("ALIYUN_SMS_SCHEME_NAME", "")
    ALIYUN_SMS_REGION_ID = os.getenv("ALIYUN_SMS_REGION_ID", "cn-hangzhou")
    ALIYUN_SMS_ENDPOINT = os.getenv("ALIYUN_SMS_ENDPOINT", "https://dypnsapi.aliyuncs.com/")
    SMS_CODE_LENGTH = int(os.getenv("SMS_CODE_LENGTH", "6"))
    SMS_CODE_TTL_SECONDS = int(os.getenv("SMS_CODE_TTL_SECONDS", "300"))
    SMS_TIMEOUT_SECONDS = float(os.getenv("SMS_TIMEOUT_SECONDS", "10"))
    SMS_CODE_MAX_ATTEMPTS = int(os.getenv("SMS_CODE_MAX_ATTEMPTS", "5"))
    SQLALCHEMY_DATABASE_URI = os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg://zhiyan:zhiyan@localhost:5432/zhiyan",
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_recycle": 1800,
    }
    CORS_ORIGINS = [
        origin.strip()
        for origin in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",")
        if origin.strip()
    ]
    KNOWLEDGE_BASE_SERVICE_URL = os.getenv(
        "KNOWLEDGE_BASE_SERVICE_URL", "http://127.0.0.1:8768"
    )
    KNOWLEDGE_BASE_ROOT = project_path(
        os.getenv("KNOWLEDGE_BASE_ROOT", "knowledge_base_runtime")
    )
    KNOWLEDGE_BASE_EMBEDDED = as_bool(os.getenv("KNOWLEDGE_BASE_EMBEDDED"), True)
    KNOWLEDGE_BASE_DB_SCHEMA = os.getenv("KB_DB_SCHEMA", "knowledge_base")
    KNOWLEDGE_BASE_SHARED_USER_SCHEMA = os.getenv("KB_SHARED_USER_SCHEMA", "zhiyan")
    KNOWLEDGE_BASE_DATA_DIR = str(project_path(os.getenv(
        "KB_DATA_DIR", str(KNOWLEDGE_BASE_ROOT / "infrastructure" / "file-storage" / "data")
    )))
    KNOWLEDGE_BASE_ELASTICSEARCH_ENABLED = as_bool(os.getenv("ELASTICSEARCH_ENABLED"), False)
    KNOWLEDGE_BASE_MILVUS_ENABLED = as_bool(os.getenv("KB_MILVUS_ENABLED"), False)
    KNOWLEDGE_BASE_PROXY_TIMEOUT_SECONDS = float(
        os.getenv("KNOWLEDGE_BASE_PROXY_TIMEOUT_SECONDS", "120")
    )
    # The formula runtime is part of this checkout.  Keep its code, model
    # files, and virtual environment together so production never depends on
    # a sibling/external delivery directory.
    FORMULA_RECOGNITION_ROOT = (
        BASE_DIR / "app" / "tools" / "formula_recognition" / "runtime"
    )
    # Do not call resolve() here: Linux venv/bin/python is often a symlink to
    # the system interpreter, and resolving it would bypass the venv itself.
    FORMULA_RECOGNITION_PYTHON = interpreter_path(os.getenv(
        "FORMULA_RECOGNITION_PYTHON",
        str(FORMULA_RECOGNITION_ROOT / ".venv" /
            ("Scripts/python.exe" if os.name == "nt" else "bin/python")),
    ))
    FORMULA_RECOGNITION_DEVICE = os.getenv("FORMULA_RECOGNITION_DEVICE", "auto")
    FORMULA_RECOGNITION_CPU_THREADS = max(
        1, int(os.getenv("FORMULA_RECOGNITION_CPU_THREADS", "1"))
    )
    FORMULA_RECOGNITION_TIMEOUT_SECONDS = float(
        os.getenv("FORMULA_RECOGNITION_TIMEOUT_SECONDS", "300")
    )
    FORMULA_RUNTIME_STATUS_CACHE_SECONDS = float(
        os.getenv("FORMULA_RUNTIME_STATUS_CACHE_SECONDS", "30")
    )
    FORMULA_UPLOAD_DIR = project_path(
        os.getenv("FORMULA_UPLOAD_DIR", UPLOAD_ROOT / "formulas")
    )
    FORMULA_UPLOAD_MAX_BYTES = int(
        os.getenv("FORMULA_UPLOAD_MAX_BYTES", str(10 * 1024 * 1024))
    )
    FORMULA_MAX_IMAGE_PIXELS = int(os.getenv("FORMULA_MAX_IMAGE_PIXELS", "40000000"))
    LITERATURE_PPT_RUNTIME_ROOT = project_path(
        os.getenv(
            "LITERATURE_PPT_RUNTIME_ROOT",
            BASE_DIR / "runtimes" / "literature_ppt_tools",
        )
    )
    LITERATURE_PPT_PYTHON = interpreter_path(
        os.getenv(
            "LITERATURE_PPT_PYTHON",
            LITERATURE_PPT_RUNTIME_ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python"),
        )
    )
    LITERATURE_PPT_UPLOAD_DIR = project_path(
        os.getenv("LITERATURE_PPT_UPLOAD_DIR", UPLOAD_ROOT / "literature-ppt")
    )
    LITERATURE_PPT_DATA_DIR = project_path(
        os.getenv("LITERATURE_PPT_DATA_DIR", GENERATED_ROOT / "literature-ppt")
    )
    LITERATURE_PPT_UPLOAD_MAX_BYTES = int(
        os.getenv("LITERATURE_PPT_UPLOAD_MAX_BYTES", str(50 * 1024 * 1024))
    )
    LITERATURE_PPT_TIMEOUT_SECONDS = int(os.getenv("LITERATURE_PPT_TIMEOUT_SECONDS", "1800"))
    JSON_AS_ASCII = False
    SKILL_CRAWL_FILE = project_path(
        os.getenv(
            "SKILL_CRAWL_FILE",
            BASE_DIR / "data" / "innovation_mining_skills.json",
        )
    )
    SKILL_IMPORT_TIMEOUT_SECONDS = float(os.getenv("SKILL_IMPORT_TIMEOUT_SECONDS", "20"))
    SKILL_IMPORT_MAX_FILE_BYTES = int(os.getenv("SKILL_IMPORT_MAX_FILE_BYTES", str(1_500_000)))
    SKILL_IMPORT_MAX_TOTAL_BYTES = int(os.getenv("SKILL_IMPORT_MAX_TOTAL_BYTES", str(8_000_000)))
    AGENT_GENERATED_DIR = project_path(
        os.getenv("AGENT_GENERATED_DIR", GENERATED_ROOT / "literature_search")
    )
    # Resolve relative paths against the backend directory, not the process
    # working directory.  WSGI/systemd and container launches commonly use a
    # different cwd than local `flask` development; without this, the
    # innovation runtime and generated output silently point at non-existent
    # locations on the server.
    INNOVATION_DATA_DIR = project_path(
        os.getenv("INNOVATION_DATA_DIR", GENERATED_ROOT / "ip")
    )
    PAPER_UPLOAD_DIR = project_path(os.getenv("PAPER_UPLOAD_DIR", UPLOAD_ROOT / "papers"))
    PAPER_UPLOAD_MAX_BYTES = int(os.getenv("PAPER_UPLOAD_MAX_BYTES", str(50 * 1024 * 1024)))
    PERSONAL_KB_UPLOAD_DIR = project_path(
        os.getenv("PERSONAL_KB_UPLOAD_DIR", UPLOAD_ROOT / "personal-knowledge")
    )
    PERSONAL_KB_UPLOAD_MAX_BYTES = int(
        os.getenv("PERSONAL_KB_UPLOAD_MAX_BYTES", str(50 * 1024 * 1024))
    )
    COMPLIANCE_UPLOAD_DIR = project_path(
        os.getenv("COMPLIANCE_UPLOAD_DIR", UPLOAD_ROOT / "compliance")
    )
    COMPLIANCE_UPLOAD_MAX_BYTES = int(
        os.getenv("COMPLIANCE_UPLOAD_MAX_BYTES", str(50 * 1024 * 1024))
    )
    TRANSLATION_UPLOAD_DIR = project_path(
        os.getenv("TRANSLATION_UPLOAD_DIR", UPLOAD_ROOT / "translations")
    )
    TRANSLATION_UPLOAD_MAX_BYTES = int(
        os.getenv("TRANSLATION_UPLOAD_MAX_BYTES", str(50 * 1024 * 1024))
    )
    PATENT_UPLOAD_DIR = project_path(
        os.getenv("PATENT_UPLOAD_DIR", UPLOAD_ROOT / "patents")
    )
    PATENT_UPLOAD_MAX_BYTES = int(
        os.getenv("PATENT_UPLOAD_MAX_BYTES", str(50 * 1024 * 1024))
    )
    PATENT_DRAFTING_RUNTIME_ROOT = project_path(
        os.getenv(
            "PATENT_DRAFTING_RUNTIME_ROOT",
            BASE_DIR / "app" / "agents" / "patent_drafting" / "runtime",
        )
    )
    PATENT_DRAFTING_DATA_DIR = project_path(
        os.getenv(
            "PATENT_DRAFTING_DATA_DIR",
            GENERATED_ROOT / "patent_drafting",
        )
    )
    PATENT_DRAFTING_TIMEOUT_SECONDS = int(
        os.getenv("PATENT_DRAFTING_TIMEOUT_SECONDS", "900")
    )
    PATENT_DRAFTING_FAKE_MODE = as_bool(os.getenv("PATENT_DRAFTING_FAKE_MODE"), False)
    PATENT_DRAFTING_ALLOW_FIXTURE_FALLBACK = as_bool(
        os.getenv("PATENT_DRAFTING_ALLOW_FIXTURE_FALLBACK"), False
    )
    FIGURE_UPLOAD_DIR = project_path(
        os.getenv("FIGURE_UPLOAD_DIR", UPLOAD_ROOT / "figures")
    )
    FIGURE_UPLOAD_MAX_BYTES = int(
        os.getenv("FIGURE_UPLOAD_MAX_BYTES", str(50 * 1024 * 1024))
    )
    ACADEMIC_FIGURE_RUNTIME_ROOT = project_path(
        os.getenv(
            "ACADEMIC_FIGURE_RUNTIME_ROOT",
            BASE_DIR / "app" / "agents" / "academic_figure" / "runtime",
        )
    )
    ACADEMIC_FIGURE_DATA_DIR = project_path(
        os.getenv(
            "ACADEMIC_FIGURE_DATA_DIR",
            GENERATED_ROOT / "academic_figure",
        )
    )
    ACADEMIC_FIGURE_TIMEOUT_SECONDS = int(
        os.getenv("ACADEMIC_FIGURE_TIMEOUT_SECONDS", "1800")
    )
    ACADEMIC_FIGURE_MODEL_MAX_RETRIES = int(
        os.getenv("ACADEMIC_FIGURE_MODEL_MAX_RETRIES", "4")
    )
    ACADEMIC_FIGURE_FONT_PATH = os.getenv("ACADEMIC_FIGURE_FONT_PATH", "")
    ARXIV_DAILY_RUNTIME_ROOT = project_path(
        os.getenv(
            "ARXIV_DAILY_RUNTIME_ROOT",
            BASE_DIR / "app" / "agents" / "arxiv_daily" / "runtime",
        )
    )
    ARXIV_DAILY_CACHE_TTL_SECONDS = int(
        os.getenv("ARXIV_DAILY_CACHE_TTL_SECONDS", "3600")
    )
    ARXIV_DAILY_TIMEOUT_SECONDS = int(
        os.getenv("ARXIV_DAILY_TIMEOUT_SECONDS", "120")
    )
    ARXIV_DAILY_PDF_CACHE_DIR = project_path(
        os.getenv(
            "ARXIV_DAILY_PDF_CACHE_DIR",
            GENERATED_ROOT / "arxiv_daily_pdfs",
        )
    )
    ARXIV_DAILY_PDF_MAX_BYTES = int(
        os.getenv("ARXIV_DAILY_PDF_MAX_BYTES", str(50 * 1024 * 1024))
    )
    PAPER_READING_RUNTIME_ROOT = project_path(
        os.getenv(
            "PAPER_READING_RUNTIME_ROOT",
            BASE_DIR / "app" / "agents" / "paper_reading" / "runtime",
        )
    )
    PAPER_READING_UV_EXECUTABLE = os.getenv("PAPER_READING_UV_EXECUTABLE", "uv")
    PAPER_READING_UV_CACHE_DIR = project_path(
        os.getenv("PAPER_READING_UV_CACHE_DIR", BASE_DIR / "tmp" / "uv-paper-reading-cache")
    )
    PAPER_READING_TIMEOUT_SECONDS = int(os.getenv("PAPER_READING_TIMEOUT_SECONDS", "3600"))
    PAPER_READING_MODEL_TIMEOUT_SECONDS = float(
        os.getenv("PAPER_READING_MODEL_TIMEOUT_SECONDS", "180")
    )
    # Docling is an optional, heavyweight table-structure enhancement. Keep
    # the production text-first flow independent from its Hugging Face stack.
    PAPER_READING_ENABLE_DOCLING = as_bool(
        os.getenv("PAPER_READING_ENABLE_DOCLING"), False
    )
    INNOVATION_AGENT_ROOT = project_path(
        os.getenv(
            "INNOVATION_AGENT_ROOT",
            BASE_DIR / "runtimes" / "paper-insight-generate",
        )
    )
    INNOVATION_AGENT_TIMEOUT_SECONDS = int(os.getenv("INNOVATION_AGENT_TIMEOUT_SECONDS", "1800"))
    INNOVATION_AGENT_MAX_DOCUMENTS = int(os.getenv("INNOVATION_AGENT_MAX_DOCUMENTS", "80"))
    COMPLIANCE_AGENT_ROOT = project_path(
        os.getenv(
            "COMPLIANCE_AGENT_ROOT",
            BASE_DIR / "runtimes" / "academic_compliance_agent",
        )
    )
    COMPLIANCE_AGENT_TIMEOUT_SECONDS = int(os.getenv("COMPLIANCE_AGENT_TIMEOUT_SECONDS", "1800"))
    COMPLIANCE_AGENT_USE_LLM = as_bool(os.getenv("COMPLIANCE_AGENT_USE_LLM"), True)
    COMPLIANCE_AGENT_MEMORY_ENABLED = as_bool(
        os.getenv("COMPLIANCE_AGENT_MEMORY_ENABLED"), False
    )
    TRANSLATION_AGENT_ROOT = project_path(
        os.getenv(
            "TRANSLATION_AGENT_ROOT",
            BASE_DIR / "runtimes" / "academic-translation-agent",
        )
    )
    TRANSLATION_AGENT_TIMEOUT_SECONDS = int(
        os.getenv("TRANSLATION_AGENT_TIMEOUT_SECONDS", "3600")
    )
    TRANSLATION_HEARTBEAT_SECONDS = max(
        5, int(os.getenv("TRANSLATION_HEARTBEAT_SECONDS", "30"))
    )
    TRANSLATION_LLM_BACKEND = os.getenv("TRANSLATION_LLM_BACKEND", "api").strip().lower()
    TRANSLATION_API_BASE_URL = os.getenv("TRANSLATION_API_BASE_URL", "https://api.deepseek.com")
    TRANSLATION_API_MODEL = os.getenv("TRANSLATION_API_MODEL", "deepseek-flash")
    TRANSLATION_API_KEY = os.getenv("TRANSLATION_API_KEY", "")
    TRANSLATION_API_TIMEOUT_SECONDS = max(30.0, float(os.getenv("TRANSLATION_API_TIMEOUT_SECONDS", "240")))
    TRANSLATION_API_MAX_TOKENS = max(256, int(os.getenv("TRANSLATION_API_MAX_TOKENS", "2048")))
    TRANSLATION_API_RETRIES = max(0, min(5, int(os.getenv("TRANSLATION_API_RETRIES", "2"))))
    TRANSLATION_API_RETRY_BACKOFF_SECONDS = max(
        0.5, float(os.getenv("TRANSLATION_API_RETRY_BACKOFF_SECONDS", "2"))
    )
    TRANSLATION_API_DISABLE_THINKING = as_bool(
        os.getenv("TRANSLATION_API_DISABLE_THINKING"), True
    )
    TRANSLATION_API_MAX_PARALLEL = max(
        1, min(5, int(os.getenv("TRANSLATION_API_MAX_PARALLEL", "1")))
    )
    # Kept for an explicit local fallback and for PDFMathTranslate compatibility.
    TRANSLATION_OLLAMA_BASE_URL = os.getenv(
        "TRANSLATION_OLLAMA_BASE_URL", "http://127.0.0.1:11434"
    )
    TRANSLATION_OLLAMA_MODEL = os.getenv(
        "TRANSLATION_OLLAMA_MODEL", "translategemma:12b"
    )
    TRANSLATION_PDF2ZH_COMMAND = os.getenv("TRANSLATION_PDF2ZH_COMMAND", "")
    LITERATURE_EXTERNAL_SEARCH = as_bool(os.getenv("LITERATURE_EXTERNAL_SEARCH"), True)
    LITERATURE_FORCE_OFFLINE_MODEL = as_bool(os.getenv("LITERATURE_FORCE_OFFLINE_MODEL"), False)
    LITERATURE_REPORT_LIMIT = int(os.getenv("LITERATURE_REPORT_LIMIT", "10"))
    # Platform model: the fine-tuned 27B deployment, reached over an
    # OpenAI-compatible endpoint.  It is deliberately NOT deployed with this
    # release because of its hardware requirements, so the integration point
    # stays but is switched off: set PLATFORM_MODEL_ENABLED=true together with
    # the three QWEN_DPO_* values once a host for it exists.  While it is off
    # the API reports it as unavailable and the web UI hides it entirely.
    PLATFORM_MODEL_ENABLED = as_bool(os.getenv("PLATFORM_MODEL_ENABLED"), False)
    QWEN_DPO_BASE_URL = os.getenv("QWEN_DPO_BASE_URL", "")
    QWEN_DPO_MODEL = os.getenv("QWEN_DPO_MODEL", "")
    QWEN_DPO_API_KEY = os.getenv("QWEN_DPO_API_KEY", "")
    QWEN_DPO_TIMEOUT_SECONDS = float(os.getenv("QWEN_DPO_TIMEOUT_SECONDS", "180"))
    # Connection checks may wait for a queued model server, but should still
    # return before the normal generation timeout becomes unbounded.
    MODEL_VERIFY_TIMEOUT_SECONDS = max(
        10.0, float(os.getenv("MODEL_VERIFY_TIMEOUT_SECONDS", "60"))
    )
    MODEL_HTTP_MAX_RETRIES = max(0, int(os.getenv("MODEL_HTTP_MAX_RETRIES", "3")))
    NORMAL_USER_FREE_TOKEN_QUOTA = int(os.getenv("NORMAL_USER_FREE_TOKEN_QUOTA", "1000000"))
    # Use deployment environment values ahead of a stale database runtime
    # record when the model server is managed outside this application.
    QWEN_DPO_ENV_OVERRIDE = as_bool(os.getenv("QWEN_DPO_ENV_OVERRIDE"), False)
    # Default account API chat model: DeepSeek's own OpenAI-compatible
    # endpoint.  Users can still point these at another gateway (Alibaba
    # DashScope, Zhipu GLM, or any OpenAI-compatible host) from the model
    # configuration page; the model ID is passed through unchanged.
    USER_API_MODEL_BASE_URL = os.getenv("USER_API_MODEL_BASE_URL", "https://api.deepseek.com")
    USER_API_MODEL_NAME = os.getenv("USER_API_MODEL_NAME", "deepseek-flash")
    USER_API_MODEL_API_KEY = os.getenv("USER_API_MODEL_API_KEY", "")
    USER_API_MODEL_TIMEOUT_SECONDS = int(os.getenv("USER_API_MODEL_TIMEOUT_SECONDS", "120"))
    USER_API_MODEL_MAX_OUTPUT_TOKENS = int(os.getenv("USER_API_MODEL_MAX_OUTPUT_TOKENS", "3072"))
    MANUSCRIPT_ALLOW_DETERMINISTIC_FALLBACK = as_bool(
        os.getenv("MANUSCRIPT_ALLOW_DETERMINISTIC_FALLBACK"), True
    )
    # Shared short-lived state (text-message rate limit, agent readiness).
    # Empty means "keep it in process memory".
    REDIS_URL = os.getenv("REDIS_URL", "").strip()
    AGENT_READINESS_CACHE_SECONDS = max(
        5, int(os.getenv("AGENT_READINESS_CACHE_SECONDS", "30"))
    )
    AGENT_READINESS_CONNECT_TIMEOUT_SECONDS = max(
        0.1, float(os.getenv("AGENT_READINESS_CONNECT_TIMEOUT_SECONDS", "0.8"))
    )
    AGENT_TEAM_STAGE_TIMEOUT_SECONDS = int(os.getenv("AGENT_TEAM_STAGE_TIMEOUT_SECONDS", "3600"))
