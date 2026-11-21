"""Regression checks for the paper reader's model response handling."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app/agents/paper_reading/runtime/agent-core/src"))
from llm.openai_compatible import ModelGatewayError, OpenAICompatibleModelGateway, _message_content
from llm.gateway import ReadingAnalysis


def _gateway(handler, url="https://api.deepseek.com/v1"):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return OpenAICompatibleModelGateway(url, "test", "test", client=client, enable_thinking=False)


def test_deepseek_repair_requests_disable_thinking_and_keep_json():
    seen = []
    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})
    gateway = _gateway(handler)
    gateway._post_chat_completion("base_analysis_repair", {"model": "test", "messages": [], "response_format": {"type": "json_object"}})
    assert seen[0]["thinking"] == {"type": "disabled"}
    assert seen[0]["response_format"] == {"type": "json_object"}
    assert seen[0]["max_tokens"] == 8192


def test_reasoning_is_not_treated_as_answer():
    with pytest.raises(ValueError, match="no text"):
        _message_content({"choices": [{"message": {"content": "", "reasoning_content": "not an answer"}}]})


def test_provider_fallback_retains_prior_compatibility_adjustments():
    seen = []
    def handler(request):
        seen.append(json.loads(request.content))
        if len(seen) == 1:
            return httpx.Response(400, text="unsupported response_format")
        if len(seen) == 2:
            return httpx.Response(400, text="use max_completion_tokens instead of max_tokens")
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})
    gateway = _gateway(handler, "https://example.test/v1")
    gateway._post_chat_completion("base_analysis", {"model": "test", "messages": [], "response_format": {"type": "json_object"}})
    assert len(seen) == 3
    assert "response_format" not in seen[2] and "enable_thinking" not in seen[2]
    assert seen[2]["max_completion_tokens"] == 8192 and "max_tokens" not in seen[2]


def test_truncated_response_is_retried_without_inventing_claims():
    valid = {"claims": [{"claim_id": "c1", "claim_type": "METHOD", "claim_source": "AUTHOR_STATED", "content": "supported", "chunk_ids": ["chunk1"]}]}
    responses = [
        {"choices": [{"finish_reason": "length", "message": {"content": json.dumps(valid)}}]},
        {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(valid)}}]},
    ]
    seen = []
    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=responses.pop(0))
    gateway = _gateway(handler)
    request = SimpleNamespace(depth="OVERVIEW", reading_goal="read", focus_aspects=[], language="zh-CN")
    paper = SimpleNamespace(title="test", authors=["author"], year=2024)
    result = gateway.analyze_paper(request, paper, [{"chunk_id": "chunk1", "text": "source"}])
    assert len(seen) == 2
    assert len(result.claims) == 1 and result.claims[0].chunk_ids == ["chunk1"]
    assert any(w.warning_code == "BASE_ANALYSIS_REPAIRED" for w in result.warnings)


def test_repeated_invalid_responses_fail_explicitly():
    seen = []
    def handler(request):
        seen.append(1)
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": " "}}]})
    gateway = _gateway(handler)
    request = SimpleNamespace(depth="OVERVIEW", reading_goal="read", focus_aspects=[], language="zh-CN")
    paper = SimpleNamespace(title="test", authors=["author"], year=2024)
    with pytest.raises(ModelGatewayError):
        gateway.analyze_paper(request, paper, [{"chunk_id": "chunk1", "text": "source"}])
    assert len(seen) == 3


def test_invalid_evidence_is_discarded_and_not_rebound():
    claims = [
        {"claim_id": "good", "claim_type": "METHOD", "claim_source": "AUTHOR_STATED", "content": "supported", "chunk_ids": ["chunk1"]},
        {"claim_id": "bad", "claim_type": "METHOD", "claim_source": "AUTHOR_STATED", "content": "unsupported", "chunk_ids": ["missing"]},
    ]
    result = OpenAICompatibleModelGateway._discard_invalid_claims(ReadingAnalysis.model_validate({"claims": claims}), {"chunk1"})
    assert [claim.claim_id for claim in result.claims] == ["good"]
    assert result.warnings[0].warning_code == "UNSUPPORTED_CLAIMS_DISCARDED"
    with pytest.raises(ValueError, match="no claims"):
        OpenAICompatibleModelGateway._discard_invalid_claims(ReadingAnalysis.model_validate({"claims": [claims[1]]}), {"chunk1"})
