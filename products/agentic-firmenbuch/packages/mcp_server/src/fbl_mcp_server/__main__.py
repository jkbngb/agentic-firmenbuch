"""Container App entrypoint: ``fbl-mcp`` — serve the FastMCP tools over HTTP (§9, §13).

Builds the Cosmos store from settings, wires the tools via :func:`build_app`, and runs
the server. Transport defaults to ``streamable-http`` (HTTP ingress on the Container App);
override with ``MCP_TRANSPORT``. The stage logic is importable/testable standalone — this
only wires production dependencies.
"""

from __future__ import annotations

import os
import sys

from fbl_core.config import get_settings
from fbl_core.logging import get_logger
from fbl_core.storage import CosmosStore

from .app import build_app

log = get_logger("mcp_server")


def cli(argv: list[str] | None = None) -> int:
    settings = get_settings()
    if settings.cosmos_endpoint is None:
        raise SystemExit("COSMOS_ENDPOINT must be set")
    cosmos = CosmosStore(settings.cosmos_endpoint, settings.cosmos_database)
    transport = os.environ.get("MCP_TRANSPORT", "streamable-http")
    log.info("mcp server start", extra={"context": {"transport": transport}})
    if transport == "streamable-http":
        # Serve the OAuth-challenge-wrapped ASGI app ourselves so unauthenticated /mcp
        # requests return 401 + WWW-Authenticate (the OAuth discovery trigger for Cowork /
        # claude.ai). FastMCP's own app.run() can't add that wrapper. See app._OAuthChallenge.
        import uvicorn

        from .app import build_asgi_app

        uvicorn.run(
            build_asgi_app(cosmos, settings),
            host="0.0.0.0",
            port=int(os.environ.get("MCP_PORT", "8000")),
        )
    else:
        build_app(cosmos, settings).run(transport=transport)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(cli())
