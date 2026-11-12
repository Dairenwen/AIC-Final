from __future__ import annotations

import re
import secrets
from datetime import UTC, datetime
from uuid import UUID

import click
from flask import Blueprint, current_app, g, request
from itsdangerous import BadData, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import select
from werkzeug.security import check_password_hash, generate_password_hash

from ..extensions import db
from ..models import User
from ..services.sms import SmsService
from .responses import error, ok


bp = Blueprint("auth", __name__)
PHONE_PATTERN = re.compile(r"^\+?[1-9][0-9]{7,14}$")
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def normalize_phone(value: object) -> str | None:
    phone = re.sub(r"[\s-]", "", str(value or "").strip())
    if re.fullmatch(r"1[3-9][0-9]{9}", phone):
        phone = f"+86{phone}"
    if not PHONE_PATTERN.fullmatch(phone):
        return None
    return phone


def normalize_display_name(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def normalize_organization(value: object) -> str:
    organization = normalize_display_name(value)
    if not organization or re.fullmatch(r"\?+", organization):
        return "未设置机构"
    return organization


def serialize_user(user: User) -> dict:
    return {
        "id": str(user.id),
        "phone": user.phone,
        "name": user.display_name,
        "organization": normalize_organization((user.profile or {}).get("organization")),
        "role": user.role_code,
        "plan": str((user.profile or {}).get("plan") or "科研基础版"),
    }


def token_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="zhiyan-auth-v1")


def create_session_token(user: User) -> tuple[str, str]:
    csrf_token = secrets.token_urlsafe(24)
    token = token_serializer().dumps(
        {"user_id": str(user.id), "session_version": user.session_version, "csrf": csrf_token}
    )
    return token, csrf_token


def authenticate_request():
    cookie_name = current_app.config["AUTH_COOKIE_NAME"]
    token = request.cookies.get(cookie_name)
    token_source = "cookie"
    authorization = request.headers.get("Authorization", "")
    if not token and authorization.startswith("Bearer "):
        token = authorization[7:].strip()
        token_source = "bearer"
    if not token:
        return error("请先登录", code="AUTH_REQUIRED", status=401)

    try:
        payload = token_serializer().loads(
            token,
            max_age=current_app.config["AUTH_TOKEN_MAX_AGE"],
        )
        user_id = UUID(str(payload.get("user_id", "")))
    except SignatureExpired:
        return error("登录状态已过期，请重新登录", code="AUTH_EXPIRED", status=401)
    except (BadData, ValueError, AttributeError):
        return error("登录凭证无效", code="AUTH_INVALID", status=401)

    user = db.session.get(User, user_id)
    if (
        user is None
        or user.status != "ACTIVE"
        or user.deleted_at is not None
        or user.session_version != payload.get("session_version")
    ):
        return error("登录状态已失效，请重新登录", code="AUTH_INVALID", status=401)

    if request.method in UNSAFE_METHODS and token_source == "cookie":
        csrf_token = request.headers.get("X-CSRF-Token", "")
        if not csrf_token or not secrets.compare_digest(csrf_token, str(payload.get("csrf", ""))):
            return error("安全校验失败，请刷新页面后重试", code="CSRF_INVALID", status=403)

    g.current_user = user
    g.auth_payload = payload
    return None


def require_role(*roles: str):
    if g.current_user.role_code not in roles:
        return error("当前账号无权访问该功能", code="FORBIDDEN", status=403)
    return None


@bp.post("/login")
def login():
    payload = request.get_json(silent=True) or {}
    phone = normalize_phone(payload.get("phone"))
    password = str(payload.get("password") or "")
    if phone is None or not password:
        return error("请输入正确的手机号和密码", code="INVALID_CREDENTIALS", status=400)

    user = db.session.scalar(select(User).where(User.phone == phone, User.deleted_at.is_(None)))
    password_configured = user is not None and user.password_hash != "LOGIN_NOT_CONFIGURED"
    if not password_configured or not check_password_hash(user.password_hash, password):
        return error("手机号或密码错误", code="INVALID_CREDENTIALS", status=401)
    if user.status != "ACTIVE":
        return error("账号当前不可用，请联系管理员", code="ACCOUNT_DISABLED", status=403)

    user.last_login_at = datetime.now(UTC)
    db.session.commit()
    token, csrf_token = create_session_token(user)
    response, status = ok({"user": serialize_user(user), "csrfToken": csrf_token})
    response.set_cookie(
        current_app.config["AUTH_COOKIE_NAME"],
        token,
        max_age=current_app.config["AUTH_TOKEN_MAX_AGE"],
        httponly=True,
        secure=current_app.config["AUTH_COOKIE_SECURE"],
        samesite="Lax",
        path="/",
    )
    return response, status


