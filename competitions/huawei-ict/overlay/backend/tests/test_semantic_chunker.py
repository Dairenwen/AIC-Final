import math

import pytest

import chunker
from knowledge_base_runtime.backend.service import chunking


def test_semantic_boundary_responds_to_embedding_change(monkeypatch):
    text = 'A' * 29 + '.' + 'B' * 29 + '.' + 'C' * 29 + '.' + 'D' * 29 + '.'
    # Chinese punctuation supplies unambiguous sentence boundaries.
    text = text.replace('.', '。')
    monkeypatch.setattr(chunker, 'embed_texts', lambda _: [[1, 0], [1, 0], [0, 1], [0, 1]])
    result = chunker.chunk_semantic_details(text, 100, 10)
    assert result[0]['end'] == 60
    assert result[0]['cut_method'] == 'embedding_cosine'
    monkeypatch.setattr(chunker, 'embed_texts', lambda _: [[1, 0], [1, 0], [1, 0], [0, 1]])
    assert chunker.chunk_semantic_details(text, 100, 10)[0]['end'] == 90


@pytest.mark.parametrize('text', ['中' * 5000, ('Sentence one.\n另一句。\n\n' * 300), 'short', '  text\n'])
def test_semantic_chunks_preserve_all_text_and_bounded_overlap(monkeypatch, text):
    monkeypatch.setattr(chunker, 'embed_texts', lambda units: [[1, 0] for _ in units])
    chunks = chunker.chunk_semantic_details(text)
    rebuilt = chunks[0]['content']
    for previous, current in zip(chunks, chunks[1:]):
        assert current['start'] == previous['end'] - 200
        rebuilt += current['content'][200:]
    assert rebuilt == text
    assert all(0 < len(c['content']) <= 1024 for c in chunks)


@pytest.mark.parametrize('vectors', [[[0, 0]], [[math.nan, 1]], [[math.inf, 1]], []])
def test_invalid_embeddings_are_not_reported_as_semantic_success(monkeypatch, vectors):
    monkeypatch.setattr(chunker, 'embed_texts', lambda _: vectors)
    with pytest.raises(ValueError):
        chunker.chunk_semantic_details('a sentence')


def test_semantic_failure_does_not_delete_original_chunks(monkeypatch):
    def unavailable(_):
        raise RuntimeError('model unavailable')
    monkeypatch.setattr(chunker, 'embed_texts', unavailable)
    monkeypatch.setattr(chunking, 'get_db', lambda: pytest.fail('Must fail before any database mutation'))
    with pytest.raises(RuntimeError, match='原有分块保持不变'):
        chunking.replace_chunks('paper', 'A paper sentence.', strategy='semantic_bge_m3_v1')


def test_semantic_empty_input_and_invalid_limits(monkeypatch):
    monkeypatch.setattr(chunker, 'embed_texts', lambda _: pytest.fail('No model needed for empty input'))
    assert chunker.chunk_semantic_details(' \n ') == []
    for size, overlap in [(0, 0), (4, 4), (5, -1), (3.5, 1)]:
        with pytest.raises(ValueError):
            chunker.chunk_semantic_details('text', size, overlap)


def test_semantic_embeddings_are_batched_without_losing_text(monkeypatch):
    text = 'Sentence。' * 21
    batches = []
    monkeypatch.setattr(chunker, 'BGE_EMBED_BATCH_SIZE', 4)
    def embed(units):
        batches.append(len(units))
        return [[1, 0] for _ in units]
    monkeypatch.setattr(chunker, 'embed_texts', embed)
    chunks = chunker.chunk_semantic_details(text, 40, 5)
    assert batches == [4, 4, 4, 4, 4, 1]
    assert chunks[0]['content'] + ''.join(chunk['content'][5:] for chunk in chunks[1:]) == text


def test_empty_paper_cannot_erase_existing_chunks(monkeypatch):
    monkeypatch.setattr(chunking, 'get_db', lambda: pytest.fail('Empty paper must not reach database'))
    with pytest.raises(ValueError, match='原有分块保持不变'):
        chunking.replace_chunks('paper', '', strategy='semantic_bge_m3_v1')


