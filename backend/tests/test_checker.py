import json
from pathlib import Path

import pytest

from app.tools.sql import Database
from benchmark.checker import check, match_value
from benchmark.gold import compute, load_questions


@pytest.mark.parametrize(
    ("answer_type", "gold", "value", "ok"),
    [
        ("number", 1234.5678, 1234.57, True),
        ("number", 1234.5678, "1,234.57", True),
        ("number", 25.0, "25%", True),
        ("number", 25.0, 0.25, False),
        ("number", 100.0, 110.0, False),
        ("number", 7.0, [7], True),
        ("list", ["East"], "east", True),
        ("list", ["A", "B", "C"], ["c", "a", "b"], True),
        ("list", ["A", "B", "C"], ["a", "b"], False),
        ("list", ["2011-11"], "November 2011", True),
        ("list", ["2011-11"], "November", True),
        ("list", ["2011-11"], "October", False),
        ("list", ["2012-09-15"], "2012-09-15", True),
        ("yes-no", True, "Yes", True),
        ("yes-no", True, False, False),
        ("table", [[14646, 279489.02]], [[14646.0, 279489.0]], True),
        ("table", [[1, 2.0], [2, 3.0]], [[2, 3.0], [1, 2.0]], True),
        ("table", [[1, 2.0]], [[1, 9.0]], False),
        ("table", [[1, 4876.79]], [[1, 4876.786, 463]], True),
        ("table", [[1, 4876.79]], [[463, 1]], False),
        ("list", ["A", "B"], [["a", 10.0], ["b", 5.0]], True),
        ("list", ["A", "B"], [["a", 10.0], ["c", 5.0]], False),
    ],
)
def test_match_value(answer_type: str, gold: object, value: object, ok: bool) -> None:
    assert match_value(answer_type, gold, value) is ok


def test_unanswerable_needs_refusal() -> None:
    q = {"answer_type": "number", "category": "unanswerable"}
    g = {"answerable": False, "values": []}
    assert check(q, g, {"answerable": False, "value": None})["correct"]
    assert not check(q, g, {"answerable": True, "value": 3})["correct"]


def test_ambiguous_needs_interpretation() -> None:
    q = {"answer_type": "number", "category": "ambiguous"}
    g = {"answerable": True, "values": [10.0, 20.0]}
    assert check(q, g, {"value": 20, "assumptions": ["sales = units"]})["correct"]
    assert not check(q, g, {"value": 20, "assumptions": []})["correct"]
    assert not check(q, g, {"value": 30, "assumptions": ["x"]})["correct"]


def test_questions_file_is_valid() -> None:
    qs = load_questions()
    assert len(qs) >= 60 and len({q["id"] for q in qs}) == len(qs)
    cats = [q["category"] for q in qs]
    assert 0.08 <= cats.count("ambiguous") / len(qs) <= 0.15
    assert 0.08 <= cats.count("unanswerable") / len(qs) <= 0.15
    for q in qs:
        assert q["answer_type"] in {"number", "list", "table", "yes-no"}
        assert q["difficulty"] in {"easy", "medium", "hard"}
        assert (q["gold_sql"] is None) == (q["category"] == "unanswerable")
        assert isinstance(q["gold_sql"], list) == (q["category"] == "ambiguous")


def test_gold_compute(db: Database) -> None:
    q = {"id": "t", "answer_type": "list", "gold_sql":
         "SELECT region FROM sales GROUP BY 1 ORDER BY sum(revenue) DESC LIMIT 1"}  # fmt: skip
    assert compute(q, db) == {"id": "t", "answerable": True, "values": [["East"]]}


@pytest.mark.skipif(
    not (Path(__file__).parents[1] / "benchmark" / "gold.jsonl").exists(),
    reason="run `python -m benchmark.gold` after downloading datasets",
)
def test_gold_file_covers_all_questions() -> None:
    gold_path = Path(__file__).parents[1] / "benchmark" / "gold.jsonl"
    gold = [json.loads(x) for x in gold_path.read_text().splitlines()]
    assert {g["id"] for g in gold} == {q["id"] for q in load_questions()}
