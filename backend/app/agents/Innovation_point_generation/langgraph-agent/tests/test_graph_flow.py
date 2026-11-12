from innovation_mining import InnovationRequest


def test_innovation_request_normalizes_unknown_mode() -> None:
    request = InnovationRequest(research_domain="test", mode="unknown")
    assert request.normalized_mode() == "full"
