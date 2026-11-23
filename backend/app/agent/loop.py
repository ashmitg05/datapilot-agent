"""The agent loop, written by hand on top of native tool calling.

question -> LLM picks tool(s) -> execute -> append observation -> repeat
until final_answer, max_steps, too many failed retries, or the cost cap.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

from app.agent.prompts import VERIFY_PROMPT, error_hint, system_prompt
from app.agent.state import AgentConfig, AgentState, Step
from app.llm import LLMClient, Pricing
from app.tools import TOOLS, ToolContext, execute, tool_specs
from app.tools.result import ToolResult

Event = dict[str, Any]


def _tools_for(config: AgentConfig) -> list[dict[str, Any]]:
    skip = set()
    if not config.schema_inspection:
        skip.add("describe_schema")
    if not config.allow_python:
        skip.add("run_python")
    return [t for t in tool_specs() if t["name"] not in skip]


def _observation(result: ToolResult) -> str:
    if result.ok:
        extra = f"\n[{len(result.images)} chart(s) created]" if result.images else ""
        return result.output + extra
    return result.error or "error"


def run_agent(
    question: str,
    llm: LLMClient,
    ctx: ToolContext,
    config: AgentConfig | None = None,
    pricing: Pricing | None = None,
    on_event: Callable[[Event], None] | None = None,
) -> AgentState:
    config = config or AgentConfig()
    pricing = pricing or Pricing()
    emit = on_event or (lambda e: None)
    state = AgentState(question=question)
    state.messages.append({"role": "user", "text": question})
    system = system_prompt(config, sorted(ctx.db.tables))
    tools = _tools_for(config)
    allowed = {t["name"] for t in tools}
    consecutive_errors = 0
    pending_error = False  # an error not yet followed by a success

    while state.status == "running":
        if len(state.steps) >= config.max_steps:
            state.status = "max_steps"
            break
        resp = llm.complete(system, state.messages, tools)
        state.input_tokens += resp.input_tokens
        state.output_tokens += resp.output_tokens
        state.cost_usd = pricing.cost(state.input_tokens, state.output_tokens)
        state.messages.append(
            {
                "role": "assistant",
                "text": resp.text,
                "tool_calls": [
                    {"id": c.id, "name": c.name, "args": c.args, "extra": c.extra}
                    for c in resp.tool_calls
                ],
            }
        )
        if not resp.tool_calls:
            # Model replied in prose; nudge it back to the protocol (counts as a step).
            state.steps.append(
                Step(
                    len(state.steps),
                    resp.text[:500],
                    "(none)",
                    {},
                    False,
                    "",
                    "no tool call",
                    0,
                    resp.input_tokens,
                    resp.output_tokens,
                )  # fmt: skip
            )
            state.messages.append(
                {"role": "user", "text": "Use the tools. Finish by calling final_answer."}
            )
            continue

        results: list[dict[str, Any]] = []
        tokens_split = len(resp.tool_calls)
        for call in resp.tool_calls:
            t0 = time.monotonic()
            if call.name not in allowed:
                result = ToolResult.failure(f"Tool '{call.name}' is not available.")
            elif call.name == "final_answer" and config.verification and not state.verified:
                state.verified = True
                result = execute(ctx, call.name, call.args)
                if result.ok:
                    result = ToolResult.failure(VERIFY_PROMPT)
            else:
                result = execute(ctx, call.name, call.args)
            step = Step(
                index=len(state.steps),
                thought=resp.text[:500],
                tool=call.name,
                input=call.args,
                ok=result.ok,
                output=result.output,
                error=result.error,
                duration_ms=int((time.monotonic() - t0) * 1000),
                input_tokens=resp.input_tokens // tokens_split,
                output_tokens=resp.output_tokens // tokens_split,
                images=result.images,
            )
            state.steps.append(step)
            emit({"type": "step", "step": step.__dict__})

            is_verify_nudge = result.error == VERIFY_PROMPT
            if result.ok:
                if pending_error:
                    state.recovered_errors += 1
                    pending_error = False
                consecutive_errors = 0
                if call.name == "final_answer":
                    state.final = json.loads(result.output)
                    state.status = "answered"
                content = _observation(result)
            elif is_verify_nudge:
                content = VERIFY_PROMPT
            else:
                state.errors += 1
                pending_error = True
                consecutive_errors += 1
                if not config.self_correction:
                    state.status = "failed"
                    content = _observation(result)
                elif consecutive_errors > config.max_retries:
                    state.status = "failed"
                    content = _observation(result)
                else:
                    content = error_hint(
                        _observation(result), consecutive_errors, config.max_retries
                    )
            results.append({"id": call.id, "content": content, "is_error": not result.ok})
        state.messages.append({"role": "tool", "results": results})

        if config.cost_cap_usd is not None and state.cost_usd > config.cost_cap_usd:
            if state.status == "running":
                state.status = "cost_cap"

    state.elapsed_s = time.monotonic() - state.started
    emit({"type": "done", "final": state.final, "summary": state.summary()})
    return state


def single_shot(question: str, llm: LLMClient, ctx: ToolContext) -> AgentState:
    """Ablation baseline: one LLM call writes one SQL query; its result is the answer."""
    from app.tools.sql import RunSQLInput, run_sql

    state = AgentState(question=question)
    system = (
        "Write exactly one DuckDB SQL query that answers the question. "
        f"Tables: {', '.join(sorted(ctx.db.tables))}. Reply with only the SQL, no prose."
    )
    resp = llm.complete(system, [{"role": "user", "text": question}], [])
    state.input_tokens, state.output_tokens = resp.input_tokens, resp.output_tokens
    sql = resp.text.strip().removeprefix("```sql").removeprefix("```").removesuffix("```").strip()
    result = run_sql(ctx.db, RunSQLInput(query=sql))
    state.steps.append(
        Step(
            0,
            "",
            "run_sql",
            {"query": sql},
            result.ok,
            result.output,
            result.error,
            0,
            resp.input_tokens,
            resp.output_tokens,
        )  # fmt: skip
    )
    if result.ok:
        state.status = "answered"
        state.final = {"answer": result.output, "value": _scalar_or_rows(result.output, ctx, sql),
                       "answerable": True, "assumptions": [], "evidence": [sql]}  # fmt: skip
    else:
        state.errors = 1
        state.status = "failed"
    state.elapsed_s = time.monotonic() - state.started
    return state


def _scalar_or_rows(_: str, ctx: ToolContext, sql: str) -> Any:
    cols, rows, _more = ctx.db.query(sql)
    if len(rows) == 1 and len(cols) == 1:
        return rows[0][0]
    if len(cols) == 1:
        return [r[0] for r in rows]
    return [list(r) for r in rows]


__all__ = ["TOOLS", "run_agent", "single_shot"]
