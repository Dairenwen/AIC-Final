from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    model_name: str = os.getenv("MODEL_NAME", "gpt-4.1")
    data_dir: str = os.getenv("DATA_DIR", "data")
    corpus_dir: str = os.getenv("CORPUS_DIR", "data/raw")
    innovation_runs_dir: str = os.getenv("INNOVATION_RUNS_DIR", "data/innovation_runs")


settings = Settings()
