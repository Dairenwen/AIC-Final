class PatentAgentError(Exception):
    exit_code = 70


class ConfigurationError(PatentAgentError):
    exit_code = 20


class ModelError(PatentAgentError):
    exit_code = 21


class ParseError(PatentAgentError):
    exit_code = 22


class DisclosureSemanticContractError(ParseError):
    error_type = "semantic_contract_error"

    def __init__(self, missing_sections: list[str], empty_sections: list[str] | None = None):
        self.missing_sections = missing_sections
        self.empty_sections = empty_sections or []
        detail = ", ".join(missing_sections)
        super().__init__(f"generated disclosure is missing required semantic sections: {detail}")


class DisclosureSectionRecoveryError(ParseError):
    error_type = "section_recovery_failed"

    def __init__(
        self,
        message: str,
        *,
        missing_sections_before: list[str],
        missing_sections_after: list[str] | None = None,
    ):
        self.missing_sections_before = list(missing_sections_before)
        self.missing_sections_after = list(
            self.missing_sections_before
            if missing_sections_after is None
            else missing_sections_after
        )
        super().__init__(message)


class MarkdownNormalizationError(ParseError):
    error_type = "ambiguous_markdown_fence"

    def __init__(
        self,
        message: str,
        *,
        fence_count: int,
        first_fence_line: int,
        raw_sha256: str,
    ):
        self.fence_count = fence_count
        self.first_fence_line = first_fence_line
        self.raw_sha256 = raw_sha256
        super().__init__(message)


class SearchError(PatentAgentError):
    exit_code = 23


class ExportError(PatentAgentError):
    exit_code = 25


class InputChangedError(PatentAgentError):
    exit_code = 20


class InternalError(PatentAgentError):
    exit_code = 70


WAITING_FOR_INPUT = 10
