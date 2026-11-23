"""Re-grade saved runs with the current checker, from the stored final answers.

    uv run python -m benchmark.regrade benchmark/results/<run_id> [...]

Used when a checker bug is fixed after a run. No LLM call. The verdict recorded at run time is
kept as `correct_at_run` so every change is visible in raw.jsonl.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from benchmark.checker import check
from benchmark.gold import ROOT, load_questions


def regrade(run_dir: Path) -> int:
    questions = {q["id"]: q for q in load_questions()}
    gold = {g["id"]: g for g in map(json.loads, (ROOT / "gold.jsonl").read_text().splitlines())}
    path = run_dir / "raw.jsonl"
    rows = [json.loads(x) for x in path.read_text().splitlines() if x]
    changed = 0
    for r in rows:
        verdict = check(questions[r["question_id"]], gold[r["question_id"]], r["final"])
        r.setdefault("correct_at_run", r["correct"])
        if verdict["correct"] != r["correct"]:
            changed += 1
            print(f"{r['config']:18} {r['question_id']}: {r['correct']} -> {verdict['correct']}")
        r["correct"], r["reason"] = verdict["correct"], verdict["reason"]
    path.write_text("".join(json.dumps(r, default=str) + "\n" for r in rows))
    return changed


if __name__ == "__main__":
    for d in sys.argv[1:]:
        print(f"{d}: {regrade(Path(d))} verdict(s) changed")
