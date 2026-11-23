"""Provider-agnostic LLM client with native tool calling.

The agent only sees these neutral types. Each adapter translates them to its provider's
wire format. Message shapes (plain dicts, so they serialize straight into traces):

    {"role": "user", "text": str}
    {"role": "assistant", "text": str, "tool_calls": [{"id", "name", "args"}]}
    {"role": "tool", "results": [{"id", "content", "is_error"}]}
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

ENV_FILE = Path(__file__).resolve().parents[3] / ".env"


def load_env(path: Path = ENV_FILE) -> None:
    """Read KEY=VALUE lines from the repo-root .env. Variables already set always win."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key and not key.startswith("#"):
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


load_env()


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict[str, Any]
    # Provider data that must be echoed back unchanged, e.g. Gemini's thought_signature.
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMResponse:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0


class LLMClient(Protocol):
    model: str

    def complete(
        self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> LLMResponse: ...


@dataclass(frozen=True)
class Pricing:
    """USD per million tokens. Read from env, never guessed: cost is 0 when unset."""

    input_per_mtok: float = 0.0
    output_per_mtok: float = 0.0

    @classmethod
    def from_env(cls, prefix: str = "LLM") -> Pricing:
        return cls(
            float(os.environ.get(f"{prefix}_PRICE_IN_PER_MTOK", "0") or 0),
            float(os.environ.get(f"{prefix}_PRICE_OUT_PER_MTOK", "0") or 0),
        )

    def cost(self, input_tokens: int, output_tokens: int) -> float:
        return (input_tokens * self.input_per_mtok + output_tokens * self.output_per_mtok) / 1e6


class AnthropicClient:
    def __init__(self, model: str, api_key: str | None = None, max_tokens: int = 2048) -> None:
        import anthropic

        self.model = model
        self.max_tokens = max_tokens
        self._client = anthropic.Anthropic(api_key=api_key)

    def complete(
        self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> LLMResponse:
        wire: list[dict[str, Any]] = []
        for m in messages:
            if m["role"] == "user":
                wire.append({"role": "user", "content": m["text"]})
            elif m["role"] == "assistant":
                content: list[dict[str, Any]] = []
                if m["text"]:
                    content.append({"type": "text", "text": m["text"]})
                content += [
                    {"type": "tool_use", "id": c["id"], "name": c["name"], "input": c["args"]}
                    for c in m["tool_calls"]
                ]
                wire.append({"role": "assistant", "content": content})
            else:
                wire.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": r["id"],
                                "content": r["content"],
                                "is_error": r["is_error"],
                            }
                            for r in m["results"]
                        ],
                    }
                )
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=wire,  # type: ignore[arg-type]
            tools=tools,  # type: ignore[arg-type]
        )
        text = "".join(b.text for b in resp.content if b.type == "text")
        calls = [
            ToolCall(b.id, b.name, dict(b.input)) for b in resp.content if b.type == "tool_use"
        ]
        return LLMResponse(text, calls, resp.usage.input_tokens, resp.usage.output_tokens)


class _Pacer:
    """Spaces calls to stay under a requests-per-minute quota (LLM_RPM; 0 = off).

    ponytail: one lock per process, so one client's pace is shared by all threads; per-model
    pacing if several models share a process.
    """

    def __init__(self, rpm: float) -> None:
        self.interval = 60.0 / rpm if rpm > 0 else 0.0
        self._next = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        if not self.interval:
            return
        with self._lock:
            now = time.monotonic()
            start = max(now, self._next)
            self._next = start + self.interval
        time.sleep(max(0.0, start - now))


class OpenAIClient:
    """Also works with any OpenAI-compatible endpoint via base_url (Gemini, Groq, vLLM...)."""

    def __init__(self, model: str, api_key: str | None = None, base_url: str | None = None) -> None:
        import openai

        self.model = model
        # Free tiers return 429/503 often; the SDK retries with backoff.
        self._client = openai.OpenAI(api_key=api_key, base_url=base_url, max_retries=5)
        self._pacer = _Pacer(float(os.environ.get("LLM_RPM", "0") or 0))

    def complete(
        self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> LLMResponse:
        wire: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for m in messages:
            if m["role"] == "user":
                wire.append({"role": "user", "content": m["text"]})
            elif m["role"] == "assistant":
                msg: dict[str, Any] = {"role": "assistant", "content": m["text"] or None}
                if m["tool_calls"]:
                    msg["tool_calls"] = [
                        {
                            "id": c["id"],
                            "type": "function",
                            "function": {"name": c["name"], "arguments": json.dumps(c["args"])},
                            **c.get("extra", {}),
                        }
                        for c in m["tool_calls"]
                    ]
                wire.append(msg)
            else:
                wire += [
                    {"role": "tool", "tool_call_id": r["id"], "content": r["content"]}
                    for r in m["results"]
                ]
        self._pacer.wait()
        resp = self._client.chat.completions.create(
            model=self.model,
            messages=wire,  # type: ignore[arg-type]
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t["description"],
                        "parameters": t["input_schema"],
                    },
                }
                for t in tools
            ],
        )
        choice = resp.choices[0].message
        calls = []
        for c in choice.tool_calls or []:
            try:
                args = json.loads(c.function.arguments or "{}")  # type: ignore[union-attr]
            except json.JSONDecodeError:
                args = {"_raw": c.function.arguments}  # type: ignore[union-attr]
            calls.append(ToolCall(c.id, c.function.name, args, dict(c.model_extra or {})))  # type: ignore[union-attr]
        usage = resp.usage
        return LLMResponse(
            choice.content or "",
            calls,
            usage.prompt_tokens if usage else 0,
            usage.completion_tokens if usage else 0,
        )


class ScriptedClient:
    """Test double: returns pre-scripted responses in order. No network, no key."""

    def __init__(self, responses: list[LLMResponse], model: str = "scripted") -> None:
        self.model = model
        self._responses = list(responses)
        self.calls: list[list[dict[str, Any]]] = []

    def complete(
        self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> LLMResponse:
        self.calls.append(json.loads(json.dumps(messages)))
        if not self._responses:
            return LLMResponse("out of script")
        return self._responses.pop(0)


def from_env(model: str | None = None) -> LLMClient:
    """LLM_PROVIDER=anthropic|openai, LLM_MODEL, LLM_API_KEY, optional LLM_BASE_URL."""
    provider = os.environ.get("LLM_PROVIDER", "anthropic").lower()
    model = model or os.environ.get("LLM_MODEL", "")
    if not model:
        raise RuntimeError("Set LLM_MODEL (see .env.example).")
    key = os.environ.get("LLM_API_KEY") or None
    if provider == "anthropic":
        return AnthropicClient(model, key)
    if provider == "openai":
        return OpenAIClient(model, key, os.environ.get("LLM_BASE_URL") or None)
    raise RuntimeError(f"Unknown LLM_PROVIDER {provider!r}")
