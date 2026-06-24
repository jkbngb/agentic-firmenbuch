# MCP server (Container App) image — serves the FastMCP tools over HTTP.
# Build context is the repo root: `docker build -f infra/docker/mcp.Dockerfile .`
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    MCP_TRANSPORT=streamable-http \
    FASTMCP_HOST=0.0.0.0 \
    FASTMCP_PORT=8000

WORKDIR /app
COPY . /app

RUN uv sync --frozen --all-packages --no-dev

EXPOSE 8000
ENTRYPOINT ["uv", "run", "--no-sync", "fbl-mcp"]
