from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx
from pydantic import ValidationError
from schemas.models import PaperRecord, ReadingRequest, ReadingWarning

from .gateway import ClaimSupportCheck, ReadingAnalysis
from .experiments import ExperimentAnalysis, discard_invalid_optional_evidence_items
from .qa import QuestionAnalysis
from .scientific_elements import (
    ScientificElementAnalysis,
    ScientificElementTarget,
    discard_invalid_scientific_evidence_items,
    normalize_table_checks_payload,
)
from .telemetry import ModelRequestTelemetry


SYSTEM_PROMPT = """You are a paper-reading agent. Return one JSON object only.

Required shape:
{
  "basic_information": {
    "title": "paper title from the supplied text",
    "authors": ["author names from the supplied text"],
    "year": 2024 or null
  },
  "narrative": {
    "one_sentence_summary": "one sentence summary",
    "background_and_motivation": ["..."],
    "problem_definition": ["..."],
    "method_data_flow": ["input -> module -> output"],
    "assumptions": ["..."],
    "further_reading_questions": ["..."]
  },
  "claims": [
    {
      "claim_id": "claim_unique_id",
      "claim_type": "RESEARCH_QUESTION|METHOD|EQUATION_FIGURE|EXPERIMENT|INNOVATION|LIMITATION",
      "claim_source": "AUTHOR_STATED|EVIDENCE_DERIVED|AGENT_INFERRED",
      "content": "one evidence-grounded statement",
      "chunk_ids": ["one or more chunk IDs from the supplied context"]
    }
  ],
  "warnings": [
    {"warning_code": "CODE", "message": "short explanation"}
  ]
}

Use only supplied chunk IDs. Never invent evidence. Produce at least one claim. Write claim content in the requested language.
Recover basic information from the paper text when the supplied metadata is missing or looks like a filename placeholder. Treat the earliest supplied first-page front matter as authoritative: join title lines split only by page layout, return every person named in the byline, preserve full names when present, and exclude affiliations or author roles. Set the year only when an explicit publication header, dated paper version, or equivalent front-matter statement supports it.
For every requested focus aspect, produce an evidence-grounded claim when the supplied paper supports it. Recover the research question from the abstract or introduction even when it is phrased as a goal rather than a question. For a survey or review paper, state this as the explicitly supported review scope (such as concepts, taxonomy, methods, applications, evaluation, or resources); do not recast that scope as building a comprehensive, reproducible, or future-oriented knowledge framework unless the Evidence explicitly says so. Method claims must describe module relationships or input-to-output data flow, not just list module names. For experiment claims, use AUTHOR_STATED for the paper's reported conclusion and AGENT_INFERRED only for an explicitly labeled assessment derived from cited evidence. For limitations, prefer author-stated limitations; otherwise provide a clearly labeled AGENT_INFERRED limitation only when the cited evaluation scope, assumptions, missing comparison, or reproducibility gap supports it.
"""


MISSING_FOCUS_SYSTEM_PROMPT = """You fill missing focus areas in an existing single-paper reading analysis. Return one JSON object only.

Required shape:
{
  "claims": [
    {
      "claim_id": "claim_unique_id",
      "claim_type": "RESEARCH_QUESTION|METHOD|EQUATION_FIGURE|EXPERIMENT|INNOVATION|LIMITATION",
      "claim_source": "AUTHOR_STATED|EVIDENCE_DERIVED|AGENT_INFERRED",
      "content": "one precise statement in the requested language",
      "chunk_ids": ["one or more exact supplied Chunk IDs"]
    }
  ],
  "warnings": []
}

Return claims only for the listed missing claim types. Use exact supplied Chunk IDs and do not repeat existing claims. Recover RESEARCH_QUESTION from the abstract or introduction when supported. METHOD must explain module relationships or input-to-output flow. LIMITATION must distinguish author self-report from an Agent inference grounded in evaluation scope, assumptions, missing comparisons, or reproducibility evidence. EXPERIMENT must distinguish a reported paper conclusion from an Agent assessment. Omit an unsupported type rather than inventing evidence.
"""


DEPTH_GUIDANCE = {
    "OVERVIEW": (
        "Return exactly one concise Claim for each supported requested aspect. "
        "Keep the one-sentence summary under 50 words, every narrative list to at "
        "most one short item, further-reading questions to at most two short items, "
        "and avoid repeating a fact across narrative fields or Claims."
    ),
    "STANDARD": "Cover every requested aspect with one or two substantial claims when evidence is available.",
    "DEEP": "Analyze the requested focus aspects deeply with multiple precise claims, assumptions, mechanisms, and limitations when evidence supports them.",
}


