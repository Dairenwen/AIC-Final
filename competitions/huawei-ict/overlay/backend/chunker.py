"""BGE-M3 semantic splitter implementing the missing legacy call contract.

This migration implementation is not the unavailable original algorithm.
Lengths and overlap are characters, matching the existing 1024/200 caller.
Adjacent sentence embeddings choose the least similar boundary in the latter
half of each window. Hard limits split overlong sentences without losing text.
It reuses the retrieval model cache; no second model or silent fallback.
"""
from __future__ import annotations

import math
import re
from bisect import bisect_right

from knowledge_base_runtime.backend.service.retrieval_backends import embed_texts
from knowledge_base_runtime.backend.config.settings import BGE_EMBED_BATCH_SIZE


def _sentence_spans(text: str, limit: int) -> list[tuple[int, int]]:
    ends = [m.end() for m in re.finditer(r'[。！？!?；;]+[”’"\']*\s*|\.(?=\s|$)\s*|\n+', text)]
    if not ends or ends[-1] != len(text):
        ends.append(len(text))
    spans, start = [], 0
    for end in ends:
        while end - start > limit:
            spans.append((start, start + limit))
            start += limit
        if end > start:
            spans.append((start, end))
        start = end
    return spans


def _unit_vectors(values, expected: int) -> list[list[float]]:
    if len(values) != expected:
        raise ValueError('Semantic embedding count does not match sentences.')
    result, dimension = [], None
    for value in values:
        vector = [float(x) for x in value]
        dimension = dimension or len(vector)
        norm = math.sqrt(sum(x * x for x in vector))
        if not vector or len(vector) != dimension or not math.isfinite(norm) or norm == 0:
            raise ValueError('Semantic embeddings must have consistent dimensions and finite nonzero norms.')
        result.append([x / norm for x in vector])
    return result


def chunk_semantic_details(text: str, chunk_size: int = 1024, overlap: int = 200) -> list[dict]:
    if not isinstance(chunk_size, int) or not isinstance(overlap, int) or not 0 <= overlap < chunk_size:
        raise ValueError('Require integer chunk_size > overlap >= 0.')
    if not text.strip():
        return []
    spans = _sentence_spans(text, chunk_size)
    # A whole paper can contain thousands of sentences. Send bounded batches to
    # embedding providers, which commonly reject an unbounded input array.
    sentences = [text[a:b] for a, b in spans]
    batch_size = max(1, BGE_EMBED_BATCH_SIZE)
    embeddings = []
    for start in range(0, len(sentences), batch_size):
        embeddings.extend(embed_texts(sentences[start:start + batch_size]))
    vectors = _unit_vectors(embeddings, len(spans))
    boundaries = [b for _, b in spans[:-1]]
    similarity = [sum(x * y for x, y in zip(a, b)) for a, b in zip(vectors, vectors[1:])]
    result, start = [], 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        method = 'document_end' if end == len(text) else 'length_limit'
        if end < len(text):
            minimum = start + max(overlap + 1, chunk_size // 2)
            first = bisect_right(boundaries, minimum - 1)
            stop = bisect_right(boundaries, end)
            if first < stop:
                # Prefer a later boundary on equal scores to avoid tiny chunks.
                chosen = min(range(first, stop), key=lambda i: (similarity[i], -boundaries[i]))
                end, method = boundaries[chosen], 'embedding_cosine'
        result.append({'content': text[start:end], 'start': start, 'end': end,
                       'splitter': 'semantic_bge_m3_v1', 'cut_method': method})
        if end == len(text):
            break
        start = end - overlap
    return result
