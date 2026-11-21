from __future__ import annotations

import json
import threading
import time
from urllib.parse import urlparse

from flask import current_app


class SmsService:
    """Alibaba Cloud Dypnsapi SMS authentication adapter.

    Dypnsapi generates and validates the verification code on the provider
    side. The application never receives or stores the plaintext code.
    """

    _last_sent: dict[tuple[str, str], float] = {}
    _lock = threading.Lock()

    @classmethod
    def enabled(cls) -> bool:
        return str(current_app.config.get("SMS_PROVIDER", "disabled")).lower() == "aliyun"

    @classmethod
    def issue(cls, phone: str, purpose: str) -> None:
        if not cls.enabled():
            raise RuntimeError("短信认证服务尚未配置")
        now = time.time()
        key = (phone, purpose)
        with cls._lock:
            previous = cls._last_sent.get(key)
            if previous and now - previous < 60:
                raise ValueError("验证码发送过于频繁，请至少等待60秒后再试")
            # The provider may count failed carrier attempts for frequency
            # control, so reserve the local window before the network call.
            cls._last_sent[key] = now
        cls._send_dypnsapi(phone, purpose)

    @classmethod
    def verify(cls, phone: str, purpose: str, code: str) -> bool:
        if not cls.enabled() or not code:
            return False
        try:
            from alibabacloud_dypnsapi20170525 import models as dypns_models
            from alibabacloud_tea_util import models as util_models

            request = dypns_models.CheckSmsVerifyCodeRequest(
                # Dypnsapi accepts only 1 (case-insensitive) or 2
                # (case-sensitive); 0 causes isv.ValidateFail.
                case_auth_policy=1,
                country_code="cn",
                phone_number=phone.removeprefix("+86"),
                verify_code=code,
                out_id=purpose,
                scheme_name=current_app.config.get("ALIYUN_SMS_SCHEME_NAME") or None,
            )
            response = cls._client().check_sms_verify_code_with_options(
                request, util_models.RuntimeOptions()
            )
            body = getattr(response, "body", response)
            return str(getattr(body, "code", "")) == "OK"
        except Exception:
            current_app.logger.exception("Aliyun Dypnsapi SMS verification failed")
            return False

    @classmethod
    def _send_dypnsapi(cls, phone: str, purpose: str) -> None:
        cfg = current_app.config
        required = (
            "ALIYUN_SMS_ACCESS_KEY_ID",
            "ALIYUN_SMS_ACCESS_KEY_SECRET",
            "ALIYUN_SMS_SIGN_NAME",
            "ALIYUN_SMS_TEMPLATE_CODE",
        )
        if any(not cfg.get(name) for name in required):
            raise RuntimeError("阿里云短信认证服务配置不完整，需要签名、模板和认证方案")
        try:
            from alibabacloud_dypnsapi20170525 import models as dypns_models
            from alibabacloud_tea_util import models as util_models

            request = dypns_models.SendSmsVerifyCodeRequest(
                phone_number=phone.removeprefix("+86"),
                country_code="cn",
                sign_name=cfg["ALIYUN_SMS_SIGN_NAME"],
                template_code=cfg["ALIYUN_SMS_TEMPLATE_CODE"],
                # Template 100001 declares both ${code} and ${min}.
                template_param=json.dumps(
                    {
                        "code": "##code##",
                        "min": str(current_app.config.get("ALIYUN_SMS_TEMPLATE_MINUTES", 5)),
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                interval=60,
                valid_time=300,
                return_verify_code=False,
                duplicate_policy=1,
                out_id=purpose,
                # Optional Dypnsapi verification service name. Do not put the
                # SMS template name here; an empty value selects the account's
                # default service.
                scheme_name=cfg.get("ALIYUN_SMS_SCHEME_NAME") or None,
            )
            response = cls._client().send_sms_verify_code_with_options(
                request, util_models.RuntimeOptions()
            )
            body = getattr(response, "body", response)
            response_code = str(getattr(body, "code", "UNKNOWN"))
            if response_code != "OK":
                message = str(getattr(body, "message", "短信认证发送失败"))
                current_app.logger.warning(
                    "Aliyun Dypnsapi SMS rejected request: code=%s message=%s request_id=%s",
                    response_code,
                    message,
                    getattr(body, "request_id", None),
                )
                if response_code in {"biz.FREQUENCY", "isv.BUSINESS_LIMIT_CONTROL"}:
                    raise ValueError(f"验证码发送过于频繁（{response_code}）：{message}")
                raise RuntimeError(f"阿里云短信认证发送失败（{response_code}）：{message}")
        except ImportError as exc:
            raise RuntimeError("阿里云短信认证 SDK 未安装，请安装 alibabacloud_dypnsapi20170525") from exc
        except (RuntimeError, ValueError):
            raise
        except Exception as exc:
            raise RuntimeError("短信认证服务暂时不可用，请稍后重试") from exc

    @classmethod
    def _client(cls):
        from alibabacloud_dypnsapi20170525.client import Client
        from alibabacloud_tea_openapi import models as open_api_models

        cfg = current_app.config
        endpoint = str(cfg.get("ALIYUN_SMS_ENDPOINT", "https://dypnsapi.aliyuncs.com/"))
        parsed = urlparse(endpoint if "://" in endpoint else f"https://{endpoint}")
        client_cfg = open_api_models.Config(
            access_key_id=cfg["ALIYUN_SMS_ACCESS_KEY_ID"],
            access_key_secret=cfg["ALIYUN_SMS_ACCESS_KEY_SECRET"],
            endpoint=parsed.netloc or parsed.path,
            region_id=cfg.get("ALIYUN_SMS_REGION_ID") or None,
        )
        return Client(client_cfg)