QA_SYSTEM_PROMPT = """You answer questions about one supplied paper. Return one JSON object only.

Required shape:
{
  "answer_status": "ANSWERED|INSUFFICIENT_EVIDENCE|OUT_OF_SCOPE",
  "answer": "direct answer in the requested language",
  "chunk_ids": ["supporting chunk IDs from the supplied context"]
}

For ANSWERED, cite one or more supplied chunk IDs. If the paper context cannot support the answer, use INSUFFICIENT_EVIDENCE and an empty chunk_ids array. If the question is unrelated to the paper, use OUT_OF_SCOPE and an empty chunk_ids array. Never invent a Chunk ID.
"""


SCIENTIFIC_ELEMENTS_SYSTEM_PROMPT = """You identify and explain the most important equations, figures, and tables in one paper. Return one JSON object only.

Required shape:
{
  "elements": [
    {
      "element_id": "element_unique_id",
      "element_type": "EQUATION|FIGURE|TABLE",
      "label": "Equation 1, Figure 2, Table 3, or a concise descriptive label",
      "page": 4,
      "explanation": "what the element means and why it matters",
      "variables": [{"symbol": "d_k", "meaning": "key/query dimension"}],
      "findings": ["the concrete trend or comparison shown"],
      "table_checks": [],
      "table_cell_facts": [],
      "chunk_ids": ["supporting IDs from the supplied context"],
      "needs_visual": true
    }
  ]
}

Use only supplied Chunk IDs and page numbers. Select important elements rather than every mention. When result tables are present, prioritize main performance, benchmark-comparison, and ablation tables over hyperparameter, configuration, taxonomy, or dataset-summary tables; select a non-result table only when it is central to the paper and no stronger result table is supported. Explain equations and variables from text when supported. For tables, put each numeric best-value, baseline, or ablation comparison in table_checks and require absolute_difference = target_value - baseline_value; never compare cells from different metrics or task scopes. For a central configuration table, put a small number of directly transcribed single-cell numeric facts in table_cell_facts using metric, scope, row_label, column_header, and normalized numeric value; do not assign baseline, best-value, direction, necessity, causality, or performance meaning to those facts. Use empty arrays when no reliable cells exist. Set needs_visual=true for figures, tables, or damaged/ambiguous equation layout that should be checked against the rendered PDF page. Do not claim visual details that are absent from the supplied text. An empty elements array is valid when the text contains no reliable scientific element.
Write every label, explanation, variable meaning, and finding in the requested language, except mathematical symbols and official figure/table identifiers.
For min, max, piecewise, summation, or multi-branch equations, state each branch and exponent exactly before explaining its behavior.
"""


TARGETED_SCIENTIFIC_ELEMENTS_SYSTEM_PROMPT = """You explain every supplied scientific target from one paper. Return one JSON object only.

Required shape:
{
  "elements": [
    {
      "element_id": "element_unique_id",
      "element_type": "EQUATION|FIGURE|TABLE",
      "label": "the exact supplied target label",
      "page": 4,
      "explanation": "what the target means, how to read it, and why it matters",
      "variables": [{"symbol": "d_k", "meaning": "key/query dimension"}],
      "findings": ["the concrete trend, comparison, or implication"],
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
      ],
      "chunk_ids": ["supporting IDs from the supplied context"],
      "needs_visual": true,
      "document_object_id": "the exact supplied object_id"
    }
  ]
}

Analyze each supplied target when its target content or supporting chunks contain enough evidence. Preserve the exact object_id, element_type, label, and page supplied for that target. Use only supplied Chunk IDs. Explain every supported variable in an equation. For a figure or table, explain how it should be read and extract supported comparisons or trends. For a table, identify the metric direction, task/dataset scope, baseline, proposed method, and ablation variable before comparing values. Put every numeric best-value, baseline, or ablation comparison in table_checks; absolute_difference must equal target_value - baseline_value. Set label_axis=ROW when baseline/target labels are row labels under one shared metric column, and label_axis=COLUMN when they are column headers on one shared row. Never calculate from missing or truncated cells. A configuration or hyperparameter table supports only directly observed values: put at most five high-confidence numeric cells in table_cell_facts with exact row/column binding, and do not claim a value is required, necessary, causal, better, or best without a controlled comparison. Do not duplicate a comparison check as a cell fact. Use empty arrays when no reliable cells exist. Set needs_visual=true for figures, tables, or damaged equation layout. Do not invent visual details. Omit only a target that truly lacks enough evidence.
Write explanations, meanings, and findings in the requested language, except mathematical symbols and official identifiers.
"""


