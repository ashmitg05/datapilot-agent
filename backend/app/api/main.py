"""HTTP API. Run: uv run uvicorn app.api.main:app --reload"""

from __future__ import annotations

import datetime as dt
import json
import os
import queue
import re
import shutil
import threading
import time
import uuid
from collections import defaultdict
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.agent.loop import run_agent
from app.agent.state import AgentConfig
from app.llm import LLMClient, Pricing, from_env
from app.sandbox.executor import executor_from_env
from app.tools import ToolContext
from app.tools.sql import Database, DescribeSchemaInput, describe_schema
from app.tracing import TraceStore

BACKEND = Path(__file__).resolve().parents[2]
SAMPLES_DIR = Path(os.environ.get("SAMPLES_DIR", BACKEND / "benchmark" / "data"))
UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR", BACKEND / "uploads"))
SAMPLES_ONLY = os.environ.get("SAMPLES_ONLY", "false").lower() == "true"
MAX_UPLOAD_BYTES = int(float(os.environ.get("MAX_UPLOAD_MB", "10")) * 1024 * 1024)
DAILY_LIMIT = int(os.environ.get("DAILY_QUESTION_LIMIT", "15"))
COST_CAP_USD = float(os.environ.get("COST_CAP_USD", "0.25"))
UPLOAD_TTL_S = 24 * 3600
MAX_COLUMNS = 200
_ID = re.compile(r"^[a-z0-9_-]{1,64}$")


# ponytail: in-memory per-instance counter. Cloud Run may run several instances, so the real
# limit is DAILY_LIMIT x instances; move to Redis/Postgres if that matters.
class RateLimiter:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self._counts: dict[tuple[str, dt.date], int] = defaultdict(int)
        self._lock = threading.Lock()

    def hit(self, key: str) -> bool:
        today = dt.date.today()
        with self._lock:
            for k in [k for k in self._counts if k[1] != today]:
                del self._counts[k]
            if self._counts[(key, today)] >= self.limit:
                return False
            self._counts[(key, today)] += 1
            return True


def cleanup_uploads(now: float | None = None) -> int:
    now = now or time.time()
    removed = 0
    for d in UPLOAD_DIR.glob("*"):
        if d.is_dir() and now - d.stat().st_mtime > UPLOAD_TTL_S:
            shutil.rmtree(d, ignore_errors=True)
            removed += 1
    return removed


def _cleanup_loop(stop: threading.Event) -> None:
    while not stop.wait(3600):
        cleanup_uploads()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    cleanup_uploads()
    stop = threading.Event()
    threading.Thread(target=_cleanup_loop, args=(stop,), daemon=True).start()
    yield
    stop.set()


app = FastAPI(title="DataPilot", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("CORS_ORIGINS", "http://localhost:3000").split(","),
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)
limiter = RateLimiter(DAILY_LIMIT)
_store: TraceStore | None = None


def get_store() -> TraceStore:
    global _store
    if _store is None:
        _store = TraceStore()
    return _store


def get_llm() -> LLMClient:
    return from_env()


def client_ip(request: Request) -> str:
    # Clients can send their own X-Forwarded-For; Cloud Run's front end appends the address it
    # actually saw, so only the last entry is trustworthy. Never key on the first entry.
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[-1].strip()
    return request.client.host if request.client else "?"


def dataset_dir(dataset_id: str) -> Path:
    if dataset_id.startswith("upload-"):
        uid = dataset_id.removeprefix("upload-")
        if not _ID.match(uid):
            raise HTTPException(404, "Unknown dataset")
        path = UPLOAD_DIR / uid
    else:
        if not _ID.match(dataset_id):
            raise HTTPException(404, "Unknown dataset")
        path = SAMPLES_DIR / dataset_id
    if not path.is_dir() or not any(path.glob("*.csv")):
        raise HTTPException(404, "Unknown or expired dataset")
    return path


def _schema(db: Database) -> dict[str, str]:
    return {t: describe_schema(db, DescribeSchemaInput(table=t)).output for t in sorted(db.tables)}


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "samples_only": SAMPLES_ONLY}


