from __future__ import annotations

import os
from contextlib import ExitStack, contextmanager
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from typing import Any, Dict, Iterator

try:
    from langgraph.graph import END, StateGraph
except ImportError:  # pragma: no cover - exercised only when LangGraph is absent.
    END = None
    StateGraph = None

from academic_compliance_agent.app.graph.nodes import (
    citation_check_node,
    figure_table_check_node,
    format_submission_check_node,
    intake_node,
    load_rules_node,
    paper_norm_check_node,
    parse_document_node,
    report_node,
    risk_aggregate_node,
    suggestion_node,
)
from academic_compliance_agent.app.graph.state import ComplianceContext, ComplianceState


MEMORY_ENABLED_VALUES = {"1", "true", "yes", "on"}


def _patch_langchain_globals() -> None:
    """Work around mixed LangChain/LangGraph installations missing legacy globals."""
    try:
        import langchain  # type: ignore
    except Exception:
        return
    defaults = {
        "debug": False,
        "verbose": False,
        "llm_cache": None,
    }
    for name, value in defaults.items():
        if not hasattr(langchain, name):
            setattr(langchain, name, value)


def build_workflow(checkpointer: Any = None, store: Any = None):
    _patch_langchain_globals()
    if StateGraph is None or END is None:
        raise RuntimeError("LangGraph is not installed. Install requirements.txt or use run_compliance_workflow fallback.")
    try:
        graph = StateGraph(ComplianceState, context_schema=ComplianceContext)
    except TypeError:
        graph = StateGraph(ComplianceState)
    graph.add_node("intake", intake_node)
    graph.add_node("parse_document", parse_document_node)
    graph.add_node("load_rules", load_rules_node)
    graph.add_node("paper_norm_check", paper_norm_check_node)
    graph.add_node("citation_check", citation_check_node)
    graph.add_node("figure_table_check", figure_table_check_node)
    graph.add_node("format_submission_check", format_submission_check_node)
    graph.add_node("risk_aggregate", risk_aggregate_node)
    graph.add_node("suggestion", suggestion_node)
    graph.add_node("report", report_node)

    graph.set_entry_point("intake")
    graph.add_edge("intake", "parse_document")
    graph.add_edge("parse_document", "load_rules")
    graph.add_edge("load_rules", "paper_norm_check")
    graph.add_edge("load_rules", "citation_check")
    graph.add_edge("load_rules", "figure_table_check")
    graph.add_edge("load_rules", "format_submission_check")
    graph.add_edge(
        [
            "paper_norm_check",
            "citation_check",
            "figure_table_check",
            "format_submission_check",
        ],
        "risk_aggregate",
    )
    graph.add_edge("risk_aggregate", "suggestion")
    graph.add_edge("suggestion", "report")
    graph.add_edge("report", END)
    return graph.compile(checkpointer=checkpointer, store=store)


def run_sequential_workflow(initial_state: Dict[str, Any]) -> Dict[str, Any]:
    """Fallback runner with the same node order, used when LangGraph is unavailable."""
    state: Dict[str, Any] = dict(initial_state)
    for node in [
        intake_node,
        parse_document_node,
        load_rules_node,
        paper_norm_check_node,
        citation_check_node,
        figure_table_check_node,
        format_submission_check_node,
        risk_aggregate_node,
        suggestion_node,
        report_node,
    ]:
        updates = node(state)  # type: ignore[arg-type]
        state.update(updates)
    return state


def run_compliance_workflow(initial_state: Dict[str, Any], config: Dict[str, Any] | None = None) -> Dict[str, Any]:
    _patch_langchain_globals()
    if StateGraph is None:
        return run_sequential_workflow(initial_state)
    with _memory_resources() as resources:
        workflow = build_workflow(
            checkpointer=resources.get("checkpointer"),
            store=resources.get("store"),
        )
        invoke_config = _build_invoke_config(initial_state, config)
        context = ComplianceContext(user_id=str(initial_state.get("user_id") or os.getenv("COMPLIANCE_AGENT_USER_ID", "default_user")))
        try:
            return workflow.invoke(initial_state, config=invoke_config, context=context)
        except TypeError:
            return workflow.invoke(initial_state, config=invoke_config)


@contextmanager
def _memory_resources() -> Iterator[Dict[str, Any]]:
    if os.getenv("COMPLIANCE_AGENT_MEMORY_ENABLED", "false").lower() not in MEMORY_ENABLED_VALUES:
        yield {}
        return
    postgres_uri = _normalize_postgres_uri(_postgres_uri())
    if not postgres_uri:
        raise RuntimeError(
            "COMPLIANCE_AGENT_MEMORY_ENABLED=true but no PostgreSQL URI is configured. "
            "Set COMPLIANCE_AGENT_POSTGRES_URI or POSTGRES_URI in .env."
        )
    try:
        from langgraph.checkpoint.postgres import PostgresSaver  # type: ignore
        from langgraph.store.postgres import PostgresStore  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "PostgreSQL memory requires LangGraph PostgreSQL packages. "
            "Run `pip install -r requirements.txt` after updating requirements.txt."
        ) from exc

    try:
        with ExitStack() as stack:
            checkpointer = stack.enter_context(PostgresSaver.from_conn_string(postgres_uri))
            store = stack.enter_context(PostgresStore.from_conn_string(postgres_uri))
            if os.getenv("COMPLIANCE_AGENT_MEMORY_SETUP", "true").lower() in MEMORY_ENABLED_VALUES:
                _setup_memory_backend(checkpointer)
                _setup_memory_backend(store)
            yield {"checkpointer": checkpointer, "store": store}
    except Exception as exc:
        raise RuntimeError(
            "LangGraph PostgreSQL memory failed to connect. Please check COMPLIANCE_AGENT_POSTGRES_URI in .env. "
            "For local Docker PostgreSQL, prefer a URI like "
            "`postgresql://postgres:<your_password>@127.0.0.1:5432/postgres?sslmode=disable&gssencmode=disable`. "
            "If you only want to run the Agent without memory, set COMPLIANCE_AGENT_MEMORY_ENABLED=false."
        ) from exc


def _postgres_uri() -> str:
    return (
        os.getenv("COMPLIANCE_AGENT_POSTGRES_URI")
        or os.getenv("POSTGRES_URI")
        or os.getenv("DATABASE_URL")
        or ""
    ).strip()


def _normalize_postgres_uri(uri: str) -> str:
    if not uri:
        return uri
    parts = urlsplit(uri)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.setdefault("sslmode", "disable")
    query.setdefault("gssencmode", "disable")
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _build_invoke_config(initial_state: Dict[str, Any], config: Dict[str, Any] | None = None) -> Dict[str, Any]:
    merged: Dict[str, Any] = dict(config or {})
    configurable = dict(merged.get("configurable", {}))
    configurable.setdefault(
        "thread_id",
        str(initial_state.get("thread_id") or os.getenv("COMPLIANCE_AGENT_THREAD_ID", initial_state.get("task_id") or "default_thread")),
    )
    merged["configurable"] = configurable
    return merged


def _setup_memory_backend(resource: Any) -> None:
    setup = getattr(resource, "setup", None)
    if callable(setup):
        setup()