EXPERIMENT_SYSTEM_PROMPT = """You are the experiment and reproducibility specialist for one paper. Return one JSON object only.

Required shape:
{
  "datasets": [{"name":"...","detail":"...","chunk_ids":["..."]}],
  "baselines": [{"name":"...","detail":"...","chunk_ids":["..."]}],
  "metrics": [{"name":"...","detail":"...","chunk_ids":["..."]}],
  "findings": [{"finding_type":"MAIN_RESULT|ABLATION|EFFICIENCY|ROBUSTNESS|OTHER","content":"...","chunk_ids":["..."]}],
  "conclusion_assessments": [{"conclusion":"...","support_status":"SUPPORTED|PARTIALLY_SUPPORTED|NOT_SUPPORTED|UNCERTAIN","reason":"...","chunk_ids":["..."]}],
  "reproducibility": {
    "code_availability":"AVAILABLE|UNAVAILABLE|NOT_STATED",
    "data_availability":"AVAILABLE|UNAVAILABLE|NOT_STATED",
    "hyperparameters":[{"name":"...","detail":"...","chunk_ids":["..."]}],
    "hardware_and_cost":[{"name":"...","detail":"...","chunk_ids":["..."]}],
    "training_details":[{"name":"...","detail":"...","chunk_ids":["..."]}],
    "missing_information":["..."]
  }
}

Use only supplied Chunk IDs. Do not turn absent information into facts. Separate main results, ablations, efficiency, and robustness. Assess whether the experiments support important author conclusions, and state missing reproducibility information explicitly. Write in the requested language except official dataset, metric, and model names.
"""


CLAIM_SUPPORT_SYSTEM_PROMPT = """Check whether one Claim is supported by its bound Evidence. Return one JSON object only.

Required shape:
{
  "status": "SUPPORTED|PARTIALLY_SUPPORTED|INSUFFICIENT_EVIDENCE",
  "unsupported_fragments": ["exact fragment copied from the Claim"],
  "reason": "brief evidence-coverage reason"
}

Judge the Claim's subject, relation, scope, numbers, causality, comparison strength, and author attribution. Cross-language paraphrases may be supported when their full meaning is entailed. PARTIALLY_SUPPORTED requires exact unsupported Claim fragments. Do not rewrite the Claim, retrieve other text, add Evidence, or use outside knowledge.
"""


logger = logging.getLogger(__name__)


class ModelGatewayError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _message_content(body: dict[str, Any]) -> str:
    choices = body.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        raise ValueError("model response contains no choices")
    message = choices[0].get("message") or {}
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, list):
        content = "\n".join(
            str(item.get("text", "")) if isinstance(item, dict) else str(item)
            for item in content
        )
    if not content and isinstance(message, dict):
        content = message.get("reasoning_content") or message.get("reasoning")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("model response contains no text content")
    return content


