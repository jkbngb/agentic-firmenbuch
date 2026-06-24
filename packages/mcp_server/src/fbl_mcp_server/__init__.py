"""fbl_mcp_server — Stage 9: FastMCP tools over 10_presentation with auth + rate limiting.

``McpService``/``build_app`` are imported lazily (PEP 562) because they pull in FastMCP, so
lightweight consumers — e.g. the signup/playground Azure Functions importing ``service`` or
``playground`` — don't load the FastMCP transport at runtime.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .errors import BadRequest, McpError, NotFound, RateLimited, Unauthorized

if TYPE_CHECKING:
    from .app import McpService, build_app, build_asgi_app

__all__ = [
    "BadRequest",
    "McpError",
    "McpService",
    "NotFound",
    "RateLimited",
    "Unauthorized",
    "build_app",
    "build_asgi_app",
]


def __getattr__(name: str) -> Any:
    """Lazily expose the FastMCP-backed symbols without importing FastMCP eagerly."""
    if name in ("McpService", "build_app", "build_asgi_app"):
        from . import app

        return getattr(app, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
