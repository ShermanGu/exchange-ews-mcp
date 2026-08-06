class ExchangeMcpError(RuntimeError):
    """Base exception for this project."""


class ConfigurationError(ExchangeMcpError):
    """Configuration is missing or invalid."""


class CredentialError(ExchangeMcpError):
    """Credential storage or lookup failed."""


class EwsError(ExchangeMcpError):
    """EWS returned an error response."""

    def __init__(self, message: str, *, response_code: str | None = None) -> None:
        super().__init__(message)
        self.response_code = response_code
