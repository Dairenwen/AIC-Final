from types import SimpleNamespace
from uuid import uuid4

from flask import g
from werkzeug.security import check_password_hash

from app import create_app
from app.api.auth import normalize_organization, normalize_phone, require_role
from app.models import User
from app.api.tasks import find_task
from app.extensions import db


def test_normalize_chinese_mobile_number():
    assert normalize_phone("138 0000 0000") == "+8613800000000"
    assert normalize_phone("+86-138-0000-0000") == "+8613800000000"


def test_rejects_invalid_phone_number():
    assert normalize_phone("12345") is None


def test_empty_or_corrupted_organization_uses_explicit_placeholder():
    assert normalize_organization("") == "未设置机构"
    assert normalize_organization("??????") == "未设置机构"
    assert normalize_organization("武汉理工大学") == "武汉理工大学"


def test_role_check_rejects_normal_user():
    app = create_app({"TESTING": True})
    with app.test_request_context():
        g.current_user = SimpleNamespace(role_code="normal_user")
        response, status = require_role("system_admin")

    assert status == 403
    assert response.get_json()["error"]["code"] == "FORBIDDEN"


def test_task_lookup_is_isolated_by_owner(monkeypatch):
    app = create_app({"TESTING": True})
    owner_id = uuid4()
    other_user_id = uuid4()
    task = SimpleNamespace(user_id=owner_id)

    with app.test_request_context():
        monkeypatch.setattr(db.session, "get", lambda _model, _task_id: task)
        g.current_user = SimpleNamespace(id=other_user_id, role_code="normal_user")
        assert find_task(str(uuid4())) is None

        g.current_user = SimpleNamespace(id=other_user_id, role_code="system_admin")
        assert find_task(str(uuid4())) is task


def test_register_creates_user(monkeypatch):
    app = create_app({"TESTING": True})
    captured = {}

    def fake_scalar(_query):
        return None

    def fake_add(user):
        captured["user"] = user

    monkeypatch.setattr(db.session, "scalar", fake_scalar)
    monkeypatch.setattr(db.session, "add", fake_add)
    monkeypatch.setattr(db.session, "commit", lambda: None)

    client = app.test_client()
    response = client.post(
        "/api/v1/auth/register",
        json={
            "phone": "13800000000",
            "password": "password123",
            "name": "测试用户",
            "organization": "武汉理工大学",
        },
    )

    assert response.status_code == 201
    payload = response.get_json()
    assert payload["data"]["message"] == "注册成功，请返回登录"
    assert isinstance(captured["user"], User)
    assert captured["user"].phone == "+8613800000000"
    assert captured["user"].display_name == "测试用户"
    assert captured["user"].profile["organization"] == "武汉理工大学"
    assert check_password_hash(captured["user"].password_hash, "password123")


def test_register_rejects_duplicate_phone(monkeypatch):
    app = create_app({"TESTING": True})
    existing = SimpleNamespace(phone="+8613800000000", deleted_at=None)

    monkeypatch.setattr(db.session, "scalar", lambda _query: existing)

    client = app.test_client()
    response = client.post(
        "/api/v1/auth/register",
        json={
            "phone": "13800000000",
            "password": "password123",
            "name": "测试用户",
        },
    )

    assert response.status_code == 409
    payload = response.get_json()
    assert payload["error"]["code"] == "PHONE_ALREADY_REGISTERED"


def test_sms_request_uses_configured_provider(monkeypatch):
    app = create_app({"TESTING": True, "SMS_PROVIDER": "aliyun"})
    sent = {}
    monkeypatch.setattr("app.api.auth.SmsService.issue", lambda phone, purpose: sent.update(phone=phone, purpose=purpose))
    response = app.test_client().post("/api/v1/auth/sms/request", json={"phone": "13800000000", "purpose": "register"})
    assert response.status_code == 200
    assert sent == {"phone": "+8613800000000", "purpose": "register"}


