"""Uniform tool result returned to the agent loop."""

from __future__ import annotations

from pydantic import BaseModel, Field

MAX_CHARS = 4000


def truncate(text: str, limit: int = MAX_CHARS) -> tuple[str, bool]:
    """Keep head and tail so the LLM sees both the start and the final error line."""
    if len(text) <= limit:
        return text, False
    half = limit // 2
    cut = len(text) - 2 * half
    return f"{text[:half]}\n... [{cut} chars truncated] ...\n{text[-half:]}", True


class ToolResult(BaseModel):
    ok: bool
    output: str = ""
    error: str | None = None
    truncated: bool = False
    images: list[str] = Field(default_factory=list, description="base64 PNGs")

    @classmethod
    def success(cls, output: str, images: list[str] | None = None) -> ToolResult:
        text, cut = truncate(output)
        return cls(ok=True, output=text, truncated=cut, images=images or [])

    @classmethod
    def failure(cls, error: str, output: str = "") -> ToolResult:
        err, cut1 = truncate(error)
        out, cut2 = truncate(output)
        return cls(ok=False, error=err, output=out, truncated=cut1 or cut2)
