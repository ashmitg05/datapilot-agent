"""Agent loop behaviour with a scripted LLM (no network)."""

from pathlib import Path

import pytest

from app.agent.loop import run_agent, single_shot
from app.agent.prompts import VERIFY_PROMPT
from app.agent.state import AgentConfig
from app.llm import LLMResponse, Pricing, ScriptedClient, ToolCall
from app.tools import ToolContext
from app.tools.sql import Database
from app.tracing import TraceStore


def call(name: str, i: int = 0, **args: object) -> LLMResponse:
    return LLMResponse("", [ToolCall(f"c{i}", name, dict(args))], 100, 20)


FINAL = {"answer": "East", "value": "East", "evidence": ["sum=510"]}
TOP_SQL = "SELECT region FROM sales GROUP BY 1 ORDER BY sum(revenue) DESC LIMIT 1"


def test_happy_path(db: Database) -> None:
    llm = ScriptedClient(
        [
            call("describe_schema", 0, table="sales"),
            call("run_sql", 1, query=TOP_SQL),
            call("final_answer", 2, **FINAL),
        ]  # fmt: skip
    )
    events: list[dict[str, object]] = []
    state = run_agent("top region?", llm, ToolContext(db), on_event=events.append)
    assert state.status == "answered"
    assert state.final and state.final["value"] == "East"
    assert len(state.steps) == 3 and state.input_tokens == 300
    assert events[-1]["type"] == "done"
    # The SQL result was fed back as an observation.
    assert "East" in llm.calls[2][-1]["results"][0]["content"]


def test_self_correction_recovers(db: Database) -> None:
    llm = ScriptedClient(
        [
            call("run_sql", 0, query="SELECT regon FROM sales"),
            call("run_sql", 1, query=TOP_SQL),
            call("final_answer", 2, **FINAL),
        ]  # fmt: skip
    )
    state = run_agent("q", llm, ToolContext(db))
    assert state.status == "answered"
    assert state.errors == 1 and state.recovered_errors == 1
    hint = llm.calls[1][-1]["results"][0]["content"]
    assert "Hint:" in hint and "attempt 1/3" in hint


def test_no_self_correction_stops_on_error(db: Database) -> None:
    llm = ScriptedClient([call("run_sql", 0, query="SELECT regon FROM sales")])
    state = run_agent("q", llm, ToolContext(db), AgentConfig(self_correction=False))
    assert state.status == "failed" and state.final is None


def test_retry_cap(db: Database) -> None:
    bad = [call("run_sql", i, query="SELECT nope FROM sales") for i in range(5)]
    state = run_agent("q", ScriptedClient(bad), ToolContext(db), AgentConfig(max_retries=3))
    assert state.status == "failed" and state.errors == 4


def test_max_steps(db: Database) -> None:
    loop = [call("run_sql", i, query="SELECT 1 FROM sales") for i in range(20)]
    state = run_agent("q", ScriptedClient(loop), ToolContext(db), AgentConfig(max_steps=4))
    assert state.status == "max_steps" and len(state.steps) == 4


def test_verification_forces_second_check(db: Database) -> None:
    llm = ScriptedClient(
        [
            call("final_answer", 0, **FINAL),
            call("run_sql", 1, query=TOP_SQL),
            call("final_answer", 2, **FINAL),
        ]  # fmt: skip
    )
    state = run_agent("q", llm, ToolContext(db), AgentConfig(verification=True))
    assert state.status == "answered" and state.verified and len(state.steps) == 3
    assert llm.calls[1][-1]["results"][0]["content"] == VERIFY_PROMPT
    assert state.errors == 0


def test_schema_tool_removed_when_ablated(db: Database) -> None:
    llm = ScriptedClient([call("describe_schema", 0, table="sales")])
    state = run_agent("q", llm, ToolContext(db), AgentConfig(self_correction=False,
                                                              schema_inspection=False))  # fmt: skip
    assert state.steps[0].error and "not available" in state.steps[0].error


def test_cost_cap(db: Database) -> None:
    loop = [call("run_sql", i, query="SELECT 1 FROM sales") for i in range(10)]
    state = run_agent(
        "q", ScriptedClient(loop), ToolContext(db), AgentConfig(cost_cap_usd=0.0005),
        Pricing(input_per_mtok=3.0, output_per_mtok=15.0),
    )  # fmt: skip
    assert state.status == "cost_cap" and len(state.steps) == 1  # 100*3e-6 + 20*15e-6 = 0.0006


def test_prose_reply_is_nudged(db: Database) -> None:
    llm = ScriptedClient([LLMResponse("I think East."), call("final_answer", 1, **FINAL)])
    state = run_agent("q", llm, ToolContext(db))
    assert state.status == "answered" and len(state.steps) == 2


def test_single_shot(db: Database) -> None:
    llm = ScriptedClient([LLMResponse(f"```sql\n{TOP_SQL}\n```")])
    state = single_shot("q", llm, ToolContext(db))
    assert state.final and state.final["value"] == "East"


def test_trace_roundtrip(db: Database, tmp_path: Path) -> None:
    llm = ScriptedClient([call("final_answer", 0, **FINAL)])
    state = run_agent("q", llm, ToolContext(db))
    store = TraceStore(tmp_path / "t.sqlite3")
    trace = store.get(store.save(state, "sales", "scripted", AgentConfig()))
    assert trace and trace["status"] == "answered" and trace["steps"][0]["tool"] == "final_answer"
    assert store.get("missing") is None


def test_load_env_does_not_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.llm import load_env

    env = tmp_path / ".env"
    env.write_text("# comment\nDP_A=from_file\nDP_B='quoted'\nDP_C=keep\n")
    monkeypatch.setenv("DP_C", "already_set")
    monkeypatch.delenv("DP_A", raising=False)
    monkeypatch.delenv("DP_B", raising=False)
    load_env(env)
    import os

    assert os.environ["DP_A"] == "from_file" and os.environ["DP_B"] == "quoted"
    assert os.environ["DP_C"] == "already_set"
