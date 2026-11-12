"""An open model choice must land on the user's own model, not on a platform
model that is not deployed."""
from types import SimpleNamespace
from uuid import uuid4

from app import create_app
from app.extensions import db
from app.services.task_model_runtime import (
    falls_back_to_personal_model,
    select_personal_chat_config,
)


def _config(*, default=False, model_type="chat", has_key=True):
    return SimpleNamespace(
        id=uuid4(),
        model_type_code=model_type,
        default_for=["chat"] if default else [],
        encrypted_api_key=b"x" if has_key else None,
        key_nonce=b"n" if has_key else None,
        key_version="aesgcm-v1" if has_key else None,
    )


def _with_configs(monkeypatch, items):
    monkeypatch.setattr(db.session, "scalars", lambda _query: SimpleNamespace(all=lambda: items))


def test_auto_choice_always_uses_the_personal_default():
    app = create_app({"TESTING": True, "PLATFORM_MODEL_ENABLED": True})
    with app.app_context():
        assert falls_back_to_personal_model("auto")
        assert falls_back_to_personal_model("")
        assert falls_back_to_personal_model(None)


def test_platform_choice_falls_back_only_while_the_platform_model_is_off():
    off = create_app({"TESTING": True, "PLATFORM_MODEL_ENABLED": False})
    with off.app_context():
        assert falls_back_to_personal_model("vertical_domain")
    on = create_app({"TESTING": True, "PLATFORM_MODEL_ENABLED": True})
    with on.app_context():
        assert not falls_back_to_personal_model("vertical_domain")


def test_an_explicit_personal_model_is_never_overridden():
    app = create_app({"TESTING": True})
    with app.app_context():
        assert not falls_back_to_personal_model(f"model_config:{uuid4()}")


def test_default_chat_model_wins_over_other_verified_models(monkeypatch):
    app = create_app({"TESTING": True})
    default = _config(default=True)
    with app.app_context():
        _with_configs(monkeypatch, [_config(), default, _config()])
        assert select_personal_chat_config(uuid4()) is default


def test_a_single_verified_chat_model_is_used_without_a_default(monkeypatch):
    app = create_app({"TESTING": True})
    only = _config()
    with app.app_context():
        _with_configs(monkeypatch, [only, _config(model_type="embedding")])
        assert select_personal_chat_config(uuid4()) is only


def test_several_models_without_a_default_are_not_guessed(monkeypatch):
    app = create_app({"TESTING": True})
    with app.app_context():
        _with_configs(monkeypatch, [_config(), _config()])
        assert select_personal_chat_config(uuid4()) is None


def test_models_without_a_stored_key_are_ignored(monkeypatch):
    app = create_app({"TESTING": True})
    with app.app_context():
        _with_configs(monkeypatch, [_config(default=True, has_key=False)])
        assert select_personal_chat_config(uuid4()) is None
