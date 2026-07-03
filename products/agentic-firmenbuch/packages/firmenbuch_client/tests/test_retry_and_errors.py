"""Retry/backoff and error-path tests (§8.2: honor 429, never crash the batch)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest
from helpers import API_KEY, API_URL, load_recorded

from fbl_firmenbuch_client import FirmenbuchApiError, JustizOnlineClient


def _client(handler: Callable[[httpx.Request], httpx.Response], **kw: Any) -> JustizOnlineClient:
    http = httpx.Client(transport=httpx.MockTransport(handler))
    return JustizOnlineClient(API_URL, API_KEY, client=http, **kw)


def test_retries_on_429_then_succeeds() -> None:
    calls = {"n": 0}
    ok = load_recorded("sucheFirma")

    def handler(_req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] <= 2:
            return httpx.Response(429, content=b"slow down")
        return httpx.Response(200, content=ok)

    sleeps: list[float] = []
    client = _client(handler, sleep=sleeps.append, backoff_base=0.1)
    results = client.suche_firma("x*")
    assert calls["n"] == 3  # two 429s, then success
    assert len(results) == 1
    assert sleeps == [0.1, 0.2]  # exponential backoff between attempts


def test_exhausted_retries_raise() -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(503, content=b"down")

    client = _client(handler, sleep=lambda _s: None, max_retries=2)
    with pytest.raises(FirmenbuchApiError) as exc:
        client.suche_urkunde("030435h")
    assert exc.value.endpoint == "sucheUrkunde"


def test_soap_fault_raises() -> None:
    fault = load_recorded("fault")

    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(500, content=fault)

    client = _client(handler, sleep=lambda _s: None)
    with pytest.raises(FirmenbuchApiError) as exc:
        client.suche_firma("x*")
    assert "Validation error" in str(exc.value)


def test_badrequest_400_raises_without_retry() -> None:
    # A 400 is deterministic (bad request) -> raise immediately, never retry.
    calls = {"n": 0}

    def handler(_req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(400, content=b"<bad/>")

    sleeps: list[float] = []
    client = _client(handler, sleep=sleeps.append, max_retries=4)
    with pytest.raises(FirmenbuchApiError) as exc:
        client.suche_urkunde("030435h")
    assert calls["n"] == 1  # no retry on a 4xx
    assert sleeps == []
    assert exc.value.status == 400


def test_suche_firma_sends_exaktesuche_and_element_order() -> None:
    # §8.2: EXAKTESUCHE=true on the wire, and the schema-enforced element order.
    seen: dict[str, str] = {}
    ok = load_recorded("sucheFirma")

    def handler(req: httpx.Request) -> httpx.Response:
        seen["body"] = req.content.decode()
        return httpx.Response(200, content=ok)

    client = _client(handler, sleep=lambda _s: None)
    client.suche_firma("Aetos*", rechtsform="GES", exaktesuche=True)
    body = seen["body"]
    assert "<fb:EXAKTESUCHE>true</fb:EXAKTESUCHE>" in body
    order = [
        "FIRMENWORTLAUT",
        "EXAKTESUCHE",
        "SUCHBEREICH",
        "GERICHT",
        "RECHTSFORM",
        "RECHTSEIGENSCHAFT",
        "ORTNR",
    ]
    positions = [body.index(f"<fb:{tag}") for tag in order]
    assert positions == sorted(positions)  # elements appear in the required order


def test_sends_api_key_header_not_wsse() -> None:
    seen: dict[str, str] = {}
    ok = load_recorded("sucheFirma")

    def handler(req: httpx.Request) -> httpx.Response:
        seen.update(req.headers)
        seen["_body"] = req.content.decode()
        return httpx.Response(200, content=ok)

    client = _client(handler, sleep=lambda _s: None)
    client.suche_firma("x*")
    assert seen["x-api-key"] == API_KEY
    assert "wsse" not in seen["_body"].lower()  # auth is the header, not WS-Security
