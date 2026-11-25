"""Agent configuration (ablation switches) and run state."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class AgentConfig:
    schema_inspection: bool = True
    self_correction: bool = True
    verification: bool = False
    allow_python: bool = True
    max_steps: int = 10
    max_retries: int = 3  # consecutive failed tool calls tolerated
    cost_cap_usd: float | None = None


@dataclass
class Step:
    index: int
    thought: str
    tool: str
    input: dict[str, Any]
    ok: bool
    output: str
    error: str | None
    duration_ms: int
    input_tokens: int
    output_tokens: int
    images: list[str] = field(default_factory=list)


@dataclass
class AgentState:
    question: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)
    errors: int = 0
    recovered_errors: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    started: float = field(default_factory=time.monotonic)
    elapsed_s: float = 0.0
    verified: bool = False
    status: str = "running"  # running | answered | max_steps | failed | cost_cap | crashed
    crash: str | None = None  # repr of a provider/tool exception that ended the run
    final: dict[str, Any] | None = None

    def summary(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "steps": len(self.steps),
            "errors": self.errors,
            "recovered_errors": self.recovered_errors,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": round(self.cost_usd, 6),
            "elapsed_s": round(self.elapsed_s, 3),
            "verified": self.verified,
        }
