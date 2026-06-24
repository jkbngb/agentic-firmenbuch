"""Signup + playground HTTP API as a Starlette ASGI app (Container App backend).

Why Starlette (not Azure Functions): the API reuses the uv workspace packages
(``fbl_auth``, ``fbl_mcp_server``), which install cleanly in a container via ``uv sync`` —
whereas Static Web Apps' managed Functions can't see ``../packages`` at build time. Static
Web Apps serves ``website/`` and links ``/api/*`` to this container.

All decision logic lives in the unit-tested pure handlers; this file is only HTTP routing +
dependency wiring from settings/env (Turnstile secret, ACS, Cosmos via managed identity).
"""

from __future__ import annotations

import json
from typing import Any

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from fbl_auth import (
    email_sender_from_settings,
    regenerate_request,
    signup_request,
    unsubscribe_request,
    verify_request,
)
from fbl_auth.turnstile import make_turnstile_verifier
from fbl_core.config import Settings
from fbl_core.storage import CosmosStore
from fbl_mcp_server import service
from fbl_mcp_server.playground import _within_cap, playground_request

_settings = Settings()
_cosmos = CosmosStore(_settings.cosmos_endpoint or "", _settings.cosmos_database)
_email = email_sender_from_settings(_settings)
_turnstile = (
    make_turnstile_verifier(_settings.turnstile_secret) if _settings.turnstile_secret else None
)


def _api_base() -> str:
    """Where the email's verify link must point — the reachable API host (this container),
    falling back to the site base when a same-origin proxy is in place."""
    return (_settings.api_public_url or _settings.site_base_url).rstrip("/")


def _verify_url(token: str) -> str:
    return f"{_api_base()}/api/verify?token={token}"


def _ip(req: Request) -> str | None:
    fwd = req.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() or None if fwd else (req.client.host if req.client else None)


async def _body(req: Request) -> dict[str, Any]:
    try:
        return await req.json()
    except Exception:
        return {}


async def health(_req: Request) -> Response:
    return JSONResponse({"status": "ok"})


# /api/demo — feeds the animated hero. Daily in-memory cache → zero per-visitor cost. Serves the
# live company count (when reachable) + curated demo scripts. `live` stays False until the demos
# are backed by real 10_presentation data (post-backfill).
_DEMO_CACHE: dict[str, Any] = {"day": None, "payload": None}
_DEMO_SCRIPTS = [
    {"q": "Zeig mir die Bilanzkennzahlen der Muster Handels GmbH."},
    {"q": "Aktive GmbHs in der Steiermark, Bilanzsumme über 5 Mio. €."},
    {"q": "Firmen mit starkem Eigenkapital-Sprung im letzten Jahr."},
]


def _active_company_count() -> int | None:
    try:
        sql = (
            "SELECT VALUE COUNT(1) FROM c WHERE c.status = 'active' AND NOT STARTSWITH(c.id, '__')"
        )
        return next(iter(_cosmos.query("99_registry", sql)), None)
    except Exception:
        return None


async def demo(_req: Request) -> Response:
    from datetime import UTC, datetime

    today = datetime.now(UTC).strftime("%Y-%m-%d")
    if _DEMO_CACHE["day"] != today:
        stats: dict[str, int] = {}
        n = _active_company_count()
        if n:
            stats["companies"] = n
        _DEMO_CACHE.update(
            day=today, payload={"live": False, "stats": stats, "demos": _DEMO_SCRIPTS}
        )
    return JSONResponse(_DEMO_CACHE["payload"])


async def signup(req: Request) -> Response:
    status, payload = signup_request(
        await _body(req),
        _ip(req),
        _cosmos,
        email_sender=_email,
        verify_url=_verify_url,
        turnstile_secret=_settings.turnstile_secret,
        turnstile_verifier=_turnstile,
        ip_limit=_settings.signup_ip_limit_per_min,
        ttl_hours=_settings.verify_token_ttl_hours,
    )
    return JSONResponse(payload, status_code=status)


async def verify(req: Request) -> Response:
    status, _ = verify_request(req.query_params.get("token", ""), _cosmos, email_sender=_email)
    target = "verified" if status == 200 else "verify-fehler"
    return RedirectResponse(f"{_settings.site_base_url.rstrip('/')}/{target}.html", status_code=302)


