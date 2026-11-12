import base64
import json
from io import BytesIO
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from flask import g

from app import create_app
from app.api.model_configs import (
    builtin_vertical_model,
    find_owned_config,
    serialize_config,
    verify_openai_compatible,
)
from app.extensions import db
from app.services.model_credentials import decrypt_api_key, encrypt_api_key
from app.services.platform_model_runtime import get_platform_model_runtime
from app.llm import run_openai_compatible_chat


def test_model_api_key_is_encrypted_and_never_serialized():
    encryption_key = base64.urlsafe_b64encode(b"m" * 32).decode("ascii")
    app = create_app({"TESTING": True, "MODEL_CONFIG_ENCRYPTION_KEY": encryption_key})
    with app.app_context():
        encrypted, nonce, version = encrypt_api_key("sk-private-value")
        assert encrypted != b"sk-private-value"
        assert decrypt_api_key(encrypted, nonce, version) == "sk-private-value"

    now = datetime.now(UTC)
    item = SimpleNamespace(
        id=uuid4(),
        provider_code="openai_compatible",
        name="研究模型",
        base_url="https://model.example/v1",
        model_name="research-model",
        status="ACTIVE",
        settings={},
        encrypted_api_key=encrypted,
        key_last_four="alue",
        last_verified_at=now,
        last_error_code=None,
        created_at=now,
        updated_at=now,
    )
    payload = serialize_config(item)
    assert payload["masked_api_key"] == "••••alue"
    assert "encrypted_api_key" not in payload
    assert "sk-private-value" not in str(payload)


def test_serialized_config_marks_chat_default():
    now = datetime.now(UTC)
    item = SimpleNamespace(
        id=uuid4(),
        provider_code="openai_compatible",
        name="默认研究模型",
        base_url="https://model.example/v1",
        model_name="research-model",
        status="ACTIVE",
        default_for=["chat"],
        settings={},
        encrypted_api_key=b"encrypted",
        key_last_four="1234",
        last_verified_at=now,
        last_error_code=None,
        created_at=now,
        updated_at=now,
    )

    payload = serialize_config(item)

    assert payload["default_for"] == ["chat"]
    assert payload["is_default"] is True


def test_builtin_platform_model_is_reported_as_not_deployed_by_default():
    """The fine-tuned 27B model is not deployed with this release.

    Its integration point stays, but it must advertise itself as unavailable
    so the web UI hides it instead of showing a permanently unconfigured card.
    """
    app = create_app({"TESTING": True, "QWEN_DPO_MODEL": "vertical-test-model"})
    with app.app_context():
        payload = builtin_vertical_model()

    assert payload == {
        "value": "vertical_domain",
        "name": "平台通用模型",
        "model_name": "",
        "source": "builtin",
        "config_id": None,
        "enabled": False,
    }


def test_builtin_platform_model_is_reported_once_enabled():
    app = create_app({
        "TESTING": True,
        "PLATFORM_MODEL_ENABLED": True,
        "QWEN_DPO_ENV_OVERRIDE": True,
        "QWEN_DPO_BASE_URL": "https://platform.example/v1",
        "QWEN_DPO_MODEL": "vertical-test-model",
    })
    with app.app_context():
        payload = builtin_vertical_model()

    assert payload["enabled"] is True
    assert payload["model_name"] == "vertical-test-model"


def test_platform_model_stays_disabled_without_an_endpoint():
    """Turning the flag on is not enough: an endpoint has to be configured."""
    app = create_app({
        "TESTING": True,
        "PLATFORM_MODEL_ENABLED": True,
        "QWEN_DPO_ENV_OVERRIDE": True,
        "QWEN_DPO_BASE_URL": "",
        "QWEN_DPO_MODEL": "",
    })
    with app.app_context():
        assert builtin_vertical_model()["enabled"] is False


def test_account_api_defaults_to_deepseek_official_endpoint():
    """The standard model is DeepSeek's own endpoint, not a reseller gateway.

    The handed-over configuration pointed at a SiliconFlow gateway tied to a
    credential that has since been removed.
    """
    app = create_app({"TESTING": True})

    assert app.config["USER_API_MODEL_BASE_URL"] == "https://api.deepseek.com"
    assert app.config["USER_API_MODEL_NAME"] == "deepseek-flash"


