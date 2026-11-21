from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from academic_figure_agent.schemas import DatasetSummary
from config.constants import SUPPORTED_DATA_SUFFIXES
from config.settings import Settings, get_settings


class DataIngestionInput(BaseModel):
    files: list[str] = Field(default_factory=list)
    output_dir: str


class DataIngestionTool(BaseTool):
    name: str = "data_ingestion"
    description: str = "Validate and normalize CSV, TSV, Excel, JSON, or JSONL experiment data."
    args_schema: type[BaseModel] = DataIngestionInput

    def _run(self, files: list[str], output_dir: str) -> dict:
        return ingest_data([Path(item) for item in files], Path(output_dir)).model_dump(mode="json")


def ingest_data(
    files: list[Path],
    output_dir: Path,
    settings: Settings | None = None,
) -> DatasetSummary:
    resolved = settings or get_settings()
    output_dir.mkdir(parents=True, exist_ok=True)
    if not files:
        return DatasetSummary()

    frames: list[pd.DataFrame] = []
    sources: list[str] = []
    for raw_path in files:
        path = raw_path.expanduser().resolve()
        _validate_input_file(path, resolved.max_input_file_mb)
        if path.suffix.lower() not in SUPPORTED_DATA_SUFFIXES:
            raise ValueError(f"Unsupported data file: {path.name}")
        frame = _read_frame(path)
        if frame.empty:
            raise ValueError(f"Data file is empty: {path.name}")
        frame.columns = [str(column).strip() for column in frame.columns]
        frame["__source_file"] = path.name
        frames.append(frame)
        sources.append(str(path))

    combined = pd.concat(frames, ignore_index=True, sort=False)
    normalized_path = output_dir / "source_data.csv"
    combined.to_csv(normalized_path, index=False, encoding="utf-8-sig")
    digest = hashlib.sha256(normalized_path.read_bytes()).hexdigest()
    numeric_columns = [
        str(column)
        for column in combined.select_dtypes(include="number").columns
        if column != "__source_file"
    ]
    categorical_columns = [
        str(column)
        for column in combined.columns
        if column not in numeric_columns and column != "__source_file"
    ]
    preview = json.loads(combined.head(8).to_json(orient="records", force_ascii=False, date_format="iso"))
    return DatasetSummary(
        normalized_path=normalized_path,
        source_files=sources,
        row_count=len(combined),
        columns=[str(column) for column in combined.columns if column != "__source_file"],
        numeric_columns=numeric_columns,
        categorical_columns=categorical_columns,
        missing_values={str(key): int(value) for key, value in combined.isna().sum().items() if value},
        preview=preview,
        sha256=digest,
    )


def _read_frame(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(path)
    if suffix == ".tsv":
        return pd.read_csv(path, sep="\t")
    if suffix in {".xlsx", ".xls"}:
        return pd.read_excel(path)
    if suffix == ".jsonl":
        return pd.read_json(path, lines=True)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        for key in ("data", "records", "results"):
            if isinstance(payload.get(key), list):
                payload = payload[key]
                break
    return pd.DataFrame(payload)


def _validate_input_file(path: Path, max_mb: int) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"Input file does not exist: {path}")
    if path.stat().st_size > max_mb * 1024 * 1024:
        raise ValueError(f"Input file exceeds {max_mb} MB limit: {path.name}")