def test_vector_replacement_reports_index_write_failure(monkeypatch):
    monkeypatch.setattr(chunking, 'KB_MILVUS_ENABLED', True)
    monkeypatch.setattr(chunking, 'replace_chunks_milvus', lambda *_: False)
    success, warning = chunking._replace_vector_index('paper', [{'chunk_id': 'x'}])
    assert not success
    assert '旧索引未主动删除' in warning
    assert '新旧索引混合' in warning


def test_rebuild_milvus_reports_failed_replacement(monkeypatch):
    from knowledge_base_runtime.backend.service import retrieval_index
    calls = []
    monkeypatch.setattr(retrieval_index, 'replace_chunks_milvus', lambda paper, chunks: calls.append(('replace', paper)) or False)
    assert retrieval_index._sync_milvus([{'id': 'p'}], {'p': [{'chunk_id': 'p_0'}]}) == (0, 1)
    assert calls == [('replace', 'p')]


@pytest.mark.parametrize('failure', ['embedding', 'upsert', 'partial'])
def test_milvus_replace_never_deletes_old_vectors_before_complete_write(monkeypatch, failure):
    from knowledge_base_runtime.backend.service import retrieval_backends as rb

    class Client:
        def upsert(self, **kwargs):
            if failure == 'upsert':
                raise RuntimeError('write failed')
            return {'upsert_count': 0 if failure == 'partial' else 1}

        def delete(self, **kwargs):
            pytest.fail('old vectors must not be deleted on failure')

    def embed(_):
        if failure == 'embedding':
            raise RuntimeError('model unavailable')
        return [[1.0, 0.0]]

    monkeypatch.setattr(rb, 'KB_MILVUS_ENABLED', True)
    monkeypatch.setattr(rb, 'embed_texts', embed)
    monkeypatch.setattr(rb, '_milvus_client', lambda: Client())
    monkeypatch.setattr(rb, '_ensure_milvus_collection', lambda: None)
    assert not rb.replace_chunks_milvus('p', [{'chunk_id': 'p_0', 'content': 'new'}])


@pytest.mark.parametrize('response', [None, {}, 'ok', {'upsert_count': None}, {'upsert_count': True}, {'upsert_count': 2}])
def test_milvus_replace_rejects_unconfirmed_upsert_without_deleting(monkeypatch, response):
    from knowledge_base_runtime.backend.service import retrieval_backends as rb

    class Client:
        def upsert(self, **_kwargs):
            return response

        def delete(self, **_kwargs):
            pytest.fail('unconfirmed writes must not delete old rows')

    monkeypatch.setattr(rb, 'KB_MILVUS_ENABLED', True)
    monkeypatch.setattr(rb, 'embed_texts', lambda _: [[1.0, 0.0]])
    monkeypatch.setattr(rb, '_milvus_client', lambda: Client())
    monkeypatch.setattr(rb, '_ensure_milvus_collection', lambda: None)
    assert not rb.replace_chunks_milvus('p', [{'chunk_id': 'p_0', 'content': 'new'}])


def test_milvus_replace_writes_then_deletes_only_obsolete_rows(monkeypatch):
    from knowledge_base_runtime.backend.service import retrieval_backends as rb
    calls = []

    class Client:
        def upsert(self, **kwargs):
            calls.append(('upsert', kwargs))
            return {'upsert_count': len(kwargs['data'])}

        def delete(self, **kwargs):
            calls.append(('delete', kwargs))

    monkeypatch.setattr(rb, 'KB_MILVUS_ENABLED', True)
    monkeypatch.setattr(rb, 'embed_texts', lambda values: [[1.0, 0.0] for _ in values])
    monkeypatch.setattr(rb, '_milvus_client', lambda: Client())
    monkeypatch.setattr(rb, '_ensure_milvus_collection', lambda: None)
    assert rb.replace_chunks_milvus('p', [{'chunk_id': 'p_0', 'content': 'new'}])
    assert [call[0] for call in calls] == ['upsert', 'delete']
    row_id = calls[0][1]['data'][0]['id']
    assert row_id >= 1 << 62  # cannot collide with legacy 31-bit IDs
    assert calls[1][1]['filter'] == f'paper_id == "p" and id not in [{row_id}]'


