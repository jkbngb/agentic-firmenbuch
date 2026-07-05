"""Signup + playground HTTP API as a Starlette ASGI app (Container App backend).

Why Starlette (not Azure Functions): the API reuses the uv workspace packages
(``fbl_auth``, ``fbl_mcp_server``), which install cleanly in a container via ``uv sync`` —
whereas Static Web Apps' managed Functions can't see ``../packages`` at build time. Static
Web Apps serves ``website/`` and links ``/api/*`` to this container.

All decision logic lives in the unit-tested pure handlers; this file is only HTTP routing +
dependency wiring from settings/env (Turnstile secret, ACS, Cosmos via managed identity).
"""

from __future__ import annotations

import html
import json
import logging
import os
import uuid
from typing import Any

import httpx
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from fbl_auth import (
    Account,
    checkout_session_params,
    email_sender_from_settings,
    handle_event,
    portal_session_params,
    regenerate_request,
    signup_request,
    unsubscribe_request,
    validate,
    validate_bearer,
    verify_request,
)
from fbl_auth.turnstile import make_turnstile_verifier
from fbl_core.config import Settings
from fbl_core.storage import RAW_CONTAINER, BlobStore, CosmosStore
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
    # Interstitial confirm: corporate mail link-scanners (Microsoft 365 Safe Links, Proofpoint, …)
    # PRE-FETCH links to scan them. Consuming the one-time verify token on that GET burned it
    # before the human clicked. So the email link (GET) only renders a confirm button and consumes
    # NOTHING; the human's button POST runs verify_request (issue + email the API key). Scanners
    # GET (harmless); only a real POST completes.
    site = _settings.site_base_url.rstrip("/")
    if req.method == "GET":
        token = req.query_params.get("token", "")
        if not token:
            return RedirectResponse(f"{site}/verify-fehler.html", status_code=302)
        return HTMLResponse(
            "<!doctype html><html lang=de><head><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            "<title>E-Mail bestätigen</title></head>"
            "<body style='font-family:system-ui,sans-serif;background:#0b0d10;color:#EDEFF3;"
            "margin:0;display:flex;min-height:100vh;align-items:center;justify-content:center'>"
            "<form method=post style='max-width:380px;width:90%;text-align:center'>"
            "<h2 style='color:#19C37D;margin:0 0 .5rem'>Agentic-Firmenbuch.at</h2>"
            "<p style='color:#9AA2AF'>Klicke, um deine E-Mail zu bestätigen — danach bekommst du "
            "deinen API-Key per Mail.</p>"
            f"<input type=hidden name=token value='{html.escape(token)}'>"
            "<button type=submit style='width:100%;padding:12px;border:0;border-radius:10px;"
            "background:#19C37D;color:#08130D;font-weight:700;font-size:15px;cursor:pointer'>"
            "E-Mail bestätigen</button></form></body></html>"
        )
    form = await req.form()
    token = str(form.get("token", "")) or req.query_params.get("token", "")
    status, _ = verify_request(token, _cosmos, email_sender=_email)
    target = "verified" if status == 200 else "verify-fehler"
    return RedirectResponse(f"{site}/{target}.html", status_code=302)


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
_blob = BlobStore(_settings.blob_account_url) if _settings.blob_account_url else None
_GH_TOKEN = os.environ.get("GH_FEEDBACK_TOKEN", "")
_GH_REPO = os.environ.get("GH_FEEDBACK_REPO", "jkbngb/agentic-firmenbuch")
_NOTIFY_SECRET = os.environ.get("FEEDBACK_NOTIFY_SECRET", "")
_IMG_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif"}