def _json_content(content: str) -> dict[str, Any]:
    value = content.strip()
    value = re.sub(r"<think>.*?</think>", "", value, flags=re.DOTALL | re.IGNORECASE).strip()
    if value.startswith("```"):
        lines = value.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        value = "\n".join(lines).strip()
    try:
        parsed = json.loads(value)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    # Some OpenAI-compatible models prepend reasoning or append prose even in
    # JSON mode. Decode every embedded object and prefer the largest candidate.
    decoder = json.JSONDecoder()
    candidates: list[tuple[int, dict[str, Any]]] = []
    for index, marker in enumerate(value):
        if marker != "{":
            continue
        try:
            candidate, end = decoder.raw_decode(value[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict):
            candidates.append((end, candidate))
    if candidates:
        return max(candidates, key=lambda item: item[0])[1]
    raise ValueError("model output must contain a JSON object")


def _normalize_reading_analysis_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    claims = []
    claim_type_aliases = {
        "EQUATION": "EQUATION_FIGURE",
        "FIGURE": "EQUATION_FIGURE",
        "TABLE": "EQUATION_FIGURE",
        "REPRODUCIBILITY": "EXPERIMENT",
        "RESULT": "EXPERIMENT",
        "ABLATION": "EXPERIMENT",
        "EFFICIENCY": "EXPERIMENT",
        "PARAMETER_EFFICIENCY": "EXPERIMENT",
        "COMPUTATIONAL_EFFICIENCY": "EXPERIMENT",
        "CONTRIBUTION": "INNOVATION",
        "RISK": "LIMITATION",
    }
    for raw_claim in payload.get("claims", []):
        if not isinstance(raw_claim, dict):
            claims.append(raw_claim)
            continue
        claim = dict(raw_claim)
        raw_type = claim.get("claim_type")
        if raw_type in claim_type_aliases:
            claim["claim_type"] = claim_type_aliases[raw_type]
        claims.append(claim)
    normalized["claims"] = claims
    return normalized


class OpenAICompatibleModelGateway:
    """Minimal chat-completions adapter for one configured OpenAI-compatible model."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        *,
        timeout_seconds: float = 90.0,
        max_context_characters: int = 120_000,
        client: httpx.Client | None = None,
        trust_env: bool = True,
        json_object_mode: bool = True,
        repair_invalid_analysis: bool = True,
        enable_thinking: bool | None = None,
    ) -> None:
        parsed_url = httpx.URL(base_url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.host:
            raise ValueError("base_url must be an absolute HTTP(S) URL")
        if not api_key.strip():
            raise ValueError("api_key is required")
        if not model.strip():
            raise ValueError("model is required")
        if timeout_seconds <= 0 or max_context_characters < 1:
            raise ValueError("model limits must be positive")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_context_characters = max_context_characters
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
        self.repair_invalid_analysis = repair_invalid_analysis
        self.enable_thinking = enable_thinking
        self._request_telemetry = ModelRequestTelemetry()

    def analyze_paper(
        self,
        request: ReadingRequest,
        paper: PaperRecord,
        context: list[dict[str, Any]],
    ) -> ReadingAnalysis:
        bounded_context = self._bounded_context(context)
        payload = {
            "request": {
                "depth": request.depth,
                "reading_goal": request.reading_goal,
                "focus_aspects": request.focus_aspects,
                "language": request.language,
                "depth_guidance": DEPTH_GUIDANCE[request.depth],
            },
            "paper": {
                "title": paper.title,
                "authors": paper.authors,
                "year": paper.year,
            },
            "chunks": bounded_context,
        }
        request_body = {
            "model": self.model,
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
        }
        if self.json_object_mode:
            request_body["response_format"] = {"type": "json_object"}
        analysis: ReadingAnalysis | None = None
        last_error: Exception | None = None
        repaired_analysis = False
        attempt_count = 2 if self.repair_invalid_analysis else 1
        for attempt in range(attempt_count):
            attempt_body = request_body
            if attempt:
                attempt_body = {
                    **request_body,
                    "temperature": 0,
                    "messages": [
                        *request_body["messages"],
                        {
                            "role": "system",
                            "content": (
                                "The previous response failed the required JSON structure. "
                                "Return one complete JSON object matching the requested fields. "
                                "Use only supplied Chunk IDs and do not add unsupported facts."
                            ),
                        },
                    ],
                }
            try:
                response = self._post_chat_completion(
                    "base_analysis" if attempt == 0 else "base_analysis_repair",
                    attempt_body,
                )
                body = response.json()
                content = _message_content(body)
                if not isinstance(content, str):
                    raise ValueError("message content must be text")
                analysis = ReadingAnalysis.model_validate(
                    _normalize_reading_analysis_payload(_json_content(content))
                )
                repaired_analysis = attempt > 0
                break
            except httpx.HTTPError as exc:
                last_error = exc
                break
            except (
                KeyError,
                IndexError,
                TypeError,
                ValueError,
                ValidationError,
            ) as exc:
                last_error = exc
        if analysis is None:
            raise ModelGatewayError(
                "MODEL_ANALYSIS_FAILED",
                "The configured model did not return a valid reading analysis.",
            ) from last_error
        if repaired_analysis:
            analysis = analysis.model_copy(
                update={
                    "warnings": [
                        *analysis.warnings,
                        ReadingWarning(
                            warning_code="BASE_ANALYSIS_REPAIRED",
                            message=(
                                "Flow-first mode repaired one malformed base "
                                "reading-analysis response."
                            ),
                        ),
                    ]
                }
            )

        missing_claim_types = self._missing_focus_claim_types(request, analysis)
        if missing_claim_types:
            try:
                supplement = self._supplement_missing_focus_claims(
                    request,
                    paper,
                    bounded_context,
                    analysis,
                    missing_claim_types,
                )
                analysis = analysis.model_copy(
                    update={
                        "claims": [*analysis.claims, *supplement.claims],
                        "warnings": [*analysis.warnings, *supplement.warnings],
                    }
                )
            except ModelGatewayError:
                analysis = analysis.model_copy(
                    update={
                        "warnings": [
                            *analysis.warnings,
                            {
                                "warning_code": "FOCUS_SUPPLEMENT_UNAVAILABLE",
                                "message": (
                                    "The model could not add evidence-grounded claims for: "
                                    + ", ".join(missing_claim_types)
                                ),
                            },
                        ]
                    }
                )

        valid_chunk_ids = {item["chunk_id"] for item in bounded_context}
        if any(set(claim.chunk_ids) - valid_chunk_ids for claim in analysis.claims):
            raise ModelGatewayError(
                "MODEL_CHUNK_REFERENCE_INVALID",
                "The model referenced a Chunk outside the supplied context.",
            )
        return analysis

    @staticmethod
    def _missing_focus_claim_types(
        request: ReadingRequest,
        analysis: ReadingAnalysis,
    ) -> list[str]:
        claim_type_by_focus = {
            "RESEARCH_QUESTION": "RESEARCH_QUESTION",
            "METHOD": "METHOD",
            "EQUATION": "EQUATION_FIGURE",
            "FIGURE": "EQUATION_FIGURE",
            "TABLE": "EQUATION_FIGURE",
            "EXPERIMENT": "EXPERIMENT",
            "REPRODUCIBILITY": "EXPERIMENT",
            "INNOVATION": "INNOVATION",
            "LIMITATION": "LIMITATION",
        }
        existing = {claim.claim_type for claim in analysis.claims}
        requested: list[str] = []
        for focus in request.focus_aspects:
            claim_type = claim_type_by_focus[focus]
            if claim_type not in requested:
                requested.append(claim_type)
        return [claim_type for claim_type in requested if claim_type not in existing]

    def _supplement_missing_focus_claims(
        self,
        request: ReadingRequest,
        paper: PaperRecord,
        bounded_context: list[dict[str, Any]],
        analysis: ReadingAnalysis,
        missing_claim_types: list[str],
    ) -> ReadingAnalysis:
        payload = {
            "language": request.language,
            "reading_goal": request.reading_goal,
            "missing_claim_types": missing_claim_types,
            "paper": {"title": paper.title, "authors": paper.authors, "year": paper.year},
            "existing_claims": [claim.model_dump(mode="json") for claim in analysis.claims],
            "chunks": bounded_context,
        }
        request_body = {
            "model": self.model,
            "temperature": 0.1,
            "messages": [
                {"role": "system", "content": MISSING_FOCUS_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
        }
        if self.json_object_mode:
            request_body["response_format"] = {"type": "json_object"}
        try:
            response = self._post_chat_completion(
                "missing_focus_supplement",
                request_body,
            )
            body = response.json()
            content = _message_content(body)
            if not isinstance(content, str):
                raise ValueError("message content must be text")
            supplement = ReadingAnalysis.model_validate(
                _normalize_reading_analysis_payload(_json_content(content))
            )
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError, ValidationError) as exc:
            raise ModelGatewayError(
                "MODEL_FOCUS_SUPPLEMENT_FAILED",
                "The configured model did not return valid missing-focus claims.",
            ) from exc
        if any(claim.claim_type not in missing_claim_types for claim in supplement.claims):
            raise ModelGatewayError(
                "MODEL_FOCUS_SUPPLEMENT_SCOPE_INVALID",
                "Missing-focus analysis returned an unrequested claim type.",
            )
        return supplement

    def answer_question(
        self,
        question: str,
        paper: PaperRecord,
        context: list[dict[str, Any]],
        language: str,
    ) -> QuestionAnalysis:
        bounded_context = self._bounded_context(context)
        payload = {
            "question": question,
            "language": language,
            "paper": {"title": paper.title, "authors": paper.authors, "year": paper.year},
            "chunks": bounded_context,
        }
        request_body = {
            "model": self.model,
            "temperature": 0.1,
            "messages": [
                {"role": "system", "content": QA_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
        }
        if self.json_object_mode:
            request_body["response_format"] = {"type": "json_object"}
        try:
            response = self._post_chat_completion("question", request_body)
            body = response.json()
            content = _message_content(body)
            if not isinstance(content, str):
                raise ValueError("message content must be text")
            analysis = QuestionAnalysis.model_validate(_json_content(content))
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError, ValidationError) as exc:
            raise ModelGatewayError(
                "MODEL_QA_FAILED",
                "The configured model did not return a valid paper-scoped answer.",
            ) from exc

        valid_chunk_ids = {item["chunk_id"] for item in bounded_context}
        if set(analysis.chunk_ids) - valid_chunk_ids:
            raise ModelGatewayError(
                "MODEL_QA_CHUNK_REFERENCE_INVALID",
                "The answer referenced a Chunk outside the supplied paper context.",
            )
        return analysis

    def check_claim_support(
        self,
        claim: str,
        evidence: str,
        source: str,
    ) -> ClaimSupportCheck:
        payload = {
            "claim": claim,
            "evidence": evidence[:20_000],
            "claim_source": source,
        }
        request_body = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": CLAIM_SUPPORT_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
        }
        if self.json_object_mode:
            request_body["response_format"] = {"type": "json_object"}
        try:
            response = self._post_chat_completion("claim_support", request_body)
            body = response.json()
            content = _message_content(body)
            if not isinstance(content, str):
                raise ValueError("message content must be text")
            return ClaimSupportCheck.model_validate(_json_content(content))
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError, ValidationError) as exc:
            raise ModelGatewayError(
                "MODEL_CLAIM_SUPPORT_CHECK_FAILED",
                "The configured model did not return a valid bounded Claim support check.",
            ) from exc

    def analyze_scientific_elements(
        self,
        paper: PaperRecord,
        context: list[dict[str, Any]],
        language: str,
    ) -> ScientificElementAnalysis:
        bounded_context = self._bounded_context(context)
        payload = {
            "language": language,
            "paper": {"title": paper.title, "authors": paper.authors, "year": paper.year},
            "chunks": bounded_context,
        }
        request_body = {
            "model": self.model,
            "temperature": 0.1,
            "messages": [
                {"role": "system", "content": SCIENTIFIC_ELEMENTS_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
        }
        if self.json_object_mode:
            request_body["response_format"] = {"type": "json_object"}
        try:
            response = self._post_chat_completion(
                "scientific_elements",
                request_body,
            )
            body = response.json()
            content = _message_content(body)
            if not isinstance(content, str):
                raise ValueError("message content must be text")
            normalized = normalize_table_checks_payload(_json_content(content))
            normalized, discarded = discard_invalid_scientific_evidence_items(
                normalized,
                {item["chunk_id"] for item in bounded_context},
            )
            if discarded:
                logger.warning(
                    "Discarded %d optional scientific element(s) with invalid Evidence lineage",
                    discarded,
                )
            analysis = ScientificElementAnalysis.model_validate(normalized)
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError, ValidationError) as exc:
            raise ModelGatewayError(
                "MODEL_SCIENTIFIC_ELEMENT_ANALYSIS_FAILED",
                "The configured model did not return valid formula/figure/table analysis.",
            ) from exc

        context_by_id = {item["chunk_id"]: item for item in bounded_context}
        for element in analysis.elements:
            if set(element.chunk_ids) - set(context_by_id):
                raise ModelGatewayError(
                    "MODEL_SCIENTIFIC_ELEMENT_CHUNK_REFERENCE_INVALID",
                    "Scientific-element analysis referenced a Chunk outside the supplied context.",
                )
        return analysis

    def analyze_targeted_scientific_elements(
        self,
        paper: PaperRecord,
        context: list[dict[str, Any]],
        targets: list[ScientificElementTarget],
        language: str,
    ) -> ScientificElementAnalysis:
        if not targets:
            return ScientificElementAnalysis(elements=[])
        bounded_context = self._bounded_context(context)
        payload = {
            "language": language,
            "paper": {"title": paper.title, "authors": paper.authors, "year": paper.year},
            "targets": [target.model_dump(mode="json") for target in targets],
            "chunks": bounded_context,
        }
        request_body = {
            "model": self.model,
            "temperature": 0.1,
            "messages": [
                {"role": "system", "content": TARGETED_SCIENTIFIC_ELEMENTS_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
        }
        if self.json_object_mode:
            request_body["response_format"] = {"type": "json_object"}
        try:
            response = self._post_chat_completion(
                "targeted_scientific_elements",
                request_body,
            )
            body = response.json()
            content = _message_content(body)
            if not isinstance(content, str):
                raise ValueError("message content must be text")
            normalized = normalize_table_checks_payload(_json_content(content))
            normalized, discarded = discard_invalid_scientific_evidence_items(
                normalized,
                {item["chunk_id"] for item in bounded_context},
            )
            if discarded:
                logger.warning(
                    "Discarded %d optional targeted scientific element(s) with invalid Evidence lineage",
                    discarded,
                )
            analysis = ScientificElementAnalysis.model_validate(normalized)
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError, ValidationError) as exc:
            raise ModelGatewayError(
                "MODEL_TARGETED_SCIENTIFIC_ELEMENT_ANALYSIS_FAILED",
                "The configured model did not return valid targeted scientific-element analysis.",
            ) from exc

        context_ids = {item["chunk_id"] for item in bounded_context}
        targets_by_id = {target.object_id: target for target in targets}
        for element in analysis.elements:
            target = targets_by_id.get(element.document_object_id or "")
            if target is None:
                raise ModelGatewayError(
                    "MODEL_TARGETED_SCIENTIFIC_ELEMENT_REFERENCE_INVALID",
                    "Targeted analysis referenced an object outside the supplied targets.",
                )
            if (
                element.element_type != target.element_type
                or element.label.casefold() != target.label.casefold()
                or element.page != target.page
            ):
                raise ModelGatewayError(
                    "MODEL_TARGETED_SCIENTIFIC_ELEMENT_REFERENCE_INVALID",
                    "Targeted analysis changed the identity or location of a supplied object.",
                )
            if set(element.chunk_ids) - context_ids:
                raise ModelGatewayError(
                    "MODEL_SCIENTIFIC_ELEMENT_CHUNK_REFERENCE_INVALID",
                    "Scientific-element analysis referenced a Chunk outside the supplied context.",
                )
        return analysis

    def analyze_experiments(
        self,
        paper: PaperRecord,
        context: list[dict[str, Any]],
        language: str,
    ) -> ExperimentAnalysis:
        bounded_context = self._bounded_context(context)
        payload = {
            "language": language,
            "paper": {"title": paper.title, "authors": paper.authors, "year": paper.year},
            "chunks": bounded_context,
        }
        request_body = {
            "model": self.model,
            "temperature": 0.1,
            "messages": [
                {"role": "system", "content": EXPERIMENT_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
        }
        if self.json_object_mode:
            request_body["response_format"] = {"type": "json_object"}
        try:
            response = self._post_chat_completion("experiments", request_body)
            body = response.json()
            content = _message_content(body)
            if not isinstance(content, str):
                raise ValueError("message content must be text")
            raw_analysis, discarded_optional_items = discard_invalid_optional_evidence_items(
                _json_content(content)
            )
            analysis = ExperimentAnalysis.model_validate(raw_analysis)
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError, ValidationError) as exc:
            raise ModelGatewayError(
                "MODEL_EXPERIMENT_ANALYSIS_FAILED",
                "The configured model did not return valid experiment analysis.",
            ) from exc
        valid_chunk_ids = {item["chunk_id"] for item in bounded_context}
        analysis = self._normalize_ordinal_experiment_chunk_references(
            analysis,
            bounded_context,
        )
        normalized_analysis, discarded_invalid_items = (
            discard_invalid_optional_evidence_items(
                analysis.model_dump(mode="json"),
                valid_chunk_ids=valid_chunk_ids,
            )
        )
        discarded_optional_items += discarded_invalid_items
        analysis = ExperimentAnalysis.model_validate(normalized_analysis)
        invalid_chunk_ids = set(analysis.all_chunk_ids()) - valid_chunk_ids
        if invalid_chunk_ids:
            analysis = self._repair_experiment_chunk_references(
                analysis,
                invalid_chunk_ids,
                bounded_context,
                language,
            )
            repaired_analysis, discarded_repaired_items = (
                discard_invalid_optional_evidence_items(
                    analysis.model_dump(mode="json"),
                    valid_chunk_ids=valid_chunk_ids,
                )
            )
            discarded_optional_items += discarded_repaired_items
            analysis = ExperimentAnalysis.model_validate(repaired_analysis)
        if discarded_optional_items:
            logger.info(
                "Discarded %d optional reproducibility item(s) with empty or invalid Evidence lineage.",
                discarded_optional_items,
            )
        if set(analysis.all_chunk_ids()) - valid_chunk_ids:
            raise ModelGatewayError(
                "MODEL_EXPERIMENT_CHUNK_REFERENCE_INVALID",
                "Experiment analysis referenced a Chunk outside the supplied context.",
            )
        return analysis

    @staticmethod
    def _normalize_ordinal_experiment_chunk_references(
        analysis: ExperimentAnalysis,
        bounded_context: list[dict[str, Any]],
    ) -> ExperimentAnalysis:
        payload = analysis.model_dump(mode="json")

        def normalize(value: Any) -> Any:
            if isinstance(value, dict):
                normalized = {key: normalize(item) for key, item in value.items()}
                if "chunk_ids" in normalized and isinstance(normalized["chunk_ids"], list):
                    references = []
                    for chunk_id in normalized["chunk_ids"]:
                        match = re.fullmatch(r"chunk[-_](\d+)", str(chunk_id), re.IGNORECASE)
                        index = int(match.group(1)) if match else 0
                        if 1 <= index <= len(bounded_context):
                            references.append(bounded_context[index - 1]["chunk_id"])
                        else:
                            references.append(chunk_id)
                    normalized["chunk_ids"] = references
                return normalized
            if isinstance(value, list):
                return [normalize(item) for item in value]
            return value

        return ExperimentAnalysis.model_validate(normalize(payload))

    def _repair_experiment_chunk_references(
        self,
        analysis: ExperimentAnalysis,
        invalid_chunk_ids: set[str],
        bounded_context: list[dict[str, Any]],
        language: str,
    ) -> ExperimentAnalysis:
        repair_payload = {
            "language": language,
            "invalid_chunk_ids": sorted(invalid_chunk_ids),
            "analysis_to_correct": analysis.model_dump(mode="json"),
            "allowed_chunks": bounded_context,
        }
        request_body = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Return the complete experiment-analysis JSON object only. "
                        "Correct invalid chunk_ids using exact IDs from allowed_chunks and only "
                        "when the chunk text supports that item. Remove an unsupported item rather "
                        "than inventing evidence. Do not change supported content unnecessarily."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(repair_payload, ensure_ascii=False),
                },
            ],
        }
        if self.json_object_mode:
            request_body["response_format"] = {"type": "json_object"}
        try:
            response = self._post_chat_completion(
                "experiment_reference_repair",
                request_body,
            )
            body = response.json()
            content = _message_content(body)
            if not isinstance(content, str):
                raise ValueError("message content must be text")
            repaired_payload = _json_content(content)
            candidate = repaired_payload.get("analysis_to_correct", repaired_payload)
            return ExperimentAnalysis.model_validate(candidate)
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError, ValidationError) as exc:
            raise ModelGatewayError(
                "MODEL_EXPERIMENT_REFERENCE_REPAIR_FAILED",
                "The configured model could not repair invalid experiment Chunk references.",
            ) from exc

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": " ".join(("Bearer", self.api_key)),
            "Content-Type": "application/json",
        }

    def _post_chat_completion(
        self,
        request_kind: str,
        request_body: dict[str, Any],
    ) -> httpx.Response:
        effective_request_body = dict(request_body)
        effective_request_body.setdefault("max_tokens", 4096)
        if request_kind.endswith("_repair"):
            effective_request_body.pop("response_format", None)
        if self.enable_thinking is not None:
            effective_request_body["enable_thinking"] = self.enable_thinking
        if request_kind.endswith("_repair"):
            effective_request_body.pop("enable_thinking", None)

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
                # Several OpenAI-compatible gateways reject JSON mode or the
                # top-level thinking flag even though they support standard
                # Chat Completions. Retry with the portable request shape;
                # downstream JSON extraction still validates the contract.
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
            if response.status_code == 400 and "max_tokens" in response.text.lower():
                fallback_body = dict(effective_request_body)
                fallback_body["max_completion_tokens"] = fallback_body.pop("max_tokens")
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

    def _bounded_context(self, context: list[dict[str, Any]]) -> list[dict[str, Any]]:
        selected: list[dict[str, Any]] = []
        remaining = self.max_context_characters
        for item in context:
            text = str(item.get("text", ""))
            if not text:
                continue
            if selected and remaining <= 0:
                break
            kept_text = text[:remaining] if remaining < len(text) else text
            if not kept_text:
                break
            selected.append(
                {
                    "chunk_id": item["chunk_id"],
                    "page": item.get("page"),
                    "section": item.get("section"),
                    "text": kept_text,
                }
            )
            remaining -= len(kept_text)
        if not selected:
            raise ModelGatewayError("MODEL_CONTEXT_EMPTY", "No readable Chunk context was supplied.")
        return selected
