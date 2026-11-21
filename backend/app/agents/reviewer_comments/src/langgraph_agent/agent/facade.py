"""ReviewAgent SDK 门面：四条图的 start / resume / get_state。

决策 2B：可 import 的 SDK，不实现 Flask / 前端轮询 / ThreadPoolTaskExecutor。
图内部逻辑不改；本模块只做依赖注入、thread 约定与 AgentResult 信封。
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from typing import Any
from uuid import UUID, uuid4

from config.settings import Settings, get_settings
from langgraph_agent.agent.runtime import (
    GraphKind,
    build_analysis_thread_id,
    build_finalize_thread_id,
    build_task_init_thread_id,
    infer_graph_kind,
    invoke_compiled,
    read_state,
    resume_compiled,
)
from langgraph_agent.agent.analysis.graph import build_suggestion_analysis_graph
from langgraph_agent.agent.finalize.graph import build_finalize_graph
from langgraph_agent.agent.reply.graph import build_source_reply_graph
from langgraph_agent.agent.reply.thread_ids import build_reply_thread_id
from langgraph_agent.agent.workspace_task.graph import (
    WorkspaceTaskStores,
    build_workspace_task_graph,
)
from langgraph_agent.memory.checkpointer import (
    CheckpointerContextFactory,
    make_memory_checkpointer,
    make_postgres_checkpointer_cm_factory,
)
from langgraph_agent.schemas.interaction import ResumeCommand
from langgraph_agent.schemas.public_api import (
    AgentResult,
    AnalysisInput,
    FinalizeInput,
    ReplyInput,
    TaskInitInput,
)
from langgraph_agent.schemas.run import GraphRunStatus
from langgraph_agent.schemas.workspace import WorkspaceMode
from langgraph_agent.utils.exceptions import AgentError, InvalidInput


# 图工厂签名：接收 checkpointer + stores，返回已 compile 的图
GraphBuilder = Callable[..., Any]
StoresMapping = Mapping[str, Any]


class ReviewAgent:
    """审稿意见回复 Agent 的稳定公共入口。

    构造方式
    --------
    1. ``ReviewAgent.from_settings()``：Postgres stores + PostgresSaver 工厂
    2. 显式注入::

        ReviewAgent(
            stores=fake_stores,
            checkpointer=make_memory_checkpointer(),
        )

    checkpointer 二选一：
    - ``checkpointer``：进程内实例（MemorySaver），start/resume 必须复用同一对象
    - ``checkpointer_cm_factory``：每次 invoke 打开上下文（PostgresSaver）
    """

    def __init__(
        self,
        *,
        stores: StoresMapping | WorkspaceTaskStores | Any,
        checkpointer: Any | None = None,
        checkpointer_cm_factory: CheckpointerContextFactory | None = None,
        graph_builders: Mapping[GraphKind, GraphBuilder] | None = None,
        langgraph_store: Any | None = None,
    ) -> None:
        if checkpointer is None and checkpointer_cm_factory is None:
            raise InvalidInput(
                "必须提供 checkpointer（内存实例）或 checkpointer_cm_factory（Postgres）"
            )
        self._stores = stores
        self._checkpointer = checkpointer
        self._checkpointer_cm_factory = checkpointer_cm_factory
        self._graph_builders = dict(graph_builders or {})
        self._langgraph_store = langgraph_store
        # thread_id → GraphKind；resume 时优先查表，再回退到 thread_id 约定解析
        self._thread_kinds: dict[str, GraphKind] = {}
        self._thread_run_ids: dict[str, UUID] = {}

    # ------------------------------------------------------------------
    # 工厂
    # ------------------------------------------------------------------

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> ReviewAgent:
        """按运行时配置装配默认 Postgres stores + Postgres checkpointer 工厂。

        FinalizeStore 的 Postgres 实现可能尚未就绪：此时 stores 不含 ``finalize``，
        调用 ``finalize()`` 会给出明确错误；Memory/Fake 场景请直接构造注入。
        """
        cfg = settings if settings is not None else get_settings()
        cfg.require_database()

        from langgraph_agent.adapters.postgres import (
            build_postgres_stores,
            create_session_factory,
        )

        session_factory = create_session_factory(settings=cfg)
        stores: dict[str, Any] = dict(build_postgres_stores(session_factory))
        # build_postgres_stores 已含 finalize；此处兜底保证键存在
        if "finalize" not in stores or stores.get("finalize") is None:
            from langgraph_agent.adapters.postgres.stores import PostgresFinalizeStore

            stores["finalize"] = PostgresFinalizeStore(session_factory)

        return cls(
            stores=stores,
            checkpointer_cm_factory=make_postgres_checkpointer_cm_factory(
                settings=cfg
            ),
        )

    @classmethod
    def from_memory(
        cls,
        stores: StoresMapping | Any,
        *,
        graph_builders: Mapping[GraphKind, GraphBuilder] | None = None,
    ) -> ReviewAgent:
        """测试/离线：MemorySaver + 调用方 FakeStores。"""
        return cls(
            stores=stores,
            checkpointer=make_memory_checkpointer(),
            graph_builders=graph_builders,
        )

    # ------------------------------------------------------------------
    # 公共 API
    # ------------------------------------------------------------------

    def start_task_init(
        self,
        input: TaskInitInput | Mapping[str, Any],
        *,
        run_id: UUID | None = None,
        thread_id: str | None = None,
    ) -> AgentResult:
        """启动 WorkspaceTaskGraph（TASK_INIT）。"""
        payload = (
            input
            if isinstance(input, TaskInitInput)
            else TaskInitInput.model_validate(input)
        )
        resolved_run_id = run_id or uuid4()
        resolved_thread = thread_id or build_task_init_thread_id(
            payload.workspace_id, resolved_run_id
        )
        input_version = payload.input_version or f"task-init:{resolved_run_id}"
        mode = payload.mode
        phase = (
            "PARSE_MANUSCRIPT"
            if mode in (WorkspaceMode.SLOW, WorkspaceMode.SLOW.value)
            else "PENDING"
        )
        initial_state: dict[str, Any] = {
            "workspace_id": payload.workspace_id,
            "user_id": payload.user_id,
            "mode": mode,
            "manuscript_version_id": payload.manuscript_version_id,
            "thread_id": resolved_thread,
            "run_id": resolved_run_id,
            "run_scope": "TASK_INIT",
            "input_version": input_version,
            "phase": phase,
            "pending_interaction_id": None,
            "draft_refs": {},
            "result_refs": [],
            "status": GraphRunStatus.RUNNING,
            "error_code": None,
        }
        return self._start(GraphKind.TASK_INIT, resolved_thread, resolved_run_id, initial_state)

    def start_analysis(
        self,
        input: AnalysisInput | Mapping[str, Any],
        *,
        run_id: UUID | None = None,
        thread_id: str | None = None,
    ) -> AgentResult:
        """启动 SuggestionAnalysisGraph。

        ``input_version`` 必须与 Suggestion 行上的版本一致（落库校验）。
        未显式传入时，从 stores 读取 suggestion.input_version（对齐 backend）。
        """
        payload = (
            input
            if isinstance(input, AnalysisInput)
            else AnalysisInput.model_validate(input)
        )
        resolved_run_id = run_id or uuid4()
        resolved_thread = thread_id or build_analysis_thread_id(
            payload.workspace_id, payload.suggestion_id, resolved_run_id
        )
        input_version = payload.input_version or self._resolve_suggestion_input_version(
            payload.suggestion_id
        )
        if not input_version:
            raise InvalidInput(
                "无法解析 analysis input_version：请传入 AnalysisInput.input_version，"
                "或确保 stores 可读取 suggestion.input_version"
            )
        initial_state: dict[str, Any] = {
            "workspace_id": payload.workspace_id,
            "suggestion_id": payload.suggestion_id,
            "user_id": payload.user_id,
            "mode": payload.mode,
            "manuscript_version_id": payload.manuscript_version_id,
            "thread_id": resolved_thread,
            "run_id": resolved_run_id,
            "input_version": input_version,
            "phase": "PENDING",
            "pending_interaction_id": None,
            "draft_refs": {},
            "result_refs": [],
            "status": GraphRunStatus.RUNNING,
            "error_code": None,
        }
        return self._start(GraphKind.ANALYSIS, resolved_thread, resolved_run_id, initial_state)

    def start_reply(
        self,
        input: ReplyInput | Mapping[str, Any],
        *,
        run_id: UUID | None = None,
        thread_id: str | None = None,
    ) -> AgentResult:
        """启动 SourceReplyGraph。

        thread_id 默认按 ``build_reply_thread_id(workspace, source, input_version)``。
        注意：strategy / facts 在图内为自动确认路径；真正需要 resume 的通常是
        ``REVIEW_REPLY_DRAFT``。
        """
        payload = (
            input
            if isinstance(input, ReplyInput)
            else ReplyInput.model_validate(input)
        )
        resolved_run_id = run_id or uuid4()
        # 与分析一致：默认沿用 suggestion.input_version，避免落库版本校验失败
        input_version = payload.input_version or self._resolve_suggestion_input_version(
            payload.suggestion_id
        )
        if not input_version:
            input_version = f"reply:{resolved_run_id}"
        # 每次 start 使用独立 thread，避免 FAILED checkpoint（脏 draft_refs）被误续跑
        base_thread = build_reply_thread_id(
            payload.workspace_id, payload.source_id, input_version
        )
        resolved_thread = thread_id or f"{base_thread}:run:{resolved_run_id}"
        initial_state: dict[str, Any] = {
            "workspace_id": payload.workspace_id,
            "suggestion_id": payload.suggestion_id,
            "source_id": payload.source_id,
            "thread_id": resolved_thread,
            "user_id": payload.user_id,
            "run_id": resolved_run_id,
            "input_version": input_version,
            "phase": "PENDING",
            "pending_interaction_id": None,
            "draft_refs": {},
            "result_refs": [],
            "status": GraphRunStatus.RUNNING,
            "error_code": None,
        }
        return self._start(GraphKind.REPLY, resolved_thread, resolved_run_id, initial_state)

    def finalize(
        self,
        input: FinalizeInput | Mapping[str, Any],
        *,
        run_id: UUID | None = None,
        thread_id: str | None = None,
    ) -> AgentResult:
        """启动 FinalizeGraph（无 interrupt；一次 invoke 到结束或失败）。"""
        payload = (
            input
            if isinstance(input, FinalizeInput)
            else FinalizeInput.model_validate(input)
        )
        resolved_run_id = run_id or uuid4()
        resolved_thread = thread_id or build_finalize_thread_id(payload.workspace_id)
        input_version = payload.input_version or f"finalize:{resolved_run_id}"
        initial_state: dict[str, Any] = {
            "workspace_id": payload.workspace_id,
            "user_id": payload.user_id,
            "run_id": resolved_run_id,
            "run_scope": "FINALIZE",
            "input_version": input_version,
            "phase": "PENDING",
            "draft_refs": {},
            "result_refs": [],
            "status": GraphRunStatus.RUNNING,
            "error_code": None,
        }
        return self._start(GraphKind.FINALIZE, resolved_thread, resolved_run_id, initial_state)

    def resume(
        self,
        thread_id: str,
        resume_command: ResumeCommand | Mapping[str, Any],
        *,
        graph_kind: GraphKind | None = None,
    ) -> AgentResult:
        """恢复挂起的人工确认点。

        Parameters
        ----------
        thread_id:
            与 start 时一致的 checkpoint 线程 ID。
        resume_command:
            完整 ``ResumeCommand``，或与图 ``_resume_payload`` 兼容的 payload dict。
        graph_kind:
            可选显式指定图类型；默认从内部登记或 thread_id 约定推断。
        """
        if not thread_id or not str(thread_id).strip():
            raise InvalidInput("thread_id 不能为空")
        kind = graph_kind or self._thread_kinds.get(thread_id) or infer_graph_kind(thread_id)
        run_id = self._thread_run_ids.get(thread_id)

        # 若 resume_command 是完整 ResumeCommand，校验 thread_id 一致
        if isinstance(resume_command, ResumeCommand):
            if resume_command.thread_id != thread_id:
                raise InvalidInput("resume_command.thread_id 与参数 thread_id 不一致")
        elif isinstance(resume_command, Mapping) and "thread_id" in resume_command:
            if str(resume_command["thread_id"]) != thread_id:
                raise InvalidInput("resume_command.thread_id 与参数 thread_id 不一致")

        with self._open_checkpointer() as checkpointer:
            graph = self._build_graph(kind, checkpointer)
            # 尝试从 checkpoint 补 run_id
            if run_id is None:
                try:
                    snap = graph.get_state(
                        {"configurable": {"thread_id": thread_id}}
                    )
                    if isinstance(snap.values, dict) and snap.values.get("run_id"):
                        run_id = UUID(str(snap.values["run_id"]))
                        self._thread_run_ids[thread_id] = run_id
                except Exception:  # noqa: BLE001
                    pass
            result = resume_compiled(
                graph,
                thread_id=thread_id,
                resume_command=resume_command,
                run_id=run_id,
            )
        self._remember_thread(thread_id, kind, result.run_id)
        return result

    def get_state(
        self,
        thread_id: str,
        *,
        graph_kind: GraphKind | None = None,
    ) -> dict[str, Any]:
        """读取 checkpoint 当前 values 与 pending 交互（可选 API）。"""
        if not thread_id or not str(thread_id).strip():
            raise InvalidInput("thread_id 不能为空")
        kind = graph_kind or self._thread_kinds.get(thread_id) or infer_graph_kind(thread_id)
        with self._open_checkpointer() as checkpointer:
            graph = self._build_graph(kind, checkpointer)
            return read_state(graph, thread_id=thread_id)

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _start(
        self,
        kind: GraphKind,
        thread_id: str,
        run_id: UUID,
        initial_state: dict[str, Any],
    ) -> AgentResult:
        with self._open_checkpointer() as checkpointer:
            graph = self._build_graph(kind, checkpointer)
            # 仅在「进行中/等待人工」时从 checkpoint 续跑；
            # FAILED/SUCCEEDED 或脏状态一律用新 initial_state，避免复用坏草稿（如 expression_settings={}）。
            graph_input: Any = initial_state
            try:
                existing = graph.get_state(
                    {"configurable": {"thread_id": thread_id}}
                )
                values = getattr(existing, "values", None)
                if isinstance(values, dict) and values:
                    raw_status = values.get("status")
                    status_text = str(
                        getattr(raw_status, "value", raw_status) or ""
                    )
                    if status_text in {
                        GraphRunStatus.RUNNING.value,
                        GraphRunStatus.WAITING_USER.value,
                        "WAITING_HUMAN",
                    }:
                        graph_input = None
            except Exception:  # noqa: BLE001
                graph_input = initial_state
            result = invoke_compiled(
                graph,
                thread_id=thread_id,
                graph_input=graph_input,
                run_id=run_id,
            )
        self._remember_thread(thread_id, kind, result.run_id)
        return result

    def _remember_thread(
        self, thread_id: str, kind: GraphKind, run_id: UUID
    ) -> None:
        self._thread_kinds[thread_id] = kind
        self._thread_run_ids[thread_id] = run_id

    def _resolve_suggestion_input_version(self, suggestion_id: UUID) -> str | None:
        """从 suggestion store 读取 suggestion.input_version。"""
        mapping = self._as_mapping(self._stores)
        suggestion_store = (
            mapping.get("suggestion_store")
            if mapping.get("suggestion_store") is not None
            else mapping.get("suggestion")
        )
        if suggestion_store is None or not hasattr(
            suggestion_store, "load_suggestion_bundle"
        ):
            return None
        try:
            bundle = suggestion_store.load_suggestion_bundle(suggestion_id)
        except Exception:  # noqa: BLE001
            return None
        return self._input_version_from_bundle(bundle)

    @staticmethod
    def _input_version_from_bundle(bundle: Any) -> str | None:
        if bundle is None:
            return None
        if isinstance(bundle, Mapping):
            direct = bundle.get("input_version")
            if direct:
                return str(direct)
            suggestion = bundle.get("suggestion")
            if isinstance(suggestion, Mapping) and suggestion.get("input_version"):
                return str(suggestion["input_version"])
            if suggestion is not None and hasattr(suggestion, "input_version"):
                value = getattr(suggestion, "input_version")
                if value:
                    return str(value)
            return None
        if hasattr(bundle, "input_version"):
            value = getattr(bundle, "input_version")
            if value:
                return str(value)
        suggestion = getattr(bundle, "suggestion", None)
        if suggestion is not None and hasattr(suggestion, "input_version"):
            value = getattr(suggestion, "input_version")
            if value:
                return str(value)
        return None

    @contextmanager
    def _open_checkpointer(self) -> Iterator[Any]:
        if self._checkpointer is not None:
            yield self._checkpointer
            return
        if self._checkpointer_cm_factory is None:
            raise AgentError("未配置 checkpointer")
        with self._checkpointer_cm_factory() as checkpointer:
            yield checkpointer

    def _build_graph(self, kind: GraphKind, checkpointer: Any) -> Any:
        custom = self._graph_builders.get(kind)
        if custom is not None:
            return custom(checkpointer=checkpointer, stores=self._stores)

        stores = self._stores
        if kind is GraphKind.TASK_INIT:
            task_stores = self._as_workspace_task_stores(stores)
            return build_workspace_task_graph(
                stores=task_stores,
                checkpointer=checkpointer,
                store=self._langgraph_store,
            )
        if kind is GraphKind.ANALYSIS:
            return build_suggestion_analysis_graph(
                stores=self._as_analysis_stores(stores),
                checkpointer=checkpointer,
                store=self._langgraph_store,
            )
        if kind is GraphKind.REPLY:
            reply_store = self._as_reply_store(stores)
            return build_source_reply_graph(
                stores=reply_store,
                checkpointer=checkpointer,
                store=self._langgraph_store,
            )
        if kind is GraphKind.FINALIZE:
            mapping = self._as_mapping(stores)
            if "finalize" not in mapping or mapping.get("finalize") is None:
                raise AgentError(
                    "finalize 需要 stores['finalize']（FinalizeStore）。"
                    "请注入 FinalizeStore 实现，或使用 from_settings()（已挂 PostgresFinalizeStore）。"
                )
            # finalize 图当前不接收 checkpointer（无 interrupt）
            return build_finalize_graph(stores=mapping)
        raise AgentError(f"未知图类型：{kind}")

    @staticmethod
    def _as_mapping(stores: Any) -> dict[str, Any]:
        if isinstance(stores, WorkspaceTaskStores):
            result: dict[str, Any] = {"workspace": stores.workspace}
            if stores.manuscript is not None:
                result["manuscript"] = stores.manuscript
            return result
        if isinstance(stores, Mapping):
            return dict(stores)
        # 对象属性风格（SimpleNamespace / 自定义聚合）
        result = {}
        for key in (
            "workspace",
            "suggestion",
            "analysis",
            "reply",
            "manuscript",
            "run",
            "finalize",
            "analysis_store",
            "suggestion_store",
            "manuscript_store",
        ):
            if hasattr(stores, key):
                result[key] = getattr(stores, key)
        if result:
            return result
        raise InvalidInput("stores 必须是 Mapping、WorkspaceTaskStores 或带端口属性的对象")

    @classmethod
    def _as_workspace_task_stores(cls, stores: Any) -> WorkspaceTaskStores:
        if isinstance(stores, WorkspaceTaskStores):
            return stores
        mapping = cls._as_mapping(stores)
        workspace = mapping.get("workspace")
        if workspace is None:
            raise InvalidInput("TASK_INIT 需要 stores['workspace']")
        return WorkspaceTaskStores(
            workspace=workspace,
            manuscript=mapping.get("manuscript"),
        )

    @classmethod
    def _as_analysis_stores(cls, stores: Any) -> dict[str, Any]:
        """analysis 图要求 analysis_store / suggestion_store / manuscript_store 键名。"""
        mapping = cls._as_mapping(stores)
        analysis = (
            mapping.get("analysis_store")
            if mapping.get("analysis_store") is not None
            else mapping.get("analysis")
        )
        if analysis is None:
            raise InvalidInput("ANALYSIS 需要 stores['analysis'] 或 stores['analysis_store']")
        result: dict[str, Any] = {"analysis_store": analysis}
        suggestion = (
            mapping.get("suggestion_store")
            if mapping.get("suggestion_store") is not None
            else mapping.get("suggestion")
        )
        if suggestion is not None:
            result["suggestion_store"] = suggestion
        manuscript = (
            mapping.get("manuscript_store")
            if mapping.get("manuscript_store") is not None
            else mapping.get("manuscript")
        )
        if manuscript is not None:
            result["manuscript_store"] = manuscript
        return result

    @classmethod
    def _as_reply_store(cls, stores: Any) -> Any:
        """reply 图接受 ReplyStore 本体，或带 .reply 属性的聚合对象。"""
        if hasattr(stores, "load_reply_context"):
            return stores
        mapping = cls._as_mapping(stores)
        reply = mapping.get("reply")
        if reply is not None:
            return reply
        raise InvalidInput("REPLY 需要 stores['reply'] 或实现 ReplyStore 的对象")


__all__ = [
    "GraphBuilder",
    "ReviewAgent",
]
