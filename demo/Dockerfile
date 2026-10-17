FROM node:22-bookworm-slim AS browser
WORKDIR /web
COPY demo/web/package*.json ./
RUN npm ci
COPY demo/web/ ./
RUN npm run build

FROM dailyco/pipecat-base:latest-py3.12
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONPATH=/app/src PYTHONUNBUFFERED=1 NLTK_DATA=/app/nltk_data
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
# Use the root lock so this demo runs the same Pipecat version as the intake bot.
RUN uv sync --no-cache --locked --no-install-project --no-dev --group demo
RUN /app/.venv/bin/python -m nltk.downloader -d /app/nltk_data punkt_tab
COPY src ./src
COPY demo/__init__.py demo/app.py demo/explain.py ./demo/
COPY --from=browser /web/dist ./demo/web/dist
EXPOSE 8080
CMD ["/app/.venv/bin/uvicorn", "demo.app:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--no-access-log", "--log-level", "warning", "--ws-max-size", "65536"]