@pytest.mark.parametrize(('target', 'confirmed', 'message'), [
    ('elasticsearch', False, '维护锁'),
    ('all', False, '可能产生费用'),
    ('all', True, '维护锁'),
])
def test_recreate_requires_maintenance_and_model_cost_consent(monkeypatch, target, confirmed, message):
    from knowledge_base_runtime.backend.service import retrieval_index
    monkeypatch.setattr(retrieval_index, '_fetch_papers', lambda: pytest.fail('must not access or mutate data'))
    with pytest.raises(ValueError, match=message):
        retrieval_index.rebuild_retrieval_indexes({'target': target, 'mode': 'recreate', 'confirm_embedding_calls': confirmed})


def test_milvus_recreate_requires_cost_consent_and_maintenance(monkeypatch):
    from knowledge_base_runtime.backend.service import retrieval_index
    monkeypatch.setattr(retrieval_index, '_fetch_papers', lambda: pytest.fail('must not access or mutate data'))
    with pytest.raises(ValueError, match='可能产生费用'):
        retrieval_index.rebuild_retrieval_indexes({'target': 'milvus', 'mode': 'recreate'})
    with pytest.raises(ValueError, match='维护锁'):
        retrieval_index.rebuild_retrieval_indexes({'target': 'milvus', 'mode': 'recreate', 'confirm_embedding_calls': True})


def test_maintenance_guard_rejects_conflicting_writes_but_allows_search(monkeypatch):
    import tempfile
    import threading
    from pathlib import Path
    from knowledge_base_runtime.backend.service import maintenance

    started = threading.Event()
    finish = threading.Event()

    class Request:
        def __init__(self, path):
            self.command, self.path, self.response = 'POST', path, None
        def _path_query(self):
            return self.path, {}
        def _send_json(self, body, status):
            self.response = (body, status)

    @maintenance.guard_knowledge_write
    def run(req):
        if req.path.endswith('/reindex'):
            assert maintenance.maintenance_held()
            started.set()
            assert finish.wait(5)
        else:
            req.response = ('handled', 200)

    with tempfile.TemporaryDirectory(prefix='zhiyan-lock-test-') as directory:
        monkeypatch.setattr(maintenance, 'DATA_DIR', Path(directory))
        rebuilder = threading.Thread(target=run, args=(Request('/api/v1/admin/retrieval/reindex'),))
        rebuilder.start()
        assert started.wait(5)
        write = Request('/api/v1/admin/knowledge/slice')
        run(write)
        assert write.response[1] == 503
        read = Request('/api/v1/search')
        run(read)
        assert read.response == ('handled', 200)
        finish.set()
        rebuilder.join(5)
        assert not rebuilder.is_alive()
        run(write)
        assert write.response == ('handled', 200)


