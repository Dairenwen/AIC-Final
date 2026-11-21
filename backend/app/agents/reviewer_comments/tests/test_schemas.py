from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import TypeAdapter, ValidationError

from langgraph_agent.schemas import (
    AgentResult,
    AgentStatus,
    AnalysisInput,
    ApiSchema,
    EditableField,
    EditableFieldType,
    FinalizeInput,
    GraphRunStatus,
    InteractionOption,
    IssueSubtype,
    IssueType,
    JsonObject,
    JsonValue,
    LlmClassificationResult,
    PendingInteraction,
    ReplyDirection,
    ReplyInput,
    ResponseSettings,
    ResultReference,
    ResumeCommand,
    ReviewPoint,
    RunStatusData,
    SettingsScope,
    SettingsSource,
    SourceClaim,
    SplitCandidate,
    SplitReviewInput,
    TaskInitInput,
    UpdateSettingsCommand,
    WorkspaceMode,
)
from langgraph_agent.schemas.reply import ConsistencyReport, LlmResponseFacts


class TestJsonValueContract:
    def setup_method(self) -> None:
        self.adapter = TypeAdapter(JsonValue)

    def test_accepts_nested_json_values(self) -> None:
        value = {
            "text": "内容",
            "count": 3,
            "ratio": 0.5,
            "enabled": True,
            "empty": None,
            "items": ["a", 2, False, {"nested": 1.25}],
        }
        assert self.adapter.validate_python(value) == value

    def test_rejects_non_json_python_values(self) -> None:
        invalid_values = [
            object(),
            ValueError("bad"),
            {"set"},
            b"bytes",
            ("tuple",),
            {1: "non-string key"},
        ]
        for value in invalid_values:
            with pytest.raises(ValidationError):
                self.adapter.validate_python(value)

    def test_rejects_non_finite_floats_at_any_depth(self) -> None:
        invalid_values = [
            math.nan,
            math.inf,
            -math.inf,
            {"value": math.nan},
            [1, {"value": math.inf}],
        ]
        for value in invalid_values:
            with pytest.raises(ValidationError):
                self.adapter.validate_python(value)

    def test_json_object_requires_a_plain_dictionary(self) -> None:
        adapter = TypeAdapter(JsonObject)
        assert adapter.validate_python({"valid": [1, 2]}) == {"valid": [1, 2]}
        for value in ([], "object", (("key", "value"),)):
            with pytest.raises(ValidationError):
                adapter.validate_python(value)


class TestInteractionContract:
    def setup_method(self) -> None:
        self.workspace_id = uuid4()
        self.interaction_id = uuid4()

    def make_pending(self, **overrides: object) -> PendingInteraction:
        data = {
            "interaction_id": self.interaction_id,
            "interaction_type": "confirm_revision_facts",
            "workspace_id": self.workspace_id,
            "suggestion_id": uuid4(),
            "source_id": None,
            "thread_id": f"workspace:{self.workspace_id}:suggestion:test",
            "input_version": "iv:test:1",
            "title": "确认修改事实",
            "question": "请确认以下修改事实。",
            "context": {"summary": "需要补充实验说明", "score": 0.8},
            "options": [
                InteractionOption(value="approve", label="确认"),
                InteractionOption(
                    value={"action": "edit"},
                    label="编辑",
                    description="修改后确认",
                ),
            ],
            "editable_fields": [
                EditableField(
                    key="revision_summary",
                    label="修改说明",
                    type=EditableFieldType.TEXTAREA,
                    required=True,
                    default="",
                    choices=[],
                )
            ],
            "blockers": [],
            "resume_action": "resume_analysis",
        }
        data.update(overrides)
        return PendingInteraction.model_validate(data)

    def test_dynamic_fields_and_options(self) -> None:
        select_field = EditableField(
            key="decision",
            label="处理方式",
            type=EditableFieldType.SELECT,
            required=True,
            default="approve",
            choices=[
                InteractionOption(value="approve", label="接受"),
                InteractionOption(value="edit", label="编辑", disabled=True),
            ],
            help_text="请选择下一步。",
        )
        assert select_field.choices[0].value == "approve"

        pending = self.make_pending(interaction_type="future_interaction_type")
        assert pending.interaction_type == "future_interaction_type"

    def test_select_and_multiselect_require_choices(self) -> None:
        for field_type in (EditableFieldType.SELECT, EditableFieldType.MULTISELECT):
            with pytest.raises(ValidationError):
                EditableField(
                    key="choice",
                    label="选择",
                    type=field_type,
                    required=False,
                    default=None,
                    choices=[],
                )

    def test_checkbox_default_must_be_boolean_when_present(self) -> None:
        valid = EditableField(
            key="confirmed",
            label="确认",
            type=EditableFieldType.CHECKBOX,
            required=False,
            default=False,
            choices=[],
        )
        assert valid.default is False

        with pytest.raises(ValidationError):
            EditableField(
                key="confirmed",
                label="确认",
                type=EditableFieldType.CHECKBOX,
                required=False,
                default="false",
                choices=[],
            )

    def test_editable_field_key_rejects_blank_value(self) -> None:
        with pytest.raises(ValidationError):
            EditableField(
                key="   ",
                label="字段",
                type=EditableFieldType.TEXT,
                required=False,
                default=None,
                choices=[],
            )

    def test_pending_interaction_rejects_invalid_contract_values(self) -> None:
        for field_name in (
            "interaction_type",
            "thread_id",
            "input_version",
            "resume_action",
        ):
            with pytest.raises(ValidationError):
                self.make_pending(**{field_name: "   "})

        with pytest.raises(ValidationError):
            self.make_pending(workspace_id="not-a-uuid")
        with pytest.raises(ValidationError):
            self.make_pending(context={"bad": object()})
        with pytest.raises(ValidationError):
            self.make_pending(context=[])

    def test_resume_command_is_exact_and_json_serializable(self) -> None:
        command = ResumeCommand(
            workspace_id=self.workspace_id,
            thread_id=f"workspace:{self.workspace_id}:task",
            interaction_id=self.interaction_id,
            input_version="iv:test:1",
            payload={"approved": True, "edits": ["补充说明"]},
        )
        serialized = json.loads(command.model_dump_json())
        assert serialized["workspace_id"] == str(self.workspace_id)
        assert serialized["payload"]["approved"] is True


