"""Single-host knowledge-base write barrier for index maintenance."""
from __future__ import annotations

import fcntl
import threading
from functools import wraps

from knowledge_base_runtime.backend.config.settings import DATA_DIR


_state = threading.local()


def maintenance_held() -> bool:
    return bool(getattr(_state, 'exclusive', False))


def guard_knowledge_write(handler):
    """Reject conflicting writes; lock is shared by processes using DATA_DIR.

    Direct database access and other hosts without the same volume bypass this
    guard and must be stopped by the operator during a rebuild.
    """
    @wraps(handler)
    def guarded(self):
        path, _ = self._path_query()
        if self.command == 'POST' and path == '/api/v1/search':
            return handler(self)
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with (DATA_DIR / '.knowledge-write.lock').open('a+') as lock_file:
            exclusive = self.command == 'POST' and path == '/api/v1/admin/retrieval/reindex'
            try:
                fcntl.flock(lock_file.fileno(), (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
            except BlockingIOError:
                self._send_json({'error': '知识库正在写入或重建，请稍后重试。'}, 503)
                return
            _state.exclusive = exclusive
            try:
                return handler(self)
            finally:
                _state.exclusive = False
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    return guarded
