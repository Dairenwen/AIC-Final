from __future__ import annotations

import importlib
import sys

_MODULES = (
    "agent",
    "audit",
    "chunking",
    "collections",
    "common",
    "knowledge",
    "local_splitters",
    "metadata",
    "pwc_crawler",
    "qa",
    "retrieval_backends",
    "retrieval_index",
    "search",
    "uploads",
    "users",
)

for _name in _MODULES:
    sys.modules[f"{__name__}.{_name}"] = importlib.import_module(f"backend.service.{_name}")