class TestRunContract:
    def setup_method(self) -> None:
        self.workspace_id = uuid4()
        self.run_id = uuid4()
        self.pending = PendingInteraction(
            interaction_id=uuid4(),
            interaction_type="confirm",
            workspace_id=self.workspace_id,
            suggestion_id=None,
            source_id=None,
            thread_id=f"workspace:{self.workspace_id}:task",
            input_version="iv:1",
            title="确认",
            question="是否继续？",
            context={},
            options=[],
            editable_fields=[],
            blockers=[],
            resume_action="resume_task",
        )

    def make_run(self, status: GraphRunStatus, **overrides: object) -> RunStatusData:
        data = {
            "run_id": self.run_id,
            "workspace_id": self.workspace_id,
            "status": status,
            "result_refs": [],
            "pending_interaction": None,
            "error_code": None,
            "error_message": None,
        }
        data.update(overrides)
        return RunStatusData.model_validate(data)

    def test_waiting_user_requires_only_waiting_interaction(self) -> None:
        run = self.make_run(
            GraphRunStatus.WAITING_USER,
            pending_interaction=self.pending,
        )
        assert run.pending_interaction == self.pending

        with pytest.raises(ValidationError):
            self.make_run(GraphRunStatus.WAITING_USER)
        with pytest.raises(ValidationError):
            self.make_run(
                GraphRunStatus.RUNNING,
                pending_interaction=self.pending,
            )

    def test_failure_statuses_require_non_empty_error_fields(self) -> None:
        for status in (
            GraphRunStatus.FAILED_RETRYABLE,
            GraphRunStatus.FAILED_FINAL,
        ):
            run = self.make_run(
                status,
                error_code="MODEL_TIMEOUT",
                error_message="模型调用超时",
            )
            assert run.error_code == "MODEL_TIMEOUT"
            with pytest.raises(ValidationError):
                self.make_run(status, error_code="", error_message="failure")
            with pytest.raises(ValidationError):
                self.make_run(status, error_code="FAIL", error_message=None)

    def test_succeeded_run_serializes_result_references(self) -> None:
        result_id = uuid4()
        run = self.make_run(
            GraphRunStatus.SUCCEEDED,
            result_refs=[ResultReference(type="reply", id=result_id)],
        )
        serialized = json.loads(run.model_dump_json())
        assert serialized["status"] == "SUCCEEDED"
        assert serialized["result_refs"][0]["id"] == str(result_id)


