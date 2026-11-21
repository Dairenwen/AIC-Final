from flask import Flask
from flask_cors import CORS

from .agents.literature_search import LiteratureSearchService
from .api import api_v1
from .api.auth import register_auth_cli
from .config import Config
from .extensions import db, migrate
from .integrations.knowledge_base.runtime import start_embedded_knowledge_base
from .integrations.personal_rag import PersonalAcademicRagService
from .services.catalog_setup import register_catalog_cli
from .tools.formula_recognition import FormulaRecognitionService
from . import model_registry  # noqa: F401  # register SQLAlchemy models for migrations


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_object(Config)
    if test_config:
        app.config.update(test_config)

    db.init_app(app)
    migrate.init_app(app, db)
    CORS(
        app,
        resources={r"/api/*": {"origins": app.config["CORS_ORIGINS"]}},
        supports_credentials=True,
    )

    app.config["AGENT_GENERATED_DIR"].mkdir(parents=True, exist_ok=True)
    app.config["INNOVATION_DATA_DIR"].mkdir(parents=True, exist_ok=True)
    app.config["PAPER_UPLOAD_DIR"].mkdir(parents=True, exist_ok=True)
    app.config["PERSONAL_KB_UPLOAD_DIR"].mkdir(parents=True, exist_ok=True)
    app.config["COMPLIANCE_UPLOAD_DIR"].mkdir(parents=True, exist_ok=True)
    app.config["TRANSLATION_UPLOAD_DIR"].mkdir(parents=True, exist_ok=True)
    app.config["PATENT_UPLOAD_DIR"].mkdir(parents=True, exist_ok=True)
    app.config["PATENT_DRAFTING_DATA_DIR"].mkdir(parents=True, exist_ok=True)
    app.config["FIGURE_UPLOAD_DIR"].mkdir(parents=True, exist_ok=True)
    app.config["ACADEMIC_FIGURE_DATA_DIR"].mkdir(parents=True, exist_ok=True)
    app.config["ARXIV_DAILY_PDF_CACHE_DIR"].mkdir(parents=True, exist_ok=True)
    app.config["FORMULA_UPLOAD_DIR"].mkdir(parents=True, exist_ok=True)
    app.config["LITERATURE_PPT_UPLOAD_DIR"].mkdir(parents=True, exist_ok=True)
    app.config["LITERATURE_PPT_DATA_DIR"].mkdir(parents=True, exist_ok=True)
    app.extensions["literature_search_service"] = LiteratureSearchService(app)
    app.extensions["formula_recognition_service"] = FormulaRecognitionService(app)
    app.extensions["personal_academic_rag"] = PersonalAcademicRagService()
    app.config["KNOWLEDGE_BASE_SERVICE_URL"] = start_embedded_knowledge_base(app)

    app.register_blueprint(api_v1, url_prefix="/api/v1")
    register_auth_cli(app)
    register_catalog_cli(app)

    @app.get("/")
    def index() -> dict:
        return {
            "name": "Zhiyan Research Assistant API",
            "version": "0.1.0",
            "docs": "/api/v1/health/ready",
        }

    return app
