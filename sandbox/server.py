"""Sandbox HTTP service for Cloud Run (production backend for run_python).

Cloud Run cannot start Docker containers, so this image runs as its own private service:
  - concurrency=1, so one job per instance; gVisor (Cloud Run gen2) isolates the instance
  - ingress internal + IAM auth, so only the API's service account can call it
  - egress forced through a VPC with no NAT, so no internet
  - each job runs runner.py in a child process with rlimits, a clean env and a fresh temp dir

POST /run  {"code": str, "files": {"name.csv": "csv text"}}
  -> {"returncode", "stdout", "stderr", "timed_out"}   (parsed by app.sandbox.executor)
"""

import contextlib
import json
import os
import re
import resource
import shutil
import signal
import subprocess
import sys
import tempfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

TIMEOUT_S = 15
# Address-space cap, not RSS: numpy/OpenBLAS reserve virtual memory, so 512 MB of AS breaks
# imports. 1 GiB AS with single-threaded BLAS keeps real usage near the 512 MB service limit.
MEMORY_BYTES = 1024 * 1024 * 1024
MAX_BODY = 12 * 1024 * 1024
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}\.csv$")


def _limits() -> None:
    resource.setrlimit(resource.RLIMIT_AS, (MEMORY_BYTES, MEMORY_BYTES))
    resource.setrlimit(resource.RLIMIT_CPU, (TIMEOUT_S + 5, TIMEOUT_S + 5))  # wall clock wins
    resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))
    resource.setrlimit(resource.RLIMIT_FSIZE, (32 * 1024 * 1024,) * 2)
    os.setsid()


def run_job(code: str, files: dict[str, str]) -> dict[str, object]:
    job = Path(tempfile.mkdtemp(prefix="job-"))
    data, out = job / "data", job / "out"
    data.mkdir()
    out.mkdir()
    try:
        for name, text in files.items():
            if not _NAME.match(name):
                return {"returncode": 2, "stdout": "", "stderr": f"bad file name {name!r}",
                        "timed_out": False}  # fmt: skip
            (data / name).write_text(text, encoding="utf-8")
        data.chmod(0o555)
        env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(job), "MPLCONFIGDIR": str(job),
               "DATA_DIR": str(data), "OUT_DIR": str(out), "PYTHONDONTWRITEBYTECODE": "1",
               "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1", "MALLOC_ARENA_MAX": "2"}  # fmt: skip
        p = subprocess.Popen(
            [sys.executable, "-I", "/app/runner.py"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env=env, cwd=job, preexec_fn=_limits,  # noqa: PLW1509 - rlimits need it
        )  # fmt: skip
        timed_out = False
        try:
            stdout, stderr = p.communicate(code, timeout=TIMEOUT_S)
        except subprocess.TimeoutExpired:
            timed_out = True
            stdout, stderr = "", ""
        finally:
            # The child leads its own session (setsid): kill every descendant, e.g. fork bombs.
            with contextlib.suppress(ProcessLookupError):
                os.killpg(p.pid, signal.SIGKILL)
            p.wait()
        return {"returncode": p.returncode, "stdout": stdout[-200_000:],
                "stderr": stderr[-4000:], "timed_out": timed_out}  # fmt: skip
    finally:
        scrub()


def scrub() -> None:
    """Leave nothing from a job behind for the next one (warm instances are reused).

    A job can detach a process into a new session (escaping killpg) or drop files in /tmp, and
    then read the next user's data. So: kill every process this uid can signal (kill(-1, ...)
    skips init and the caller), reap them, and empty the temp dir.
    """
    with contextlib.suppress(ProcessLookupError):
        os.kill(-1, signal.SIGKILL)
    with contextlib.suppress(ChildProcessError):
        while os.waitpid(-1, os.WNOHANG)[0]:
            pass
    tmp = Path(tempfile.gettempdir())
    for entry in tmp.iterdir():
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry, ignore_errors=True)
        else:
            with contextlib.suppress(OSError):
                entry.unlink()


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: dict[str, object]) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        self._send(200, {"status": "ok"})

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        if self.path != "/run" or not 0 < length <= MAX_BODY:
            self._send(400, {"error": "bad request"})
            return
        try:
            body = json.loads(self.rfile.read(length))
            code, files = str(body["code"]), dict(body.get("files", {}))
        except (ValueError, KeyError, TypeError):
            self._send(400, {"error": "invalid JSON"})
            return
        self._send(200, run_job(code, files))


if __name__ == "__main__":
    if os.getuid() == 0:
        raise SystemExit("refusing to run as root: scrub() kills every process of this user")
    # Single-threaded on purpose: one job at a time per instance (Cloud Run concurrency=1), so
    # scrub() never touches a concurrent job.
    HTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8080"))), Handler).serve_forever()  # noqa: S104