async def feedback(req: Request) -> Response:
    """User feedback → a labelled GitHub issue (which triggers the auto-fix agent). Optional
    screenshot is stored in Blob and embedded via a 7-day signed URL (the agent reads it at once).
    Turnstile-gated; the agent can only open PRs, never merge/deploy (branch protection)."""
    form = await req.form()
    message = str(form.get("message", "")).strip()
    contact = str(form.get("contact", "")).strip()[:200]
    if len(message) < 5:
        return JSONResponse({"error": "Bitte beschreibe dein Feedback kurz."}, status_code=400)
    message = message[:5000]
    # Turnstile widget auto-injects `cf-turnstile-response` into the form; accept that (reliable)
    # or the explicit `turnstile` field.
    ts_token = str(form.get("cf-turnstile-response") or form.get("turnstile") or "")
    if _turnstile and not _turnstile(ts_token, _ip(req)):
        return JSONResponse(
            {"error": "Bot-Prüfung fehlgeschlagen — bitte die Box neu bestätigen."},
            status_code=403,
        )
    if not _GH_TOKEN:
        return JSONResponse({"error": "Feedback-Kanal noch nicht konfiguriert."}, status_code=503)

    shot_md = ""
    up = form.get("screenshot")
    filename = getattr(up, "filename", "") if up is not None else ""
    if filename:
        data = await up.read()  # type: ignore[union-attr]
        if len(data) > 5_000_000:
            return JSONResponse({"error": "Screenshot zu groß (max 5 MB)."}, status_code=400)
        ext = _IMG_EXT.get(getattr(up, "content_type", "") or "")
        if not ext:
            return JSONResponse({"error": "Nur PNG/JPG/WEBP/GIF."}, status_code=400)
        if _blob is not None:
            path = f"feedback/{uuid.uuid4().hex}.{ext}"
            _blob.put_bytes(RAW_CONTAINER, path, data)
            try:
                url = _blob.download_link(RAW_CONTAINER, path, ttl_minutes=7 * 24 * 60).url
                shot_md = (
                    f"\n\n![screenshot]({url})\n\n"
                    f"_(Screenshot-Link 7 Tage gültig; dauerhaft: `{path}`)_"
                )
            except Exception:
                shot_md = f"\n\n_(Screenshot gespeichert: `{path}`)_"

    title = (message.splitlines()[0] or "User-Feedback")[:80]
    body = message + shot_md + "\n\n---\n_Via Feedback-Formular_"
    if contact:
        body += f" · Kontakt: {contact}"
    try:
        r = httpx.post(
            f"https://api.github.com/repos/{_GH_REPO}/issues",
            headers={
                "Authorization": f"Bearer {_GH_TOKEN}",
                "Accept": "application/vnd.github+json",
            },
            json={"title": f"[Feedback] {title}", "body": body, "labels": ["user-feedback"]},
            timeout=15,
        )
    except Exception:
        return JSONResponse({"error": "Übermittlung fehlgeschlagen."}, status_code=502)
    if r.status_code >= 300:
        return JSONResponse({"error": "Übermittlung fehlgeschlagen."}, status_code=502)
    return JSONResponse({"ok": True, "issue": r.json().get("html_url")})


async def notify_fixed(req: Request) -> Response:
    """Internal: the notify-reporter workflow calls this when a feedback issue is closed as fixed.
    Sends the reporter a short 'your feedback was implemented' e-mail. Secret-header gated; a
    reporter is only ever mailed on a completed fix (never on intermediate updates)."""
    if not _NOTIFY_SECRET or req.headers.get("x-notify-secret") != _NOTIFY_SECRET:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    body = await _body(req)
    email = str(body.get("email", "")).strip()
    url = str(body.get("issue_url", ""))
    if "@" not in email:
        return JSONResponse({"ok": True, "skipped": "no reporter e-mail"})
    text = (
        "Danke fürs Einmelden!\n\n"
        "Dein Feedback wurde umgesetzt und ist erledigt.\n\n"
        f"Zur Nachverfolgung: {url}\n\n"
        "Viele Grüße\nAgentic-Firmenbuch.at\n\n"
        "Hinweis: automatische Benachrichtigung, du musst nicht darauf antworten."
    )
    try:
        _email.send_alert(email, "Dein Feedback wurde umgesetzt", text)
    except Exception:
        return JSONResponse({"error": "send failed"}, status_code=502)
    return JSONResponse({"ok": True})


# --- Stripe billing (Aufgabe 2) -----------------------------------------------------------
# Endpoints: start a Pro checkout, open the customer portal, receive webhooks. The plan-change
# logic is the pure, unit-tested fbl_auth.billing; here we only do Stripe I/O (session creation
# + signature verification). The stripe SDK is imported lazily so the rest of the API loads even
# when it isn't installed (it's a container-only dep; keys come from ENV/Key Vault, never repo).

_billing_log = logging.getLogger("fbl_billing")


def _stripe() -> Any:
    """The Stripe SDK with the secret key applied (test key in test mode). Raises if not
    installed/configured — callers guard on ``_settings.stripe_secret_key`` first."""
    import stripe

    stripe.api_key = _settings.stripe_secret_key
    return stripe


