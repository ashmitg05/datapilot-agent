"""CLI: python -m app.agent "question" --dataset path/to/sales.csv --verbose"""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from app.agent.loop import run_agent
from app.agent.state import AgentConfig
from app.llm import Pricing, from_env
from app.sandbox.executor import DockerExecutor
from app.tools import ToolContext
from app.tools.sql import Database
from app.tracing import TraceStore


def _dataset_dir(path: Path) -> Path:
    """A directory of CSVs is used as is; a single CSV is copied into a temp dir."""
    if path.is_dir():
        return path
    tmp = Path(tempfile.mkdtemp(prefix="datapilot_cli_"))
    shutil.copy(path, tmp / path.name)
    return tmp


def main() -> None:
    p = argparse.ArgumentParser(prog="python -m app.agent")
    p.add_argument("question")
    p.add_argument("--dataset", required=True, type=Path, help="CSV file or directory of CSVs")
    p.add_argument("--model", default=None)
    p.add_argument("--verbose", action="store_true")
    p.add_argument("--no-schema", action="store_true")
    p.add_argument("--no-self-correction", action="store_true")
    p.add_argument("--verify", action="store_true")
    p.add_argument("--sql-only", action="store_true", help="disable run_python (no Docker)")
    p.add_argument("--max-steps", type=int, default=10)
    a = p.parse_args()

    config = AgentConfig(
        schema_inspection=not a.no_schema,
        self_correction=not a.no_self_correction,
        verification=a.verify,
        allow_python=not a.sql_only,
        max_steps=a.max_steps,
    )
    llm = from_env(a.model)
    db = Database(_dataset_dir(a.dataset))
    ctx = ToolContext(db, None if a.sql_only else DockerExecutor())

    def show(event: dict[str, Any]) -> None:
        if not a.verbose or event["type"] != "step":
            return
        s = event["step"]
        mark = "ok" if s["ok"] else "ERROR"
        print(f"\n[{s['index']}] {s['tool']} ({mark}, {s['duration_ms']} ms)")
        if s["thought"]:
            print(f"  thought: {s['thought'][:200]}")
        print(f"  input: {json.dumps(s['input'])[:500]}")
        print(f"  {'output' if s['ok'] else 'error'}: {(s['output'] or s['error'] or '')[:800]}")

    state = run_agent(a.question, llm, ctx, config, Pricing.from_env(), show)
    trace_id = TraceStore().save(state, str(a.dataset), llm.model, config)
    print("\n=== Answer ===")
    print(json.dumps(state.final, indent=2, default=str) if state.final else f"({state.status})")
    print(f"\n{json.dumps(state.summary())}\ntrace: {trace_id}")
    db.close()


if __name__ == "__main__":
    main()