def test_no_model_credentials_are_baked_into_the_defaults():
    """Shipping a placeholder key invites it being mistaken for a real one."""
    app = create_app({"TESTING": True})

    for key in ("QWEN_DPO_API_KEY", "USER_API_MODEL_API_KEY", "TRANSLATION_API_KEY"):
        assert app.config.get(key, "") == "", key


def test_platform_runtime_can_prefer_deployment_environment(monkeypatch):
    app = create_app({
        "TESTING": True,
        "QWEN_DPO_BASE_URL": "https://k9n4n0vf52f6239t-9001.js01-webservice.gpuhome.cc:50000/v1",
        "QWEN_DPO_MODEL": "Qwen3.8-27B-UD-Q4_K_XL",
        "QWEN_DPO_API_KEY": "sk-unsloth-YOUR_KEY",
        "QWEN_DPO_ENV_OVERRIDE": True,
    })
    with app.app_context():
        runtime = get_platform_model_runtime()

    assert runtime.base_url == "https://k9n4n0vf52f6239t-9001.js01-webservice.gpuhome.cc:50000/v1"
    assert runtime.model_name == "Qwen3.8-27B-UD-Q4_K_XL"
    assert runtime.api_key == "sk-unsloth-YOUR_KEY"


def test_owned_model_lookup_includes_current_user_filter(monkeypatch):
    app = create_app({"TESTING": True})
    captured = {}

    def fake_scalar(statement):
        captured["statement"] = str(statement)
        return None

    monkeypatch.setattr(db.session, "scalar", fake_scalar)
    with app.test_request_context():
        g.current_user = SimpleNamespace(id=uuid4())
        assert find_owned_config(str(uuid4())) is None

    sql = captured["statement"]
    assert "model_configs.owner_user_id" in sql
    assert "model_configs.deleted_at IS NULL" in sql


def test_model_verification_uses_chat_completions_without_exposing_key(monkeypatch):
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return b'{"choices":[{"message":{"content":"OK"}}]}'

    def fake_urlopen(api_request, timeout):
        captured["url"] = api_request.full_url
        captured["authorization"] = api_request.headers["Authorization"]
        captured["body"] = api_request.data
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("app.api.model_configs.url_request.urlopen", fake_urlopen)
    verify_openai_compatible(
        base_url="https://model.example/v1",
        model_name="research-model",
        api_key="sk-secret",
        timeout=12,
    )
    assert captured["url"] == "https://model.example/v1/chat/completions"
    assert captured["authorization"] == "Bearer sk-secret"
    assert b"sk-secret" not in captured["body"]
    assert captured["timeout"] == 12
    assert b'"max_tokens": 64' in captured["body"]
    assert b'"enable_thinking": false' in captured["body"]


@pytest.mark.parametrize("base_url", [
    "https://api.deepseek.com",
    "https://api.deepseek.com/v1",
    "https://api.siliconflow.cn/v1",
    "https://api.siliconflow.com/v1",
])
def test_model_verification_disables_thinking_for_provider(monkeypatch, base_url):
    calls = []

    def fake_urlopen(api_request, timeout):
        payload = json.loads(api_request.data)
        assert "chat_template_kwargs" not in payload
        assert payload["max_tokens"] == 64
        if "deepseek.com" in base_url:
            assert payload["thinking"] == {"type": "disabled"}
            assert "enable_thinking" not in payload
        else:
            assert payload["enable_thinking"] is False
            assert "thinking" not in payload
        calls.append(api_request.full_url)
        return BytesIO(b'{"choices":[{"message":{"content":"OK"}}]}')

    monkeypatch.setattr("app.api.model_configs.url_request.urlopen", fake_urlopen)
    verify_openai_compatible(
        base_url=base_url, model_name="test-model", api_key="test-key", timeout=12
    )
    assert calls == [base_url + "/chat/completions"]


def test_chat_runtime_normalizes_list_content(monkeypatch):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return b'{"choices":[{"message":{"content":[{"type":"text","text":"hello"}]}}]}'

    monkeypatch.setattr("app.llm.request.urlopen", lambda *_args, **_kwargs: FakeResponse())
    app = create_app({
        "TESTING": True,
        "PLATFORM_MODEL_ENABLED": True,
        "QWEN_DPO_ENV_OVERRIDE": True,
        "QWEN_DPO_BASE_URL": "https://model.example/v1",
        "QWEN_DPO_MODEL": "test-model",
        "QWEN_DPO_API_KEY": "test-key",
    })
    with app.app_context():
        result = run_openai_compatible_chat(messages=[{"role": "user", "content": "hi"}])
    assert result["content"] == "hello"


