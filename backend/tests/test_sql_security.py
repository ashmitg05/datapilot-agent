"""Malicious SQL must be rejected by the parser AND, independently, by the locked engine."""

import time

import duckdb
import pytest

from app.tools.sql import Database, SQLValidationError, validate_sql

TABLES = {"sales"}

MALICIOUS = [
    "DROP TABLE sales",
    "DELETE FROM sales",
    "INSERT INTO sales VALUES (1)",
    "UPDATE sales SET revenue = 0",
    "CREATE TABLE x AS SELECT * FROM sales",
    "ALTER TABLE sales ADD COLUMN x INT",
    "SELECT 1; DROP TABLE sales",
    "SELECT * FROM sales; SELECT 1",
    "ATTACH '/tmp/evil.db' AS evil",
    "COPY sales TO '/tmp/out.csv'",
    "COPY (SELECT * FROM sales) TO '/tmp/out.csv'",
    "INSTALL httpfs",
    "LOAD httpfs",
    "PRAGMA database_list",
    "SET enable_external_access = true",
    "SELECT * FROM read_csv_auto('/etc/passwd')",
    "SELECT * FROM read_csv('C:/Windows/win.ini')",
    "SELECT * FROM read_text('/etc/passwd')",
    "SELECT * FROM read_parquet('https://evil.example/x.parquet')",
    "SELECT * FROM '/etc/passwd'",
    "SELECT * FROM 'https://evil.example/data.csv'",
    "SELECT * FROM glob('/*')",
    "SELECT getenv('HOME')",
    "SELECT current_setting('enable_external_access')",
    "SELECT * FROM duckdb_settings()",
    "SELECT * FROM other_db.main.sales",
    "SELECT * FROM secrets",
    "WITH x AS (SELECT 1) DELETE FROM sales",
    "/* hi */ DROP TABLE sales -- SELECT",
    "SELECT * FROM sales WHERE 1=1; --\nDROP TABLE sales",
]


@pytest.mark.parametrize("query", MALICIOUS)
def test_parser_rejects(query: str) -> None:
    with pytest.raises(SQLValidationError):
        validate_sql(query, TABLES)


@pytest.mark.parametrize(
    "query",
    [
        "SELECT * FROM sales",
        "select region, sum(revenue) from sales group by region",
        "WITH t AS (SELECT * FROM sales) SELECT * FROM t",
        "DESCRIBE sales",
        "SELECT * FROM sales UNION ALL SELECT * FROM sales",
        "SELECT 'drop table sales' AS s FROM sales",  # keyword inside a string is data
    ],
)
def test_parser_allows_reads(query: str) -> None:
    validate_sql(query, TABLES)


# Defense in depth: bypass the parser entirely and hit the engine directly.
@pytest.mark.parametrize(
    "query",
    [
        "DROP TABLE sales",
        "CREATE TABLE x (a INT)",
        "INSERT INTO sales SELECT * FROM sales",
        "SELECT * FROM read_csv_auto('/etc/passwd')",
        "COPY sales TO 'out.csv'",
        "ATTACH 'evil.db'",
        "SET enable_external_access = true",
        "INSTALL httpfs",
    ],
)
def test_engine_blocks_without_parser(db: Database, query: str) -> None:
    with pytest.raises(duckdb.Error):
        db.conn.execute(query)


def test_query_timeout(db: Database) -> None:
    start = time.monotonic()
    with pytest.raises(duckdb.InterruptException):
        db.query("SELECT count(*) FROM range(100000000000) a", timeout_s=0.5)
    assert time.monotonic() - start < 5
