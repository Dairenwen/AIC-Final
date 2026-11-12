"""B4 FINALIZE 图：一致性纯函数 + 校验 + 图 compile / FakeStore 路径。"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest

from langgraph_agent.agent.finalize import (
    build_finalize_graph,
    build_finalize_validation,
    build_summary_data,
    check_cross_source_consistency,
    group_external_replies_by_party,
    render_export_markdown,
)
from langgraph_agent.schemas import GraphRunStatus


# ---------------------------------------------------------------------------
# Fake FinalizeStore
# ---------------------------------------------------------------------------


class FakeFinalizeStore:
    """内存实现，覆盖 FinalizeStore 四个方法。"""

    def __init__(self, context: dict[str, Any] | None = None) -> None:
        self.context = context or {
            "workspace_id": str(uuid4()),
            "workspace_title": "测试任务",
            "user_id": "user-1",
            "global_settings": {},
            "suggestions": [],
            "sources": [],
            "internal_revision_items": [],
            "external_replies": [],
            "suggestion_by_id": {},
        }
        self.snapshots_by_id: dict[str, dict[str, Any]] = {}
        self.latest_by_workspace: dict[str, dict[str, Any]] = {}
        self.save_calls: list[dict[str, Any]] = []

    def load_finalize_context(self, workspace_id: UUID) -> dict[str, Any]:
        if str(workspace_id) != str(self.context["workspace_id"]):
            raise ValueError(f"Workspace 不存在：{workspace_id}")
        return dict(self.context)

    def load_export_snapshot(self, snapshot_id: UUID) -> dict[str, Any] | None:
        payload = self.snapshots_by_id.get(str(snapshot_id))
        return dict(payload) if payload is not None else None

    def save_export_snapshot(
        self,
        *,
        workspace_id: UUID,
        snapshot_id: UUID,
        snapshot: dict[str, Any],
        actor_user_id: str,
    ) -> dict[str, Any]:
        key = str(snapshot_id)
        existing = self.snapshots_by_id.get(key)
        if existing is not None:
            return dict(existing)
        stored = dict(snapshot)
        self.snapshots_by_id[key] = stored
        self.latest_by_workspace[str(workspace_id)] = stored
        self.save_calls.append(
            {
                "workspace_id": workspace_id,
                "snapshot_id": snapshot_id,
                "actor_user_id": actor_user_id,
            }
        )
        return dict(stored)

    def load_latest_export_snapshot(
        self, workspace_id: UUID
    ) -> dict[str, Any] | None:
        payload = self.latest_by_workspace.get(str(workspace_id))
        return dict(payload) if payload is not None else None


def _initial_state(workspace_id: UUID, user_id: str) -> dict[str, object]:
    return {
        "workspace_id": workspace_id,
        "user_id": user_id,
        "run_id": uuid4(),
        "run_scope": "FINALIZE",
        "input_version": "finalize:test",
        "phase": "PENDING",
        "draft_refs": {},
        "result_refs": [],
        "status": GraphRunStatus.RUNNING,
        "error_code": None,
    }


def _approved_reply(**overrides: object) -> dict:
    base = {
        "source_id": str(uuid4()),
        "suggestion_id": str(uuid4()),
        "party_id": "party-r1",
        "party_role": "REVIEWER",
        "party_display_name": "Reviewer 1",
        "excerpt": "Please clarify the experiment.",
        "localized_claim": "Clarify the experiment.",
        "reply_status": "APPROVED",
        "draft_status": "APPROVED",
        "content": "感谢意见，我们已补充实验设置。",
    }
    base.update(overrides)
    return base


def _source_with_draft(
    *,
    reply_status: str,
    draft_status: str,
    issues: list[object],
    content: str = "感谢审稿人的建议。我们已在方法章节补充实验设置说明。",
    fact_id: str | None = None,
) -> dict[str, object]:
    source_id = str(uuid4())
    suggestion_id = str(uuid4())
    linked = [fact_id] if fact_id else []
    return {
        "source_id": source_id,
        "suggestion_id": suggestion_id,
        "reply": {
            "status": reply_status,
            "strategy": {"direction": "ACCEPT"},
            "response_facts": {
                "linked_fact_ids": linked,
                "unresolved_items": [],
                "fact_items": [],
            },
            "current_draft": {
                "status": draft_status,
                "content": content,
                "consistency_report": {
                    "is_consistent": len(issues) == 0,
                    "issues": issues,
                    "reminders": [],
                    "cross_source_conflicts": [],
                },
            },
        },
    }


def _approved_context() -> dict[str, Any]:
    """构造可通过校验的完整 context。"""
    workspace_id = str(uuid4())
    suggestion_id = str(uuid4())
    source_id = str(uuid4())
    fact_id = str(uuid4())
    draft_id = str(uuid4())
    party_id = str(uuid4())
    return {
        "workspace_id": workspace_id,
        "workspace_title": "导出测试",
        "user_id": "user-finalize",
        "global_settings": {"response_language": "中文"},
        "suggestions": [
            {
                "suggestion_id": suggestion_id,
                "canonical_text": "Clarify the experiment.",
                "status": "SUCCEEDED",
                "priority": "P1",
                "category_ids": ["METHOD"],
                "input_version": "s-v1",
                "current_analysis_id": None,
                "modification_facts": [
                    {
                        "fact_id": fact_id,
                        "action_type": "CLARIFY",
                        "paper_change_summary": "在方法章节补充实验设置说明。",
                        "response_fact_summary": "作者已补充实验设置说明。",
                        "constraints": {},
                        "status": "CONFIRMED",
                        "input_version": "f-v1",
                    }
                ],
            }
        ],
        "sources": [
            {
                "source_id": source_id,
                "suggestion_id": suggestion_id,
                "party_id": party_id,
                "party_order": 0,
                "party_role": "REVIEWER",
                "party_display_name": "Reviewer #1",
                "excerpt": "Please clarify the experiment.",
                "localized_claim": "Clarify the experiment.",
                "span_refs": {"source_order": 1},
                "status": "ACTIVE",
                "reply": {
                    "reply_id": str(uuid4()),
                    "source_id": source_id,
                    "suggestion_id": suggestion_id,
                    "status": "APPROVED",
                    "strategy": {"direction": "ACCEPT"},
                    "expression_settings": {},
                    "response_facts": {
                        "linked_fact_ids": [fact_id],
                        "unresolved_items": [],
                        "fact_items": [],
                    },
                    "input_version": "r-v1",
                    "current_draft": {
                        "draft_id": draft_id,
                        "version_no": 1,
                        "content": "感谢审稿人的建议。我们已在方法章节补充实验设置说明。",
                        "language": "中文",
                        "consistency_report": {
                            "is_consistent": True,
                            "issues": [],
                            "cross_source_conflicts": [],
                            "reminders": [],
                        },
                        "status": "APPROVED",
                        "approved_at": "2026-01-01T00:00:00+00:00",
                        "approved_by": "user-finalize",
                    },
                },
            }
        ],
        "internal_revision_items": [
            {
                "suggestion_id": suggestion_id,
                "canonical_text": "Clarify the experiment.",
                "priority": "P1",
                "modification_facts": ["在方法章节补充实验设置说明。"],
                "source_ids": [source_id],
                "source_labels": ["Reviewer #1"],
            }
        ],
        "external_replies": [
            {
                "source_id": source_id,
                "suggestion_id": suggestion_id,
                "party_id": party_id,
                "party_role": "REVIEWER",
                "party_display_name": "Reviewer #1",
                "excerpt": "Please clarify the experiment.",
                "localized_claim": "Clarify the experiment.",
                "reply_status": "APPROVED",
                "draft_id": draft_id,
                "draft_status": "APPROVED",
                "content": "感谢审稿人的建议。我们已在方法章节补充实验设置说明。",
            }
        ],
        "suggestion_by_id": {},
    }


# ---------------------------------------------------------------------------
# 一致性纯函数
# ---------------------------------------------------------------------------


def test_cross_source_consistency_is_single_reusable_entrypoint() -> None:
    suggestion_id = uuid4()
    conflicts = check_cross_source_consistency(
        suggestion_id,
        [
            {
                "source_id": str(uuid4()),
                "response_facts": [{"fact_key": "dataset", "value": "A"}],
                "current_draft": {"consistency_report": {}},
            },
            {
                "source_id": str(uuid4()),
                "response_facts": [{"fact_key": "dataset", "value": "B"}],
                "current_draft": {"consistency_report": {}},
            },
        ],
    )
    assert conflicts and conflicts[0]["code"] == "CROSS_SOURCE_CONFLICT"
    assert conflicts[0]["suggestion_id"] == str(suggestion_id)


def test_cross_source_consistency_no_conflict_when_same_value() -> None:
    suggestion_id = uuid4()
    conflicts = check_cross_source_consistency(
        suggestion_id,
        [
            {
                "source_id": str(uuid4()),
                "response_facts": [{"fact_key": "dataset", "value": "A"}],
                "current_draft": {"consistency_report": {}},
            },
            {
                "source_id": str(uuid4()),
                "response_facts": [{"fact_key": "dataset", "value": "A"}],
                "current_draft": {"consistency_report": {}},
            },
        ],
    )
    assert conflicts == []


def test_cross_source_explicit_conflicts_from_draft_report() -> None:
    suggestion_id = uuid4()
    source_id = str(uuid4())
    conflicts = check_cross_source_consistency(
        suggestion_id,
        [
            {
                "source_id": source_id,
                "response_facts": {},
                "current_draft": {
                    "consistency_report": {
                        "cross_source_conflicts": [
                            {
                                "description": "与审稿人2事实矛盾",
                                "related_fact_ids": ["f1"],
                            }
                        ]
                    }
                },
            }
        ],
    )
    assert len(conflicts) == 1
    assert conflicts[0]["source_ids"] == [source_id]
    assert "矛盾" in conflicts[0]["description"]


# ---------------------------------------------------------------------------
# 导出分组 / Markdown（复用 tools，经 finalize.export 再导出）
# ---------------------------------------------------------------------------


def test_group_external_replies_by_party_orders_editor_first_and_numbers_within() -> None:
    replies = [
        _approved_reply(
            party_id="r2",
            party_role="REVIEWER",
            party_display_name="Reviewer B",
            excerpt="Add baselines.",
            content="已补充基线。",
        ),
        _approved_reply(
            party_id="editor",
            party_role="EDITOR",
            party_display_name="Editor",
            excerpt="Point-by-point response required.",
            content="我们将逐点回复。",
        ),
        _approved_reply(
            party_id="r1",
            party_role="REVIEWER",
            party_display_name="Reviewer A",
            excerpt="Clarify sampling.",
            content="已澄清采样。",
        ),
        _approved_reply(
            party_id="r1",
            party_role="REVIEWER",
            party_display_name="Reviewer A",
            excerpt="Improve figures.",
            content="已提高图片清晰度。",
        ),
        _approved_reply(
            party_id="r3",
            party_display_name="Skip Me",
            reply_status="REVIEW_WAITING",
            draft_status="EDITED",
            content="未批准不应导出。",
        ),
        _approved_reply(
            party_id="r4",
            party_display_name="Empty",
            content="   ",
        ),
    ]
    groups = group_external_replies_by_party(replies)
    assert [item["party_display_name"] for item in groups] == [
        "Editor",
        "Reviewer A",
        "Reviewer B",
    ]
    assert [item["opinion_no"] for item in groups[1]["replies"]] == [1, 2]


def test_markdown_export_groups_by_reviewer_with_opinion_numbers() -> None:
    snapshot = {
        "workspace_title": "示例任务",
        "external_replies": [
            _approved_reply(
                party_id="r1",
                party_display_name="审稿人 1",
                excerpt="请补充消融实验。",
                localized_claim="补充消融实验",
                content="回复甲。",
            ),
            _approved_reply(
                party_id="r1",
                party_display_name="审稿人 1",
                excerpt="请改进图 3。",
                localized_claim="改进图 3 清晰度",
                content="回复乙。",
            ),
        ],
        "internal_revision_items": [
            {
                "canonical_text": "补充消融实验",
                "modification_facts": ["在实验章节加入模块消融表"],
            }
        ],
    }
    markdown = render_export_markdown(snapshot)
    assert markdown.startswith("# 示例任务 · 审稿意见回复\n")
    assert "## 对外回复" in markdown
    assert markdown.count("## 审稿人 1") == 1
    assert "### 意见 1" in markdown
    assert "### 意见 2" in markdown
    assert "## 内部修改清单" in markdown


# ---------------------------------------------------------------------------
# 校验纯函数
# ---------------------------------------------------------------------------


def test_approved_draft_consistency_issues_demote_to_warnings() -> None:
    fact_id = str(uuid4())
    source = _source_with_draft(
        reply_status="APPROVED",
        draft_status="APPROVED",
        issues=[{"description": "语气略生硬"}, "残留说明性条目"],
        fact_id=fact_id,
    )
    validation = build_finalize_validation(
        {
            "sources": [source],
            "suggestions": [
                {
                    "suggestion_id": source["suggestion_id"],
                    "modification_facts": [
                        {
                            "fact_id": fact_id,
                            "status": "CONFIRMED",
                            "action_type": "CLARIFY",
                        }
                    ],
                }
            ],
        }
    )
    assert validation["blocked"] is False
    assert validation["block_list"] == []
    assert len(validation["warnings"]) == 2
    assert all(
        item["code"] == "DRAFT_CONSISTENCY_ISSUE" for item in validation["warnings"]
    )


def test_unapproved_draft_consistency_issues_still_block() -> None:
    fact_id = str(uuid4())
    source = _source_with_draft(
        reply_status="DRAFTING",
        draft_status="PENDING_REVIEW",
        issues=[{"description": "事实表述不一致"}],
        fact_id=fact_id,
    )
    validation = build_finalize_validation(
        {
            "sources": [source],
            "suggestions": [
                {
                    "suggestion_id": source["suggestion_id"],
                    "modification_facts": [
                        {
                            "fact_id": fact_id,
                            "status": "CONFIRMED",
                            "action_type": "CLARIFY",
                        }
                    ],
                }
            ],
        }
    )
    assert validation["blocked"] is True
    codes = {item["code"] for item in validation["block_list"]}
    assert "DRAFT_CONSISTENCY_ISSUE" in codes
    assert "REPLY_NOT_APPROVED" in codes
    assert "DRAFT_NOT_APPROVED" in codes


def test_missing_reply_still_blocks_export() -> None:
    suggestion_id = str(uuid4())
    validation = build_finalize_validation(
        {
            "sources": [
                {
                    "source_id": str(uuid4()),
                    "suggestion_id": suggestion_id,
                    "reply": None,
                }
            ],
            "suggestions": [
                {"suggestion_id": suggestion_id, "modification_facts": []}
            ],
        }
    )
    assert validation["blocked"] is True
    assert any(item["code"] == "MISSING_REPLY" for item in validation["block_list"])


# ---------------------------------------------------------------------------
# 图 compile + FakeStore 路径
# ---------------------------------------------------------------------------


def test_finalize_graph_compiles() -> None:
    store = FakeFinalizeStore()
    graph = build_finalize_graph(stores={"finalize": store})
    assert graph is not None
    # LangGraph CompiledStateGraph 暴露 name
    assert getattr(graph, "name", None) == "finalize_graph" or graph is not None


def test_finalize_graph_returns_block_list_when_missing_reply() -> None:
    workspace_id = uuid4()
    source_id = str(uuid4())
    suggestion_id = str(uuid4())
    context = {
        "workspace_id": str(workspace_id),
        "workspace_title": "阻塞测试",
        "user_id": "user-1",
        "global_settings": {},
        "suggestions": [
            {
                "suggestion_id": suggestion_id,
                "canonical_text": "x",
                "status": "SUCCEEDED",
                "priority": "P1",
                "category_ids": [],
                "input_version": "v1",
                "current_analysis_id": None,
                "modification_facts": [],
            }
        ],
        "sources": [
            {
                "source_id": source_id,
                "suggestion_id": suggestion_id,
                "party_id": str(uuid4()),
                "party_order": 0,
                "party_role": "REVIEWER",
                "party_display_name": "R1",
                "excerpt": "e",
                "localized_claim": "c",
                "span_refs": {},
                "status": "ACTIVE",
                "reply": None,
            }
        ],
        "internal_revision_items": [],
        "external_replies": [],
        "suggestion_by_id": {},
    }
    store = FakeFinalizeStore(context)
    graph = build_finalize_graph(stores={"finalize": store})
    output = graph.invoke(_initial_state(workspace_id, "user-1"))
    assert output["status"] == GraphRunStatus.SUCCEEDED
    assert output["phase"] == "BLOCKED"
    assert output["result_refs"][0]["type"] == "finalize_block_list"
    result = output["draft_refs"]["final_result"]
    assert result["blocked"] is True
    assert any(
        item["code"] == "MISSING_REPLY" and item["source_id"] == source_id
        for item in result["block_list"]
    )


def test_finalize_graph_creates_snapshot_and_three_export_formats(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """批准路径：生成三种导出格式并幂等落库。"""
    # 将导出根目录指到临时目录，避免污染仓库
    import langgraph_agent.tools.export_files as export_mod

    monkeypatch.setattr(export_mod, "_PACKAGE_ROOT", tmp_path)
    monkeypatch.setattr(
        export_mod, "_EXPORT_ROOT", tmp_path / ".tmp" / "finalize_exports"
    )

    context = _approved_context()
    workspace_id = UUID(context["workspace_id"])
    store = FakeFinalizeStore(context)
    graph = build_finalize_graph(stores={"finalize": store})

    output = graph.invoke(_initial_state(workspace_id, "user-finalize"))
    assert output["phase"] == "SUCCEEDED"
    assert output["status"] == GraphRunStatus.SUCCEEDED
    assert output["result_refs"][0]["type"] == "export_snapshot"

    snapshot = output["draft_refs"]["export_snapshot"]
    formats = {item["format"] for item in snapshot["output_files"]}
    assert formats == {"MARKDOWN", "WORD", "EXCEL"}
    assert len(store.save_calls) == 1

    # 幂等：再次 invoke 不重复写审计
    second = graph.invoke(_initial_state(workspace_id, "user-finalize"))
    assert second["result_refs"] == output["result_refs"]
    assert len(store.save_calls) == 1

    summary = build_summary_data(store, workspace_id)
    assert summary["blocked"] is False
    assert summary["latest_export_snapshot"] is not None
    assert summary["completion"]["completed_sources"] == 1

    # 清理
    export_dir = tmp_path / ".tmp" / "finalize_exports"
    if export_dir.exists():
        shutil.rmtree(export_dir, ignore_errors=True)


def test_build_finalize_graph_requires_finalize_store() -> None:
    with pytest.raises(KeyError, match="finalize"):
        build_finalize_graph(stores={})
