from types import SimpleNamespace
from uuid import uuid4

from app.agents import task_recovery
from app.extensions import db


def test_worker_detection_requires_same_host_and_a_definite_process_identity(monkeypatch):
    monkeypatch.setattr(task_recovery.socket, "gethostname", lambda: "host-a")
    monkeypatch.setattr(task_recovery, "_boot_id", lambda: "boot-1")
    monkeypatch.setattr(task_recovery, "_process_start", lambda _pid: "start-1")
    marker = {"host": "host-a", "boot": "boot-1", "pid": 123, "start": "start-1"}
    assert task_recovery.worker_is_gone(marker) is False
    assert task_recovery.worker_is_gone({**marker, "host": "other-host"}) is False
    assert task_recovery.worker_is_gone({"host": "host-a"}) is False
    assert task_recovery.worker_is_gone({**marker, "start": "old-start"}) is True
    assert task_recovery.worker_is_gone({**marker, "boot": "old-boot"}) is True


def test_missing_worker_is_detected_but_inaccessible_process_is_left_alone(monkeypatch):
    monkeypatch.setattr(task_recovery.socket, "gethostname", lambda: "host-a")
    monkeypatch.setattr(task_recovery, "_boot_id", lambda: "boot-1")
    marker = {"host": "host-a", "boot": "boot-1", "pid": 123, "start": "start-1"}

    def missing(_pid):
        raise FileNotFoundError

    monkeypatch.setattr(task_recovery, "_process_start", missing)
    assert task_recovery.worker_is_gone(marker) is True

    def inaccessible(_pid):
        raise PermissionError

    monkeypatch.setattr(task_recovery, "_process_start", inaccessible)
    assert task_recovery.worker_is_gone(marker) is False


def test_waiting_for_user_input_is_never_marked_interrupted(monkeypatch):
    task = SimpleNamespace(status="WAITING_INPUT", trace_summary={"_worker": {}})
    monkeypatch.setattr(task_recovery, "worker_is_gone", lambda _marker: True)
    assert task_recovery.reconcile_interrupted_task(task) is False


def test_interrupted_task_preserves_output_and_adds_failure_event(monkeypatch):
    task = SimpleNamespace(
        id=uuid4(), status="RUNNING", progress=30, current_step="分析中", output_json={"existing": "kept"},
        error_code=None, safe_error_message=None, finished_at=None,
    )
    paper_run = SimpleNamespace(status="RUNNING")
    results = iter([paper_run, None, 4])
    events = []
    monkeypatch.setattr(db.session, "scalar", lambda _query: next(results))
    monkeypatch.setattr(db.session, "add", events.append)

    task_recovery.mark_interrupted(task)

    assert task.status == "FAILED"
    assert task.error_code == "WORKER_INTERRUPTED"
    assert task.output_json["existing"] == "kept"
    assert task.output_json["task_error"]["retryable"] is True
    assert paper_run.status == "FAILED"
    assert events[0].sequence == 5
    assert events[0].event_type == "task.failed"
