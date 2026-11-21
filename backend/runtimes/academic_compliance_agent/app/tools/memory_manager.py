from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List


MEMORY_NAMESPACE_ROOT = "academic_compliance_agent"


def get_runtime_user_id(state: Dict[str, Any], runtime: Any = None) -> str:
    if runtime is not None:
        context = getattr(runtime, "context", None)
        if context is not None:
            user_id = getattr(context, "user_id", None)
            if user_id:
                return str(user_id)
            if isinstance(context, dict) and context.get("user_id"):
                return str(context["user_id"])
    return str(state.get("user_id") or "default_user")


def read_long_term_memory(state: Dict[str, Any], runtime: Any = None) -> Dict[str, Any]:
    store = getattr(runtime, "store", None) if runtime is not None else None
    if store is None:
        return {
            "enabled": False,
            "user_id": get_runtime_user_id(state, runtime),
            "profile": {},
            "recent_runs": [],
        }

    user_id = get_runtime_user_id(state, runtime)
    namespace = (MEMORY_NAMESPACE_ROOT, "users", user_id)
    profile = _store_get_value(store, namespace, "profile", {})
    recent_runs_data = _store_get_value(store, namespace, "recent_runs", {})
    recent_runs = recent_runs_data.get("items", []) if isinstance(recent_runs_data, dict) else []
    return {
        "enabled": True,
        "user_id": user_id,
        "profile": profile,
        "recent_runs": recent_runs,
    }


def write_long_term_memory(state: Dict[str, Any], runtime: Any = None) -> None:
    store = getattr(runtime, "store", None) if runtime is not None else None
    if store is None:
        return

    user_id = get_runtime_user_id(state, runtime)
    namespace = (MEMORY_NAMESPACE_ROOT, "users", user_id)
    previous_profile = _store_get_value(store, namespace, "profile", {})
    previous_runs_data = _store_get_value(store, namespace, "recent_runs", {})
    previous_runs = previous_runs_data.get("items", []) if isinstance(previous_runs_data, dict) else []
    compliance_summary = state.get("compliance_summary", {})
    risk_summary = state.get("structured_output", {}).get("summary", {})

    profile = {
        **previous_profile,
        "user_id": user_id,
        "last_task_id": state.get("task_id", ""),
        "last_task_type": state.get("task_type", ""),
        "last_rule_set": state.get("target_rule_set", "default"),
        "last_compliance_score": compliance_summary.get("compliance_score"),
        "last_overall_level": risk_summary.get("overall_level"),
        "last_updated_at": datetime.now().isoformat(timespec="seconds"),
        "run_count": int(previous_profile.get("run_count", 0) or 0) + 1,
    }
    recent_run = {
        "task_id": state.get("task_id", ""),
        "task_type": state.get("task_type", ""),
        "compliance_score": compliance_summary.get("compliance_score"),
        "overall_level": risk_summary.get("overall_level"),
        "risk_count": risk_summary.get("risk_count"),
        "module_counts": risk_summary.get("module_counts", {}),
        "revision_suggestions": compliance_summary.get("revision_suggestions", [])[:5],
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    recent_runs = ([recent_run] + _as_list(previous_runs))[:10]

    store.put(namespace, "profile", profile)
    store.put(namespace, "recent_runs", {"items": recent_runs})


def _store_get_value(store: Any, namespace: tuple[str, ...], key: str, default: Any) -> Any:
    try:
        item = store.get(namespace, key)
    except Exception:
        return default
    if item is None:
        return default
    if hasattr(item, "value"):
        return item.value
    if isinstance(item, dict) and "value" in item:
        return item["value"]
    return default


def _as_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []
