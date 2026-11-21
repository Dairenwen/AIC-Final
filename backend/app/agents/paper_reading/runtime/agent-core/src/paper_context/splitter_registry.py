from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from types import MappingProxyType
from typing import Mapping

from .splitter_contract import SplitterGatewayError


STRATEGY_ORDER = (
    "fixed_boundary_v1",
    "paragraph_sentence_v1",
    "section_parent_child_v1",
)
PROFILE_NAME = "splitter-api-v1"


def canonical_sha256(value: object) -> str:
    content = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return sha256(content).hexdigest()


def frozen_config_sha256(value: object) -> str:
    content = (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    return sha256(content).hexdigest()


@dataclass(frozen=True)
class StrategyDefinition:
    strategy: str
    config: Mapping[str, int]
    config_hash: str
    supports_parent_child: bool
    strategy_version: str = "v1"
    profile: str = PROFILE_NAME
    profile_version: str = "v1"


DEFINITIONS = (
    StrategyDefinition(
        strategy="fixed_boundary_v1",
        config=MappingProxyType({"chunk_size": 1024, "overlap": 200}),
        config_hash="75ea91c2830e087a14198fb581d120cf0bece252a3219656c4c5ef5297cf031b",
        supports_parent_child=False,
    ),
    StrategyDefinition(
        strategy="paragraph_sentence_v1",
        config=MappingProxyType(
            {"target_chars": 1024, "max_chars": 1280, "overlap_target_chars": 200}
        ),
        config_hash="ef0c89877d5e4ce7bd98170891df981f10dff6010472a266dcf3bdd60f6edc9e",
        supports_parent_child=False,
    ),
    StrategyDefinition(
        strategy="section_parent_child_v1",
        config=MappingProxyType(
            {"target_chars": 1024, "max_chars": 1280, "overlap_target_chars": 200}
        ),
        config_hash="ef0c89877d5e4ce7bd98170891df981f10dff6010472a266dcf3bdd60f6edc9e",
        supports_parent_child=True,
    ),
)


class SplitterRegistry:
    """Frozen local registry for the three V0.4 in-process strategies."""

    def __init__(self) -> None:
        if tuple(item.strategy for item in DEFINITIONS) != STRATEGY_ORDER:
            raise SplitterGatewayError(
                "SPLITTER_CONFIG_DRIFT_DETECTED", "The local strategy order has drifted."
            )
        for item in DEFINITIONS:
            if frozen_config_sha256(dict(item.config)) != item.config_hash:
                raise SplitterGatewayError(
                    "SPLITTER_CONFIG_DRIFT_DETECTED",
                    "A local splitter configuration hash does not match its parameters.",
                )

    def list_strategies(self) -> list[str]:
        return list(STRATEGY_ORDER)

    def resolve(self, strategy: str, profile: str) -> StrategyDefinition:
        if strategy not in STRATEGY_ORDER:
            raise SplitterGatewayError(
                "UNSUPPORTED_SPLITTER_STRATEGY", "The requested splitter strategy is not supported."
            )
        if profile != PROFILE_NAME:
            raise SplitterGatewayError(
                "UNKNOWN_SPLITTER_PROFILE", "The requested splitter profile is not registered."
            )
        return next(item for item in DEFINITIONS if item.strategy == strategy)
