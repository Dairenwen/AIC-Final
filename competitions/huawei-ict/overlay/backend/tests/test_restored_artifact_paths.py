"""Read-only migration tests using existing repository files; no fixture files."""
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask

from app.api.tasks import _resolve_restored_artifact, task_artifact


FIXTURE_ROOT = Path(__file__).resolve().parent
FIXTURE_FILE = Path(__file__).resolve()


@pytest.mark.parametrize('old_root', [
    'E:/postgraduate/competition/demov1.5/backend',
    r'D:\研究生文档\demov1.3\backend',
    '/srv/old-release/backend',
])
@pytest.mark.parametrize('directory', [
    'literature_search', 'patent_drafting', 'academic_figure', 'literature-ppt',
])
def test_restore_retains_exact_directory_suffix(old_root, directory):
    raw = f'{old_root}/generated/{directory}/{FIXTURE_FILE.name}'
    assert _resolve_restored_artifact(raw, FIXTURE_ROOT, directory) == FIXTURE_FILE


def test_current_absolute_path_stays_usable():
    assert _resolve_restored_artifact(FIXTURE_FILE, FIXTURE_ROOT, 'literature_search') == FIXTURE_FILE


@pytest.mark.parametrize('raw', [
    f'E:/old/generated/academic_figure/{FIXTURE_FILE.name}',
    f'E:/old/generated/literature_search/../{FIXTURE_FILE.name}',
    f'E:/old/generated/literature_search/../../app/api/tasks.py',
    f'E:/old/{FIXTURE_FILE.name}',
    'E:/old/generated/literature_search/nonexistent-history.json',
    'E:/old/generated/literature_search/invalid\x00.json',
    str(FIXTURE_ROOT.parent / 'app/api/tasks.py'),
])
def test_missing_wrong_directory_and_escaping_paths_are_rejected(raw):
    assert _resolve_restored_artifact(raw, FIXTURE_ROOT, 'literature_search') is None


def test_history_download_reads_restored_file_without_changing_record(monkeypatch):
    app = Flask(__name__)
    app.config['AGENT_GENERATED_DIR'] = FIXTURE_ROOT
    artifacts = {'report_markdown': f'E:/old/backend/generated/literature_search/{FIXTURE_FILE.name}'}
    task = SimpleNamespace(output_json={'artifacts': artifacts})
    original = dict(artifacts)
    monkeypatch.setattr('app.api.tasks.find_task', lambda _: task)
    with app.test_request_context():
        response = task_artifact('fixture-task', 'compliance-report')
        response.direct_passthrough = False
        assert response.status_code == 200
        assert response.get_data() == FIXTURE_FILE.read_bytes()
        response.close()
    assert artifacts == original


def test_missing_history_download_preserves_existing_not_ready_response(monkeypatch):
    app = Flask(__name__)
    app.config['AGENT_GENERATED_DIR'] = FIXTURE_ROOT
    task = SimpleNamespace(output_json={'artifacts': {
        'report_markdown': 'E:/old/backend/generated/literature_search/missing-report.md',
    }})
    monkeypatch.setattr('app.api.tasks.find_task', lambda _: task)
    with app.test_request_context():
        response, status = task_artifact('fixture-task', 'compliance-report')
        assert status == 404
        assert response.get_json()['error']['code'] == 'ARTIFACT_NOT_READY'