def test_maintenance_guard_rejects_writers_in_other_process(monkeypatch):
    import subprocess
    import sys
    import tempfile
    from pathlib import Path
    from knowledge_base_runtime.backend.service import maintenance

    class Request:
        command = 'POST'
        response = None
        def _path_query(self):
            return '/api/v1/admin/knowledge/slice', {}
        def _send_json(self, body, status):
            self.response = (body, status)

    @maintenance.guard_knowledge_write
    def write(req):
        req.response = ('handled', 200)

    code = ('import fcntl,sys; f=open(sys.argv[1],"a+"); '
            'fcntl.flock(f.fileno(),fcntl.LOCK_EX); print("locked",flush=True); sys.stdin.read(1)')
    with tempfile.TemporaryDirectory(prefix='zhiyan-lock-process-test-') as directory:
        monkeypatch.setattr(maintenance, 'DATA_DIR', Path(directory))
        proc = subprocess.Popen([sys.executable, '-c', code, str(Path(directory) / '.knowledge-write.lock')],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        try:
            assert proc.stdout.readline().strip() == 'locked'
            request = Request()
            write(request)
            assert request.response[1] == 503
        finally:
            proc.stdin.write('x')
            proc.stdin.flush()
            proc.wait(timeout=5)
        write(request)
        assert request.response == ('handled', 200)


@pytest.mark.parametrize('failure', ['embedding', 'upsert', 'short_count', 'switch'])
def test_rebuild_failure_keeps_old_collection_and_alias(monkeypatch, failure):
    from knowledge_base_runtime.backend.service import retrieval_backends as rb
    from knowledge_base_runtime.backend.service import maintenance

    class Client:
        current = 'original'
        dropped = []
        def describe_alias(self, **_):
            return {'collection_name': self.current}
        def create_collection(self, **_):
            pass
        def upsert(self, **_):
            if failure == 'upsert':
                raise RuntimeError('write failed')
            return {'upsert_count': 0 if failure == 'short_count' else 1}
        def get_collection_stats(self, **_):
            return {'row_count': 1}
        def alter_alias(self, **_):
            if failure == 'switch':
                raise RuntimeError('switch failed')
        def drop_collection(self, **kwargs):
            self.dropped.append(kwargs)

    client = Client()
    def embed(_):
        if failure == 'embedding':
            raise RuntimeError('model failed')
        return [[1.0, 0.0]]
    monkeypatch.setattr(maintenance, 'maintenance_held', lambda: True)
    monkeypatch.setattr(rb, 'KB_MILVUS_ENABLED', True)
    monkeypatch.setattr(rb, '_milvus_client', lambda: client)
    monkeypatch.setattr(rb, '_ensure_milvus_collection', lambda: 'live')
    monkeypatch.setattr(rb, 'embed_texts', embed)
    with pytest.raises(RuntimeError):
        rb.rebuild_milvus_safely({'p': [{'chunk_id': 'p_0', 'content': 'new'}]})
    assert client.current == 'original'
    assert not client.dropped


def test_rebuild_resolves_ambiguous_switch_timeout(monkeypatch):
    from knowledge_base_runtime.backend.service import retrieval_backends as rb
    from knowledge_base_runtime.backend.service import maintenance

    class Client:
        current = 'original'
        def describe_alias(self, **_):
            return {'collection_name': self.current}
        def create_collection(self, **_):
            pass
        def upsert(self, **_):
            return {'upsert_count': 1}
        def get_collection_stats(self, **_):
            return {'row_count': 1}
        def alter_alias(self, collection_name, **_):
            self.current = collection_name
            raise TimeoutError('response lost after server committed')

    client = Client()
    monkeypatch.setattr(maintenance, 'maintenance_held', lambda: True)
    monkeypatch.setattr(rb, 'KB_MILVUS_ENABLED', True)
    monkeypatch.setattr(rb, '_milvus_client', lambda: client)
    monkeypatch.setattr(rb, '_ensure_milvus_collection', lambda: 'live')
    monkeypatch.setattr(rb, 'embed_texts', lambda _: [[1.0, 0.0]])
    result = rb.rebuild_milvus_safely({'p': [{'chunk_id': 'p_0', 'content': 'new'}]})
    assert result['previous_collection'] == 'original'
    assert client.current == result['new_collection']


def test_missing_live_alias_with_staged_generations_never_reverts_to_old(monkeypatch):
    from knowledge_base_runtime.backend.service import retrieval_backends as rb

    alias = rb._milvus_live_alias()
    class Client:
        def has_collection(self, name):
            return name != alias
        def list_collections(self):
            return ['original', f'{alias}__v_interrupted']
        def create_alias(self, **_):
            pytest.fail('missing alias must not silently point back to old data')

    monkeypatch.setattr(rb, '_milvus_client', lambda: Client())
    with pytest.raises(RuntimeError, match='活动别名缺失'):
        rb._ensure_milvus_collection()
