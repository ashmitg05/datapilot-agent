"""Malicious Python must be contained by the Docker sandbox. Needs Docker + the image:

docker build -t datapilot-sandbox:latest sandbox
"""

import subprocess
import time
from pathlib import Path

import pytest

from app.sandbox.executor import IMAGE, DockerExecutor, Limits, _parse
from app.tools.result import ToolResult


def _image_available() -> bool:
    try:
        r = subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True, timeout=10)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0


pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(not _image_available(), reason=f"Docker image {IMAGE} not available"),
]


@pytest.fixture(scope="module")
def sandbox() -> DockerExecutor:
    return DockerExecutor(limits=Limits(timeout_s=8))


def test_runs_pandas_on_dataset(sandbox: DockerExecutor, data_dir: Path) -> None:
    r = sandbox.run("print(df.groupby('region')['revenue'].sum().idxmax())", data_dir)
    assert r.ok, r.error
    assert r.output.strip() == "East"


def test_returns_charts(sandbox: DockerExecutor, data_dir: Path) -> None:
    r = sandbox.run("df.plot.bar(x='region', y='revenue')", data_dir)
    assert r.ok, r.error
    assert len(r.images) == 1


def test_error_returns_traceback(sandbox: DockerExecutor, data_dir: Path) -> None:
    r = sandbox.run("df['nope']", data_dir)
    assert not r.ok and "KeyError" in (r.error or "")


def test_runs_as_non_root(sandbox: DockerExecutor, data_dir: Path) -> None:
    r = sandbox.run("import os; print(os.getuid())", data_dir)
    assert r.output.strip() == "1000"


def test_no_network(sandbox: DockerExecutor, data_dir: Path) -> None:
    code = (
        "import socket\n"
        "s = socket.socket(); s.settimeout(3)\n"
        "s.connect(('1.1.1.1', 80))\n"
        "print('CONNECTED')"
    )
    r = sandbox.run(code, data_dir)
    assert not r.ok and "CONNECTED" not in r.output


def test_no_dns_or_http(sandbox: DockerExecutor, data_dir: Path) -> None:
    code = "import urllib.request\nurllib.request.urlopen('http://example.com', timeout=3)"
    r = sandbox.run(code, data_dir)
    assert not r.ok and "URLError" in (r.error or "")


def test_dataset_mount_is_read_only(sandbox: DockerExecutor, data_dir: Path) -> None:
    r = sandbox.run("open('/data/sales.csv', 'w').write('pwned')", data_dir)
    assert not r.ok and "Read-only file system" in (r.error or "")
    assert "pwned" not in (data_dir / "sales.csv").read_text()


@pytest.mark.parametrize("path", ["/evil.txt", "/app/runner.py", "/etc/passwd", "/home/x"])
def test_root_filesystem_is_read_only(sandbox: DockerExecutor, data_dir: Path, path: str) -> None:
    r = sandbox.run(f"open({path!r}, 'w').write('x')", data_dir)
    assert not r.ok


def test_host_files_not_visible(sandbox: DockerExecutor, data_dir: Path) -> None:
    secret = data_dir.parent / "host_secret.txt"
    secret.write_text("TOPSECRET")
    code = (
        "import subprocess\n"
        "print(subprocess.run(['grep', '-rl', 'TOPSECRET', '/', '--exclude-dir=proc'],"
        " capture_output=True, text=True, timeout=5).stdout)"
    )
    r = sandbox.run(code, data_dir)
    assert "host_secret" not in r.output