def _billing_account(req: Request, body: dict[str, Any]) -> Account | None:
    """Resolve the calling account from an X-API-Key header or an ``api_key`` body field
    (same credential the MCP server accepts: API key or OAuth bearer)."""
    token = req.headers.get("x-api-key", "") or str(body.get("api_key", "")).strip()
    if not token:
        return None
    return validate(token, _cosmos) or validate_bearer(_cosmos, token)


async def billing_checkout(req: Request) -> Response:
    """Start a Pro subscription checkout for the calling account. Returns ``{url}`` to redirect
    the browser to Stripe Checkout. Binds the purchase to the account via client_reference_id."""
    if not _settings.stripe_secret_key:
        return JSONResponse({"error": "billing not configured"}, status_code=503)
    body = await _body(req)
    account = _billing_account(req, body)
    if account is None:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        stripe = _stripe()
        prices = stripe.Price.list(
            lookup_keys=[_settings.stripe_price_lookup_key], expand=["data.product"]
        )
        if not prices.data:
            _billing_log.error(
                "checkout: no price for lookup_key=%s", _settings.stripe_price_lookup_key
            )
            return JSONResponse({"error": "price not available"}, status_code=500)
        params = checkout_session_params(
            account,
            price_id=prices.data[0].id,
            success_url=_settings.billing_success_url + "?session_id={CHECKOUT_SESSION_ID}",
            cancel_url=_settings.billing_cancel_url,
            trial_days=_settings.stripe_trial_days,
        )
        session = stripe.checkout.Session.create(**params)
    except Exception:
        _billing_log.exception("checkout session creation failed")
        return JSONResponse({"error": "checkout failed"}, status_code=502)
    return JSONResponse({"url": session.url})


async def billing_portal(req: Request) -> Response:
    """Open the Stripe customer portal (self-service cancel + invoices) for the calling account."""
    if not _settings.stripe_secret_key:
        return JSONResponse({"error": "billing not configured"}, status_code=503)
    body = await _body(req)
    account = _billing_account(req, body)
    if account is None:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    params = portal_session_params(account, return_url=_settings.billing_portal_return_url)
    if params is None:
        return JSONResponse({"error": "no subscription to manage"}, status_code=409)
    try:
        stripe = _stripe()
        session = stripe.billing_portal.Session.create(**params)
    except Exception:
        _billing_log.exception("portal session creation failed")
        return JSONResponse({"error": "portal failed"}, status_code=502)
    return JSONResponse({"url": session.url})


async def billing_webhook(req: Request) -> Response:
    """Receive Stripe webhooks. Verifies the signature (STRIPE_WEBHOOK_SECRET) against the RAW
    body, then applies the plan change idempotently via fbl_auth.billing.handle_event."""
    secret = _settings.stripe_webhook_secret
    if not secret or not _settings.stripe_secret_key:
        return JSONResponse({"error": "webhook not configured"}, status_code=503)
    payload = await req.body()  # RAW bytes — required for signature verification
    signature = req.headers.get("stripe-signature", "")
    try:
        stripe = _stripe()
        event = stripe.Webhook.construct_event(payload, signature, secret)
    except Exception:
        # Bad/absent signature (or malformed body) — never trust it.
        return JSONResponse({"error": "invalid signature"}, status_code=400)
    # Deep-convert the StripeObject to a plain dict (version-robust) for the pure handler.
    event_dict = json.loads(str(event))
    try:
        result = handle_event(_cosmos, event_dict)
    except Exception:
        _billing_log.exception("webhook handling failed for event %s", event_dict.get("id"))
        return JSONResponse({"error": "handling failed"}, status_code=500)
    return JSONResponse(result)


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
        Route("/api/verify", verify, methods=["GET", "POST"]),
        Route("/api/regenerate", regenerate, methods=["POST"]),
        Route("/api/unsubscribe", unsubscribe, methods=["POST"]),
        Route("/api/playground", playground, methods=["POST"]),
        Route("/api/company/{fnr}", company, methods=["GET"]),
        Route("/api/feedback", feedback, methods=["POST"]),
        Route("/api/notify-fixed", notify_fixed, methods=["POST"]),
        Route("/api/billing/checkout", billing_checkout, methods=["POST"]),
        Route("/api/billing/portal", billing_portal, methods=["POST"]),
        Route("/api/billing/webhook", billing_webhook, methods=["POST"]),
    ],
    middleware=[
        Middleware(
            CORSMiddleware,
            allow_origins=_cors_origins,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["content-type", "x-api-key"],
            max_age=3600,
        )
    ],
)
