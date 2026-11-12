"""Shared state uses Redis when configured and process memory otherwise."""
import pytest

from app import create_app
from app.services import shared_cache


@pytest.fixture(autouse=True)
def _clean():
    shared_cache.reset_for_tests()
    yield
    shared_cache.reset_for_tests()


class FakeRedis:
    def __init__(self):
        self.data = {}

    def ping(self):
        return True

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.data:
            return None
        self.data[key] = value
        return True

    def get(self, key):
        return self.data.get(key)

    def delete(self, key):
        self.data.pop(key, None)

    def scan_iter(self, match="*", count=None):
        return [key for key in list(self.data) if key.startswith(match.rstrip("*"))]


def test_without_redis_state_is_kept_in_process():
    app = create_app({"TESTING": True, "REDIS_URL": ""})
    with app.app_context():
        assert shared_cache.status() == "not_configured"
        assert shared_cache.acquire_once("sms:login:+8613800000000", 60) is True
        assert shared_cache.acquire_once("sms:login:+8613800000000", 60) is False
        shared_cache.set_json("readiness:x", {"readiness": "READY"}, 30)
        assert shared_cache.get_json("readiness:x") == {"readiness": "READY"}
        shared_cache.clear_prefix("readiness:")
        assert shared_cache.get_json("readiness:x") is None


def test_with_redis_state_is_written_to_redis(monkeypatch):
    import redis

    fake = FakeRedis()
    monkeypatch.setattr(redis.Redis, "from_url", classmethod(lambda cls, url, **kwargs: fake))
    app = create_app({"TESTING": True, "REDIS_URL": "redis://cache.example:6379/0"})
    with app.app_context():
        assert shared_cache.status() == "up"
        assert shared_cache.acquire_once("sms:login:+8613800000000", 60) is True
        assert shared_cache.acquire_once("sms:login:+8613800000000", 60) is False
        shared_cache.set_json("readiness:x", {"readiness": "READY"}, 30)
    assert fake.data["zhiyan:sms:login:+8613800000000"] == "1"
    assert "zhiyan:readiness:x" in fake.data


def test_unreachable_redis_degrades_to_process_memory(monkeypatch):
    import redis

    def refuse(cls, url, **kwargs):
        raise redis.ConnectionError("refused")

    monkeypatch.setattr(redis.Redis, "from_url", classmethod(refuse))
    app = create_app({"TESTING": True, "REDIS_URL": "redis://cache.example:6379/0"})
    with app.app_context():
        assert shared_cache.status() == "unavailable"
        assert shared_cache.acquire_once("sms:login:+8613800000000", 60) is True
        assert shared_cache.acquire_once("sms:login:+8613800000000", 60) is False


def test_sms_rate_limit_uses_the_shared_window(monkeypatch):
    from app.services.sms import SmsService

    sent = []
    monkeypatch.setattr(SmsService, "_send_dypnsapi", classmethod(lambda cls, phone, purpose: sent.append(phone)))
    app = create_app({"TESTING": True, "SMS_PROVIDER": "aliyun", "REDIS_URL": ""})
    with app.app_context():
        SmsService.issue("+8613800000000", "login")
        with pytest.raises(ValueError):
            SmsService.issue("+8613800000000", "login")
    assert sent == ["+8613800000000"]
