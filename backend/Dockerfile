FROM python:3.11-slim
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY app ./app
COPY benchmark/data ./benchmark/data
RUN useradd --uid 1000 --create-home api && mkdir -p /app/uploads && chown api /app/uploads
USER api
ENV PATH="/app/.venv/bin:$PATH" PORT=8080 SANDBOX_MODE=http DATABASE_PATH=/tmp/datapilot.sqlite3
CMD ["sh", "-c", "uvicorn app.api.main:app --host 0.0.0.0 --port $PORT"]
