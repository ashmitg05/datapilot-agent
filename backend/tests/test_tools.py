import json

from app.tools import ToolContext, execute, tool_specs
from app.tools.result import truncate
from app.tools.sql import Database


def test_specs_are_strict_json_schemas() -> None:
    specs = {s["name"]: s for s in tool_specs()}
    assert set(specs) == {"describe_schema", "run_sql", "run_python", "make_chart", "final_answer"}
    assert specs["run_sql"]["input_schema"]["additionalProperties"] is False


def test_describe_schema(db: Database) -> None:
    r = execute(ToolContext(db), "describe_schema", {"table": "sales"})
    assert r.ok, r.error
    assert "6 rows, 5 columns" in r.output
    assert "revenue" in r.output and "Sample rows" in r.output


def test_describe_schema_counts_nulls(db: Database) -> None:
    r = execute(ToolContext(db), "describe_schema", {"table": "sales"})
    revenue_line = next(
        line for line in r.output.splitlines() if line.strip().startswith("revenue")
    )
    assert revenue_line.split()[-1] == "1"


def test_unknown_table_hint(db: Database) -> None:
    r = execute(ToolContext(db), "describe_schema", {"table": "orders"})
    assert not r.ok and "Available tables: sales" in (r.error or "")


def test_run_sql_aggregation(db: Database) -> None:
    r = execute(
        ToolContext(db),
        "run_sql",
        {"query": "SELECT region, sum(revenue) AS rev FROM sales GROUP BY 1 ORDER BY 2 DESC"},
    )
    assert r.ok, r.error
    assert r.output.splitlines()[1].split()[0] == "East"


def test_run_sql_cte_allowed(db: Database) -> None:
    q = "WITH t AS (SELECT * FROM sales) SELECT count(*) FROM t"
    assert execute(ToolContext(db), "run_sql", {"query": q}).ok


def test_run_sql_error_is_returned_not_raised(db: Database) -> None:
    r = execute(ToolContext(db), "run_sql", {"query": "SELECT revenu FROM sales"})
    assert not r.ok and "revenu" in (r.error or "")


def test_row_limit(db: Database) -> None:
    q = "SELECT a.order_id FROM sales a, sales b, sales c, sales d"  # 1296 rows
    r = execute(ToolContext(db), "run_sql", {"query": q})
    assert r.ok and "only first 200 rows" in r.output


def test_invalid_args_rejected(db: Database) -> None:
    r = execute(ToolContext(db), "run_sql", {"query": "SELECT 1", "extra": True})
    assert not r.ok and "Invalid arguments" in (r.error or "")


def test_unknown_tool(db: Database) -> None:
    assert not execute(ToolContext(db), "rm_rf", {}).ok


def test_run_python_disabled_in_sql_only_mode(db: Database) -> None:
    r = execute(ToolContext(db, executor=None), "run_python", {"code": "print(1)"})
    assert not r.ok and "SQL-only" in (r.error or "")


def test_make_chart(db: Database) -> None:
    spec = {
        "type": "bar",
        "sql": "SELECT region, sum(revenue) AS rev FROM sales GROUP BY 1",
        "x": "region",
        "y": "rev",
        "title": "Revenue by region",
    }
    r = execute(ToolContext(db), "make_chart", {"spec": spec})
    assert r.ok, r.error
    assert len(r.images) == 1 and r.images[0].startswith("iVBOR")  # PNG magic, base64


def test_make_chart_missing_column(db: Database) -> None:
    spec = {"type": "line", "sql": "SELECT region FROM sales", "x": "region", "y": "rev",
            "title": "t"}  # fmt: skip
    r = execute(ToolContext(db), "make_chart", {"spec": spec})
    assert not r.ok and "rev" in (r.error or "")


def test_final_answer(db: Database) -> None:
    r = execute(ToolContext(db), "final_answer", {"answer": "East", "evidence": ["sum=510"]})
    out = json.loads(r.output)
    assert out["answer"] == "East" and out["evidence"] == ["sum=510"] and out["answerable"]


def test_truncate_keeps_head_and_tail() -> None:
    text, cut = truncate("A" * 5000 + "TAIL", limit=100)
    assert cut and text.startswith("A") and text.endswith("TAIL") and len(text) < 200