def test_aliyun_sms_request_uses_dypnsapi(monkeypatch):
    app = create_app({
        "TESTING": True,
        "SMS_PROVIDER": "aliyun",
        "ALIYUN_SMS_ACCESS_KEY_ID": "key",
        "ALIYUN_SMS_ACCESS_KEY_SECRET": "secret",
        "ALIYUN_SMS_SIGN_NAME": "速通互联验证码",
        "ALIYUN_SMS_TEMPLATE_CODE": "SMS_100001",
        "ALIYUN_SMS_TEMPLATE_MINUTES": 5,
    })
    captured = {}

    class FakeClient:
        def send_sms_verify_code_with_options(self, request, _runtime_options):
            captured.update(request.to_map())
            return type("Response", (), {"body": type("Body", (), {"code": "OK"})()})()

    monkeypatch.setattr("app.services.sms.SmsService._client", classmethod(lambda cls: FakeClient()))
    with app.test_request_context():
        from app.services.sms import SmsService
        SmsService._send_dypnsapi("+8613800000000", "login")

    assert captured["PhoneNumber"] == "13800000000"
    assert captured["TemplateParam"] == '{"code":"##code##","min":"5"}'
    assert "SchemeName" not in captured


def test_dypnsapi_verify_uses_valid_case_auth_policy(monkeypatch):
    app = create_app({
        "TESTING": True,
        "SMS_PROVIDER": "aliyun",
        "ALIYUN_SMS_SCHEME_NAME": "",
    })
    captured = {}

    class FakeClient:
        def check_sms_verify_code_with_options(self, request, _runtime_options):
            captured.update(request.to_map())
            return type("Response", (), {
                "body": type("Body", (), {"code": "OK"})(),
            })()

    monkeypatch.setattr("app.services.sms.SmsService._client", classmethod(lambda cls: FakeClient()))
    with app.app_context():
        from app.services.sms import SmsService
        assert SmsService.verify("+8613349849410", "login", "1234") is True

    assert captured["CaseAuthPolicy"] == 1
    assert captured["CountryCode"] == "cn"
    assert captured["PhoneNumber"] == "13349849410"
    assert captured["OutId"] == "login"


def test_aliyun_sms_provider_error_returns_service_unavailable(monkeypatch):
    app = create_app({
        "TESTING": True,
        "SMS_PROVIDER": "aliyun",
        "ALIYUN_SMS_ACCESS_KEY_ID": "key",
        "ALIYUN_SMS_ACCESS_KEY_SECRET": "secret",
        "ALIYUN_SMS_SIGN_NAME": "速通互联验证码",
        "ALIYUN_SMS_TEMPLATE_CODE": "SMS_100001",
    })

    calls = 0

    class FakeClient:
        def send_sms_verify_code_with_options(self, _request, _runtime_options):
            nonlocal calls
            calls += 1
            body = type("Body", (), {
                "code": "isp.SYSTEM_ERROR",
                "message": "网元系统异常",
            })()
            return type("Response", (), {"body": body})()

    monkeypatch.setattr("app.services.sms.SmsService._client", classmethod(lambda cls: FakeClient()))
    response = app.test_client().post(
        "/api/v1/auth/sms/request",
        json={"phone": "13900000000", "purpose": "login"},
    )

    assert response.status_code == 503
    assert response.get_json()["error"]["code"] == "SMS_PROVIDER_ERROR"

    second_response = app.test_client().post(
        "/api/v1/auth/sms/request",
        json={"phone": "13900000000", "purpose": "login"},
    )
    assert second_response.status_code == 429
    assert calls == 1


def test_register_requires_sms_code_when_provider_enabled(monkeypatch):
    app = create_app({"TESTING": True, "SMS_PROVIDER": "aliyun"})
    response = app.test_client().post("/api/v1/auth/register", json={"phone": "13800000000", "password": "password123", "name": "测试用户"})
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "SMS_CODE_INVALID"
