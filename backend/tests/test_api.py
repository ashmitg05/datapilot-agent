"""API tests with a scripted LLM. SANDBOX_MODE=none so no Docker is needed."""

import json
import os
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ["SANDBOX_MODE"] = "none"

from app.api import main  # noqa: E402
from app.llm import LLMResponse, ScriptedClient, ToolCall  # noqa: E402
from app.tracing import TraceStore  # noqa: E402
from tests.conftest import SALES_CSV  # noqa: E402

SQL = "SELECT region FROM sales GROUP BY 1 ORDER BY sum(revenue) DESC LIMIT 1"


def script() -> ScriptedClient:
    return ScriptedClient(
        [
            LLMResponse("look", [ToolCall("a", "describe_schema", {"table": "sales"})], 10, 5),
            LLMResponse("query", [ToolCall("b", "run_sql", {"query": SQL})], 10, 5),
            LLMResponse("", [ToolCall("c", "final_answer",
                                      {"answer": "East", "value": "East", "evidence": [SQL]})],
                        10, 5),
        ]
    )  # fmt: skip


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    samples = tmp_path / "samples" / "demo"
    samples.mkdir(parents=True)
    (samples / "sales.csv").write_text(SALES_CSV)
    monkeypatch.setattr(main, "SAMPLES_DIR", tmp_path / "samples")
    monkeypatch.setattr(main, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(main, "SAMPLES_ONLY", False)
    monkeypatch.setattr(main, "limiter", main.RateLimiter(3))
    store = TraceStore(tmp_path / "t.sqlite3")
    main.app.dependency_overrides[main.get_llm] = script
    main.app.dependency_overrides[main.get_store] = lambda: store
    with TestClient(main.app) as c:
        yield c
    main.app.dependency_overrides.clear()


def events(resp_text: str) -> list[dict[str, object]]:
    return [json.loads(x[6:]) for x in resp_text.split("\n\n") if x.startswith("data: ")]


def test_health(client: TestClient) -> None:
    assert client.get("/health").json()["status"] == "ok"


def test_samples(client: TestClient) -> None:
    [s] = client.get("/datasets/samples").json()
    assert s["id"] == "demo" and s["tables"] == ["sales"] and "revenue" in s["schema"]["sales"]


def test_ask_streams_steps_and_saves_trace(client: TestClient) -> None:
    r = client.post("/ask", json={"dataset_id": "demo", "question": "Top region?"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    evs = events(r.text)
    assert [e["type"] for e in evs] == ["step", "step", "step", "done", "trace"]
    assert evs[3]["final"]["value"] == "East"  # type: ignore[index]
    trace = client.get(f"/traces/{evs[4]['trace_id']}").json()
    assert [s["tool"] for s in trace["steps"]] == ["describe_schema", "run_sql", "final_answer"]


def test_unknown_dataset(client: TestClient) -> None:
    assert client.post("/ask", json={"dataset_id": "../etc", "question": "hi?"}).status_code == 404
    assert client.post("/ask", json={"dataset_id": "nope", "question": "hi?"}).status_code == 404


def test_rate_limit(client: TestClient) -> None:
    codes = [
        client.post("/ask", json={"dataset_id": "demo", "question": "Top region?"}).status_code
        for _ in range(4)
    ]
    assert codes == [200, 200, 200, 429]


def test_question_validation(client: TestClient) -> None:
    r = client.post("/ask", json={"dataset_id": "demo", "question": "x" * 501})
    assert r.status_code == 422


def test_upload_and_ask(client: TestClient) -> None:
    r = client.post("/datasets", files={"file": ("my.csv", b"a,b\n1,2\n3,4\n", "text/csv")})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"].startswith("upload-") and "2 rows" in body["schema"]["data"]


@pytest.mark.parametrize(
    ("name", "data", "code"),
    [
        ("x.txt", b"a\n1\n", 400),
        ("x.csv", b"\xff\xfe\x00bad", 400),
        ("x.csv", b"onlyheader", 400),
    ],
)
def test_upload_rejects(client: TestClient, name: str, data: bytes, code: int) -> None:
    assert client.post("/datasets", files={"file": (name, data, "text/csv")}).status_code == code


def test_upload_size_limit(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "MAX_UPLOAD_BYTES", 100)
    big = b"a,b\n" + b"1,2\n" * 100
    assert client.post("/datasets", files={"file": ("x.csv", big, "text/csv")}).status_code == 413


def test_samples_only_mode(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "SAMPLES_ONLY", True)
    r = client.post("/datasets", files={"file": ("x.csv", b"a\n1\n", "text/csv")})
    assert r.status_code == 403


def test_upload_expiry(client: TestClient) -> None:
    r = client.post("/datasets", files={"file": ("x.csv", b"a\n1\n", "text/csv")})
    uid = r.json()["id"]
    assert main.cleanup_uploads(now=time.time() + 25 * 3600) == 1
    assert client.post("/ask", json={"dataset_id": uid, "question": "count?"}).status_code == 404


def test_cost_cap_stops_agent(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "COST_CAP_USD", 0.0)
    monkeypatch.setenv("LLM_PRICE_IN_PER_MTOK", "1")
    r = client.post("/ask", json={"dataset_id": "demo", "question": "Top region?"})
    done = next(e for e in events(r.text) if e["type"] == "done")
    assert done["summary"]["status"] == "cost_cap"  # type: ignore[index]


def test_rate_limit_ignores_spoofed_forwarded_for(client: TestClient) -> None:
    codes = [
        client.post(
            "/ask",
            json={"dataset_id": "demo", "question": "Top region?"},
            headers={"X-Forwarded-For": f"10.0.0.{i}, 203.0.113.7"},
        ).status_code
        for i in range(4)
    ]
    assert codes == [200, 200, 200, 429]


def test_ipv6_rotation_shares_one_quota(client: TestClient) -> None:
    codes = [
        client.post("/ask", json={"dataset_id": "demo", "question": "Top region?"},
                    headers={"X-Forwarded-For": f"2001:db8:1:2::{i:x}"}).status_code
        for i in range(4)
    ]  # fmt: skip
    assert codes == [200, 200, 200, 429]


def test_global_cap_and_budget() -> None:
    lim = main.RateLimiter(10, total=2, budget_usd=1.0)
    assert lim.hit("a") is None and lim.hit("b") is None
    assert "question limit" in (lim.hit("c") or "")
    lim = main.RateLimiter(10, total=99, budget_usd=1.0)
    assert lim.hit("a") is None
    lim.spend(1.5)
    assert "budget" in (lim.hit("b") or "")


def test_client_key() -> None:
    assert main.client_key("2001:db8::1") == main.client_key("2001:db8::ffff") == "2001:db8::/64"
    assert main.client_key("::ffff:203.0.113.7") == "203.0.113.7"
    assert main.client_key("203.0.113.7") == "203.0.113.7"
