"""Runs inside the sandbox container. Reads agent code from stdin, prints one JSON line.

Every CSV in /data (read-only mount) is loaded as a DataFrame named after the file stem;
with a single CSV it is also available as `df`. Open matplotlib figures and PNGs saved
to /out are returned base64-encoded.
"""

import base64
import contextlib
import io
import json
import os
import sys
import traceback
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

MAX_OUTPUT = 20_000
MAX_IMAGES = 3
MARKER = "__DATAPILOT_RESULT__"
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
OUT_DIR = Path(os.environ.get("OUT_DIR", "/out"))


def main() -> None:
    code = sys.stdin.read()
    tables = {p.stem: pd.read_csv(p) for p in sorted(DATA_DIR.glob("*.csv"))}
    ns: dict[str, object] = {"__name__": "__main__", "pd": pd, "plt": plt, **tables}
    if len(tables) == 1:
        ns["df"] = next(iter(tables.values()))

    buf = io.StringIO()
    error = None
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            exec(compile(code, "<agent>", "exec"), ns)  # noqa: S102 - this is the sandbox
    except BaseException:  # noqa: BLE001 - report every failure, incl. SystemExit
        error = traceback.format_exc(limit=-3)

    images = []
    for num in plt.get_fignums()[:MAX_IMAGES]:
        img = io.BytesIO()
        plt.figure(num).savefig(img, format="png", dpi=100)
        images.append(base64.b64encode(img.getvalue()).decode())
    for png in sorted(OUT_DIR.glob("*.png"))[: MAX_IMAGES - len(images)]:
        images.append(base64.b64encode(png.read_bytes()).decode())

    result = {"stdout": buf.getvalue()[-MAX_OUTPUT:], "error": error, "images": images}
    sys.__stdout__.write("\n" + MARKER + json.dumps(result) + "\n")


if __name__ == "__main__":
    main()
