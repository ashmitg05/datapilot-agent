"""Tool registry: JSON schemas for the LLM plus a single validated dispatch entry point."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.sandbox.executor import Executor
from app.tools.chart import ChartSpec, make_chart
from app.tools.result import ToolResult
from app.tools.sql import Database, DescribeSchemaInput, RunSQLInput, describe_schema, run_sql


@dataclass
class ToolContext:
    db: Database
    executor: Executor | None = None  # None = SQL-only mode


class RunPythonInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(
        description="Python code. The dataset is preloaded as `df` (pandas). Use print() for "
        "output; matplotlib figures are returned automatically."
    )


class MakeChartInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    spec: ChartSpec


class FinalAnswerInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer: str = Field(description="Clear answer to the question, stating any assumptions.")
    value: float | str | bool | list[Any] | None = Field(
        default=None,
        description="Machine-readable answer: a number, a string, true/false, a list of values "
        "(or of rows for tables). null if the question cannot be answered from the data.",
    )
    answerable: bool = Field(
        default=True, description="false if the data does not contain what the question needs."
    )
    assumptions: list[str] = Field(default_factory=list, description="Interpretations made.")
    evidence: list[str] = Field(description="Key numbers and the queries that produced them.")


def _run_python(ctx: ToolContext, args: RunPythonInput) -> ToolResult:
    if ctx.executor is None:
        return ToolResult.failure("run_python is disabled (SQL-only mode). Use run_sql.")
    return ctx.executor.run(args.code, ctx.db.data_dir)


def _final_answer(ctx: ToolContext, args: FinalAnswerInput) -> ToolResult:
    return ToolResult.success(args.model_dump_json())


@dataclass(frozen=True)
class Tool:
    model: type[BaseModel]
    fn: Callable[[ToolContext, Any], ToolResult]
    description: str


TOOLS: dict[str, Tool] = {
    "describe_schema": Tool(
        DescribeSchemaInput,
        lambda ctx, a: describe_schema(ctx.db, a),
        "Columns, types, null counts, basic stats and 5 sample rows for a table.",
    ),
    "run_sql": Tool(
        RunSQLInput,
        lambda ctx, a: run_sql(ctx.db, a),
        "Run one read-only DuckDB SQL query. Returns at most 200 rows.",
    ),
    "run_python": Tool(
        RunPythonInput,
        _run_python,
        "Run pandas/matplotlib code in an isolated sandbox (no network, 15s limit).",
    ),
    "make_chart": Tool(
        MakeChartInput,
        lambda ctx, a: make_chart(ctx.db, a.spec),
        "Create a bar/line/scatter/hist chart from a SQL query result.",
    ),
    "final_answer": Tool(
        FinalAnswerInput, _final_answer, "Finish with the answer and supporting evidence."
    ),
}


def tool_specs() -> list[dict[str, Any]]:
    """Provider-neutral tool definitions; the LLM client adapts them in Phase 2."""
    return [
        {"name": n, "description": t.description, "input_schema": t.model.model_json_schema()}
        for n, t in TOOLS.items()
    ]


def execute(ctx: ToolContext, name: str, raw_args: dict[str, Any]) -> ToolResult:
    tool = TOOLS.get(name)
    if tool is None:
        return ToolResult.failure(f"Unknown tool '{name}'. Available: {', '.join(TOOLS)}.")
    try:
        args = tool.model.model_validate(raw_args)
    except ValidationError as e:
        return ToolResult.failure(f"Invalid arguments for {name}: {e}")
    return tool.fn(ctx, args)
