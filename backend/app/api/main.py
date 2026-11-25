"""HTTP API. Run: uv run uvicorn app.api.main:app --reload"""

from __future__ import annotations

import datetime as dt
import ipaddress
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
GLOBAL_DAILY_LIMIT = int(os.environ.get("GLOBAL_DAILY_QUESTION_LIMIT", "300"))
DAILY_BUDGET_USD = float(os.environ.get("DAILY_BUDGET_USD", "5"))
COST_CAP_USD = float(os.environ.get("COST_CAP_USD", "0.25"))
UPLOAD_TTL_S = 24 * 3600
MAX_COLUMNS = 200
_ID = re.compile(r"^[a-z0-9_-]{1,64}$")


# ponytail: in-memory, per instance. DEPLOY.md pins the API to --max-instances 1 so these are
# real global caps; move the counters to Redis/Firestore before scaling out.
class RateLimiter:
    """Per-client daily questions, plus a global daily question cap and spend budget, so
    rotating IPs can't run up the LLM bill past DAILY_BUDGET_USD."""

    def __init__(
        self, limit: int, total: int = GLOBAL_DAILY_LIMIT, budget_usd: float = DAILY_BUDGET_USD
    ) -> None:
        self.limit, self.total, self.budget_usd = limit, total, budget_usd
        self._counts: dict[str, int] = defaultdict(int)
        self._spent = 0.0
        self._day = dt.date.today()
        self._lock = threading.Lock()

    def _roll(self) -> None:
        if (today := dt.date.today()) != self._day:
            self._day, self._counts, self._spent = today, defaultdict(int), 0.0

    def hit(self, key: str) -> str | None:
        """Count one question. Returns None if allowed, else the reason it was refused."""
        with self._lock:
            self._roll()
            if self._spent >= self.budget_usd:
                return "The demo's daily budget is used up. Try again tomorrow."
            if sum(self._counts.values()) >= self.total:
                return "The demo's daily question limit is reached. Try again tomorrow."
            if self._counts[key] >= self.limit:
                return f"Daily limit of {self.limit} questions reached."
            self._counts[key] += 1
            return None

    def spend(self, usd: float) -> None:
        with self._lock:
            self._roll()
            self._spent += usd


def client_key(ip: str) -> str:
    """One IPv6 user usually holds a whole /64, so count the /64, not the address."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    if isinstance(addr, ipaddress.IPv6Address):
        if addr.ipv4_mapped:
            return str(addr.ipv4_mapped)
        return str(ipaddress.ip_network(f"{addr}/64", strict=False))
    return str(addr)


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
    if refused := limiter.hit(client_key(client_ip(request))):
        raise HTTPException(429, refused)
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
            limiter.spend(state.cost_usd)
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
