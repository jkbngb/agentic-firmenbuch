"""Offline test helpers: serve recorded SOAP responses via httpx.MockTransport."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import httpx

from fbl_firmenbuch_client import JustizOnlineClient

RECORDED = Path(__file__).resolve().parent / "recorded"
API_URL = "https://example.test/ws"
API_KEY = "test-key"


def load_recorded(name: str) -> bytes:
    return (RECORDED / f"{name}.xml").read_bytes()


def make_client(
    handler: Callable[[httpx.Request], httpx.Response], **kwargs: object
) -> JustizOnlineClient:
    """Build a client whose transport is driven by *handler* (no network)."""
    transport = httpx.MockTransport(handler)
    http = httpx.Client(transport=transport)
    return JustizOnlineClient(API_URL, API_KEY, client=http, sleep=lambda _s: None, **kwargs)  # type: ignore[arg-type]


def fixed_response(name: str, status: int = 200) -> Callable[[httpx.Request], httpx.Response]:
    """A handler that always returns the recorded fixture *name*."""
    body = load_recorded(name)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=body, headers={"Content-Type": "text/xml"})

    return handler
