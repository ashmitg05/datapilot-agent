"""Deterministic answer checker. An LLM judge is used only for free-text explanations and is
reported as a separate metric, never mixed into task success."""

from __future__ import annotations

import math
import re
from typing import Any

import pandas as pd

from app.llm import LLMClient

REL_TOL = 0.005
ABS_TOL = 0.01
_NUM = re.compile(r"^[\s$€£]*(-?[\d,]*\.?\d+(?:e-?\d+)?)\s*%?\s*$", re.I)
_YES = {"yes", "true", "y", "1"}
_NO = {"no", "false", "n", "0"}


def to_number(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, int | float):
        return float(v)
    if isinstance(v, str) and (m := _NUM.match(v)):
        return float(m.group(1).replace(",", ""))
    return None


def norm(v: Any) -> Any:
    """Canonical form for comparing scalars: float, ISO date, or lowercase string."""
    n = to_number(v)
    if n is not None:
        return n
    s = str(v).strip().lower()
    if re.search(r"[a-z/-]", s) and re.search(r"\d{4}", s):
        try:
            return pd.Timestamp(s).normalize().isoformat()[:10]
        except (ValueError, TypeError):
            pass
    return s


def same(a: Any, b: Any) -> bool:
    a, b = norm(a), norm(b)
    if isinstance(a, float) and isinstance(b, float):
        return math.isclose(a, b, rel_tol=REL_TOL, abs_tol=ABS_TOL)
    if isinstance(a, str) and isinstance(b, str) and len(a) == 7 and b.startswith(a):
        return True  # "2011-11" vs "2011-11-01" (month answers parsed to a date)
    if isinstance(a, str) and isinstance(b, str) and len(b) == 7 and a.startswith(b):
        return True
    return bool(a == b)


def _row_eq(g: list[Any], r: list[Any]) -> bool:
    return len(g) == len(r) and all(same(a, b) for a, b in zip(g, r, strict=True))


def _as_list(v: Any) -> list[Any]:
    return v if isinstance(v, list) else [v]


def match_value(answer_type: str, gold: Any, value: Any) -> bool:
    if value is None:
        return False
    if answer_type == "number":
        if isinstance(value, list) and len(value) == 1:
            value = value[0]
        return same(gold, value)
    if answer_type == "yes-no":
        s = str(value).strip().lower()
        yes: bool | None = True if s in _YES else False if s in _NO else None
        return yes is not None and yes == bool(gold)
    if answer_type == "list":
        got: list[Any] = _as_list(value)
        if len(got) == 1 and isinstance(got[0], list):  # [[x]] from a 1-col table
            got = got[0]
        got = [g[0] if isinstance(g, list) and len(g) == 1 else g for g in got]
        return len(got) == len(gold) and all(any(same(g, v) for v in got) for g in gold)
    # table: order-insensitive row match, cell-wise tolerance
    rows = [_as_list(r) for r in _as_list(value)]
    if len(rows) != len(gold):
        return False
    unused = list(rows)
    for g in gold:
        hit = next((r for r in unused if _row_eq(g, r)), None)
        if hit is None:
            return False
        unused.remove(hit)
    return True


def check(
    question: dict[str, Any], gold: dict[str, Any], final: dict[str, Any] | None
) -> dict[str, Any]:
    """Returns {"correct", "reason"}. Ambiguous questions also need a stated interpretation."""
    if final is None:
        return {"correct": False, "reason": "no final answer"}
    if not gold["answerable"]:
        refused = final.get("answerable") is False
        return {"correct": refused, "reason": "refused" if refused else "answered unanswerable"}
    if final.get("answerable") is False:
        return {"correct": False, "reason": "refused an answerable question"}
    matched = any(
        match_value(question["answer_type"], g, final.get("value")) for g in gold["values"]
    )
    if question["category"] == "ambiguous" and matched and not final.get("assumptions"):
        return {"correct": False, "reason": "matched but stated no interpretation"}
    return {"correct": matched, "reason": "match" if matched else "value mismatch"}


JUDGE_PROMPT = """You grade a data analyst's explanation. Reply with exactly PASS or FAIL.
PASS only if the explanation is consistent with the reference value, cites supporting numbers,
and does not claim things the evidence does not show."""


def judge_explanation(
    llm: LLMClient, question: str, gold_values: list[Any], final: dict[str, Any]
) -> bool:
    msg = (
        f"Question: {question}\nReference value(s): {gold_values}\n"
        f"Answer: {final.get('answer')}\nEvidence: {final.get('evidence')}"
    )
    resp = llm.complete(JUDGE_PROMPT, [{"role": "user", "text": msg}], [])
    return resp.text.strip().upper().startswith("PASS")
