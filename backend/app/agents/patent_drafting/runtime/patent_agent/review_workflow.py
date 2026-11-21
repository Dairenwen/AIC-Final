from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from patent_agent.config import AppConfig
from patent_agent.errors import ParseError
from patent_agent.runner import RunStore
from patent_agent.utils import (
    local_timestamp,
    read_json,
    sha256_file,
    utc_now,
    write_json,
)


COMPLETED_RUN_STATUSES = {
    "completed",
    "completed_with_warnings",
    "demo_completed_with_fixture",
}
REVIEWER_TYPES = {
    "owner_accepted_gpt",
    "human_reviewer",
    "patent_professional",
}
FEATURE_VERDICTS = {"SUPPORTED", "PARTIAL", "UNSUPPORTED"}
OVERALL_VERDICTS = {"PASS", "REWORK"}


def _read_json_file(path: Path, *, label: str) -> Any:
    selected = path.expanduser().resolve()
    if not selected.is_file():
        raise ParseError(f"{label} not found: {selected}")
    try:
        return read_json(selected)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ParseError(f"{label} is not readable JSON: {selected.name}") from exc


def _completed_run(
    config: AppConfig,
    run_id: str,
) -> tuple[RunStore, dict[str, Any], dict[str, Any]]:
    store = RunStore(config, run_id)
    state = store.load()
    if state.get("status") not in COMPLETED_RUN_STATUSES:
        raise ParseError(
            "review workflow requires a completed Run; "
            "use resume or revalidate before preparing review artifacts"
        )
    manifest_path = store.path / "artifacts" / "manifest.json"
    if not manifest_path.is_file():
        raise ParseError("completed Run has no artifact manifest")
    manifest = _read_json_file(manifest_path, label="artifact manifest")
    if not isinstance(manifest, dict):
        raise ParseError("artifact manifest must be a JSON object")
    return store, state, manifest


def _verified_artifact(
    store: RunStore,
    manifest: dict[str, Any],
    *,
    manifest_key: str,
    relative_path: str,
) -> Path:
    artifact = store.path / relative_path
    expected = (manifest.get(manifest_key) or {}).get("sha256")
    if not artifact.is_file() or not isinstance(expected, str) or not expected:
        raise ParseError(f"Run is missing verified artifact: {relative_path}")
    if sha256_file(artifact) != expected:
        raise ParseError(f"Run artifact does not match manifest: {relative_path}")
    return artifact


def _new_output_directory(
    config: AppConfig,
    *,
    category: str,
    stem: str,
    requested: Path | None,
) -> Path:
    outputs_root = config.outputs_dir.expanduser().resolve()
    outputs_root.mkdir(parents=True, exist_ok=True)
    if requested is not None:
        target = requested.expanduser().resolve()
        try:
            target.relative_to(outputs_root)
        except ValueError as exc:
            raise ParseError(
                f"output directory must be inside configured outputs_dir: {outputs_root}"
            ) from exc
        if target.exists():
            raise ParseError(f"output directory already exists: {target}")
    else:
        base = outputs_root / category / f"{stem}-{local_timestamp()}"
        target = base
        suffix = 1
        while target.exists():
            suffix += 1
            target = base.with_name(f"{base.name}-{suffix}")
    target.mkdir(parents=True)
    return target


