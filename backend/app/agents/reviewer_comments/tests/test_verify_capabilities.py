"""verify_capabilities 独立探针命令离线测试（自 backend/tests 迁入）。"""

from __future__ import annotations

from langgraph_agent.llm import verify_capabilities


def test_probe_command_prints_matrix_without_business_data(monkeypatch, capsys):
    class FakeSettings:
        def validate(self):
            return None

        def require_llm(self):
            return None

    monkeypatch.setattr(verify_capabilities, "get_settings", lambda: FakeSettings())

    class FakeEngine:
        def probe_capabilities(self, purpose, schema, messages, *, timeout_seconds=None):
            assert schema is verify_capabilities.CapabilityProbe
            assert "business data" in messages[0]["content"]
            return [
                {
                    "purpose": purpose,
                    "model": f"model-{purpose}",
                    "endpoint_host": "example.test",
                    "strategy": "native_json_schema",
                    "status": "supported",
                    "detail": "validated",
                    "tool_name": "CapabilityProbe",
                }
            ]

    monkeypatch.setattr(verify_capabilities, "StructuredOutputEngine", FakeEngine)

    assert verify_capabilities.main() == 0
    output = capsys.readouterr().out
    assert "purpose" in output
    assert "native_json_schema" in output
    assert "model-paper_card" in output
    assert "reviewer" not in output.casefold()


def test_probe_command_reports_configuration_error(monkeypatch, capsys):
    class BrokenSettings:
        def require_llm(self):
            raise RuntimeError("缺少 LLM_API_KEY")

    monkeypatch.setattr(verify_capabilities, "get_settings", lambda: BrokenSettings())
    assert verify_capabilities.main() == 1
    output = capsys.readouterr().out
    assert "configuration error" in output
    assert "LLM_API_KEY" in output
