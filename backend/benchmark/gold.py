"""Compute gold answers from the reviewed gold_sql in questions.jsonl. Never uses an LLM.

uv run python -m benchmark.gold          # writes benchmark/gold.jsonl and prints for review
"""

from __future__ import annotations

import datetime as dt
import decimal
import json
from pathlib import Path
from typing import Any

from app.tools.sql import Database

ROOT = Path(__file__).parent
DATA = ROOT / "data"


def load_questions(path: Path = ROOT / "questions.jsonl") -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def to_json(v: Any) -> Any:
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, dt.datetime | dt.date):
        return v.isoformat()
    if isinstance(v, list | tuple):
        return [to_json(x) for x in v]
    return v


def shape(answer_type: str, cols: list[str], rows: list[tuple[Any, ...]]) -> Any:
    if answer_type in ("number", "yes-no"):
        assert len(rows) == 1 and len(cols) == 1, f"expected 1x1, got {len(rows)}x{len(cols)}"
        return to_json(rows[0][0])
    if answer_type == "list":
        return [to_json(r[0]) for r in rows]
    return [to_json(list(r)) for r in rows]


def compute(q: dict[str, Any], db: Database) -> dict[str, Any]:
    if q["gold_sql"] is None:
        return {"id": q["id"], "answerable": False, "values": []}
    sqls = q["gold_sql"] if isinstance(q["gold_sql"], list) else [q["gold_sql"]]
    values = []
    for sql in sqls:
        cols, rows, more = db.query(sql, limit=1000)
        assert not more, f"{q['id']}: gold query returned too many rows"
        values.append(shape(q["answer_type"], cols, rows))
    return {"id": q["id"], "answerable": True, "values": values}


def main() -> None:
    questions = load_questions()
    dbs = {d: Database(DATA / d) for d in sorted({q["dataset"] for q in questions})}
    gold = [compute(q, dbs[q["dataset"]]) for q in questions]
    for db in dbs.values():
        db.close()
    with (ROOT / "gold.jsonl").open("w") as f:
        for q, g in zip(questions, gold, strict=True):
            f.write(json.dumps(g) + "\n")
            print(f"{q['id']:4} {q['question'][:70]:70} -> {json.dumps(g['values'])[:80]}")


if __name__ == "__main__":
    main()
