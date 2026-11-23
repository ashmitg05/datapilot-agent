"""System prompt, built from the active feature flags so ablations change only what they claim."""

from __future__ import annotations

import re

from app.agent.state import AgentConfig


def system_prompt(config: AgentConfig, tables: list[str]) -> str:
    rules = [
        "You are DataPilot, a careful data analyst. Answer the user's question using only the "
        "data in the available tables, by calling tools.",
        f"Available tables: {', '.join(tables)}.",
    ]
    if config.schema_inspection:
        rules.append(
            "Always call describe_schema on the relevant table(s) before writing any query. "
            "Never guess column names: use the exact names from the schema."
        )
    else:
        rules.append("You do not have a schema tool. Write queries using your best knowledge.")
    if config.allow_python:
        rules.append(
            "Prefer run_sql (DuckDB dialect) for aggregation and filtering. Use run_python only "
            "for analysis SQL cannot express well. Use make_chart for simple charts."
        )
    else:
        rules.append("Use run_sql (DuckDB dialect) for all analysis. Use make_chart for charts.")
    rules += [
        "If the question is ambiguous, pick the most reasonable interpretation and list it "
        "under assumptions.",
        "If the data does not contain what is needed (e.g. a column that does not exist), do "
        "not invent an answer: call final_answer with answerable=false and value=null, and "
        "explain what is missing.",
        "Finish by calling final_answer. Put the direct answer in `value` (a number, string, "
        "true/false, or a list), and the numbers and queries behind it in `evidence`.",
    ]
    return "\n- ".join([rules[0], *rules[1:]])


VERIFY_PROMPT = (
    "Before finishing: verify the key number(s) in your answer with a second, independent "
    "query (a different approach, e.g. different grouping or a subquery instead of a join). "
    "If the results match, call final_answer again. If they do not, investigate, and state the "
    "mismatch in assumptions."
)

_HINTS: list[tuple[str, str]] = [
    (
        r"Binder Error|not found in FROM clause|Referenced column|KeyError",
        "A column name is wrong. Check the exact column names (describe_schema) and retry.",
    ),
    (r"Unknown table|Catalog Error", "The table name is wrong. Use one of the available tables."),
    (r"Parser Error|Could not parse|SyntaxError", "Fix the syntax error and retry."),
    (r"Conversion Error|Could not convert|TypeError", "Cast the column to the right type first."),
    (r"timed out", "The query was too slow. Aggregate or filter more before returning rows."),
    (r"Rejected", "Only read-only queries over the available tables are allowed."),
]


def error_hint(error: str, attempt: int, max_retries: int) -> str:
    hint = next(
        (h for pat, h in _HINTS if re.search(pat, error)),
        "Read the error, fix the cause, and retry.",
    )
    return f"Error (attempt {attempt}/{max_retries}): {error}\nHint: {hint}"
