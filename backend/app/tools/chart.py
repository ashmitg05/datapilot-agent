"""make_chart: fixed matplotlib templates, so basic charts need no agent-written code."""

from __future__ import annotations

import base64
import io
from typing import Literal

import duckdb
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402
from pydantic import BaseModel, ConfigDict, Field  # noqa: E402

from app.tools.result import ToolResult  # noqa: E402
from app.tools.sql import Database, SQLValidationError, validate_sql  # noqa: E402

MAX_POINTS = 1000


class ChartSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["bar", "line", "scatter", "hist"]
    sql: str = Field(description="Read-only SQL that returns the columns to plot.")
    x: str = Field(description="Column for the x axis (or the values, for hist).")
    y: str | None = Field(default=None, description="Column for the y axis. Not used for hist.")
    title: str


def make_chart(db: Database, spec: ChartSpec) -> ToolResult:
    if spec.type != "hist" and not spec.y:
        return ToolResult.failure(f"'y' is required for {spec.type} charts.")
    try:
        validate_sql(spec.sql, db.tables)
        cols, rows, more = db.query(spec.sql, limit=MAX_POINTS)
    except SQLValidationError as e:
        return ToolResult.failure(f"Rejected: {e}")
    except duckdb.Error as e:
        return ToolResult.failure(f"{type(e).__name__}: {e}")
    df = pd.DataFrame(rows, columns=cols)
    needed = [spec.x] + ([spec.y] if spec.y and spec.type != "hist" else [])
    missing = [c for c in needed if c not in df.columns]
    if missing:
        return ToolResult.failure(f"Columns {missing} not in query result {list(df.columns)}.")

    fig, ax = plt.subplots(figsize=(8, 4.5))
    try:
        if spec.type == "bar":
            ax.bar(df[spec.x].astype(str), df[spec.y])
            ax.tick_params(axis="x", rotation=45)
        elif spec.type == "line":
            ax.plot(df[spec.x], df[spec.y])
        elif spec.type == "scatter":
            ax.scatter(df[spec.x], df[spec.y], s=12)
        else:
            ax.hist(df[spec.x].dropna(), bins=30)
        ax.set(title=spec.title, xlabel=spec.x, ylabel=spec.y or "count")
        fig.tight_layout()
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=100)
    except (TypeError, ValueError) as e:
        return ToolResult.failure(f"Could not plot: {e}")
    finally:
        plt.close(fig)
    note = f" (first {MAX_POINTS} rows only)" if more else ""
    return ToolResult.success(
        f"Chart created: {spec.type} of {len(df)} rows{note}.",
        images=[base64.b64encode(buf.getvalue()).decode()],
    )
