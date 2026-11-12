import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]


def as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def project_path(value: str | Path) -> Path:
    path = Path(value)
    return (path if path.is_absolute() else BASE_DIR / path).resolve()


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
    KNOWLEDGE_BASE_DATA_DIR = os.getenv(
        "KB_DATA_DIR", str(KNOWLEDGE_BASE_ROOT / "infrastructure" / "file-storage" / "data")
    )
    KNOWLEDGE_BASE_ELASTICSEARCH_ENABLED = as_bool(os.getenv("ELASTICSEARCH_ENABLED"), False)
    KNOWLEDGE_BASE_MILVUS_ENABLED = as_bool(os.getenv("KB_MILVUS_ENABLED"), False)
    KNOWLEDGE_BASE_PROXY_TIMEOUT_SECONDS = float(
        os.getenv("KNOWLEDGE_BASE_PROXY_TIMEOUT_SECONDS", "120")
    )
    # Knowledge-base QA generation uses its own runtime but shares the main
    # deployment's model settings. Keep these keys explicit so the embedded
    # runtime cannot silently fall back to qwen-plus.
    CHUNK_QA_MODEL_BASE_URL = os.getenv("CHUNK_QA_MODEL_BASE_URL", "")
    CHUNK_QA_MODEL_API_KEY = os.getenv("CHUNK_QA_MODEL_API_KEY", "")
    CHUNK_QA_MODEL_NAME = os.getenv("CHUNK_QA_MODEL_NAME", "")
    CHUNK_QA_MODEL_TIMEOUT = float(os.getenv("CHUNK_QA_MODEL_TIMEOUT", "60"))
    CHUNK_QA_MODEL_TEMPERATURE = float(os.getenv("CHUNK_QA_MODEL_TEMPERATURE", "0.2"))
    CHUNK_QA_MODEL_RESPONSE_FORMAT = os.getenv(
        "CHUNK_QA_MODEL_RESPONSE_FORMAT", "json_object"
    )
    # The formula runtime is part of this checkout.  Keep its code, model
    # files, and virtual environment together so production never depends on
    # a sibling/external delivery directory.
    FORMULA_RECOGNITION_ROOT = (
        BASE_DIR / "app" / "tools" / "formula_recognition" / "runtime"
    ).resolve()
    # Do not call resolve() here: Linux venv/bin/python is often a symlink to
    # the system interpreter, and resolving it would bypass the venv itself.
    FORMULA_RECOGNITION_PYTHON = (
        FORMULA_RECOGNITION_ROOT
        / ".venv"
        / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    )
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
    FORMULA_UPLOAD_DIR = Path(
        os.getenv("FORMULA_UPLOAD_DIR", BASE_DIR / "uploads" / "formulas")
    ).resolve()
    FORMULA_UPLOAD_MAX_BYTES = int(
        os.getenv("FORMULA_UPLOAD_MAX_BYTES", str(10 * 1024 * 1024))
    )
    FORMULA_MAX_IMAGE_PIXELS = int(os.getenv("FORMULA_MAX_IMAGE_PIXELS", "40000000"))
    LITERATURE_PPT_RUNTIME_ROOT = Path(
        os.getenv(
            "LITERATURE_PPT_RUNTIME_ROOT",
            BASE_DIR.parents[2] / "小组传输文件" / "戴rw" / "literature_ppt_tools",
        )
    ).resolve()
    LITERATURE_PPT_PYTHON = Path(
        os.getenv(
            "LITERATURE_PPT_PYTHON",
            LITERATURE_PPT_RUNTIME_ROOT / ".venv-windows" / "Scripts" / "python.exe",
        )
    ).resolve()
    LITERATURE_PPT_UPLOAD_DIR = Path(
        os.getenv("LITERATURE_PPT_UPLOAD_DIR", BASE_DIR / "uploads" / "literature-ppt")
    ).resolve()
    LITERATURE_PPT_DATA_DIR = Path(
        os.getenv("LITERATURE_PPT_DATA_DIR", BASE_DIR / "generated" / "literature-ppt")
    ).resolve()
    LITERATURE_PPT_UPLOAD_MAX_BYTES = int(
        os.getenv("LITERATURE_PPT_UPLOAD_MAX_BYTES", str(50 * 1024 * 1024))
    )
    LITERATURE_PPT_TIMEOUT_SECONDS = int(os.getenv("LITERATURE_PPT_TIMEOUT_SECONDS", "1800"))
    JSON_AS_ASCII = False
    SKILL_CRAWL_FILE = Path(
        os.getenv(
            "SKILL_CRAWL_FILE",
            BASE_DIR.parents[2]
            / "小组传输文件"
            / "宋xr"
            / "Skill"
            / "innovation_mining_skills.json",
        )
    ).resolve()
    SKILL_IMPORT_TIMEOUT_SECONDS = float(os.getenv("SKILL_IMPORT_TIMEOUT_SECONDS", "20"))
    SKILL_IMPORT_MAX_FILE_BYTES = int(os.getenv("SKILL_IMPORT_MAX_FILE_BYTES", str(1_500_000)))
    SKILL_IMPORT_MAX_TOTAL_BYTES = int(os.getenv("SKILL_IMPORT_MAX_TOTAL_BYTES", str(8_000_000)))
    AGENT_GENERATED_DIR = Path(
        os.getenv("AGENT_GENERATED_DIR", BASE_DIR / "generated" / "literature_search")
    ).resolve()
    # Resolve relative paths against the backend directory, not the process
    # working directory.  WSGI/systemd and container launches commonly use a
    # different cwd than local `flask` development; without this, the
    # innovation runtime and generated output silently point at non-existent
    # locations on the server.
    INNOVATION_DATA_DIR = project_path(
        os.getenv("INNOVATION_DATA_DIR", BASE_DIR / "generated" / "ip")
    )
    PAPER_UPLOAD_DIR = Path(os.getenv("PAPER_UPLOAD_DIR", BASE_DIR / "uploads" / "papers")).resolve()
    PAPER_UPLOAD_MAX_BYTES = int(os.getenv("PAPER_UPLOAD_MAX_BYTES", str(50 * 1024 * 1024)))
    PERSONAL_KB_UPLOAD_DIR = Path(
        os.getenv("PERSONAL_KB_UPLOAD_DIR", BASE_DIR / "uploads" / "personal-knowledge")
    ).resolve()
    PERSONAL_KB_UPLOAD_MAX_BYTES = int(
        os.getenv("PERSONAL_KB_UPLOAD_MAX_BYTES", str(50 * 1024 * 1024))
    )
    COMPLIANCE_UPLOAD_DIR = Path(
        os.getenv("COMPLIANCE_UPLOAD_DIR", BASE_DIR / "uploads" / "compliance")
    ).resolve()
    COMPLIANCE_UPLOAD_MAX_BYTES = int(
        os.getenv("COMPLIANCE_UPLOAD_MAX_BYTES", str(50 * 1024 * 1024))
    )
    TRANSLATION_UPLOAD_DIR = Path(
        os.getenv("TRANSLATION_UPLOAD_DIR", BASE_DIR / "uploads" / "translations")
    ).resolve()
    TRANSLATION_UPLOAD_MAX_BYTES = int(
        os.getenv("TRANSLATION_UPLOAD_MAX_BYTES", str(50 * 1024 * 1024))
    )
    PATENT_UPLOAD_DIR = Path(
        os.getenv("PATENT_UPLOAD_DIR", BASE_DIR / "uploads" / "patents")
    ).resolve()
    PATENT_UPLOAD_MAX_BYTES = int(
        os.getenv("PATENT_UPLOAD_MAX_BYTES", str(50 * 1024 * 1024))
    )
    PATENT_DRAFTING_RUNTIME_ROOT = Path(
        os.getenv(
            "PATENT_DRAFTING_RUNTIME_ROOT",
            BASE_DIR / "app" / "agents" / "patent_drafting" / "runtime",
        )
    ).resolve()
    PATENT_DRAFTING_DATA_DIR = Path(
        os.getenv(
            "PATENT_DRAFTING_DATA_DIR",
            BASE_DIR / "generated" / "patent_drafting",
        )
    ).resolve()
    PATENT_DRAFTING_TIMEOUT_SECONDS = int(
        os.getenv("PATENT_DRAFTING_TIMEOUT_SECONDS", "900")
    )
    PATENT_DRAFTING_FAKE_MODE = as_bool(os.getenv("PATENT_DRAFTING_FAKE_MODE"), False)
    PATENT_DRAFTING_ALLOW_FIXTURE_FALLBACK = as_bool(
        os.getenv("PATENT_DRAFTING_ALLOW_FIXTURE_FALLBACK"), False
    )
    FIGURE_UPLOAD_DIR = Path(
        os.getenv("FIGURE_UPLOAD_DIR", BASE_DIR / "uploads" / "figures")
    ).resolve()
    FIGURE_UPLOAD_MAX_BYTES = int(
        os.getenv("FIGURE_UPLOAD_MAX_BYTES", str(50 * 1024 * 1024))
    )
    ACADEMIC_FIGURE_RUNTIME_ROOT = Path(
        os.getenv(
            "ACADEMIC_FIGURE_RUNTIME_ROOT",
            BASE_DIR / "app" / "agents" / "academic_figure" / "runtime",
        )
    ).resolve()
    ACADEMIC_FIGURE_DATA_DIR = Path(
        os.getenv(
            "ACADEMIC_FIGURE_DATA_DIR",
            BASE_DIR / "generated" / "academic_figure",
        )
    ).resolve()
    ACADEMIC_FIGURE_TIMEOUT_SECONDS = int(
        os.getenv("ACADEMIC_FIGURE_TIMEOUT_SECONDS", "1800")
    )
    ACADEMIC_FIGURE_MODEL_MAX_RETRIES = int(
        os.getenv("ACADEMIC_FIGURE_MODEL_MAX_RETRIES", "4")
    )
    ACADEMIC_FIGURE_FONT_PATH = os.getenv("ACADEMIC_FIGURE_FONT_PATH", "")
    ARXIV_DAILY_RUNTIME_ROOT = Path(
        os.getenv(
            "ARXIV_DAILY_RUNTIME_ROOT",
            BASE_DIR / "app" / "agents" / "arxiv_daily" / "runtime",
        )
    ).resolve()
    ARXIV_DAILY_CACHE_TTL_SECONDS = int(
        os.getenv("ARXIV_DAILY_CACHE_TTL_SECONDS", "3600")
    )
    ARXIV_DAILY_TIMEOUT_SECONDS = int(
        os.getenv("ARXIV_DAILY_TIMEOUT_SECONDS", "120")
    )
    ARXIV_DAILY_PDF_CACHE_DIR = Path(
        os.getenv(
            "ARXIV_DAILY_PDF_CACHE_DIR",
            BASE_DIR / "generated" / "arxiv_daily_pdfs",
        )
    ).resolve()
    ARXIV_DAILY_PDF_MAX_BYTES = int(
        os.getenv("ARXIV_DAILY_PDF_MAX_BYTES", str(50 * 1024 * 1024))
    )
    PAPER_READING_RUNTIME_ROOT = Path(
        os.getenv(
            "PAPER_READING_RUNTIME_ROOT",
            BASE_DIR / "app" / "agents" / "paper_reading" / "runtime",
        )
    ).resolve()
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
    COMPLIANCE_AGENT_ROOT = Path(
        os.getenv(
            "COMPLIANCE_AGENT_ROOT",
            BASE_DIR / "runtimes" / "academic_compliance_agent",
        )
    ).resolve()
    COMPLIANCE_AGENT_TIMEOUT_SECONDS = int(os.getenv("COMPLIANCE_AGENT_TIMEOUT_SECONDS", "1800"))
    COMPLIANCE_AGENT_USE_LLM = as_bool(os.getenv("COMPLIANCE_AGENT_USE_LLM"), True)
    COMPLIANCE_AGENT_MEMORY_ENABLED = as_bool(
        os.getenv("COMPLIANCE_AGENT_MEMORY_ENABLED"), False
    )
    TRANSLATION_AGENT_ROOT = Path(
        os.getenv(
            "TRANSLATION_AGENT_ROOT",
            BASE_DIR / "runtimes" / "academic-translation-agent",
        )
    ).resolve()
    TRANSLATION_AGENT_TIMEOUT_SECONDS = int(
        os.getenv("TRANSLATION_AGENT_TIMEOUT_SECONDS", "3600")
    )
    TRANSLATION_HEARTBEAT_SECONDS = max(
        5, int(os.getenv("TRANSLATION_HEARTBEAT_SECONDS", "30"))
    )
    TRANSLATION_LLM_BACKEND = os.getenv("TRANSLATION_LLM_BACKEND", "api").strip().lower()
    TRANSLATION_API_BASE_URL = os.getenv("TRANSLATION_API_BASE_URL", "https://api.siliconflow.cn/v1")
    TRANSLATION_API_MODEL = os.getenv(
        "TRANSLATION_API_MODEL", "deepseek-ai/DeepSeek-V4-Flash"
    )
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
    # Platform model: OpenAI-compatible Unsloth deployment.
    QWEN_DPO_BASE_URL = os.getenv(
        "QWEN_DPO_BASE_URL",
        "https://k9n4n0vf52f6239t-9001.js01-webservice.gpuhome.cc:50000/v1",
    )
    QWEN_DPO_MODEL = os.getenv("QWEN_DPO_MODEL", "Qwen3.8-27B-UD-Q4_K_XL")
    QWEN_DPO_API_KEY = os.getenv("QWEN_DPO_API_KEY", "sk-unsloth-YOUR_KEY")
    QWEN_DPO_TIMEOUT_SECONDS = float(os.getenv("QWEN_DPO_TIMEOUT_SECONDS", "180"))
    # Stable public label for the platform model. The actual request is sent
    # through USER_API_MODEL_* below, while this name is shown in the UI.
    PLATFORM_MODEL_DISPLAY_NAME = os.getenv("PLATFORM_MODEL_DISPLAY_NAME", "qwen3.6-27B-sft")
    # Connection checks may wait for a queued model server, but should still
    # return before the normal generation timeout becomes unbounded.
    MODEL_VERIFY_TIMEOUT_SECONDS = max(
        10.0, float(os.getenv("MODEL_VERIFY_TIMEOUT_SECONDS", "60"))
    )
    MODEL_HTTP_MAX_RETRIES = max(0, int(os.getenv("MODEL_HTTP_MAX_RETRIES", "3")))
    NORMAL_USER_FREE_TOKEN_QUOTA = int(os.getenv("NORMAL_USER_FREE_TOKEN_QUOTA", "1000000"))
    # Deployment credentials are authoritative by default. Set this to false
    # only when the platform model is intentionally managed in the database.
    # This prevents a rotated .env key from being shadowed by a stale encrypted
    # database runtime record after a restart.
    QWEN_DPO_ENV_OVERRIDE = as_bool(os.getenv("QWEN_DPO_ENV_OVERRIDE"), True)
    # Default account API chat model. Keep the model ID in provider-native form
    # so OpenAI-compatible gateways (such as SiliconFlow) receive it unchanged.
    USER_API_MODEL_BASE_URL = os.getenv("USER_API_MODEL_BASE_URL", "https://api.siliconflow.cn/v1")
    USER_API_MODEL_NAME = os.getenv("USER_API_MODEL_NAME", "deepseek-ai/DeepSeek-V4-Flash")
    USER_API_MODEL_API_KEY = os.getenv("USER_API_MODEL_API_KEY", "")
    USER_API_MODEL_TIMEOUT_SECONDS = int(os.getenv("USER_API_MODEL_TIMEOUT_SECONDS", "120"))
    USER_API_MODEL_MAX_OUTPUT_TOKENS = int(os.getenv("USER_API_MODEL_MAX_OUTPUT_TOKENS", "3072"))
    # Keep the provisioned account API model synchronized with deployment
    # settings. Set false only when each user's database record is managed
    # independently from the backend environment.
    USER_API_MODEL_ENV_OVERRIDE = as_bool(os.getenv("USER_API_MODEL_ENV_OVERRIDE"), True)
    MANUSCRIPT_ALLOW_DETERMINISTIC_FALLBACK = as_bool(
        os.getenv("MANUSCRIPT_ALLOW_DETERMINISTIC_FALLBACK"), True
    )
    AGENT_READINESS_CACHE_SECONDS = max(
        5, int(os.getenv("AGENT_READINESS_CACHE_SECONDS", "30"))
    )
    AGENT_READINESS_CONNECT_TIMEOUT_SECONDS = max(
        0.1, float(os.getenv("AGENT_READINESS_CONNECT_TIMEOUT_SECONDS", "0.8"))
    )
    AGENT_TEAM_STAGE_TIMEOUT_SECONDS = int(os.getenv("AGENT_TEAM_STAGE_TIMEOUT_SECONDS", "3600"))
