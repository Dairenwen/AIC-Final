from __future__ import annotations

import base64
import json

import httpx
from pydantic import ValidationError

from .openai_compatible import ModelGatewayError, _json_content
from .scientific_elements import (
    PageTableCheckVerification,
    PageVisualAnalysis,
    ScientificElement,
    normalize_table_checks_payload,
)
from .telemetry import ModelRequestTelemetry


VISION_SYSTEM_PROMPT = """You inspect one rendered PDF page to verify selected equations, figures, and tables. Return one JSON object only.

Required shape:
{
  "elements": [
    {
      "element_id": "an ID supplied by the caller",
      "verification_status": "VERIFIED|NOT_VISIBLE|UNCERTAIN",
      "explanation": "corrected explanation using visible page content and supplied text",
      "variables": [{"symbol": "x", "meaning": "meaning supported by the page or text"}],
      "findings": ["specific visible qualitative trend, structure, or relationship"],
      "table_checks": [
        {
          "check_type": "BEST_VALUE|BASELINE_COMPARISON|ABLATION_COMPARISON",
          "label_axis": "ROW|COLUMN",
          "metric": "BLEU",
          "scope": "EN-DE",
          "direction": "HIGHER_IS_BETTER|LOWER_IS_BETTER|NEUTRAL",
          "baseline_label": "baseline row",
          "baseline_value": 26.3,
          "target_label": "proposed row",
          "target_value": 28.4,
          "absolute_difference": 2.1,
          "relative_difference_percent": 7.9848
        }
      ],
      "table_cell_facts": [
        {
          "metric": "FLOPs",
          "scope": "ImageNet architectures",
          "row_label": "FLOPs",
          "column_header": "101-layer",
          "value": 7600000000
        }
      ]
    }
  ]
}

Use only supplied element IDs. Before analysis, confirm that the exact target label or equation is visible on the rendered page. Use VERIFIED only when it is visible, NOT_VISIBLE when absent, and UNCERTAIN when the image is unreadable. For NOT_VISIBLE or UNCERTAIN, explain the verification failure and return empty variables and findings. Read labels, axes, legends, cells, arrows, and equation layout only for VERIFIED targets. Do not invent details that are unreadable. Write in the requested language.
The rendered page may contain multiple neighboring figures or tables. Analyze only the supplied target labels, keep their boundaries separate, and never attribute a neighboring element's boxes, arrows, labels, or values to the target.
When the image is target-focused, the upper region is the high-resolution target crop and the lower region is a full-page overview for context. The payload's visual_target_order identifies the supplied targets represented in that crop.
Page context may include a table_grid candidate produced by Docling and anchored by PyMuPDF. Use its row, column, span, text, and bounding-box fields as a reading aid, but cross-check them against the rendered page. The candidate is not independent proof and may contain extra or merged columns.
For each table, first locate its exact caption in the target crop, then independently transcribe the visible column headers and row labels before reading any values. Treat the table_grid only as a search map: compare its candidate text and bounding boxes with the pixels, and ignore candidate rows or columns that are not visibly supported. Do not return UNCERTAIN merely because the candidate grid contains an extra or merged column when the caption and requested cells are still readable.
Set label_axis to ROW when baseline_label and target_label are method/configuration row labels under one shared metric column. Set label_axis to COLUMN when baseline_label and target_label are method/configuration column headers on one shared metric or setting row. Never rotate a table mentally without recording the correct label_axis.
Use BEST_VALUE only when the target is not worse than the baseline under the stated direction. A NEUTRAL direction cannot support BEST_VALUE. Use BASELINE_COMPARISON for a factual comparison whose target is not the best value.
For a VERIFIED table, read the metric direction, task/dataset scope, row and column headers, baseline, proposed method, and ablation variable before interpreting values. For a result or ablation table, emit only the small number of high-confidence comparisons whose two row labels, shared column header, scope, and values are all visible. Put every numeric best-value, baseline, or ablation comparison in table_checks, with absolute_difference exactly equal to target_value - baseline_value. Keep findings qualitative when possible. Identify the best value only after deciding whether higher or lower is better, preserve ties, and compute absolute or relative improvement only from clearly visible cells in the same metric and scope. For a configuration or hyperparameter table, emit at most five high-confidence directly visible numeric cells in table_cell_facts. A cell fact only states the row, column, scope, metric, and value; never turn it into "required/necessary/causal/better/best" without a controlled comparison. Do not duplicate comparison checks as cell facts. Use empty arrays when no reliable evidence is visible. Never infer a missing digit, decimal point, boldface, underline, or table cell.
"""