class TestWorkspaceContract:
    def make_settings(self, **overrides: object) -> ResponseSettings:
        data = {
            "response_language": "中文",
            "tone": "正式、礼貌",
            "author_reference": "The authors",
            "target_length": "标准",
            "terminology_preferences": [" Transformer ", "微调", "Transformer"],
            "source": SettingsSource.USER_LONG_TERM,
            "copied_from_store_at": datetime.now(timezone.utc),
        }
        data.update(overrides)
        return ResponseSettings.model_validate(data)

    def test_settings_normalize_terms_and_serialize(self) -> None:
        settings = self.make_settings()
        assert settings.terminology_preferences == ["Transformer", "微调"]
        serialized = json.loads(settings.model_dump_json())
        assert serialized["source"] == "user_long_term"
        assert serialized["copied_from_store_at"].endswith(("Z", "+00:00"))

        command = UpdateSettingsCommand(
            settings=settings,
            scope=SettingsScope.SAVE_AS_DEFAULT,
        )
        assert command.scope == SettingsScope.SAVE_AS_DEFAULT
        assert WorkspaceMode.FAST.value == "FAST"

    def test_settings_reject_blank_strings_and_terms(self) -> None:
        for field_name in (
            "response_language",
            "tone",
            "author_reference",
            "target_length",
        ):
            with pytest.raises(ValidationError):
                self.make_settings(**{field_name: "   "})

        with pytest.raises(ValidationError):
            self.make_settings(terminology_preferences=["valid", "   "])

    def test_settings_require_timezone_aware_datetime(self) -> None:
        with pytest.raises(ValidationError):
            self.make_settings(copied_from_store_at=datetime.now())


class TestMigratedGraphSchemas:
    def test_split_candidate_defaults_and_normalizes(self) -> None:
        candidate = SplitCandidate.model_validate(
            {
                "atomic_concern": "缺少消融实验",
                "explicit_request": "  ",
                "implicit_concern": "结果不可信",
                "source_quote": "Please add ablation study.",
                "split_confidence": None,
            }
        )
        assert candidate.explicit_request is None
        assert candidate.split_confidence == 0.5

        review_point = ReviewPoint.model_validate(
            {
                "point_id": "P-01",
                "reviewer_id": "R1",
                "original_item_id": None,
                "original_item_number": "1",
                "original_text": "Please add ablation study.",
                "atomic_concern": "缺少消融实验",
                "explicit_request": None,
                "implicit_concern": "结果不可信",
                "source_order": 1,
                "source_quote": "Please add ablation study.",
                "split_confidence": 0.9,
                "split_status": "CONFIRMED",
                "parent_point_id": None,
            }
        )
        assert review_point.point_id == "P-01"
        assert SplitReviewInput(original_text="原始意见").language is None

    def test_classification_result_validates_subtype_membership(self) -> None:
        result = LlmClassificationResult.model_validate(
            {
                "primary_type": IssueType.EXPERIMENT_EVALUATION,
                "target_subtype": IssueSubtype.ABLATION_STUDY,
                "secondary_types": [],
                "issue_natures": ["MISSING"],
                "explicit_action": "补充消融",
                "inferred_action": None,
                "implicit_concern": None,
                "classification_confidence": None,
                "classification_reason": "明确要求消融实验",
                "candidate_types": [IssueType.EXPERIMENT_EVALUATION],
            }
        )
        assert result.classification_confidence == 0.75

        with pytest.raises(ValidationError):
            LlmClassificationResult.model_validate(
                {
                    "primary_type": IssueType.EXPERIMENT_EVALUATION,
                    "target_subtype": IssueSubtype.NOVELTY,
                    "secondary_types": [],
                    "issue_natures": ["MISSING"],
                    "classification_reason": "错误子类型",
                }
            )

    def test_reply_source_claim_and_direction(self) -> None:
        claim = SourceClaim(
            source_id=uuid4(),
            suggestion_id=uuid4(),
            original_text="Please clarify the baseline.",
            localized_claim="请澄清基线设置",
        )
        assert claim.localized_claim.startswith("请澄清")
        assert ReplyDirection.ACCEPT_AND_REVISE.value == "ACCEPT_AND_REVISE"

    def test_reply_llm_schemas_fill_omitted_list_fields(self) -> None:
        """模型常省略空列表；迁移后契约必须自动补 []，避免 REPLY 崩溃。"""
        facts = LlmResponseFacts.model_validate(
            {
                "acknowledgement": "感谢审稿意见",
                "direct_answer": "我们将补充实验",
                "author_position": "部分接受",
                # 故意省略 linked_fact_ids / modification_locations / unresolved_items
            }
        )
        assert facts.linked_fact_ids == []
        assert facts.modification_locations == []
        assert facts.unresolved_items == []

        report = ConsistencyReport.model_validate(
            {
                "is_consistent": True,
                "issues": [],
                "reminders": [],
                # 故意省略 cross_source_conflicts（本次线上失败点）
            }
        )
        assert report.cross_source_conflicts == []
        assert report.issues == []
        assert report.reminders == []

        minimal = ConsistencyReport.model_validate({"is_consistent": True})
        assert minimal.issues == []
        assert minimal.cross_source_conflicts == []
        assert minimal.reminders == []

    def test_analysis_llm_schemas_tolerate_omitted_and_alias_fields(self) -> None:
        """分析侧：省略可选/列表字段 + 动作别名，不应直接 ValidationError。"""
        from langgraph_agent.schemas.analysis import (
            LlmActionCandidate,
            LlmActionRecommendations,
            LlmClassificationResult,
            LlmPriorityAssessment,
        )

        classification = LlmClassificationResult.model_validate(
            {
                "primary_type": "DATA_SAMPLE",
                "target_subtype": "DATA_SPLIT",
                "issue_natures": ["MISSING"],
                "classification_reason": "审稿人要求补充划分细节",
                # 省略 secondary_types / implicit_concern / candidate_types
            }
        )
        assert classification.secondary_types == []
        assert classification.implicit_concern is None
        assert classification.candidate_types == []

        priority = LlmPriorityAssessment.model_validate(
            {
                "academic_impact": "MAJOR",
                "handling_requirement": "MUST_ADDRESS",
                "revision_effort": "LOW",
                "feasibility": "FEASIBLE",
                "work_priority": "P1",
                "assessment_reason": "低成本文档补充但必须回应",
                # 省略 schedule_flag / risk_signals
            }
        )
        assert priority.schedule_flag is None
        assert priority.risk_signals == []

        actions = LlmActionRecommendations.model_validate(
            {
                "recommendations": [
                    {
                        "action_type": "TRANSPARENCY_COMPLIANCE",
                        "title": "补充数据集划分",
                        "description": "在实验设置中写清划分细节",
                        "addressed_concern": "dataset split 不充分",
                        "necessity": "CORE",
                        "recommendation_basis": "审稿人明确要求",  # 字符串 → 列表
                        "expected_output": "划分说明段落",  # 单数别名
                        "estimated_cost": "LOW",
                        "feasibility": "UNKNOWN",
                        "expected_resolution_level": "FULL",
                        "location_hint": "Section 4",  # 多余字段应被丢弃
                        "risk_notes": "无",
                    }
                ]
            }
        )
        item = actions.recommendations[0]
        assert item.recommendation_basis == ["审稿人明确要求"]
        assert item.expected_outputs == ["划分说明段落"]
        assert item.required_facts == []
        assert item.alternative_actions == []


