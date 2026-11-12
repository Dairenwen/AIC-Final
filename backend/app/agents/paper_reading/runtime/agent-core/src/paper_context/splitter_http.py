from __future__ import annotations

from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .models import SplitterChunk, SplitterRequest, SplitterResult
from .splitter_contract import SplitterGatewayError


class TransportModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class CreateRunResponse(TransportModel):
    run_id: str
    status: str
    paper_id: str
    strategy: str
    strategy_version: str
    profile: str
    profile_version: str
    source_text_sha256: str
    config_hash: str
    chunk_count: int = Field(ge=1)


class ChunkPageResponse(TransportModel):
    items: list[dict[str, Any]]
    total: int = Field(ge=1)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)


class RunDetailResponse(CreateRunResponse):
    warnings: list[dict[str, Any]]


class HttpSplitterGateway:
    """Compatibility adapter for the former unified Splitter HTTP service."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 30.0,
        client: httpx.Client | None = None,
        page_size: int = 200,
        trust_env: bool = True,
    ) -> None:
        if not 1 <= page_size <= 200:
            raise ValueError("page_size must be between 1 and 200")
        parsed_base_url = httpx.URL(base_url)
        if parsed_base_url.scheme not in {"http", "https"} or not parsed_base_url.host:
            raise ValueError("base_url must be an absolute HTTP(S) URL")
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.client = client
        self.page_size = page_size
        self.trust_env = trust_env

    def list_strategies(self) -> list[str]:
        return ["fixed_boundary_v1", "paragraph_sentence_v1", "section_parent_child_v1"]

    def split(self, request: SplitterRequest) -> SplitterResult:
        if self.client is not None:
            return self._split_with_client(self.client, request)
        with httpx.Client(
            base_url=self.base_url,
            timeout=self.timeout_seconds,
            trust_env=self.trust_env,
        ) as client:
            return self._split_with_client(client, request)

    def _split_with_client(self, client: httpx.Client, request: SplitterRequest) -> SplitterResult:
        try:
            response = client.post(
                f"{self.base_url}/api/v1/chunking/runs",
                json=request.model_dump(mode="json", exclude_none=True),
            )
            response.raise_for_status()
            created = CreateRunResponse.model_validate(response.json())
            detail_response = client.get(f"{self.base_url}/api/v1/chunking/runs/{created.run_id}")
            detail_response.raise_for_status()
            detail = RunDetailResponse.model_validate(detail_response.json())
            if any(
                getattr(detail, field) != getattr(created, field)
                for field in (
                    "run_id",
                    "status",
                    "paper_id",
                    "strategy",
                    "strategy_version",
                    "profile",
                    "profile_version",
                    "source_text_sha256",
                    "config_hash",
                    "chunk_count",
                )
            ):
                raise SplitterGatewayError(
                    "SPLITTER_RUN_DETAIL_INVALID", "Splitter Run detail does not match creation lineage."
                )
            items: list[dict[str, Any]] = []
            offset = 0
            while len(items) < created.chunk_count:
                page_response = client.get(
                    f"{self.base_url}/api/v1/chunking/runs/{created.run_id}/chunks",
                    params={"limit": self.page_size, "offset": offset},
                )
                page_response.raise_for_status()
                page = ChunkPageResponse.model_validate(page_response.json())
                if page.offset != offset or page.total != created.chunk_count or not page.items:
                    raise SplitterGatewayError(
                        "SPLITTER_PAGINATION_INVALID", "Splitter pagination was incomplete or inconsistent."
                    )
                items.extend(page.items)
                offset += len(page.items)
            if len(items) != created.chunk_count:
                raise SplitterGatewayError(
                    "SPLITTER_PAGINATION_INVALID", "Splitter pagination returned an invalid chunk count."
                )
            return SplitterResult(
                execution_id=created.run_id,
                status=created.status,
                paper_id=created.paper_id,
                strategy=created.strategy,
                strategy_version=created.strategy_version,
                profile=created.profile,
                profile_version=created.profile_version,
                source_text_sha256=created.source_text_sha256,
                config_hash=created.config_hash,
                chunks=[SplitterChunk.model_validate(item) for item in items],
                warnings=detail.warnings,
            )
        except SplitterGatewayError:
            raise
        except (httpx.HTTPError, ValueError, ValidationError) as exc:
            raise SplitterGatewayError(
                "SPLITTER_GATEWAY_FAILED", "The reused splitter request failed contract validation."
            ) from exc
