# Minimal image for agent-written pandas code. Two ways to run it:
#  - local: `docker run` per job via app.sandbox.executor.DockerExecutor (entrypoint = runner)
#  - Cloud Run: `--command python --args /app/server.py` (HTTP service, see server.py)
FROM python:3.11-slim
RUN pip install --no-cache-dir numpy==2.1.3 pandas==2.2.3 matplotlib==3.9.2 \
    && useradd --uid 1000 --no-create-home --shell /usr/sbin/nologin sandbox
COPY runner.py server.py /app/
ENV MPLCONFIGDIR=/tmp PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
# No USER: DockerExecutor passes --user 1000:1000; server.py starts as root and drops
# each job to uid 1000 in its own netns.
ENTRYPOINT ["python", "-I", "/app/runner.py"]
