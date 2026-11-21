from __future__ import annotations

from hashlib import sha256

from .models import SplitterChunk, SplitterRequest, SplitterResult
from .splitter_contract import RawSplitChunk, SplitterGatewayError
from .splitter_registry import SplitterRegistry, StrategyDefinition, canonical_sha256
from .splitters import split_fixed_boundary, split_paragraph_sentence, split_section_parent_child


class LocalSplitterGateway:
    """Execute the frozen splitter core in process without HTTP, storage, or polling."""

    def __init__(self, registry: SplitterRegistry | None = None) -> None:
        self.registry = registry or SplitterRegistry()

    def list_strategies(self) -> list[str]:
        return self.registry.list_strategies()

    def split(self, request: SplitterRequest) -> SplitterResult:
        actual_hash = sha256(request.text.encode("utf-8")).hexdigest()
        if actual_hash != request.source_text_sha256:
            raise SplitterGatewayError(
                "SOURCE_TEXT_HASH_MISMATCH",
                "The supplied source text hash does not match the text body.",
            )
        definition = self.registry.resolve(request.strategy, request.profile)
        try:
            raw_chunks = self._split_text(request.text, definition)
            if not raw_chunks:
                raise ValueError("splitter produced no chunks")
            return self._materialize(request, definition, raw_chunks)
        except SplitterGatewayError:
            raise
        except (TypeError, ValueError) as exc:
            raise SplitterGatewayError(
                "SPLITTER_EXECUTION_ERROR", "The local splitter could not complete."
            ) from exc

    @staticmethod
    def _split_text(text: str, definition: StrategyDefinition) -> list[RawSplitChunk]:
        config = dict(definition.config)
        if definition.strategy == "fixed_boundary_v1":
            return split_fixed_boundary(text, **config)
        if definition.strategy == "paragraph_sentence_v1":
            return split_paragraph_sentence(text, **config)
        if definition.strategy == "section_parent_child_v1":
            return split_section_parent_child(text, **config)
        raise SplitterGatewayError(
            "UNSUPPORTED_SPLITTER_STRATEGY", "The requested splitter strategy is not supported."
        )

    @staticmethod
    def _materialize(
        request: SplitterRequest,
        definition: StrategyDefinition,
        raw_chunks: list[RawSplitChunk],
    ) -> SplitterResult:
        identity = {
            "paper_id": request.paper_id,
            "source_text_sha256": request.source_text_sha256,
            "strategy": definition.strategy,
            "strategy_version": definition.strategy_version,
            "profile": definition.profile,
            "profile_version": definition.profile_version,
            "config_hash": definition.config_hash,
        }
        execution_id = f"splitrun-{canonical_sha256(identity)[:24]}"
        chunks: list[SplitterChunk] = []
        for index, raw in enumerate(raw_chunks):
            if (
                raw.source_start < 0
                or raw.source_end <= raw.source_start
                or raw.source_end > len(request.text)
                or request.text[raw.source_start : raw.source_end] != raw.text
            ):
                raise SplitterGatewayError(
                    "SPLITTER_EXECUTION_ERROR", "The local splitter emitted invalid source lineage."
                )
            content_hash = sha256(raw.text.encode("utf-8")).hexdigest()
            chunk_identity = {
                "run_id": execution_id,
                "paper_id": request.paper_id,
                "strategy": definition.strategy,
                "chunk_index": index,
                "content_sha256": content_hash,
            }
            parent_chunk_id = None
            chunk_level = "flat"
            if definition.supports_parent_child:
                parent_source_id = raw.parent_source_id or "parent-unknown-000"
                parent_identity = {
                    "run_id": execution_id,
                    "source_parent_id": parent_source_id,
                }
                parent_chunk_id = f"parent-{canonical_sha256(parent_identity)[:24]}"
                chunk_level = "child"
            chunks.append(
                SplitterChunk(
                    chunk_id=f"chunk-{canonical_sha256(chunk_identity)[:24]}",
                    paper_id=request.paper_id,
                    chunk_index=index,
                    text=raw.text,
                    content_sha256=content_hash,
                    source_start=raw.source_start,
                    source_end=raw.source_end,
                    source_span_status="EXACT",
                    source_span_ambiguous=raw.source_span_ambiguous,
                    section_name=raw.section_name,
                    section_path=[] if raw.section_name is None else [raw.section_name],
                    parent_chunk_id=parent_chunk_id,
                    chunk_level=chunk_level,
                    strategy=definition.strategy,
                    strategy_version=definition.strategy_version,
                    profile=definition.profile,
                    profile_version=definition.profile_version,
                    config_hash=definition.config_hash,
                    source_text_sha256=request.source_text_sha256,
                )
            )
        warnings: list[dict[str, object]] = []
        if definition.strategy == "fixed_boundary_v1":
            configured_size = int(definition.config["chunk_size"])
            oversized_count = sum(len(chunk.text) > configured_size for chunk in chunks)
            if oversized_count:
                warnings.append(
                    {
                        "code": "FIXED_BOUNDARY_OVERLAP_RECOMBINATION_EXCEEDS_CHUNK_SIZE",
                        "message": (
                            "Some frozen fixed-boundary chunks exceed chunk_size after "
                            "overlap recombination."
                        ),
                        "details": {
                            "chunk_count": oversized_count,
                            "configured_chunk_size": configured_size,
                        },
                    }
                )
        if definition.strategy == "section_parent_child_v1":
            missing_path_count = sum(not chunk.section_path for chunk in chunks)
            if missing_path_count:
                warnings.append(
                    {
                        "code": "SECTION_PATH_MISSING",
                        "message": (
                            "Some child chunks use the document-level parent because no section "
                            "path was recognized."
                        ),
                        "details": {"chunk_count": missing_path_count},
                    }
                )
        return SplitterResult(
            execution_id=execution_id,
            status="COMPLETED",
            paper_id=request.paper_id,
            strategy=definition.strategy,
            strategy_version=definition.strategy_version,
            profile=definition.profile,
            profile_version=definition.profile_version,
            source_text_sha256=request.source_text_sha256,
            config_hash=definition.config_hash,
            chunks=chunks,
            warnings=warnings,
        )