class TestPublicApi:
    def test_agent_result_and_inputs(self) -> None:
        workspace_id = uuid4()
        run_id = uuid4()
        suggestion_id = uuid4()
        source_id = uuid4()

        pending = PendingInteraction(
            interaction_id=uuid4(),
            interaction_type="confirm",
            workspace_id=workspace_id,
            suggestion_id=suggestion_id,
            source_id=source_id,
            thread_id=f"workspace:{workspace_id}:reply:{source_id}",
            input_version="iv:1",
            title="确认",
            question="是否继续？",
            context={},
            options=[],
            editable_fields=[],
            blockers=[],
            resume_action="resume_reply",
        )
        result = AgentResult(
            status=AgentStatus.WAITING_HUMAN,
            thread_id=pending.thread_id,
            run_id=run_id,
            pending=pending,
            result_refs=[],
            phase="await_human",
            error_code=None,
            artifacts={"draft_key": "v1"},
        )
        assert result.status is AgentStatus.WAITING_HUMAN
        assert result.pending is not None

        assert TaskInitInput(
            workspace_id=workspace_id,
            user_id="user-1",
            mode=WorkspaceMode.FAST,
        ).mode is WorkspaceMode.FAST
        assert AnalysisInput(
            workspace_id=workspace_id,
            suggestion_id=suggestion_id,
            user_id="user-1",
        ).suggestion_id == suggestion_id
        assert ReplyInput(
            workspace_id=workspace_id,
            suggestion_id=suggestion_id,
            source_id=source_id,
            user_id="user-1",
            input_version="iv:1",
        ).source_id == source_id
        assert FinalizeInput(
            workspace_id=workspace_id,
            user_id="user-1",
        ).input_version is None


class TestBaseContract:
    def test_every_schema_forbids_extra_fields_and_non_finite_floats(self) -> None:
        schema_types = (
            InteractionOption,
            EditableField,
            PendingInteraction,
            ResumeCommand,
            ResultReference,
            RunStatusData,
            ResponseSettings,
            UpdateSettingsCommand,
            AgentResult,
            TaskInitInput,
        )
        for schema_type in schema_types:
            assert issubclass(schema_type, ApiSchema)
            assert schema_type.model_config.get("extra") == "forbid"
            assert schema_type.model_config.get("allow_inf_nan") is False

        with pytest.raises(ValidationError):
            InteractionOption(value="ok", label="确认", unknown=True)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(pytest.main([__file__, "-q"]))


if __name__ == "__main__":
    main()
