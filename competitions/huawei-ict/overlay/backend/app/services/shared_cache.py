"""Small shared state on Redis, with an in-process fallback.

Two things in this service are per-process when kept in memory: the
"one text message per minute" limit and the cached agent readiness.  With
Redis (Huawei Cloud DCS in production, a container locally) both hold across
restarts and across any number of backend instances.

Redis is optional.  Without REDIS_URL, or while Redis is unreachable, the
same calls use process memory, so a cache outage degrades to the previous
behaviour instead of failing requests.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

KEY_PREFIX = "zhiyan:"
_RETRY_AFTER_SECONDS = 15.0

_lock = threading.Lock()
_client: Any = None
_client_url = ""
_unavailable_until = 0.0
_memory: dict[str, tuple[float, str]] = {}


def redis_url() -> str:
    try:
        from flask import current_app

        return str(current_app.config.get("REDIS_URL") or "").strip()
    except RuntimeError:
        return os.getenv("REDIS_URL", "").strip()


def _connect():
    """Return a connected client, or None when Redis is absent or down."""
    global _client, _client_url, _unavailable_until
    url = redis_url()
    if not url:
        return None
    with _lock:
        if _client is not None and _client_url == url:
            return _client
        if time.monotonic() < _unavailable_until:
            return None
        try:
            import redis

            client = redis.Redis.from_url(
                url, decode_responses=True, socket_connect_timeout=2, socket_timeout=2, health_check_interval=30,
            )
            client.ping()
        except Exception as exc:  # noqa: BLE001 - any failure means "use memory"
            _unavailable_until = time.monotonic() + _RETRY_AFTER_SECONDS
            logger.warning("Redis unavailable, using in-process state: %s", type(exc).__name__)
            return None
        _client, _client_url = client, url
        return client


def _mark_failed(exc: Exception) -> None:
    global _client, _unavailable_until
    with _lock:
        _client = None
        _unavailable_until = time.monotonic() + _RETRY_AFTER_SECONDS
    logger.warning("Redis call failed, using in-process state: %s", type(exc).__name__)


def status() -> str:
    """'up', 'unavailable' or 'not_configured' — for the readiness endpoint."""
    if not redis_url():
        return "not_configured"
    return "up" if _connect() is not None else "unavailable"


def acquire_once(key: str, ttl_seconds: int) -> bool:
    """True for the first caller within ttl_seconds, False for the rest."""
    full_key = KEY_PREFIX + key
    client = _connect()
    if client is not None:
        try:
            return bool(client.set(full_key, "1", nx=True, ex=max(1, int(ttl_seconds))))
        except Exception as exc:  # noqa: BLE001
            _mark_failed(exc)
    now = time.monotonic()
    with _lock:
        held = _memory.get(full_key)
        if held and held[0] > now:
            return False
        _memory[full_key] = (now + ttl_seconds, "1")
        return True


def release(key: str) -> None:
    full_key = KEY_PREFIX + key
    client = _connect()
    if client is not None:
        try:
            client.delete(full_key)
        except Exception as exc:  # noqa: BLE001
            _mark_failed(exc)
    with _lock:
        _memory.pop(full_key, None)


def get_json(key: str) -> Any | None:
    full_key = KEY_PREFIX + key
    client = _connect()
    if client is not None:
        try:
            raw = client.get(full_key)
            return json.loads(raw) if raw else None
        except Exception as exc:  # noqa: BLE001
            _mark_failed(exc)
    with _lock:
        held = _memory.get(full_key)
        if not held or held[0] <= time.monotonic():
            return None
        return json.loads(held[1])


def set_json(key: str, value: Any, ttl_seconds: int) -> None:
    full_key = KEY_PREFIX + key
    raw = json.dumps(value, ensure_ascii=False)
    client = _connect()
    if client is not None:
        try:
            client.set(full_key, raw, ex=max(1, int(ttl_seconds)))
            return
        except Exception as exc:  # noqa: BLE001
            _mark_failed(exc)
    with _lock:
        _memory[full_key] = (time.monotonic() + ttl_seconds, raw)


def clear_prefix(prefix: str) -> None:
    full_prefix = KEY_PREFIX + prefix
    client = _connect()
    if client is not None:
        try:
            for name in client.scan_iter(match=full_prefix + "*", count=200):
                client.delete(name)
        except Exception as exc:  # noqa: BLE001
            _mark_failed(exc)
    with _lock:
        for name in [name for name in _memory if name.startswith(full_prefix)]:
            _memory.pop(name, None)


def reset_for_tests() -> None:
    global _client, _client_url, _unavailable_until
    with _lock:
        _client, _client_url, _unavailable_until = None, "", 0.0
        _memory.clear()