@app.get("/datasets/samples")
def samples() -> list[dict[str, Any]]:
    out = []
    for d in sorted(p for p in SAMPLES_DIR.glob("*") if p.is_dir() and any(p.glob("*.csv"))):
        db = Database(d)
        try:
            out.append({"id": d.name, "tables": sorted(db.tables), "schema": _schema(db)})
        finally:
            db.close()
    return out


@app.post("/datasets")
async def upload(file: UploadFile) -> dict[str, Any]:
    if SAMPLES_ONLY:
        raise HTTPException(403, "Uploads are disabled on this demo. Pick a sample dataset.")
    if not (file.filename or "").lower().endswith(".csv"):
        raise HTTPException(400, "Only .csv files are accepted.")
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"File too large (max {MAX_UPLOAD_BYTES // 1024 // 1024} MB).")
    try:
        header = data[:65536].decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(400, "File must be UTF-8 encoded CSV.") from None
    if header.count("\n") < 1:
        raise HTTPException(400, "CSV needs a header row and at least one data row.")

    uid = uuid.uuid4().hex
    target = UPLOAD_DIR / uid
    target.mkdir(parents=True)
    (target / "data.csv").write_bytes(data)
    try:
        db = Database(target)
    except Exception as e:  # noqa: BLE001 - any parse failure is a bad upload
        shutil.rmtree(target, ignore_errors=True)
        raise HTTPException(400, f"Could not parse CSV: {str(e)[:200]}") from None
    try:
        _, cols, _ = db.query('DESCRIBE "data"', limit=MAX_COLUMNS + 1)
        if len(cols) > MAX_COLUMNS:
            raise HTTPException(400, f"Too many columns (max {MAX_COLUMNS}).")
        return {"id": f"upload-{uid}", "tables": ["data"], "schema": _schema(db),
                "expires_in_hours": 24}  # fmt: skip
    finally:
        db.close()


class AskRequest(BaseModel):
    dataset_id: str
    question: str = Field(min_length=3, max_length=500)
    verification: bool = False


def _sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, default=str)}\n\n"


@app.post("/ask")
def ask(
    body: AskRequest,
    request: Request,
    llm: LLMClient = Depends(get_llm),  # noqa: B008
    store: TraceStore = Depends(get_store),  # noqa: B008
) -> StreamingResponse:
    path = dataset_dir(body.dataset_id)
    if not limiter.hit(client_ip(request)):
        raise HTTPException(429, f"Daily limit of {DAILY_LIMIT} questions reached.")
    executor = executor_from_env()
    config = AgentConfig(
        verification=body.verification,
        allow_python=executor is not None,
        cost_cap_usd=COST_CAP_USD,
    )
    events: queue.Queue[dict[str, Any] | None] = queue.Queue()

    def work() -> None:
        db: Database | None = None
        try:
            db = Database(path)
            state = run_agent(body.question, llm, ToolContext(db, executor), config,
                              Pricing.from_env(), _emitter(events))  # fmt: skip
            trace_id = store.save(state, body.dataset_id, llm.model, config)
            events.put({"type": "trace", "trace_id": trace_id})
        except Exception as e:  # noqa: BLE001 - surface provider errors to the client
            events.put({"type": "error", "message": str(e)[:300]})
        finally:
            if db is not None:
                db.close()
            events.put(None)

    threading.Thread(target=work, daemon=True).start()

    def stream() -> Iterator[str]:
        while (event := events.get()) is not None:
            yield _sse(event)

    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    return StreamingResponse(stream(), media_type="text/event-stream", headers=headers)


def _emitter(q: queue.Queue[dict[str, Any] | None]) -> Callable[[dict[str, Any]], None]:
    return q.put


@app.get("/traces/{trace_id}")
def trace(trace_id: str, store: TraceStore = Depends(get_store)) -> dict[str, Any]:  # noqa: B008
    t = store.get(trace_id)
    if t is None:
        raise HTTPException(404, "Trace not found")
    return t
