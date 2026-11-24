"""The one error type tools raise. The server turns it into an MCP error result."""

from typing import Any


class ToolFailure(Exception):
    """A clear, expected failure: `error` is a short code, `detail` says what to do.

    Never build `detail` from exception text of a library (it can repeat inputs) or from
    credentials. The server scrubs secrets from every result anyway, as a second guard.
    """

    def __init__(self, error: str, detail: str, **extra: Any) -> None:
        super().__init__(f"{error}: {detail}")
        self.error = error
        self.detail = detail
        self.extra = extra

    def as_dict(self) -> dict[str, Any]:
        return {"error": self.error, "detail": self.detail, **self.extra}


def not_configured(*names: str) -> ToolFailure:
    return ToolFailure(
        "not_configured",
        f"not configured: set {', '.join(names)} in the environment Claude Code starts in "
        "(see the README, 'Use the simulator from Claude Code')",
    )
