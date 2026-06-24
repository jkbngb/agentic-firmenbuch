# Pipeline (Container Apps Job) image — runs `fbl-pipeline --mode {sync-registry|...|daily}`.
# Build context is the repo root: `docker build -f infra/docker/pipeline.Dockerfile .`
FROM python:3.12-slim

# uv for fast, reproducible workspace installs.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY . /app

# Install the whole uv workspace (all member packages + locked deps).
RUN uv sync --frozen --all-packages --no-dev

ENTRYPOINT ["uv", "run", "--no-sync", "fbl-pipeline"]
# Mode is supplied by the Job/cron at runtime, e.g. CMD ["--mode", "daily"].
CMD ["--mode", "daily"]
