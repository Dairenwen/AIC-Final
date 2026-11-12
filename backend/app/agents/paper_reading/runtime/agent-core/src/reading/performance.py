from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from time import perf_counter

from reading.execution import optional_analysis_enabled


@dataclass(frozen=True)
class SpeedProfile:
    depth: str
    analyze_experiments: bool
    analyze_elements: bool


SPEED_PROFILES: dict[str, SpeedProfile] = {
    "fast": SpeedProfile(
        depth="OVERVIEW",
        analyze_experiments=False,
        analyze_elements=False,
    ),
    "balanced": SpeedProfile(
        depth="STANDARD",
        analyze_experiments=False,
        analyze_elements=False,
    ),
    "quality": SpeedProfile(
        depth="DEEP",
        analyze_experiments=True,
        analyze_elements=True,
    ),
}


@dataclass(frozen=True)
class EffectivePerformanceOptions:
    profile_name: str
    depth: str
    analyze_experiments: bool
    analyze_elements: bool


def resolve_performance_options(
    speed_profile: str | None,
    *,
    depth: str | None,
    analyze_experiments: bool | None,
    analyze_elements: bool | None,
    execution_mode: str,
) -> EffectivePerformanceOptions:
    """Resolve named profile defaults while preserving explicit CLI overrides."""

    profile = SPEED_PROFILES.get(speed_profile) if speed_profile else None
    effective_depth = depth or (profile.depth if profile else "STANDARD")
    if profile is None:
        effective_experiments = optional_analysis_enabled(
            analyze_experiments,
            mode=execution_mode,
            depth=effective_depth,
        )
        effective_elements = optional_analysis_enabled(
            analyze_elements,
            mode=execution_mode,
            depth=effective_depth,
        )
        profile_name = (
            "balanced"
            if depth is None
            and analyze_experiments is None
            and analyze_elements is None
            else "custom"
        )
    else:
        effective_experiments = (
            profile.analyze_experiments
            if analyze_experiments is None
            else analyze_experiments
        )
        effective_elements = (
            profile.analyze_elements
            if analyze_elements is None
            else analyze_elements
        )
        profile_name = speed_profile

    return EffectivePerformanceOptions(
        profile_name=profile_name,
        depth=effective_depth,
        analyze_experiments=effective_experiments,
        analyze_elements=effective_elements,
    )


@dataclass
class PipelineTimer:
    clock: Callable[[], float] = field(default=perf_counter, repr=False)
    stages_seconds: dict[str, float] = field(default_factory=dict, init=False)
    _started_at: float = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._started_at = self.clock()

    @contextmanager
    def measure(self, stage: str) -> Iterator[None]:
        started_at = self.clock()
        try:
            yield
        finally:
            self.stages_seconds[stage] = max(0.0, self.clock() - started_at)

    def snapshot(self) -> dict[str, object]:
        return {
            "stages_seconds": {
                stage: round(seconds, 3)
                for stage, seconds in self.stages_seconds.items()
            },
            "total_seconds": round(max(0.0, self.clock() - self._started_at), 3),
        }