def test_chat_runtime_retries_connection_reset(monkeypatch):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return b'{"choices":[{"message":{"content":"recovered"}}]}'

    attempts = 0

    def flaky_urlopen(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError(10053, "connection aborted")
        return FakeResponse()

    monkeypatch.setattr("app.llm.request.urlopen", flaky_urlopen)
    monkeypatch.setattr("app.llm.time.sleep", lambda _seconds: None)
    app = create_app({
        "TESTING": True,
        "PLATFORM_MODEL_ENABLED": True,
        "QWEN_DPO_ENV_OVERRIDE": True,
        "QWEN_DPO_BASE_URL": "https://model.example/v1",
        "QWEN_DPO_MODEL": "test-model",
        "QWEN_DPO_API_KEY": "test-key",
        "MODEL_HTTP_MAX_RETRIES": 2,
    })
    with app.app_context():
        result = run_openai_compatible_chat(messages=[{"role": "user", "content": "hi"}])

    assert attempts == 2
    assert result["content"] == "recovered"


def test_disabled_platform_model_never_sends_chat_request(monkeypatch):
    from flask import Flask
    monkeypatch.setattr('app.llm.request.urlopen', lambda *_args, **_kwargs: pytest.fail('unexpected model request'))
    app = Flask(__name__)
    app.config['PLATFORM_MODEL_ENABLED'] = False
    with app.app_context(), pytest.raises(RuntimeError, match='还没有可用的模型'):
        run_openai_compatible_chat(messages=[{'role': 'user', 'content': 'test'}])


def test_disabled_platform_model_is_not_probed_for_readiness(monkeypatch):
    from flask import Flask
    from app.services.agent_readiness import _calculate_readiness
    monkeypatch.setattr('app.services.agent_readiness.endpoint_reachable', lambda *_: pytest.fail('unexpected model probe'))
    app = Flask('app')
    for key in ('INNOVATION_AGENT_ROOT', 'PAPER_READING_RUNTIME_ROOT', 'PATENT_DRAFTING_RUNTIME_ROOT',
                'COMPLIANCE_AGENT_ROOT', 'ARXIV_DAILY_RUNTIME_ROOT', 'ACADEMIC_FIGURE_RUNTIME_ROOT',
                'TRANSLATION_AGENT_ROOT'):
        app.config[key] = '.'
    app.config.update(PLATFORM_MODEL_ENABLED=False, AGENT_READINESS_CONNECT_TIMEOUT_SECONDS=1)
    result = _calculate_readiness(app, 'manuscript_assistance', has_personal_model=False)
    assert result['status'] in {'降级可用', '依赖异常'}


def test_agent_model_fallbacks_respect_disabled_platform(monkeypatch, tmp_path):
    import subprocess
    from app.agents.manuscript_assistance.service import ManuscriptAssistanceService
    from app.agents.academic_figure.service import AcademicFigureService
    from app.agents.patent_drafting.service import PatentDraftingService
    from app.agents.paper_reading.service import PaperReadingService
    app = SimpleNamespace(config={'PLATFORM_MODEL_ENABLED': False})
    monkeypatch.setattr(subprocess, 'run', lambda *_args, **_kwargs: pytest.fail('unexpected subprocess'))
    with pytest.raises(RuntimeError, match='还没有可用的模型'):
        ManuscriptAssistanceService(app).run_core('test', tmp_path)
    with pytest.raises(RuntimeError, match='还没有可用的模型'):
        PatentDraftingService(app)._resolve_model_runtime(SimpleNamespace(model_config_id=None), None)
    with pytest.raises(RuntimeError, match='还没有可用的模型'):
        PaperReadingService(app).run_core([], '', 'fast', tmp_path, tmp_path, tmp_path)
    runtime = AcademicFigureService(app)._resolve_model_runtime(SimpleNamespace(model_config_id=None), None)
    assert not runtime['base_url'] and not runtime['api_key']


def test_manuscript_personal_model_still_passes_through(monkeypatch, tmp_path):
    import subprocess
    from app.agents.manuscript_assistance.service import ManuscriptAssistanceService
    calls = []
    monkeypatch.setattr(subprocess, 'run', lambda *args, **kwargs: calls.append(kwargs['env']) or subprocess.CompletedProcess(args, 0, stdout='ok', stderr=''))
    app = SimpleNamespace(config={'PLATFORM_MODEL_ENABLED': False})
    ManuscriptAssistanceService(app).run_core('test', tmp_path, {
        'base_url': 'https://personal.test/v1', 'model_name': 'personal', 'api_key': 'test', 'timeout_seconds': 10,
    })
    assert calls[0]['OPENAI_API_BASE'] == 'https://personal.test/v1'


def test_compliance_disabled_model_passes_offline_flag(monkeypatch, tmp_path):
    import subprocess
    from pathlib import Path
    from app.agents.academic_compliance.service import AcademicComplianceService
    calls = []
    monkeypatch.setattr(subprocess, 'run', lambda *args, **kwargs: calls.append(kwargs['env']) or subprocess.CompletedProcess(args, 0, stdout='ok', stderr=''))
    runtime_root = Path(__file__).resolve().parents[1] / 'runtimes/academic_compliance_agent'
    app = SimpleNamespace(config={
        'PLATFORM_MODEL_ENABLED': False, 'COMPLIANCE_AGENT_ROOT': runtime_root,
        'QWEN_DPO_TIMEOUT_SECONDS': 10, 'COMPLIANCE_AGENT_USE_LLM': True,
        'COMPLIANCE_AGENT_MEMORY_ENABLED': False, 'COMPLIANCE_AGENT_TIMEOUT_SECONDS': 10,
    })
    AcademicComplianceService(app).run_core(tmp_path/'in.txt', tmp_path, 'u', 't', {'task_type': 'all', 'target_rule_set': 'default'})
    assert calls[0]['COMPLIANCE_AGENT_USE_LLM'] == 'false'
    assert calls[0]['OPENAI_BASE_URL'] == ''


def _fake_urlopen(responses, sent):
    import io, json as _json

    class _Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    def fake(api_request, timeout=None):
        sent.append(_json.loads(api_request.data.decode("utf-8")))
        return _Response(_json.dumps(responses[min(len(sent), len(responses)) - 1]).encode("utf-8"))

    return fake


def test_deepseek_small_budget_requests_switch_reasoning_off(monkeypatch):
    from flask import Flask
    sent = []
    answer = {"choices": [{"message": {"content": "ok"}}], "usage": {}}
    monkeypatch.setattr('app.llm.request.urlopen', _fake_urlopen([answer], sent))
    app = Flask(__name__)
    with app.app_context():
        result = run_openai_compatible_chat(
            messages=[{'role': 'user', 'content': 'test'}], model='deepseek-flash',
            base_url='https://api.deepseek.com/v1', api_key='k', max_tokens=1024,
        )
    assert result['content'] == 'ok'
    assert sent[0]['thinking'] == {'type': 'disabled'}


def test_other_gateways_are_not_sent_the_reasoning_switch_up_front(monkeypatch):
    from flask import Flask
    sent = []
    answer = {"choices": [{"message": {"content": "ok"}}], "usage": {}}
    monkeypatch.setattr('app.llm.request.urlopen', _fake_urlopen([answer], sent))
    app = Flask(__name__)
    with app.app_context():
        run_openai_compatible_chat(
            messages=[{'role': 'user', 'content': 'test'}], model='some-model',
            base_url='https://gateway.example/v1', api_key='k', max_tokens=1024,
        )
    assert 'thinking' not in sent[0]


def test_reasoning_only_answer_is_asked_again_without_reasoning(monkeypatch):
    from flask import Flask
    sent = []
    empty = {"choices": [{"message": {"content": ""}, "finish_reason": "length"}], "usage": {}}
    answer = {"choices": [{"message": {"content": "final answer"}}], "usage": {}}
    monkeypatch.setattr('app.llm.request.urlopen', _fake_urlopen([empty, answer], sent))
    app = Flask(__name__)
    with app.app_context():
        result = run_openai_compatible_chat(
            messages=[{'role': 'user', 'content': 'test'}], model='reasoner',
            base_url='https://gateway.example/v1', api_key='k', max_tokens=1024,
        )
    assert result['content'] == 'final answer'
    assert len(sent) == 2 and sent[1]['thinking'] == {'type': 'disabled'}
