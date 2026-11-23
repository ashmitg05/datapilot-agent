"""Download the three benchmark datasets into benchmark/data/<dataset>/<table>.csv.

Run once:  uv run python -m benchmark.download
Licenses and sources: benchmark/DATASETS.md. A sha256 manifest is written so gold answers
are tied to an exact snapshot (the NYC 311 API is live data).
"""

from __future__ import annotations

import hashlib
import io
import json
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

import pandas as pd

DATA = Path(__file__).parent / "data"
UA = {"User-Agent": "datapilot-benchmark/0.1"}

RETAIL_URL = "https://archive.ics.uci.edu/static/public/352/online+retail.zip"
BIKES_URL = "https://archive.ics.uci.edu/static/public/275/bike+sharing+dataset.zip"
NYC311_COLUMNS = (
    "unique_key,created_date,closed_date,agency,complaint_type,descriptor,borough,city,"
    "incident_zip,status"
)
NYC311_URL = "https://data.cityofnewyork.us/resource/erm2-nwe9.csv?" + urllib.parse.urlencode(
    {
        "$select": NYC311_COLUMNS,
        "$where": "created_date between '2023-01-01T00:00:00' and '2023-01-07T23:59:59'",
        "$order": "unique_key",
        "$limit": "200000",
    }
)


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers=UA)  # noqa: S310 - fixed https URLs above
    with urllib.request.urlopen(req, timeout=300) as r:  # noqa: S310
        data: bytes = r.read()
    return data


def retail() -> None:
    with zipfile.ZipFile(io.BytesIO(_get(RETAIL_URL))) as z:
        name = next(n for n in z.namelist() if n.endswith(".xlsx"))
        df = pd.read_excel(z.open(name), dtype={"InvoiceNo": str, "StockCode": str})
    df.columns = pd.Index(["invoice_no", "stock_code", "description", "quantity", "invoice_date",
                  "unit_price", "customer_id", "country"])  # fmt: skip
    df["customer_id"] = df["customer_id"].astype("Int64")
    _save(df, "retail", "retail")


def bikes() -> None:
    with zipfile.ZipFile(io.BytesIO(_get(BIKES_URL))) as z:
        df = pd.read_csv(z.open("day.csv"))
    _save(df, "bikes", "bike_day")


def nyc311() -> None:
    df = pd.read_csv(io.BytesIO(_get(NYC311_URL)), dtype={"incident_zip": str})
    _save(df, "nyc311", "complaints")


def _save(df: pd.DataFrame, dataset: str, table: str) -> None:
    out = DATA / dataset / f"{table}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    print(f"{out}: {len(df)} rows")


def main() -> None:
    for fn in (retail, bikes, nyc311):
        fn()
    manifest = {
        str(p.relative_to(DATA)).replace("\\", "/"): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(DATA.glob("*/*.csv"))
    }
    (DATA / "manifest.json").write_text(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
