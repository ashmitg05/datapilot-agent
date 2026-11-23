"""Runner + report end to end with a scripted LLM (no network, no real data)."""

import json
from pathlib import Path

import pytest

from app.llm import LLMResponse, Pricing, ScriptedClient, ToolCall
from app.tools import ToolContext
from app.tools.sql import Database
from app.tracing import TraceStore
from benchmark.report import aggregate, markdown
from benchmark.run import run_one

Q = {"id": "t1", "dataset": "sales", "question": "top region?", "answer_type": "list",
     "difficulty": "easy", "category": "aggregation"}  # fmt: skip
GOLD = {"id": "t1", "answerable": True, "values": [["East"]]}


def _final(value: str) -> LLMResponse:
    args = {"answer": value, "value": value, "evidence": []}
    return LLMResponse("", [ToolCall("c", "final_answer", args)], 50, 10)


def test_run_one_and_report(db: Database, tmp_path: Path) -> None:
    store = TraceStore(tmp_path / "t.sqlite3")
    rows = []
    for repeat, value in enumerate(["East", "North"]):
        llm = ScriptedClient([_final(value)])
        rows.append(
            run_one(Q, GOLD, "self_correction", repeat, llm, Pricing(1, 1), ToolContext(db), store)
        )
    assert [r["correct"] for r in rows] == [True, False]
    assert store.get(rows[0]["trace_id"]) is not None

    agg = aggregate(json.loads(json.dumps(rows)))
    sr = agg["self_correction"]["success_rate"]
    assert sr["mean"] == 50.0 and sr["n_repeats"] == 2
    meta = {"run_id": "x", "configs": {"self_correction": {}}, "repeats": 2, "provider": "p",
            "models": {}}  # fmt: skip
    assert "| self_correction | 50.0 ±" in markdown(agg, meta, 1)


def test_provider_crash_is_recorded(db: Database, tmp_path: Path) -> None:
    class Boom(ScriptedClient):
        def complete(self, *a: object, **k: object) -> LLMResponse:
            raise RuntimeError("rate limited")

    row = run_one(Q, GOLD, "self_correction", 0, Boom([]), Pricing(), ToolContext(db),
                  TraceStore(tmp_path / "t.sqlite3"))  # fmt: skip
    assert row["status"] == "crashed" and "rate limited" in row["crash"] and not row["correct"]


def test_resume_reruns_only_provider_errors(
    data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys

    from benchmark import run

    q2 = {**Q, "id": "t2"}
    monkeypatch.setattr(run, "DATA", data_dir.parent)
    qs = [{**Q, "dataset": data_dir.name}, {**q2, "dataset": data_dir.name}]
    monkeypatch.setattr(run, "load_questions", lambda: qs)
    gold = tmp_path / "bench"
    gold.mkdir()
    gold_rows = [GOLD, {**GOLD, "id": "t2"}]
    (gold / "gold.jsonl").write_text("\n".join(json.dumps(g) for g in gold_rows))
    monkeypatch.setattr(run, "ROOT", gold)
    monkeypatch.setenv("LLM_MODEL", "scripted")
    monkeypatch.setattr(run, "from_env", lambda m=None: ScriptedClient([_final("East")] * 5))
    d = tmp_path / "prev"
    d.mkdir()
    (d / "meta.json").write_text(json.dumps(
        {"configs": {"self_correction": {}}, "repeats": 1, "questions": ["t1", "t2"],
         "sql_only": True}))  # fmt: skip
    done = {"config": "self_correction", "question_id": "t1", "repeat": 0, "correct": True,
            "crash": None}  # fmt: skip
    crashed = {**done, "question_id": "t2", "correct": False, "crash": "RateLimitError"}
    (d / "raw.jsonl").write_text(json.dumps(done) + "\n" + json.dumps(crashed) + "\n")
    monkeypatch.setattr(sys, "argv", ["run", "--resume", str(d), "--workers", "1"])
    run.main()
    rows = [json.loads(x) for x in (d / "raw.jsonl").read_text().splitlines()]
    assert sorted((r["question_id"], r["correct"]) for r in rows) == [("t1", True), ("t2", True)]
    assert all(not r["crash"] for r in rows)
