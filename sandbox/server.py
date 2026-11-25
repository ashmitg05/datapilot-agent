"""Sandbox HTTP service for Cloud Run (production backend for run_python).

Cloud Run cannot start Docker containers, so this image runs as its own private service:
  - concurrency=1, so one job per instance; gVisor (Cloud Run gen2) isolates the instance
  - ingress internal + IAM auth, so only the API's service account can call it
  - egress forced through a VPC with no NAT, so no internet
  - each job runs runner.py in a child process with rlimits, a clean env and a fresh temp dir,
    in its own empty network namespace (no metadata server, no DNS) and as JOB_UID, so it
    cannot signal, ptrace or overwrite the server (which runs as root to set this up)

POST /run  {"code": str, "files": {"name.csv": "csv text"}}
  -> {"returncode", "stdout", "stderr", "timed_out"}   (parsed by app.sandbox.executor)
"""

import contextlib
import ctypes
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
JOB_UID = 1000
CLONE_NEWNET = 0x40000000
PR_SET_NO_NEW_PRIVS = 38
PR_SET_DUMPABLE = 4
_libc = ctypes.CDLL(None, use_errno=True)


def _isolate() -> None:
    """Child side, before exec: empty netns, then drop to JOB_UID for good. Fails closed:
    any error here aborts the exec, so the job never runs with network or root."""
    if _libc.unshare(CLONE_NEWNET) != 0:
        raise OSError(ctypes.get_errno(), "unshare(CLONE_NEWNET) failed")
    _limits()
    os.setgroups([])
    os.setgid(JOB_UID)
    os.setuid(JOB_UID)
    if _libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "prctl(NO_NEW_PRIVS) failed")


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
    os.chown(job, JOB_UID, JOB_UID)  # HOME/MPLCONFIGDIR; data/ stays root-owned 0555
    os.chown(out, JOB_UID, JOB_UID)
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
        try:
            p = subprocess.Popen(
                [sys.executable, "-I", "/app/runner.py"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                env=env, cwd=job, preexec_fn=_isolate,  # noqa: PLW1509 - netns/uid need it
            )  # fmt: skip
        except subprocess.SubprocessError:  # _isolate failed: never run the job unisolated
            return {"returncode": 3, "stdout": "", "stderr": "sandbox isolation unavailable",
                    "timed_out": False}  # fmt: skip
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
    then read the next user's data. So: from a helper that has dropped to JOB_UID, kill every
    process of that uid (kill(-1, ...) as root would hit the server too), reap, empty /tmp.
    """
    if (pid := os.fork()) == 0:
        try:
            os.setgid(JOB_UID)
            os.setuid(JOB_UID)
            os.kill(-1, signal.SIGKILL)
        finally:
            os._exit(0)
    os.waitpid(pid, 0)
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
    if os.getuid() != 0:
        raise SystemExit("must start as root: jobs need a fresh netns and a dropped uid")
    _libc.prctl(PR_SET_DUMPABLE, 0, 0, 0, 0)  # jobs can't read this process's memory
    # Single-threaded on purpose: one job at a time per instance (Cloud Run concurrency=1), so
    # scrub() never touches a concurrent job.
    HTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8080"))), Handler).serve_forever()  # noqa: S104
