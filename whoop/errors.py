"""
Typed errors shared by auth, HTTP client, CLI and MCP.
Messages must never contain tokens, secrets or health data.
"""

import time
from datetime import timezone
from email.utils import parsedate_to_datetime
from enum import Enum


class ErrorCode(str, Enum):
    AUTH_REQUIRED = "AUTH_REQUIRED"  # no tokens stored: explicit login needed
    REAUTH_REQUIRED = "REAUTH_REQUIRED"  # refresh token rejected / consent revoked
    INSUFFICIENT_SCOPE = "INSUFFICIENT_SCOPE"  # 403 or missing granted scope
    RATE_LIMITED = "RATE_LIMITED"  # 429 from WHOOP
    TRANSIENT_ERROR = "TRANSIENT_ERROR"  # timeout, network, 5xx, lock wait
    TOKEN_STORE_ERROR = "TOKEN_STORE_ERROR"  # unreadable/undecryptable token file
    CONFIG_ERROR = "CONFIG_ERROR"  # wrong client credentials / env
    INCOMPLETE_DATA = "INCOMPLETE_DATA"  # pagination stopped before the end
    API_ERROR = "API_ERROR"  # any other WHOOP API failure


# Hint shown to the owner (CLI) or to the agent (MCP) for each code.
ACTION_HINTS: dict[ErrorCode, str] = {
    ErrorCode.AUTH_REQUIRED: "Owner must log in to WHOOP: `whoop auth login-headless` on the host.",
    ErrorCode.REAUTH_REQUIRED: "WHOOP authorization is no longer valid. Owner must re-login: `whoop auth login-headless` on the host.",
    ErrorCode.INSUFFICIENT_SCOPE: "Owner must re-login to grant all required WHOOP scopes.",
    ErrorCode.RATE_LIMITED: "WHOOP rate limit reached. Retry later.",
    ErrorCode.TRANSIENT_ERROR: "Temporary failure. Retry later; tokens were not changed.",
    ErrorCode.TOKEN_STORE_ERROR: "Token file could not be read (wrong encryption key or damaged file). The file was preserved; check the key.",
    ErrorCode.CONFIG_ERROR: "Check WHOOP client configuration (client id/secret, env).",
    ErrorCode.INCOMPLETE_DATA: "Result would be incomplete; narrow the date range.",
    ErrorCode.API_ERROR: "WHOOP API request failed.",
}


class WhoopError(Exception):
    def __init__(
        self,
        code: ErrorCode,
        message: str,
        retry_after: float | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.retry_after = retry_after
        super().__init__(f"{code.value}: {message}")

    @property
    def hint(self) -> str:
        return ACTION_HINTS[self.code]

    def to_dict(self) -> dict:
        data = {"code": self.code.value, "message": self.message, "hint": self.hint}
        if self.retry_after is not None:
            data["retry_after_seconds"] = self.retry_after
        return data


def parse_retry_after(value: str | None) -> float | None:
    """Parse a Retry-After header (delta-seconds or HTTP-date) into seconds."""
    if not value:
        return None
    value = value.strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:  # "-0000" means UTC per RFC 5322
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, when.timestamp() - time.time())