@bp.post("/register")
def register():
    payload = request.get_json(silent=True) or {}
    phone = normalize_phone(payload.get("phone"))
    password = str(payload.get("password") or "")
    display_name = normalize_display_name(payload.get("name") or payload.get("display_name"))
    organization = normalize_organization(payload.get("organization"))
    if phone is None:
        return error("请输入正确的手机号", code="PHONE_INVALID", status=400)
    if not display_name:
        return error("请输入昵称或姓名", code="DISPLAY_NAME_REQUIRED", status=400)
    if len(password) < 8:
        return error("密码至少需要 8 个字符", code="PASSWORD_TOO_SHORT", status=400)
    sms_code = str(payload.get("code") or "").strip()
    if SmsService.enabled() and not SmsService.verify(phone, "register", sms_code):
        return error("短信验证码无效或已过期", code="SMS_CODE_INVALID", status=400)

    existing = db.session.scalar(select(User).where(User.phone == phone, User.deleted_at.is_(None)))
    if existing is not None:
        return error("该手机号已注册，请直接登录", code="PHONE_ALREADY_REGISTERED", status=409)

    user = User(
        phone=phone,
        password_hash=generate_password_hash(password),
        display_name=display_name[:100],
        role_code="normal_user",
        status="ACTIVE",
        phone_verified_at=datetime.now(UTC),
        profile={
            "organization": organization,
            "plan": "科研基础版",
        },
    )
    db.session.add(user)
    db.session.commit()
    return ok(
        {
            "user": serialize_user(user),
            "message": "注册成功，请返回登录",
        },
        status=201,
    )


@bp.get("/me")
def me():
    return ok(
        {
            "user": serialize_user(g.current_user),
            "csrfToken": str(g.auth_payload.get("csrf", "")),
        }
    )


@bp.post("/logout")
def logout():
    response, status = ok({"loggedOut": True})
    response.delete_cookie(current_app.config["AUTH_COOKIE_NAME"], path="/")
    return response, status


@bp.post("/sms/request")
def request_sms_code():
    payload = request.get_json(silent=True) or {}
    phone = normalize_phone(payload.get("phone"))
    if phone is None:
        return error("请输入正确的手机号", code="PHONE_INVALID", status=400)
    purpose = str(payload.get("purpose") or "login").strip().lower()
    if purpose not in {"login", "register"}:
        return error("短信验证码用途无效", code="SMS_PURPOSE_INVALID", status=400)
    if not SmsService.enabled():
        return error("短信服务尚未配置", code="SMS_PROVIDER_NOT_CONFIGURED", status=501)
    try:
        SmsService.issue(phone, purpose)
    except ValueError as exc:
        return error(str(exc), code="SMS_RATE_LIMITED", status=429)
    except RuntimeError as exc:
        return error(str(exc), code="SMS_PROVIDER_ERROR", status=503)
    return ok({"message": "验证码已发送", "expiresIn": 300})


@bp.post("/sms/login")
def sms_login():
    payload = request.get_json(silent=True) or {}
    phone = normalize_phone(payload.get("phone"))
    code = str(payload.get("code") or "").strip()
    if phone is None or not code:
        return error("请输入正确的手机号和验证码", code="SMS_CREDENTIALS_REQUIRED", status=400)
    if not SmsService.enabled():
        return error("短信服务尚未配置", code="SMS_PROVIDER_NOT_CONFIGURED", status=501)
    if not SmsService.verify(phone, "login", code):
        return error("短信验证码无效或已过期", code="SMS_CODE_INVALID", status=401)
    user = db.session.scalar(select(User).where(User.phone == phone, User.deleted_at.is_(None)))
    if user is None:
        return error("该手机号尚未注册，请先注册", code="PHONE_NOT_REGISTERED", status=404)
    if user.status != "ACTIVE":
        return error("账号当前不可用，请联系管理员", code="ACCOUNT_DISABLED", status=403)
    user.last_login_at = datetime.now(UTC)
    db.session.commit()
    token, csrf_token = create_session_token(user)
    response, status = ok({"user": serialize_user(user), "csrfToken": csrf_token})
    response.set_cookie(current_app.config["AUTH_COOKIE_NAME"], token, max_age=current_app.config["AUTH_TOKEN_MAX_AGE"], httponly=True, secure=current_app.config["AUTH_COOKIE_SECURE"], samesite="Lax", path="/")
    return response, status


def register_auth_cli(app) -> None:
    @app.cli.command("set-user-password")
    @click.option("--phone", prompt="手机号")
    @click.option("--password", prompt=True, hide_input=True, confirmation_prompt=True)
    def set_user_password(phone: str, password: str) -> None:
        """Set a user's password and revoke their existing sessions."""
        normalized = normalize_phone(phone)
        if normalized is None:
            raise click.ClickException("手机号格式不正确")
        if len(password) < 8:
            raise click.ClickException("密码至少需要 8 个字符")
        user = db.session.scalar(select(User).where(User.phone == normalized, User.deleted_at.is_(None)))
        if user is None:
            raise click.ClickException("用户不存在")
        user.password_hash = generate_password_hash(password)
        user.session_version += 1
        db.session.commit()
        click.echo(f"已更新用户 {normalized} 的密码")
