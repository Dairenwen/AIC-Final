from __future__ import annotations

from app import create_app
from app.extensions import db
from app.models import PersonalKnowledgeFolder, PersonalKnowledgePaper


def main() -> None:
    app = create_app({"KNOWLEDGE_BASE_EMBEDDED": False})
    with app.app_context():
        PersonalKnowledgeFolder.__table__.create(bind=db.engine, checkfirst=True)
        PersonalKnowledgePaper.__table__.create(bind=db.engine, checkfirst=True)
    print("academic space tables are ready")


if __name__ == "__main__":
    main()
