"""Turn a run's raw.jsonl into summary.md, summary.json, a chart and a failure list.

    uv run python -m benchmark.report benchmark/results/<run_id>

Every metric is computed per repeat, then reported as mean ± std across repeats.
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

Row = dict[str, Any]


def _pct(xs: list[bool]) -> float | None:
    return 100 * sum(xs) / len(xs) if xs else None


def _quantile(xs: list[float], q: float) -> float:
    s = sorted(xs)
    return s[min(len(s) - 1, int(round(q * (len(s) - 1))))]


def repeat_metrics(rows: list[Row]) -> dict[str, float | None]:
    answerable = [r for r in rows if r["category"] != "unanswerable"]
    unanswerable = [r for r in rows if r["category"] == "unanswerable"]
    errors = sum(r["errors"] for r in rows)
    m: dict[str, float | None] = {
        "success_rate": _pct([r["correct"] for r in rows]),
        "success_answerable": _pct([r["correct"] for r in answerable]),
        "correct_refusal_rate": _pct([r["correct"] for r in unanswerable]),
        "avg_steps": statistics.fmean(r["steps"] for r in rows),
        "avg_tokens": statistics.fmean(r["input_tokens"] + r["output_tokens"] for r in rows),
        "cost_per_question": statistics.fmean(r["cost_usd"] for r in rows),
        "p50_latency_s": _quantile([r["elapsed_s"] for r in rows], 0.5),
        "p95_latency_s": _quantile([r["elapsed_s"] for r in rows], 0.95),
        "error_recovery_rate": 100 * sum(r["recovered_errors"] for r in rows) / errors
        if errors
        else None,
    }
    for key in ("difficulty", "category"):
        groups: dict[str, list[bool]] = defaultdict(list)
        for r in rows:
            groups[r[key]].append(r["correct"])
        for g, xs in groups.items():
            m[f"{key}:{g}"] = _pct(xs)
    return m


def aggregate(rows: list[Row]) -> dict[str, dict[str, dict[str, float | None]]]:
    by: dict[str, dict[int, list[Row]]] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        by[r["config"]][r["repeat"]].append(r)
    out: dict[str, dict[str, dict[str, float | None]]] = {}
    for config, repeats in by.items():
        per = [repeat_metrics(rs) for rs in repeats.values()]
        keys = sorted({k for m in per for k in m})
        out[config] = {}
        for k in keys:
            vals = [v for m in per if (v := m.get(k)) is not None]
            out[config][k] = {
                "mean": statistics.fmean(vals) if vals else None,
                "std": statistics.stdev(vals) if len(vals) > 1 else 0.0,
                "n_repeats": len(vals),
            }
    return out


def _fmt(cell: dict[str, float | None], digits: int = 1, money: bool = False) -> str:
    if cell["mean"] is None:
        return "n/a"
    if money:
        return f"${cell['mean']:.4f} ± {cell['std'] or 0:.4f}"
    return f"{cell['mean']:.{digits}f} ± {cell['std'] or 0:.{digits}f}"


def markdown(agg: dict[str, Any], meta: dict[str, Any], n_questions: int) -> str:
    order = [c for c in meta["configs"] if c in agg]
    lines = [
        f"Run `{meta['run_id']}` · {n_questions} questions × {meta['repeats']} repeats · "
        f"provider {meta['provider']} · models {meta['models']}"
        + (" · SQL-only" if meta.get("sql_only") else ""),
        "",
        "| Config | Success % | Correct refusal % | Avg steps | Avg tokens | Cost / q | "
        "p50 s | p95 s | Error recovery % |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for c in order:
        m = agg[c]
        lines.append(
            f"| {c} | {_fmt(m['success_rate'])} | {_fmt(m['correct_refusal_rate'])} | "
            f"{_fmt(m['avg_steps'])} | {_fmt(m['avg_tokens'], 0)} | "
            f"{_fmt(m['cost_per_question'], money=True)} | {_fmt(m['p50_latency_s'])} | "
            f"{_fmt(m['p95_latency_s'])} | {_fmt(m['error_recovery_rate'])} |"
        )
    for key in ("difficulty", "category"):
        groups = sorted({k.split(":", 1)[1] for c in order for k in agg[c] if k.startswith(key)})
        lines += ["", f"Success % by {key}", "",
                  "| Config | " + " | ".join(groups) + " |",
                  "|---|" + "---|" * len(groups)]  # fmt: skip
        for c in order:
            cells = [_fmt(agg[c][f"{key}:{g}"]) if f"{key}:{g}" in agg[c] else "n/a"
                     for g in groups]  # fmt: skip
            lines.append(f"| {c} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def chart(agg: dict[str, Any], order: list[str], path: Path) -> None:
    means = [agg[c]["success_rate"]["mean"] for c in order]
    stds = [agg[c]["success_rate"]["std"] for c in order]
    fig, ax = plt.subplots(figsize=(9, 4.5))
    ax.bar(order, means, yerr=stds, capsize=4, color="#3b6fd4")
    ax.set(ylabel="Task success (%)", ylim=(0, 100), title="DataPilot ablations")
    ax.tick_params(axis="x", rotation=20)
    for i, v in enumerate(means):
        ax.text(i, v + 2, f"{v:.0f}", ha="center")
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


def failures(rows: list[Row]) -> str:
    """Candidates for the written failure analysis: wrong in the best config, all repeats."""
    best = [r for r in rows if r["config"] == "verification"] or rows
    wrong: dict[str, list[Row]] = defaultdict(list)
    for r in best:
        if not r["correct"]:
            wrong[r["question_id"]].append(r)
    out = ["| Question | Category | Failed repeats | Reason | Trace ids |", "|---|---|---|---|---|"]
    for qid, rs in sorted(wrong.items(), key=lambda kv: -len(kv[1])):
        out.append(
            f"| {qid} | {rs[0]['category']} | {len(rs)} | {rs[0]['reason']} | "
            + ", ".join(r["trace_id"][:8] for r in rs)
            + " |"
        )
    return "\n".join(out) + "\n"


def main() -> None:
    run_dir = Path(sys.argv[1])
    meta = json.loads((run_dir / "meta.json").read_text())
    rows = [json.loads(x) for x in (run_dir / "raw.jsonl").read_text().splitlines() if x]
    agg = aggregate(rows)
    order = [c for c in meta["configs"] if c in agg]
    (run_dir / "summary.json").write_text(json.dumps(agg, indent=2))
    (run_dir / "summary.md").write_text(markdown(agg, meta, len(meta["questions"])))
    (run_dir / "failure_candidates.md").write_text(failures(rows))
    chart(agg, order, run_dir / "success.png")
    print((run_dir / "summary.md").read_text())


if __name__ == "__main__":
    main()
