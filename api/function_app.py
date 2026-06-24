"""Azure Functions (Python v2) for the signup workflow — Static Web Apps `/api/*` backend.

Thin HTTP adapter only: it builds the dependencies (Cosmos store, ACS email sender, Turnstile
verifier) from settings and delegates every decision to the unit-tested handlers in
``fbl_auth.api_handlers``. Routes (Distribution §4–§7):

    POST /api/signup       email + consent + Turnstile  → pending + verify mail
    GET  /api/verify       ?token=…                      → issue + email API key
    POST /api/regenerate   email                          → new verify link (revokes old on verify)
    POST /api/unsubscribe  email                          → revoke key + remove PII
"""

from __future__ import annotations

import json
from typing import Any

import azure.functions as func

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
from fbl_mcp_server.playground import playground_request

app = func.FunctionApp()

_settings = Settings()
_cosmos = CosmosStore(_settings.cosmos_endpoint or "", _settings.cosmos_database)
_email = email_sender_from_settings(_settings)
_turnstile = (
    make_turnstile_verifier(_settings.turnstile_secret) if _settings.turnstile_secret else None
)


def _verify_url(token: str) -> str:
    return f"{_settings.site_base_url.rstrip('/')}/api/verify?token={token}"


def _client_ip(req: func.HttpRequest) -> str | None:
    fwd = req.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() or None if fwd else None


def _body(req: func.HttpRequest) -> dict[str, Any]:
    try:
        return req.get_json()
    except ValueError:
        return {}


def _json(status: int, payload: dict[str, str]) -> func.HttpResponse:
    return func.HttpResponse(json.dumps(payload), status_code=status, mimetype="application/json")


@app.route(route="signup", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
def signup(req: func.HttpRequest) -> func.HttpResponse:
    status, payload = signup_request(
        _body(req),
        _client_ip(req),
        _cosmos,
        email_sender=_email,
        verify_url=_verify_url,
        turnstile_secret=_settings.turnstile_secret,
        turnstile_verifier=_turnstile,
        ip_limit=_settings.signup_ip_limit_per_min,
        ttl_hours=_settings.verify_token_ttl_hours,
    )
    return _json(status, payload)


@app.route(route="verify", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def verify(req: func.HttpRequest) -> func.HttpResponse:
    token = req.params.get("token", "")
    status, _ = verify_request(token, _cosmos, email_sender=_email)
    # A click in an email expects a page, not JSON → redirect to a friendly result page.
    target = "verified" if status == 200 else "verify-fehler"
    return func.HttpResponse(
        status_code=302,
        headers={"Location": f"{_settings.site_base_url.rstrip('/')}/{target}.html"},
    )


@app.route(route="regenerate", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
def regenerate(req: func.HttpRequest) -> func.HttpResponse:
    status, payload = regenerate_request(
        _body(req),
        _client_ip(req),
        _cosmos,
        email_sender=_email,
        verify_url=_verify_url,
        ip_limit=_settings.signup_ip_limit_per_min,
        ttl_hours=_settings.verify_token_ttl_hours,
    )
    return _json(status, payload)


@app.route(route="unsubscribe", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
def unsubscribe(req: func.HttpRequest) -> func.HttpResponse:
    status, payload = unsubscribe_request(_body(req), _cosmos)
    return _json(status, payload)


@app.route(route="playground", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
def playground(req: func.HttpRequest) -> func.HttpResponse:
    body = _body(req)
    visitor = str(body.get("visitor_id", "")).strip() or (_client_ip(req) or "anon")
    status, payload = playground_request(
        body,
        _client_ip(req),
        visitor,
        _cosmos,
        enabled=_settings.playground_enabled,
        turnstile_secret=_settings.turnstile_secret,
        turnstile_verifier=_turnstile,
        per_visitor_day=_settings.playground_per_visitor_day,
        per_ip_day=_settings.playground_per_ip_day,
        global_day=_settings.playground_global_day,
        max_results=_settings.playground_max_results,
    )
    return func.HttpResponse(
        json.dumps(payload, ensure_ascii=False), status_code=status, mimetype="application/json"
    )
