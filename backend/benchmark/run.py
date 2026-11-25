"""Run the benchmark across ablation configs. Every number in the report comes from raw.jsonl.

    uv run python -m benchmark.run --limit 10 --repeats 1          # pilot: measure cost first
    uv run python -m benchmark.run --repeats 3                     # full run
    uv run python -m benchmark.report benchmark/results/<run_id>   # tables + chart

Configs (cumulative, each adds one feature):
    1 single_shot      one LLM call writes one SQL query; no loop, no retries
    2 loop_no_schema   agent loop, no describe_schema, an error ends the run
    3 loop_schema      + schema inspection
    4 self_correction  + error hints and retries (max 3)
    5 verification     + independent re-check before the final answer
    6 verification_cheap  config 5 with LLM_MODEL_CHEAP instead of LLM_MODEL
"""

from __future__ import annotations

import argparse
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path
from typing import Any

from app.agent.loop import run_agent, single_shot
from app.agent.state import AgentConfig, AgentState
from app.llm import LLMClient, Pricing, from_env
from app.sandbox.executor import DockerExecutor
from app.tools import ToolContext
from app.tools.sql import Database
from app.tracing import TraceStore
from benchmark.checker import check
from benchmark.gold import DATA, ROOT, load_questions

CONFIGS: dict[str, AgentConfig | None] = {
    "single_shot": None,
    "loop_no_schema": AgentConfig(schema_inspection=False, self_correction=False),
    "loop_schema": AgentConfig(self_correction=False),
    "self_correction": AgentConfig(),
    "verification": AgentConfig(verification=True),
    "verification_cheap": AgentConfig(verification=True),
}


def _model_for(config: str) -> tuple[str, str]:
    """(model, env price prefix). The cheap config reads LLM_CHEAP_PRICE_* for its prices."""
    if config == "verification_cheap":
        model = os.environ.get("LLM_MODEL_CHEAP", "")
        if not model:
            raise RuntimeError("Set LLM_MODEL_CHEAP for the verification_cheap config.")
        return model, "LLM_CHEAP"
    return os.environ["LLM_MODEL"], "LLM"


def run_one(
    q: dict[str, Any],
    gold: dict[str, Any],
    config_name: str,
    repeat: int,
    llm: LLMClient,
    pricing: Pricing,
    ctx: ToolContext,
    store: TraceStore,
) -> dict[str, Any]:
    config = CONFIGS[config_name]
    t0 = time.monotonic()
    try:
        if config is None:
            state = single_shot(q["question"], llm, ctx)
            state.cost_usd = pricing.cost(state.input_tokens, state.output_tokens)
        else:
            state = run_agent(q["question"], llm, ctx, config, pricing)
        crash = state.crash
    except Exception as e:  # noqa: BLE001 - provider errors are recorded, not fatal
        state, crash = AgentState(question=q["question"], status="crashed"), repr(e)
    state.elapsed_s = time.monotonic() - t0
    trace_id = store.save(state, q["dataset"], llm.model, config or AgentConfig())
    verdict = check(q, gold, state.final)
    return {
        "question_id": q["id"],
        "config": config_name,
        "repeat": repeat,
        "model": llm.model,
        "dataset": q["dataset"],
        "difficulty": q["difficulty"],
        "category": q["category"],
        "correct": verdict["correct"],
        "reason": verdict["reason"],
        "final": state.final,
        "trace_id": trace_id,
        "crash": crash,
        **state.summary(),
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--configs", default=",".join(CONFIGS))
    p.add_argument("--repeats", type=int, default=3)
    p.add_argument("--limit", type=int, default=None, help="first N questions (pilot)")
    p.add_argument("--ids", default=None, help="comma-separated question ids")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--sql-only", action="store_true", help="disable run_python (no Docker)")
    p.add_argument(
        "--resume",
        type=Path,
        default=None,
        help="existing run dir: keep finished rows, re-run missing ones and provider errors "
        "(for free tiers with a daily quota). Configs, questions and repeats come from it.",
    )
    a = p.parse_args()
    kept: list[dict[str, Any]] = []
    if a.resume:
        meta = json.loads((a.resume / "meta.json").read_text())
        a.configs, a.repeats = ",".join(meta["configs"]), meta["repeats"]
        a.ids, a.limit, a.sql_only = ",".join(meta["questions"]), None, meta["sql_only"]
        rows = [json.loads(x) for x in (a.resume / "raw.jsonl").read_text().splitlines() if x]
        kept = [r for r in rows if not r.get("crash")]

    questions = load_questions()
    if a.ids:
        wanted = set(a.ids.split(","))
        questions = [q for q in questions if q["id"] in wanted]
    questions = questions[: a.limit]
    gold_path = ROOT / "gold.jsonl"
    if not gold_path.exists():
        raise SystemExit("benchmark/gold.jsonl missing: run `python -m benchmark.gold` first.")
    gold = {g["id"]: g for g in map(json.loads, gold_path.read_text().splitlines())}

    configs = a.configs.split(",")
    if a.resume:
        out, run_id = a.resume, a.resume.name
        (out / "raw.jsonl").write_text("".join(json.dumps(r, default=str) + "\n" for r in kept))
    else:
        run_id = time.strftime("%Y%m%d-%H%M%S")
        out = ROOT / "results" / run_id
        out.mkdir(parents=True)
    finished = {(r["config"], r["question_id"], r["repeat"]) for r in kept}
    if not a.resume:
        (out / "meta.json").write_text(json.dumps({
        "run_id": run_id, "configs": {c: asdict(cfg) if (cfg := CONFIGS[c]) else None
                                       for c in configs},
        "models": {c: _model_for(c)[0] for c in configs}, "repeats": a.repeats,
        "questions": [q["id"] for q in questions], "sql_only": a.sql_only,
        "provider": os.environ.get("LLM_PROVIDER", "anthropic"),
    }, indent=2))  # fmt: skip
    store = TraceStore(out / "traces.sqlite3")
    executor = None if a.sql_only else DockerExecutor()
    dbs = {d: Database(DATA / d) for d in sorted({q["dataset"] for q in questions})}
    lock = threading.Lock()
    raw = (out / "raw.jsonl").open("a")
    done = 0
    total = len(configs) * a.repeats * len(questions) - len(finished)

    for config_name in configs:
        model, prefix = _model_for(config_name)
        llm, pricing = from_env(model), Pricing.from_env(prefix)
        jobs = [
            (q, r)
            for r in range(a.repeats)
            for q in questions
            if (config_name, q["id"], r) not in finished
        ]

        def work(job: tuple[dict[str, Any], int]) -> None:
            nonlocal done
            q, r = job
            ctx = ToolContext(dbs[q["dataset"]], executor)
            row = run_one(q, gold[q["id"]], config_name, r, llm, pricing, ctx, store)  # noqa: B023
            with lock:
                raw.write(json.dumps(row, default=str) + "\n")
                raw.flush()
                done += 1
                mark = "ok " if row["correct"] else "BAD"
                print(f"[{done}/{total}] {mark} {config_name:18} r{r} {q['id']} {row['reason']}")  # noqa: B023

        with ThreadPoolExecutor(a.workers) as pool:
            list(pool.map(work, jobs))
    raw.close()
    print(f"\nraw results: {out / 'raw.jsonl'}\nnext: python -m benchmark.report {out}")


if __name__ == "__main__":
    main()
