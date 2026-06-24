# Signup + playground API (Container App) image — Starlette ASGI over uvicorn.
# Build context is the repo root: `docker build -f infra/docker/signup.Dockerfile .`
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY . /app

RUN uv sync --frozen --all-packages --no-dev

EXPOSE 8000
# api/ is imported as a namespace package from the repo root.
ENTRYPOINT ["uv", "run", "--no-sync", "uvicorn", "api.asgi:app", "--host", "0.0.0.0", "--port", "8000"]