def _review_summary(
    *,
    run_id: str,
    state: dict[str, Any],
    disclosure_review: dict[str, Any] | None,
    review: dict[str, Any],
    command: str,
) -> str:
    quality_issues = (
        (state.get("quality_gate") or {}).get("unresolved_issues") or []
    )
    claim_issues = (
        (state.get("claim_validation") or {}).get("issues") or []
    )
    warnings = state.get("warnings") or []
    lines = [
        "# 修订工作区",
        "",
        f"- 父 Run：`{run_id}`",
        f"- 输入快照 SHA-256：`{review.get('input_sha256')}`",
        f"- Claims SHA-256：`{review.get('claims_sha256')}`",
        f"- Claim–Evidence 特征数：`{review.get('feature_count', 0)}`",
        f"- 未定位候选材料：`{review.get('missing_candidate_locator_count', 0)}`",
        (
            "- 交底书核心章节数："
            f"`{(disclosure_review or {}).get('section_count', 0)}`"
        ),
        (
            "- 未定位章节候选材料："
            f"`{(disclosure_review or {}).get('missing_candidate_locator_count', 0)}`"
        ),
        f"- Quality Gate 未解决项：`{len(quality_issues)}`",
        f"- Claim Validation Issue：`{len(claim_issues)}`",
        f"- Run Warning：`{len(warnings)}`",
        "",
        "## 集中问题摘要",
        "",
    ]
    if not quality_issues and not claim_issues and not warnings:
        lines.extend(["- 未记录阻断项或 Warning。", ""])
    for source, rows in (
        ("Quality Gate", quality_issues),
        ("Claim Validation", claim_issues),
    ):
        for row in rows:
            if isinstance(row, dict):
                rule = str(row.get("rule") or row.get("type") or "issue")
                message = str(row.get("message") or row.get("detail") or row)
            else:
                rule = "issue"
                message = str(row)
            lines.append(f"- {source} `{rule}`：{message}")
    for warning in warnings:
        lines.append(f"- Run Warning：{warning}")
    if quality_issues or claim_issues or warnings:
        lines.append("")
    lines.extend(
        [
            "## 交底书章节—材料定位摘要",
            "",
        ]
    )
    disclosure_entries = (disclosure_review or {}).get("entries") or []
    if not disclosure_entries:
        lines.extend(["- 当前 Run 没有章节材料定位 Artifact。", ""])
    else:
        for entry in disclosure_entries:
            if not isinstance(entry, dict):
                continue
            lines.append(
                f"- `{entry.get('section_key', 'unknown')}`："
                f"候选材料位置 `{len(entry.get('candidate_evidence') or [])}`；"
                f"当前结论 `{entry.get('review_verdict', 'REVIEW_REQUIRED')}`"
            )
        lines.append("")
    lines.extend(
        [
            "## Claim–Evidence 定位摘要",
            "",
        ]
    )
    entries = review.get("entries") or []
    if not entries:
        lines.extend(["- 没有 Claim–Evidence 条目。", ""])
    else:
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            lines.append(
                f"- `{entry.get('feature_id', 'unknown')}`："
                f"候选材料位置 `{len(entry.get('candidate_evidence') or [])}`；"
                f"当前结论 `{entry.get('review_verdict', 'REVIEW_REQUIRED')}`"
            )
        lines.append("")
    lines.extend(
        [
            "## 可编辑文件",
            "",
            "- `disclosure_sections.json`",
            "- `claim_plan.json`",
            "- `claims.json`",
            "",
            "候选材料位置见 `disclosure_evidence_review.md`（如存在）和 "
            "`claim_evidence_review.md`。这些文件只帮助定位，不自动判定材料支撑。",
            "",
            "## 离线复验命令",
            "",
            "```text",
            command,
            "```",
            "",
            "该命令会创建新的修订 Run，不修改父 Run，也不调用 Qwen/CNIPA。",
        ]
    )
    return "\n".join(lines) + "\n"


