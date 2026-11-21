from __future__ import annotations

import json
from pathlib import Path

from jsonschema import validators
from referencing import Registry, Resource


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_DIR = REPOSITORY_ROOT / "shared" / "contracts" / "schemas"


class ResultContractError(ValueError):
    pass


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_contract_payload(payload: dict, contract_name: str) -> None:
    registry = Registry()
    schemas: dict[str, dict] = {}
    for path in sorted(SCHEMA_DIR.glob("*.schema.json")):
        schema = _load(path)
        schema_id = schema.get("$id")
        if schema_id:
            registry = registry.with_resource(schema_id, Resource.from_contents(schema))
        schemas[path.name] = schema
    schema = schemas[f"{contract_name}.schema.json"]
    validator_class = validators.validator_for(schema)
    errors = sorted(validator_class(schema, registry=registry).iter_errors(payload), key=lambda error: list(error.path))
    if errors:
        rendered = "; ".join(f"{'/'.join(map(str, error.path)) or '<root>'}: {error.message}" for error in errors)
        raise ResultContractError(rendered)


def validate_reading_result_contract(payload: dict) -> None:
    validate_contract_payload(payload, "reading_result")