def test_host_env_not_leaked(
    sandbox: DockerExecutor, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LLM_API_KEY", "sk-should-not-leak")
    r = sandbox.run("import os; print(dict(os.environ))", data_dir)
    assert r.ok, r.error
    assert "sk-should-not-leak" not in r.output and "LLM_API_KEY" not in r.output


def test_out_dir_is_writable(sandbox: DockerExecutor, data_dir: Path) -> None:
    code = "df.plot(x='order_id', y='revenue').figure.savefig('/out/a.png')"
    r = sandbox.run(code + "\nimport matplotlib.pyplot as p; p.close('all')", data_dir)
    assert r.ok, r.error
    assert len(r.images) == 1


def test_infinite_loop_times_out(sandbox: DockerExecutor, data_dir: Path) -> None:
    start = time.monotonic()
    r = sandbox.run("while True: pass", data_dir)
    assert not r.ok and "timed out" in (r.error or "")
    assert time.monotonic() - start < 15


def test_fork_bomb_contained(sandbox: DockerExecutor, data_dir: Path) -> None:
    start = time.monotonic()
    r = sandbox.run(
        "import os\nwhile True:\n    try: os.fork()\n    except OSError: pass", data_dir
    )
    assert not r.ok
    assert time.monotonic() - start < 20
    # Host still healthy and no container left behind.
    ps = subprocess.run(
        ["docker", "ps", "-q", "--filter", "name=datapilot-sbx-"], capture_output=True, text=True
    )
    assert ps.stdout.strip() == ""


def test_memory_bomb_killed(sandbox: DockerExecutor, data_dir: Path) -> None:
    r = sandbox.run("x = bytearray(2 * 1024**3)\nprint('ALLOCATED')", data_dir)
    assert not r.ok and "ALLOCATED" not in r.output


def test_huge_output_truncated(sandbox: DockerExecutor, data_dir: Path) -> None:
    r = sandbox.run("print('A' * 10_000_000)", data_dir)
    assert r.ok and r.truncated and len(r.output) < 5000


def test_cannot_escalate(sandbox: DockerExecutor, data_dir: Path) -> None:
    r = sandbox.run("import os; os.setuid(0)", data_dir)
    assert not r.ok and "PermissionError" in (r.error or "")


# --- Cloud Run backend: same attacks against sandbox/server.py's run_job (rlimits, no Docker
# flags). The server starts as root and puts each job in an empty netns as uid 1000, so these
# containers get the default bridge network (internet) to prove the job itself can't reach it.
SERVER_DOCKER = ["docker", "run", "--rm", "-i", "--user", "0", "--cap-drop", "ALL",
                 "--cap-add", "SYS_ADMIN", "--cap-add", "SETUID", "--cap-add", "SETGID",
                 "--cap-add", "CHOWN", "--cap-add", "KILL", "--cap-add", "DAC_OVERRIDE",
                 "--entrypoint", "python", IMAGE]  # fmt: skip


def _server_job(
    code: str, files: dict[str, str] | None = None, docker: list[str] | None = None
) -> ToolResult:
    import json

    script = (
        "import sys, json; sys.path.insert(0, '/app'); import server; "
        "print(json.dumps(server.run_job(sys.stdin.read(), json.loads(sys.argv[1]))))"
    )
    p = subprocess.run(
        [*(docker or SERVER_DOCKER), "-c", script, json.dumps(files or {})],
        input=code, capture_output=True, text=True, timeout=60,
    )  # fmt: skip
    return _parse(json.loads(p.stdout))


def test_server_runs_code() -> None:
    r = _server_job("print(df['a'].sum())", {"t.csv": "a\n1\n2\n"})
    assert r.ok and r.output.strip() == "3"


def test_server_timeout() -> None:
    assert "timed out" in (_server_job("while True: pass").error or "")


def test_server_memory_limit() -> None:
    r = _server_job("x = bytearray(2 * 1024**3)\nprint('ALLOCATED')")
    assert not r.ok and "ALLOCATED" not in r.output


def test_server_fork_bomb() -> None:
    r = _server_job("import os\nwhile True:\n    try: os.fork()\n    except OSError: pass")
    assert not r.ok


def test_server_rejects_path_traversal_names() -> None:
    assert not _server_job("print(1)", {"../../etc/x.csv": "a\n1\n"}).ok


def test_server_clean_env() -> None:
    r = _server_job("import os; print(sorted(os.environ))")
    assert r.ok and "KEY" not in r.output.replace("GPG_KEY", "")


_LEAK_POC = r"""
import sys
sys.path.insert(0, "/app")
import server
spy = (
    "import glob, shutil, time\n"
    "for _ in range(400):\n"
    "    for f in glob.glob('/tmp/job-*/data/*.csv'):\n"
    "        shutil.copy(f, '/tmp/stash.csv')\n"
    "    time.sleep(0.05)\n"
)
attacker = (
    "import subprocess, sys\n"
    f"subprocess.Popen([sys.executable, '-c', {spy!r}], start_new_session=True,"
    " stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
)
server.run_job(attacker, {})
server.run_job("import time; time.sleep(1)", {"secret.csv": "ssn\nVICTIM-123\n"})
print(server.run_job("print(open('/tmp/stash.csv').read())", {})["stdout"])
"""


def test_server_no_cross_job_leak() -> None:
    """Regression: a detached process from one job must not read the next job's data."""
    p = subprocess.run(
        [*SERVER_DOCKER, "-"],
        input=_LEAK_POC, capture_output=True, text=True, timeout=90,
    )  # fmt: skip
    assert p.returncode == 0, p.stderr[-500:]
    assert "VICTIM-123" not in p.stdout


def test_server_job_has_no_network() -> None:
    """Regression: VPC egress rules don't cover the metadata server or DNS; the netns does."""
    code = """
import socket
for host, port in [("1.1.1.1", 53), ("169.254.169.254", 80)]:
    s = socket.socket()
    s.settimeout(3)
    try:
        s.connect((host, port))
        print("CONNECTED", host)
    except OSError:
        pass
try:
    socket.getaddrinfo("example.com", 80)
    print("RESOLVED")
except OSError:
    pass
print("done")
"""
    r = _server_job(code)
    assert r.ok and "done" in r.output
    assert "CONNECTED" not in r.output and "RESOLVED" not in r.output


def test_server_job_cannot_touch_server() -> None:
    """Regression: the job runs as uid 1000, not the server's uid, so no signal/ptrace."""
    code = """
import os
print("uid", os.getuid())
try:
    os.kill(1, 0)
    print("CAN_SIGNAL")
except PermissionError:
    pass
try:
    open("/app/server.py", "a")
    print("CAN_WRITE")
except OSError:
    pass
"""
    r = _server_job(code)
    assert r.ok and "uid 1000" in r.output
    assert "CAN_SIGNAL" not in r.output and "CAN_WRITE" not in r.output


def test_server_fails_closed_without_isolation() -> None:
    """No CAP_SYS_ADMIN means no netns: the job must not run at all."""
    i = SERVER_DOCKER.index("SYS_ADMIN")
    no_admin = SERVER_DOCKER[: i - 1] + SERVER_DOCKER[i + 1 :]
    r = _server_job("print('RAN')", docker=no_admin)
    assert not r.ok and "RAN" not in r.output


_KILLER_POC = r"""
import sys
sys.path.insert(0, "/app")
import server
killer = '''
import os, time
end = time.time() + 6
while time.time() < end:
    try:
        os.kill(-1, 9)
    except OSError:
        pass
'''
attacker = (
    "import subprocess, sys\n"
    f"subprocess.Popen([sys.executable, '-c', {killer!r}], start_new_session=True,"
    " stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
)
server.run_job(attacker, {})
print("LEFT", len(server._job_pids()))
"""


def test_server_scrub_survives_job_that_kills_cleaner() -> None:
    """Regression: a detached job looping kill(-1) must not outlive scrub()."""
    p = subprocess.run([*SERVER_DOCKER, "-"], input=_KILLER_POC, capture_output=True,
                       text=True, timeout=90)  # fmt: skip
    assert p.returncode == 0, p.stderr[-500:]
    assert "LEFT 0" in p.stdout


def test_server_output_cap() -> None:
    r = _server_job("import os\nwhile True: os.write(1, b'A' * 65536)")
    assert not r.ok


def test_docker_output_cap(sandbox: DockerExecutor, data_dir: Path) -> None:
    r = sandbox.run("import os\nwhile True: os.write(1, b'A' * 65536)", data_dir)
    assert not r.ok and "Output limit" in (r.error or "")