TABLE_CHECK_VERIFICATION_SYSTEM_PROMPT = """You independently verify proposed numeric table checks against one rendered PDF page. Return one JSON object only.

Required shape:
{
  "checks": [
    {
      "element_id": "an exact supplied element ID",
      "check_index": 0,
      "verification_status": "VERIFIED|REJECTED|UNCERTAIN",
      "reason": "brief row-column verification reason",
      "table_scope_text": "exact visible table title, caption, or table-wide task/dataset label or null",
      "target_row_label": "exact visible target row label or null",
      "target_column_header": "exact visible target metric/scope header or null",
      "target_cell_value": 28.4,
      "baseline_row_label": "exact visible baseline row label or null",
      "baseline_column_header": "exact visible baseline metric/scope header or null",
      "baseline_cell_value": 26.3
    }
  ],
  "cell_facts": [
    {
      "element_id": "an exact supplied element ID",
      "fact_index": 0,
      "verification_status": "VERIFIED|REJECTED|UNCERTAIN",
      "reason": "brief single-cell verification reason",
      "table_scope_text": "exact visible table title, caption, or table-wide scope or null",
      "row_label": "exact visible row label or null",
      "column_header": "exact visible column header or null",
      "cell_value": 7600000000
    }
  ]
}

Return exactly one result for every supplied check and cell fact. For each value, locate the exact table, task/dataset scope, metric, row, and column. Transcribe the exact visible table title, caption, or table-wide task/dataset label into table_scope_text; never copy the proposed scope unless those words are visible. For comparison checks, transcribe the visible row labels, column headers, and cell values into the six cell proof fields. For label_axis ROW, the target/baseline labels must appear in target_row_label/baseline_row_label under the shared metric column. For label_axis COLUMN, put the shared visible row label in both row-label fields and the target/baseline method labels in target_column_header/baseline_column_header. VERIFIED requires the proposed scope and metric to appear in the corresponding visible headers, shared row label, or table_scope_text, and requires both values to occupy the transcribed row-column intersections. Recompute target_value - baseline_value. For a cell fact, independently transcribe one row label, column header, and numeric value; VERIFIED requires all three plus the proposed scope/metric to be visibly bound. REJECT evidence when its scope or metric is absent, when a value belongs to a neighboring row or column, when a required cell is blank, when task scopes are mixed, or when arithmetic differs. Use null for a blank or unreadable field and UNCERTAIN when the cells are unreadable. Do not repair or replace proposed evidence.
For BEST_VALUE, also REJECT when the target is worse than the baseline under the proposed direction or when the direction is NEUTRAL. Cell visibility alone does not prove the semantic "best" role.
Page context may include a Docling/PyMuPDF table_grid candidate. Use it to locate cells, then independently verify every field against the rendered page; never mark a check VERIFIED from the candidate alone.
"""