def prepare_revision_workspace(
    config: AppConfig,
    run_id: str,
    *,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    store, state, manifest = _completed_run(config, run_id)
    sources = {
        "disclosure_sections.json": _verified_artifact(
            store,
            manifest,
            manifest_key="disclosure_sections",
            relative_path="artifacts/disclosure_sections.json",
        ),
        "claim_plan.json": _verified_artifact(
            store,
            manifest,
            manifest_key="claim_plan",
            relative_path="artifacts/claim_plan.json",
        ),
        "claims.json": _verified_artifact(
            store,
            manifest,
            manifest_key="claims_json",
            relative_path="artifacts/claims.json",
        ),
        "claim_evidence_review.json": _verified_artifact(
            store,
            manifest,
            manifest_key="claim_evidence_review_json",
            relative_path="artifacts/claim_evidence_review.json",
        ),
        "claim_evidence_review.md": _verified_artifact(
            store,
            manifest,
            manifest_key="claim_evidence_review_markdown",
            relative_path="artifacts/claim_evidence_review.md",
        ),
    }
    disclosure_review: dict[str, Any] | None = None
    disclosure_review_json = (
        store.path / "artifacts" / "disclosure_evidence_review.json"
    )
    disclosure_review_markdown = (
        store.path / "artifacts" / "disclosure_evidence_review.md"
    )
    if (
        "disclosure_evidence_review_json" in manifest
        or disclosure_review_json.exists()
        or disclosure_review_markdown.exists()
    ):
        sources["disclosure_evidence_review.json"] = _verified_artifact(
            store,
            manifest,
            manifest_key="disclosure_evidence_review_json",
            relative_path="artifacts/disclosure_evidence_review.json",
        )
        sources["disclosure_evidence_review.md"] = _verified_artifact(
            store,
            manifest,
            manifest_key="disclosure_evidence_review_markdown",
            relative_path="artifacts/disclosure_evidence_review.md",
        )
        loaded_disclosure_review = _read_json_file(
            sources["disclosure_evidence_review.json"],
            label="disclosure evidence review",
        )
        if not isinstance(loaded_disclosure_review, dict):
            raise ParseError(
                "disclosure evidence review must be a JSON object"
            )
        if loaded_disclosure_review.get("input_sha256") != state.get(
            "input_sha256"
        ):
            raise ParseError(
                "disclosure evidence input_sha256 does not match the Run"
            )
        if loaded_disclosure_review.get(
            "disclosure_sections_sha256"
        ) != sha256_file(sources["disclosure_sections.json"]):
            raise ParseError(
                "disclosure evidence hash does not match the Run disclosure"
            )
        disclosure_review = loaded_disclosure_review
    review = _read_json_file(
        sources["claim_evidence_review.json"],
        label="Claim–Evidence review",
    )
    if not isinstance(review, dict):
        raise ParseError("Claim–Evidence review must be a JSON object")
    if review.get("input_sha256") != state.get("input_sha256"):
        raise ParseError(
            "Claim–Evidence input_sha256 does not match the Run"
        )
    if review.get("claims_sha256") != sha256_file(sources["claims.json"]):
        raise ParseError(
            "Claim–Evidence claims_sha256 does not match the Run claims"
        )
    target = _new_output_directory(
        config,
        category="revisions",
        stem=f"{run_id}-revision",
        requested=output_dir,
    )
    for name, source in sources.items():
        shutil.copy2(source, target / name)

    quoted = {
        name: f'"{(target / name).as_posix()}"'
        for name in ("disclosure_sections.json", "claim_plan.json", "claims.json")
    }
    command = (
        f"python -m patent_agent revalidate --run-id {run_id} "
        f"--disclosure-sections {quoted['disclosure_sections.json']} "
        f"--claim-plan {quoted['claim_plan.json']} "
        f"--claims {quoted['claims.json']}"
    )
    summary_path = target / "REVISION_GUIDE.md"
    summary_path.write_text(
        _review_summary(
            run_id=run_id,
            state=state,
            disclosure_review=disclosure_review,
            review=review,
            command=command,
        ),
        encoding="utf-8",
    )
    workspace_manifest = {
        "schema_version": "revision_workspace_v1",
        "created_at": utc_now(),
        "parent_run_id": run_id,
        "parent_run_state_sha256": sha256_file(store.state_path),
        "input_sha256": review.get("input_sha256"),
        "claims_sha256": review.get("claims_sha256"),
        "external_calls": {"qwen": 0, "cnipa": 0},
        "files": {
            path.name: {"sha256": sha256_file(path)}
            for path in sorted(target.iterdir())
            if path.is_file()
        },
    }
    manifest_path = target / "workspace_manifest.json"
    write_json(manifest_path, workspace_manifest)
    return {
        "status": "revision_workspace_prepared",
        "parent_run_id": run_id,
        "output_dir": str(target),
        "workspace_manifest": str(manifest_path),
        "revalidate_command": command,
        "external_calls": {"qwen": 0, "cnipa": 0},
    }


def validate_review_decision(
    payload: Any,
    *,
    expected_review: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ParseError("review decision must be a JSON object")
    if not isinstance(expected_review, dict):
        raise ParseError("Run Claim–Evidence review must be a JSON object")
    allowed_keys = {
        "schema_version",
        "reviewer_type",
        "reviewer_label",
        "reviewed_at",
        "input_sha256",
        "claims_sha256",
        "overall_verdict",
        "legal_opinion_provided",
        "entries",
        "notes",
    }
    extra_keys = sorted(set(payload) - allowed_keys)
    if extra_keys:
        raise ParseError(
            "review decision contains unsupported fields: "
            + ", ".join(extra_keys)
        )
    if payload.get("schema_version") != "claim_evidence_decision_v1":
        raise ParseError(
            "review decision schema_version must be claim_evidence_decision_v1"
        )
    reviewer_type = payload.get("reviewer_type")
    if reviewer_type not in REVIEWER_TYPES:
        raise ParseError(
            "reviewer_type must be one of: " + ", ".join(sorted(REVIEWER_TYPES))
        )
    reviewer_label = payload.get("reviewer_label")
    if not isinstance(reviewer_label, str) or not reviewer_label.strip():
        raise ParseError("reviewer_label must be a non-empty string")
    if payload.get("legal_opinion_provided") is not False:
        raise ParseError("review record must set legal_opinion_provided=false")
    if payload.get("input_sha256") != expected_review.get("input_sha256"):
        raise ParseError("review input_sha256 does not match the Run")
    if payload.get("claims_sha256") != expected_review.get("claims_sha256"):
        raise ParseError("review claims_sha256 does not match the Run")
    overall = payload.get("overall_verdict")
    if overall not in OVERALL_VERDICTS:
        raise ParseError("overall_verdict must be PASS or REWORK")

    expected_entries = expected_review.get("entries")
    entries = payload.get("entries")
    if not isinstance(expected_entries, list) or not isinstance(entries, list):
        raise ParseError("review entries must be a JSON array")
    if any(not isinstance(row, dict) for row in expected_entries):
        raise ParseError("Run Claim–Evidence entries must be JSON objects")
    expected_ids = [row.get("feature_id") for row in expected_entries]
    if any(not isinstance(feature_id, str) for feature_id in expected_ids):
        raise ParseError("Run Claim–Evidence review has invalid feature IDs")
    supplied: dict[str, dict[str, Any]] = {}
    for row in entries:
        if not isinstance(row, dict):
            raise ParseError("each review entry must be a JSON object")
        extra_entry_keys = sorted(set(row) - {"feature_id", "verdict", "note"})
        if extra_entry_keys:
            raise ParseError(
                "review entry contains unsupported fields: "
                + ", ".join(extra_entry_keys)
            )
        feature_id = row.get("feature_id")
        if not isinstance(feature_id, str) or not feature_id:
            raise ParseError("each review entry requires feature_id")
        if feature_id in supplied:
            raise ParseError(f"duplicate review feature_id: {feature_id}")
        verdict = row.get("verdict")
        if verdict not in FEATURE_VERDICTS:
            raise ParseError(
                f"review verdict for {feature_id} must be "
                "SUPPORTED, PARTIAL or UNSUPPORTED"
            )
        note = row.get("note", "")
        if not isinstance(note, str):
            raise ParseError(f"review note for {feature_id} must be a string")
        supplied[feature_id] = {
            "feature_id": feature_id,
            "verdict": verdict,
            "note": note.strip(),
        }
    if set(supplied) != set(expected_ids) or len(supplied) != len(expected_ids):
        raise ParseError(
            "review entries must cover every Run feature exactly once"
        )
    ordered = [supplied[feature_id] for feature_id in expected_ids]
    derived_overall = (
        "PASS"
        if all(row["verdict"] == "SUPPORTED" for row in ordered)
        else "REWORK"
    )
    if overall != derived_overall:
        raise ParseError(
            f"overall_verdict {overall} conflicts with feature verdicts; "
            f"expected {derived_overall}"
        )
    notes = payload.get("notes", "")
    if not isinstance(notes, str):
        raise ParseError("review notes must be a string")
    reviewed_at = payload.get("reviewed_at")
    if reviewed_at is not None and not isinstance(reviewed_at, str):
        raise ParseError("reviewed_at must be a string when provided")
    return {
        "schema_version": "claim_evidence_decision_v1",
        "reviewer_type": reviewer_type,
        "reviewer_label": reviewer_label.strip(),
        "reviewed_at": reviewed_at,
        "input_sha256": payload["input_sha256"],
        "claims_sha256": payload["claims_sha256"],
        "overall_verdict": overall,
        "legal_opinion_provided": False,
        "entries": ordered,
        "notes": notes.strip(),
    }


def _render_review_record(record: dict[str, Any]) -> str:
    lines = [
        "# Claim–Evidence 外部审核记录",
        "",
        f"- Run ID：`{record['run_id']}`",
        f"- 审核主体类型：`{record['reviewer_type']}`",
        f"- 审核主体标识：{record['reviewer_label']}",
        f"- 整体结论：`{record['overall_verdict']}`",
        f"- 输入快照 SHA-256：`{record['input_sha256']}`",
        f"- Claims SHA-256：`{record['claims_sha256']}`",
        f"- 原始审核文件 SHA-256：`{record['source_review_sha256']}`",
        "- 法律意见：`false`",
        "",
        (
            "> 该记录只保存外部材料支撑审核结论。"
            "审核主体类型是提交者声明，"
        ),
        "Agent 不验证专业资质，也不把该记录解释为法律意见。",
        "",
        "## 逐项结论",
        "",
    ]
    for entry in record["entries"]:
        lines.append(
            f"- `{entry['feature_id']}`：`{entry['verdict']}`"
            + (f"；{entry['note']}" if entry["note"] else "")
        )
    if record["notes"]:
        lines.extend(["", "## 整体备注", "", record["notes"]])
    return "\n".join(lines) + "\n"


def record_review(
    config: AppConfig,
    run_id: str,
    review_file: Path,
    *,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    store, state, manifest = _completed_run(config, run_id)
    review_artifact = _verified_artifact(
        store,
        manifest,
        manifest_key="claim_evidence_review_json",
        relative_path="artifacts/claim_evidence_review.json",
    )
    claims_artifact = _verified_artifact(
        store,
        manifest,
        manifest_key="claims_json",
        relative_path="artifacts/claims.json",
    )
    expected_review = _read_json_file(
        review_artifact,
        label="Run Claim–Evidence review",
    )
    if not isinstance(expected_review, dict):
        raise ParseError("Run Claim–Evidence review must be a JSON object")
    if expected_review.get("input_sha256") != state.get("input_sha256"):
        raise ParseError(
            "Run Claim–Evidence input_sha256 does not match the Run"
        )
    if expected_review.get("claims_sha256") != sha256_file(claims_artifact):
        raise ParseError(
            "Run Claim–Evidence claims_sha256 does not match the Run claims"
        )
    selected_review = review_file.expanduser().resolve()
    submitted = _read_json_file(selected_review, label="submitted review")
    normalized = validate_review_decision(
        submitted,
        expected_review=expected_review,
    )
    target = _new_output_directory(
        config,
        category=f"reviews/{run_id}",
        stem="review",
        requested=output_dir,
    )
    submitted_path = target / "submitted_review.json"
    shutil.copy2(selected_review, submitted_path)
    record = {
        **normalized,
        "schema_version": "claim_evidence_review_record_v1",
        "recorded_at": utc_now(),
        "run_id": run_id,
        "run_state_sha256": sha256_file(store.state_path),
        "run_manifest_sha256": sha256_file(
            store.path / "artifacts" / "manifest.json"
        ),
        "claim_evidence_review_sha256": sha256_file(review_artifact),
        "claims_sha256": sha256_file(claims_artifact),
        "source_review_sha256": sha256_file(submitted_path),
        "automatic_support_decision": False,
        "reviewer_identity_verified": False,
        "notice": (
            "该记录保存提交的外部审核结论，不验证审核者专业资质，"
            "不构成法律意见，也不修改原 Run。"
        ),
    }
    record_path = target / "review_record.json"
    write_json(record_path, record)
    markdown_path = target / "review_record.md"
    markdown_path.write_text(_render_review_record(record), encoding="utf-8")
    review_manifest = {
        "schema_version": "review_record_manifest_v1",
        "created_at": utc_now(),
        "run_id": run_id,
        "overall_verdict": record["overall_verdict"],
        "reviewer_type": record["reviewer_type"],
        "files": {
            path.name: {"sha256": sha256_file(path)}
            for path in (submitted_path, record_path, markdown_path)
        },
    }
    manifest_path = target / "review_manifest.json"
    write_json(manifest_path, review_manifest)
    return {
        "status": "review_recorded",
        "run_id": run_id,
        "overall_verdict": record["overall_verdict"],
        "reviewer_type": record["reviewer_type"],
        "output_dir": str(target),
        "review_record": str(record_path),
        "review_record_sha256": sha256_file(record_path),
        "review_manifest": str(manifest_path),
        "parent_run_modified": False,
        "legal_opinion_provided": False,
    }