async def regenerate(req: Request) -> Response:
    status, payload = regenerate_request(
        await _body(req),
        _ip(req),
        _cosmos,
        email_sender=_email,
        verify_url=_verify_url,
        ip_limit=_settings.signup_ip_limit_per_min,
        ttl_hours=_settings.verify_token_ttl_hours,
    )
    return JSONResponse(payload, status_code=status)


async def unsubscribe(req: Request) -> Response:
    status, payload = unsubscribe_request(await _body(req), _cosmos)
    return JSONResponse(payload, status_code=status)


async def playground(req: Request) -> Response:
    body = await _body(req)
    visitor = str(body.get("visitor_id", "")).strip() or (_ip(req) or "anon")
    status, payload = playground_request(
        body,
        _ip(req),
        visitor,
        _cosmos,
        enabled=_settings.playground_enabled,
        # No per-message Turnstile on the playground (bad UX for a chat); abuse/spend is bounded
        # by the per-visitor + per-IP + global daily caps below, the cheap model + max_tokens,
        # and the kill-switch. A one-time Turnstile gate per session is a documented fast-follow.
        turnstile_secret=None,
        turnstile_verifier=None,
        per_visitor_day=_settings.playground_per_visitor_day,
        per_ip_day=_settings.playground_per_ip_day,
        global_day=_settings.playground_global_day,
        max_results=_settings.playground_max_results,
        llm_enabled=_settings.playground_llm_enabled,
        anthropic_api_key=_settings.anthropic_api_key,
        llm_model=_settings.playground_llm_model,
        llm_max_tokens=_settings.playground_llm_max_tokens,
    )
    return Response(
        json.dumps(payload, ensure_ascii=False), status_code=status, media_type="application/json"
    )


async def company(req: Request) -> Response:
    """Public, rate-limited served-record fetch for the playground's detail view.

    Returns exactly what an MCP client's ``get_company_details`` returns — the served
    ``10_presentation`` doc (officer names already withheld at write time, §8.7). A light
    per-IP/global daily cap keeps it from being a bulk-scrape vector.
    """
    from datetime import UTC, datetime

    fnr = str(req.path_params.get("fnr", "")).strip()
    if not fnr or len(fnr) > 16 or not fnr.replace("-", "").isalnum():
        return Response(
            json.dumps({"error": "bad_fnr"}), status_code=400, media_type="application/json"
        )
    now = datetime.now(UTC)
    ip = _ip(req)
    if not _within_cap(_cosmos, "company_global", 20000, now):
        return Response(
            json.dumps({"error": "global_cap"}), status_code=429, media_type="application/json"
        )
    if ip and not _within_cap(_cosmos, f"company_ip:{ip}", 300, now):
        return Response(
            json.dumps({"error": "ip_cap"}), status_code=429, media_type="application/json"
        )
    try:
        payload = service.get_company_details(_cosmos, fnr)
    except Exception:
        return Response(
            json.dumps({"error": "not_found"}), status_code=404, media_type="application/json"
        )
    return Response(
        json.dumps(payload, ensure_ascii=False), status_code=200, media_type="application/json"
    )


# Browser CORS: the static site (www / apex) fetches signup + playground from this container,
# so those origins must be allowed. Configurable via CORS_ALLOWED_ORIGINS; sensible prod
# defaults otherwise. Credentials are not used (no cookies), so an explicit origin list is enough.
_DEFAULT_ORIGINS = [
    "https://www.agentic-firmenbuch.at",
    "https://agentic-firmenbuch.at",
]
_cors_origins = [
    o.strip() for o in (_settings.cors_allowed_origins or "").split(",") if o.strip()
] or _DEFAULT_ORIGINS

app = Starlette(
    routes=[
        Route("/api/health", health, methods=["GET"]),
        Route("/api/demo", demo, methods=["GET"]),
        Route("/api/signup", signup, methods=["POST"]),
        Route("/api/verify", verify, methods=["GET"]),
        Route("/api/regenerate", regenerate, methods=["POST"]),
        Route("/api/unsubscribe", unsubscribe, methods=["POST"]),
        Route("/api/playground", playground, methods=["POST"]),
        Route("/api/company/{fnr}", company, methods=["GET"]),
    ],
    middleware=[
        Middleware(
            CORSMiddleware,
            allow_origins=_cors_origins,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["content-type"],
            max_age=3600,
        )
    ],
)