class OpenAICompatibleVisionGateway:
    """Minimal OpenAI-compatible page-image adapter for a configured vision model."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        *,
        timeout_seconds: float = 90.0,
        maximum_image_bytes: int = 12 * 1024 * 1024,
        client: httpx.Client | None = None,
        trust_env: bool = True,
        json_object_mode: bool = True,
        enable_thinking: bool | None = None,
    ) -> None:
        parsed_url = httpx.URL(base_url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.host:
            raise ValueError("base_url must be an absolute HTTP(S) URL")
        if not api_key.strip() or not model.strip():
            raise ValueError("vision API key and model are required")
        if timeout_seconds <= 0 or maximum_image_bytes < 1:
            raise ValueError("vision limits must be positive")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.maximum_image_bytes = maximum_image_bytes
        self.trust_env = trust_env
        self._owns_client = client is None
        self.client = (
            client
            if client is not None
            else httpx.Client(
                timeout=self.timeout_seconds,
                trust_env=self.trust_env,
            )
        )
        self.json_object_mode = json_object_mode
        self.enable_thinking = enable_thinking
        self._request_telemetry = ModelRequestTelemetry()

    def analyze_page(
        self,
        *,
        page_number: int,
        image_png: bytes,
        elements: list[ScientificElement],
        page_context: list[dict],
        language: str,
    ) -> PageVisualAnalysis:
        if not image_png or len(image_png) > self.maximum_image_bytes:
            raise ModelGatewayError("VISION_IMAGE_INVALID", "Rendered PDF page image is empty or too large.")
        if not elements:
            return PageVisualAnalysis(elements=[])
        payload = {
            "page": page_number,
            "language": language,
            "visual_target_order": [
                {
                    "element_id": item.element_id,
                    "label": item.label,
                    "element_type": item.element_type,
                }
                for item in elements
            ],
            "elements": [
                item.model_dump(mode="json", exclude={"visual_status"}) for item in elements
            ],
            "page_context": page_context,
        }
        image_url = "data:image/png;base64," + base64.b64encode(image_png).decode("ascii")
        request_body = {
            "model": self.model,
            "temperature": 0.1,
            "messages": [
                {"role": "system", "content": VISION_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": json.dumps(payload, ensure_ascii=False)},
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                },
            ],
        }
        if self.json_object_mode:
            request_body["response_format"] = {"type": "json_object"}
        try:
            response = self._post_chat_completion("page_visual_analysis", request_body)
            body = response.json()
            content = body["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise ValueError("message content must be text")
            analysis = PageVisualAnalysis.model_validate(
                normalize_table_checks_payload(_json_content(content))
            )
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError, ValidationError) as exc:
            raise ModelGatewayError(
                "VISION_PAGE_ANALYSIS_FAILED",
                "The configured vision model did not return valid page analysis.",
            ) from exc

        expected_ids = {item.element_id for item in elements}
        returned_ids = [item.element_id for item in analysis.elements]
        if len(returned_ids) != len(set(returned_ids)) or set(returned_ids) - expected_ids:
            raise ModelGatewayError(
                "VISION_ELEMENT_REFERENCE_INVALID",
                "Vision analysis referenced an unknown or duplicate scientific element.",
            )
        return analysis

    def verify_table_checks(
        self,
        *,
        page_number: int,
        image_png: bytes,
        elements: list[ScientificElement],
        page_context: list[dict],
        language: str,
    ) -> PageTableCheckVerification:
        if not image_png or len(image_png) > self.maximum_image_bytes:
            raise ModelGatewayError("VISION_IMAGE_INVALID", "Rendered PDF page image is empty or too large.")
        proposed = [
            (element, index, check)
            for element in elements
            if element.element_type == "TABLE"
            for index, check in enumerate(element.table_checks)
        ]
        proposed_facts = [
            (element, index, fact)
            for element in elements
            if element.element_type == "TABLE"
            for index, fact in enumerate(element.table_cell_facts)
        ]
        if not proposed and not proposed_facts:
            return PageTableCheckVerification()
        payload = {
            "page": page_number,
            "language": language,
            "visual_target_order": [
                {
                    "element_id": item.element_id,
                    "label": item.label,
                    "element_type": item.element_type,
                }
                for item in elements
            ],
            "table_checks": [
                {
                    "element_id": element.element_id,
                    "label": element.label,
                    "check_index": index,
                    "check": check.model_dump(mode="json"),
                }
                for element, index, check in proposed
            ],
            "table_cell_facts": [
                {
                    "element_id": element.element_id,
                    "label": element.label,
                    "fact_index": index,
                    "fact": fact.model_dump(mode="json"),
                }
                for element, index, fact in proposed_facts
            ],
            "page_context": page_context,
        }
        image_url = "data:image/png;base64," + base64.b64encode(image_png).decode("ascii")
        request_body = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": TABLE_CHECK_VERIFICATION_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": json.dumps(payload, ensure_ascii=False)},
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                },
            ],
        }
        if self.json_object_mode:
            request_body["response_format"] = {"type": "json_object"}
        try:
            response = self._post_chat_completion("table_check_verification", request_body)
            body = response.json()
            content = body["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise ValueError("message content must be text")
            verification_payload = _json_content(content)
            for raw_check in verification_payload.get("checks", []):
                if isinstance(raw_check, dict) and isinstance(raw_check.get("reason"), str):
                    raw_check["reason"] = raw_check["reason"][:2000]
            for raw_fact in verification_payload.get("cell_facts", []):
                if isinstance(raw_fact, dict) and isinstance(raw_fact.get("reason"), str):
                    raw_fact["reason"] = raw_fact["reason"][:2000]
            verification = PageTableCheckVerification.model_validate(verification_payload)
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError, ValidationError) as exc:
            raise ModelGatewayError(
                "VISION_TABLE_CHECK_VERIFICATION_FAILED",
                "The configured vision model did not return valid table-check verification.",
            ) from exc

        expected = {(element.element_id, index) for element, index, _check in proposed}
        returned = [(item.element_id, item.check_index) for item in verification.checks]
        if len(returned) != len(set(returned)) or set(returned) - expected:
            raise ModelGatewayError(
                "VISION_TABLE_CHECK_REFERENCE_INVALID",
                "Table-check verification referenced an unknown or duplicate proposed check.",
            )
        expected_facts = {
            (element.element_id, index)
            for element, index, _fact in proposed_facts
        }
        returned_facts = [
            (item.element_id, item.fact_index)
            for item in verification.cell_facts
        ]
        if (
            len(returned_facts) != len(set(returned_facts))
            or set(returned_facts) - expected_facts
        ):
            raise ModelGatewayError(
                "VISION_TABLE_FACT_REFERENCE_INVALID",
                "Table verification referenced an unknown or duplicate proposed cell fact.",
            )
        return verification

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": " ".join(("Bearer", self.api_key)),
            "Content-Type": "application/json",
        }

    def _post_chat_completion(
        self,
        request_kind: str,
        request_body: dict,
    ) -> httpx.Response:
        effective_request_body = dict(request_body)
        effective_request_body.setdefault("max_tokens", 2048)
        if self.enable_thinking is not None:
            effective_request_body["enable_thinking"] = self.enable_thinking

        def operation() -> httpx.Response:
            response = self.client.post(
                f"{self.base_url}/chat/completions",
                headers=self._headers(),
                json=effective_request_body,
                timeout=self.timeout_seconds,
            )
            if response.status_code == 400 and (
                "response_format" in effective_request_body or "enable_thinking" in effective_request_body
            ):
                fallback_body = {
                    key: value
                    for key, value in effective_request_body.items()
                    if key not in {"response_format", "enable_thinking"}
                }
                response = self.client.post(
                    f"{self.base_url}/chat/completions",
                    headers=self._headers(),
                    json=fallback_body,
                    timeout=self.timeout_seconds,
                )
            response.raise_for_status()
            return response

        return self._request_telemetry.record(request_kind, operation)

    def request_metrics_snapshot(self) -> dict[str, object]:
        return self._request_telemetry.snapshot()

    def close(self) -> None:
        if self._owns_client and not self.client.is_closed:
            self.client.close()
