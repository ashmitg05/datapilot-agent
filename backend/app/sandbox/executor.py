"""Runs agent-written Python in a locked-down Docker container.

Local/dev backend. Cloud Run cannot run Docker, so production gets a second backend
in Phase 7 (private sandbox service) behind the same run() signature.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from app.tools.result import ToolResult

IMAGE = os.environ.get("SANDBOX_IMAGE", "datapilot-sandbox:latest")


class Executor(Protocol):
    def run(self, code: str, data_dir: Path) -> ToolResult: ...


def executor_from_env() -> Executor | None:
    """SANDBOX_MODE: docker (local), http (Cloud Run sandbox service), none (SQL-only)."""
    mode = os.environ.get("SANDBOX_MODE", "docker").lower()
    if mode == "docker":
        return DockerExecutor()
    if mode == "http":
        return HttpExecutor(os.environ["SANDBOX_URL"])
    return None


class HttpExecutor:
    """Calls the private sandbox Cloud Run service (sandbox/server.py).

    The dataset CSVs are sent with the code; the service holds no data. On Cloud Run the
    request is authenticated with a Google ID token for the service URL.
    """

    def __init__(self, url: str, timeout_s: float = 30.0) -> None:
        self.url = url.rstrip("/")
        self.timeout_s = timeout_s

    def _token(self) -> str | None:
        try:
            import google.auth.transport.requests
            import google.oauth2.id_token

            req = google.auth.transport.requests.Request()
            token: str = google.oauth2.id_token.fetch_id_token(req, self.url)  # type: ignore[no-untyped-call]
            return token
        except Exception:  # noqa: BLE001 - local dev without GCP credentials
            return None

    def run(self, code: str, data_dir: Path) -> ToolResult:
        import httpx

        files = {p.name: p.read_text(encoding="utf-8") for p in sorted(data_dir.glob("*.csv"))}
        token = self._token()
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        try:
            r = httpx.post(
                f"{self.url}/run",
                json={"code": code, "files": files},
                headers=headers,
                timeout=self.timeout_s,
            )
        except httpx.HTTPError as e:
            return ToolResult.failure(f"Sandbox unreachable: {type(e).__name__}")
        if r.status_code != 200:
            return ToolResult.failure(f"Sandbox error {r.status_code}: {r.text[:300]}")
        return _parse(r.json())


MARKER = "__DATAPILOT_RESULT__"


@dataclass(frozen=True)
class Limits:
    cpus: float = 1.0
    memory_mb: int = 512
    timeout_s: float = 15.0  # wall clock, includes ~1s container start
    pids: int = 64


class DockerExecutor:
    def __init__(self, image: str = IMAGE, limits: Limits | None = None) -> None:
        self.image = image
        self.limits = limits or Limits()

    def command(self, name: str, data_dir: Path) -> list[str]:
        lim = self.limits
        return [
            "docker", "run", "--rm", "-i", "--name", name,
            "--network", "none",
            "--read-only",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",  # noqa: S108 - container tmpfs
            "--tmpfs", "/out:rw,noexec,nosuid,size=32m,mode=1777",
            "--user", "1000:1000",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--cpus", str(lim.cpus),
            "--memory", f"{lim.memory_mb}m",
            "--memory-swap", f"{lim.memory_mb}m",
            "--pids-limit", str(lim.pids),
            "-v", f"{data_dir.resolve()}:/data:ro",
            self.image,
        ]  # fmt: skip

    def run(self, code: str, data_dir: Path) -> ToolResult:
        name = f"datapilot-sbx-{uuid.uuid4().hex[:12]}"
        proc = subprocess.Popen(
            self.command(name, data_dir),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8",
        )  # fmt: skip
        out, err, timed_out, over = _capped(proc, code, self.limits.timeout_s, MAX_OUTPUT_BYTES)
        if timed_out or over:
            subprocess.run(["docker", "kill", name], capture_output=True, check=False)
        proc.wait()
        if over:
            return ToolResult.failure(f"Output limit exceeded ({MAX_OUTPUT_BYTES} bytes).")
        if timed_out:
            return ToolResult.failure(
                f"Execution timed out after {self.limits.timeout_s:.0f}s. Simplify the code."
            )
        return parse_process(proc.returncode, out, err, self.limits)


MAX_OUTPUT_BYTES = 1024 * 1024


def _capped(
    proc: subprocess.Popen[str], code: str, timeout: float, cap: int
) -> tuple[str, str, bool, bool]:
    """Feed stdin and read both pipes, but stop at `cap` chars per stream or `timeout`, so
    job output cannot grow the API process's memory. Returns (out, err, timed_out, over)."""
    bufs: dict[str, list[str]] = {"out": [], "err": []}
    over = threading.Event()

    def pump(key: str, stream: Any) -> None:
        size = 0
        while chunk := stream.read(65536):
            size += len(chunk)
            if size > cap:
                over.set()
                return
            bufs[key].append(chunk)

    threads = [threading.Thread(target=pump, args=(k, st), daemon=True)
               for k, st in (("out", proc.stdout), ("err", proc.stderr))]  # fmt: skip
    for t in threads:
        t.start()
    with contextlib.suppress(BrokenPipeError, OSError):
        assert proc.stdin is not None
        proc.stdin.write(code)
        proc.stdin.close()
    deadline = time.monotonic() + timeout
    timed_out = False
    while any(t.is_alive() for t in threads) and not over.is_set():
        if time.monotonic() > deadline:
            timed_out = True
            break
        time.sleep(0.05)
    if timed_out or over.is_set():
        proc.kill()
    for t in threads:
        t.join(1)
    return "".join(bufs["out"]), "".join(bufs["err"]), timed_out, over.is_set()


def parse_process(returncode: int, stdout: str, stderr: str, limits: Limits) -> ToolResult:
    """Shared by both backends: turns the runner's process output into a ToolResult."""
    if returncode in (137, -9):
        return ToolResult.failure(
            f"Killed: resource limit hit (memory {limits.memory_mb} MB). Use less data."
        )
    _, sep, payload = stdout.rpartition(MARKER)
    if not sep:
        return ToolResult.failure(f"Sandbox failed (exit {returncode}).", output=stderr[-2000:])
    try:
        result = json.loads(payload)
    except json.JSONDecodeError:
        return ToolResult.failure("Sandbox returned malformed output.")
    if result["error"]:
        return ToolResult.failure(result["error"], output=result["stdout"])
    return ToolResult.success(result["stdout"] or "(no output)", images=result["images"])


def _parse(body: dict[str, Any]) -> ToolResult:
    if body.get("timed_out"):
        return ToolResult.failure(
            f"Execution timed out after {Limits().timeout_s:.0f}s. Simplify the code."
        )
    return parse_process(body["returncode"], body["stdout"], body["stderr"], Limits())
