from flask import Blueprint, request

from .admin import bp as admin_bp
from .academic_daily import bp as academic_daily_bp
from .academic_space import bp as academic_space_bp
from .auth import authenticate_request, bp as auth_bp
from .chat import bp as chat_bp
from .formula_tools import bp as formula_tools_bp
from .health import bp as health_bp
from .knowledge_base import bp as knowledge_base_bp
from .model_configs import bp as model_configs_bp
from .projects import bp as projects_bp
from .rag import bp as rag_bp
from .research_tools import bp as research_tools_bp
from .tasks import bp as tasks_bp
from .uploads import bp as uploads_bp
from .usage import bp as usage_bp
from .workspace import bp as workspace_bp

api_v1 = Blueprint("api_v1", __name__)
api_v1.register_blueprint(auth_bp, url_prefix="/auth")
api_v1.register_blueprint(health_bp, url_prefix="/health")
api_v1.register_blueprint(model_configs_bp)
api_v1.register_blueprint(projects_bp)
api_v1.register_blueprint(rag_bp)
api_v1.register_blueprint(chat_bp)
api_v1.register_blueprint(formula_tools_bp)
api_v1.register_blueprint(research_tools_bp)
api_v1.register_blueprint(workspace_bp)
api_v1.register_blueprint(tasks_bp, url_prefix="/tasks")
api_v1.register_blueprint(uploads_bp)
api_v1.register_blueprint(usage_bp)
api_v1.register_blueprint(admin_bp, url_prefix="/admin")
api_v1.register_blueprint(knowledge_base_bp, url_prefix="/knowledge-base")
api_v1.register_blueprint(academic_daily_bp)
api_v1.register_blueprint(academic_space_bp)

PUBLIC_PATHS = {
    "/api/v1/auth/login",
    "/api/v1/auth/register",
    "/api/v1/auth/sms/request",
    "/api/v1/auth/sms/login",
}


@api_v1.before_request
def require_authentication():
    if request.method == "OPTIONS" or request.path.startswith("/api/v1/health/"):
        return None
    if request.path in PUBLIC_PATHS:
        return None
    return authenticate_request()
