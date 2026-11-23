"""DuckDB access: SQL validation (sqlglot) plus an engine-level lockdown.

Two layers on purpose:
1. validate_sql parses the query and allows one read-only statement over known tables.
2. The DuckDB connection is read-only with external access disabled and config locked,
   so even a query that slips past the parser cannot read files or write.
"""

from __future__ import annotations

import re
import tempfile
import threading
from collections.abc import Sequence
from pathlib import Path

import duckdb
import pandas as pd
import sqlglot
from pydantic import BaseModel, ConfigDict, Field
from sqlglot import exp

from app.tools.result import ToolResult

ROW_LIMIT = 200
TIMEOUT_S = 10.0
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
_FORBIDDEN_NODES = tuple(
    getattr(exp, n)
    for n in (
        "Insert",
        "Update",
        "Delete",
        "Drop",
        "Create",
        "Alter",
        "Merge",
        "Command",
        "Copy",
        "Set",
        "Use",
        "Pragma",
        "Attach",
        "Detach",
        "Install",
        "Load",
        "Transaction",
    )
    if hasattr(exp, n)
)
_FORBIDDEN_FUNC_PREFIXES = ("read_", "glob", "sniff_", "parquet_", "duckdb_", "iceberg_", "delta_")
_FORBIDDEN_FUNCS = {"getenv", "current_setting", "query", "query_table", "load_extension"}


class SQLValidationError(ValueError):
    pass


def validate_sql(query: str, tables: set[str]) -> None:
    """Raise SQLValidationError unless query is one SELECT/WITH/DESCRIBE over known tables."""
    try:
        statements = [s for s in sqlglot.parse(query, read="duckdb") if s is not None]
    except sqlglot.errors.SqlglotError as e:
        raise SQLValidationError(f"Could not parse SQL: {e}") from e
    if len(statements) != 1:
        raise SQLValidationError(f"Exactly one statement allowed, got {len(statements)}.")
    tree = statements[0]
    if not isinstance(tree, exp.Query | exp.Describe):
        raise SQLValidationError(f"Only SELECT/WITH/DESCRIBE allowed, got {tree.key.upper()}.")

    for node in tree.walk():
        if isinstance(node, _FORBIDDEN_NODES):
            raise SQLValidationError(f"{node.key.upper()} is not allowed.")
        if isinstance(node, exp.Func):
            name = (node.name if isinstance(node, exp.Anonymous) else node.sql_name()).lower()  # type: ignore[no-untyped-call]
            if name.startswith(_FORBIDDEN_FUNC_PREFIXES) or name in _FORBIDDEN_FUNCS:
                raise SQLValidationError(f"Function {name}() is not allowed.")

    known = {t.lower() for t in tables}
    ctes = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    for table in tree.find_all(exp.Table):
        qualified = table.args.get("db") or table.args.get("catalog")
        if not isinstance(table.this, exp.Identifier) or qualified:
            raise SQLValidationError("Only plain table names are allowed in FROM/JOIN.")
        if table.name.lower() not in known | ctes:
            raise SQLValidationError(
                f"Unknown table '{table.name}'. Available tables: {', '.join(sorted(tables))}."
            )


class Database:
    """Loads every CSV in data_dir as a table (name = file stem), then reopens read-only."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        csvs = sorted(data_dir.glob("*.csv"))
        if not csvs:
            raise ValueError(f"No CSV files in {data_dir}")
        self._tmp = tempfile.TemporaryDirectory(prefix="datapilot_", ignore_cleanup_errors=True)
        db_path = str(Path(self._tmp.name) / "data.duckdb")
        with duckdb.connect(db_path) as writer:
            for csv in csvs:
                if not _IDENT.match(csv.stem):
                    raise ValueError(f"Invalid table name from file: {csv.name}")
                src = str(csv).replace("'", "''")
                writer.execute(
                    f"CREATE TABLE \"{csv.stem}\" AS SELECT * FROM read_csv_auto('{src}')"
                )
        self.tables = {c.stem for c in csvs}
        self.conn = duckdb.connect(
            db_path,
            read_only=True,
            config={
                "enable_external_access": "false",
                "autoinstall_known_extensions": "false",
                "autoload_known_extensions": "false",
                "memory_limit": "512MB",
                "threads": "2",
                "lock_configuration": "true",
            },
        )

    def query(
        self, sql: str, limit: int = ROW_LIMIT, timeout_s: float = TIMEOUT_S
    ) -> tuple[list[str], list[tuple[object, ...]], bool]:
        """Run sql with a wall-clock timeout. Returns (columns, rows, more_rows_exist)."""
        cur = self.conn.cursor()
        timer = threading.Timer(timeout_s, cur.interrupt)
        timer.start()
        try:
            cur.execute(sql)
            cols = [d[0] for d in cur.description or []]
            rows = cur.fetchmany(limit + 1)
        finally:
            timer.cancel()
            cur.close()
        return cols, rows[:limit], len(rows) > limit

    def close(self) -> None:
        self.conn.close()
        self._tmp.cleanup()


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _table_text(cols: list[str], rows: Sequence[Sequence[object]]) -> str:
    return pd.DataFrame(rows, columns=cols).to_string(index=False, max_colwidth=60)


class DescribeSchemaInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    table: str = Field(description="Table name to inspect.")


class RunSQLInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(description="One read-only DuckDB SQL statement (SELECT/WITH/DESCRIBE).")


def describe_schema(db: Database, args: DescribeSchemaInput) -> ToolResult:
    if args.table not in db.tables:
        return ToolResult.failure(
            f"Unknown table '{args.table}'. Available tables: {', '.join(sorted(db.tables))}."
        )
    t = _q(args.table)
    try:
        _, count_rows, _ = db.query(f"SELECT count(*) FROM {t}")
        _, schema, _ = db.query(f"DESCRIBE {t}")
        names = [str(r[0]) for r in schema]
        null_sql = ", ".join(f"count(*) - count({_q(c)})" for c in names)
        _, null_rows, _ = db.query(f"SELECT {null_sql} FROM {t}")
        s_cols, stats, _ = db.query(
            f"SELECT column_name, min, max, approx_unique, avg, std FROM (SUMMARIZE {t})"
        )
        sc, sample, _ = db.query(f"SELECT * FROM {t} LIMIT 5")
    except duckdb.Error as e:
        return ToolResult.failure(f"{type(e).__name__}: {e}")
    nulls = null_rows[0]
    col_rows = [(n, r[1], nulls[i]) for i, (n, r) in enumerate(zip(names, schema, strict=True))]
    text = (
        f"Table {args.table}: {count_rows[0][0]} rows, {len(names)} columns\n\n"
        f"Columns:\n{_table_text(['column', 'type', 'nulls'], col_rows)}\n\n"
        f"Stats:\n{_table_text(s_cols, stats)}\n\n"
        f"Sample rows:\n{_table_text(sc, sample)}"
    )
    return ToolResult.success(text)


def run_sql(db: Database, args: RunSQLInput) -> ToolResult:
    try:
        validate_sql(args.query, db.tables)
        cols, rows, more = db.query(args.query)
    except SQLValidationError as e:
        return ToolResult.failure(f"Rejected: {e}")
    except duckdb.InterruptException:
        return ToolResult.failure(f"Query timed out after {TIMEOUT_S:.0f}s. Aggregate or filter.")
    except duckdb.Error as e:
        return ToolResult.failure(f"{type(e).__name__}: {e}")
    text = _table_text(cols, rows)
    if more:
        text += f"\n[only first {ROW_LIMIT} rows shown; aggregate or add LIMIT]"
    return ToolResult.success(text)
