FROM node:22.23.4-bookworm-slim AS browser

# eMudhra certificate incident: refresh each stage's system trust store.
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && update-ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /web
COPY demo/web/package*.json ./
RUN npm ci
COPY demo/web/ ./
RUN npm run build

FROM dailyco/pipecat-base:latest-py3.12

# eMudhra certificate incident: refresh each stage's system trust store.
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && update-ca-certificates && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONPATH=/app/src PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
# Use the root lock so this demo runs the same Pipecat version as the intake bot.
RUN uv sync --no-cache --locked --no-install-project --no-dev --group demo
COPY src ./src
COPY demo/__init__.py demo/app.py demo/explain.py ./demo/
COPY --from=browser /web/dist ./demo/web/dist
EXPOSE 8080
CMD ["/app/.venv/bin/uvicorn", "demo.app:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--no-access-log", "--log-level", "warning", "--ws-max-size", "65536"]
