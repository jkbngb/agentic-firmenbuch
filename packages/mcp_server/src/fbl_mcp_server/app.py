"""FastMCP app + auth-enforcing service wrapper (§8.9).

``McpService`` is the testable core: it validates the token, enforces the rate limit,
meters usage, then delegates to the read functions in ``service.py``. ``build_app``
wires those onto a FastMCP server (the transport is not unit-tested here).
"""

from __future__ import annotations

import contextlib
import html
import json
import os
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

from mcp.server.fastmcp import Context, FastMCP

from fbl_auth import (
    Account,
    check_rate_limit,
    get_usage,
    quota_for,
    record_metered_usage,
    record_usage,
    validate,
    validate_bearer,
)
from fbl_core.config import Settings, get_settings
from fbl_core.models import SearchFilters, Sort
from fbl_core.storage import CosmosStoreLike

from . import service
from .errors import RateLimited, Unauthorized

# FastMCP injects a tool param annotated as the bare `Context` class (its matcher needs a class,
# not a parameterized generic). mypy --strict wants the type args, so alias it: at type-check time
# it's the fully-parameterized generic; at runtime it's the bare class FastMCP detects.
if TYPE_CHECKING:
    ToolContext = Context[Any, Any, Any]
else:
    ToolContext = Context


class McpService:
    """Auth + rate limit + metering in front of the read tools."""

    def __init__(self, cosmos: CosmosStoreLike, settings: Settings | None = None) -> None:
        self._cosmos = cosmos
        self._settings = settings or get_settings()

    def _authorize(self, token: str, tool: str) -> Account:
        # Two credential kinds resolve to the same Account: an X-API-Key (legacy header
        # path) OR an OAuth Bearer token (Cowork/claude.ai, §8.10b). Try API key first
        # since most live traffic still uses it; fall back to bearer.
        account = validate(token, self._cosmos) or validate_bearer(self._cosmos, token)
        if account is None:
            raise Unauthorized("invalid or unknown token")
        per_min, per_day = quota_for(account.tier, self._settings)
        decision = check_rate_limit(account, per_min=per_min, per_day=per_day)
        if not decision.allowed:
            raise RateLimited(decision.reason or "rate limited")
        record_usage(account, tool, self._cosmos)  # rolling counters (rate limit)
        # Persistent daily-rollup meter (Erweiterungen-Spec §8). Best-effort: a metering write must
        # never fail a tool call — the rate-limit counters above are authoritative.
        with contextlib.suppress(Exception):
            record_metered_usage(account, tool, self._cosmos)
        return account

    def search_companies(
        self,
        token: str,
        filters: SearchFilters | None = None,
        sort: Sort | None = None,
        page: int = 1,
        page_size: int = 25,
    ) -> dict[str, Any]:
        self._authorize(token, "search_companies")
        return service.search_companies(self._cosmos, filters, sort, page, page_size).model_dump(
            mode="json"
        )

    def get_company_details(self, token: str, fnr: str) -> dict[str, Any]:
        self._authorize(token, "get_company_details")
        return service.get_company_details(self._cosmos, fnr)

    def describe_fields(self, token: str) -> dict[str, Any]:
        """Static catalog of every field the server can return, by tool tier (§9)."""
        self._authorize(token, "describe_fields")
        return service.describe_fields()

    def get_company_history(
        self, token: str, fnr: str, metrics: list[str] | None = None
    ) -> dict[str, Any]:
        self._authorize(token, "get_company_history")
        return service.get_company_history(self._cosmos, fnr, metrics)

    def get_full_record(self, token: str, fnr: str) -> dict[str, Any]:
        """The complete consolidated/derived record — full superset, nothing reduced (§5.1)."""
        self._authorize(token, "get_full_record")
        return service.get_full_record(
            self._cosmos, fnr, expose_personal_data=self._settings.expose_personal_data
        )

    def get_document(self, token: str, doc_key: str) -> dict[str, Any]:
        self._authorize(token, "get_document")
        return service.get_document(self._cosmos, doc_key)

    def list_sectors(self, token: str) -> dict[str, Any]:
        self._authorize(token, "list_sectors")
        return service.list_sectors(self._cosmos)

    def get_cohort_summary(self, token: str, dimension: str, value: str) -> dict[str, Any]:
        self._authorize(token, "get_cohort_summary")
        return service.get_cohort_summary(self._cosmos, dimension, value)

    def find_peers(self, token: str, fnr: str, n: int = 10) -> dict[str, Any]:
        self._authorize(token, "find_peers")
        return service.find_peers(self._cosmos, fnr, n)

    def get_coverage(self, token: str) -> dict[str, Any]:
        """Internal coverage dashboard (XML vs PDF-only vs none) — auth-restricted (§11).
        Served from the precomputed ``__stats__`` doc so it can't full-scan in-request."""
        self._authorize(token, "get_coverage")
        return service.coverage(self._cosmos)

    def get_my_usage(self, token: str, window: str = "today") -> dict[str, Any]:
        """The caller's own consumption over *window* (Erweiterungen-Spec §8.5). Reads only
        the key's own usage docs; never exposes another user's data or the e-mail behind it."""
        account = self._authorize(token, "get_my_usage")
        return dict(get_usage(self._cosmos, account.token_hash, window=window))


def _http_credential(ctx: Any) -> tuple[str, str]:
    """Return ``(kind, token)`` for the credential the client presented.

    Two paths produce the same Account downstream (§8.10b):
    * ``X-API-Key: <token>`` -- the existing header path (Claude Code, Copilot, Cursor).
    * ``Authorization: Bearer <token>`` -- the OAuth path (Cowork, claude.ai), validated
      against ``00_oauth_tokens`` instead of ``00_accounts``.

    ``kind`` is one of ``"api_key"``, ``"bearer"``, or ``""`` (unauthenticated).
    Headers are case-insensitive (Starlette).
    """
    try:
        request = ctx.request_context.request
    except Exception:
        return "", ""  # no HTTP request context (e.g. stdio transport)
    if request is None:
        return "", ""
    api_key = request.headers.get("x-api-key", "")
    if api_key:
        return "api_key", str(api_key)
    auth = request.headers.get("authorization", "")
    if auth and auth.lower().startswith("bearer "):
        return "bearer", auth[7:].strip()
    return "", ""


def _http_token(ctx: Any) -> str:
    """Backwards-compatible: return whichever credential the client presented as a string.
    McpService._authorize knows to try X-API-Key first then bearer (see ``McpService``)."""
    _, token = _http_credential(ctx)
    return token


def build_app(cosmos: CosmosStoreLike, settings: Settings | None = None) -> Any:
    """Construct the FastMCP server with all tools registered (§9)."""
    svc = McpService(cosmos, settings)
    # Bind 0.0.0.0 so the Container App ingress can reach the streamable-HTTP server
    # (FastMCP defaults to 127.0.0.1, which is unreachable from outside the container).
    mcp = FastMCP(
        "firmenbuch-live",
        host="0.0.0.0",
        port=int(os.environ.get("MCP_PORT", "8000")),
    )

    # Friendly landing for humans who open the bare host in a browser. The MCP
    # protocol itself lives at ``/mcp`` (a bare GET there correctly returns 406);
    # without this, ``GET /`` would 404 with an unhelpful "Not Found".
    @mcp.custom_route("/", methods=["GET"])  # type: ignore[untyped-decorator]
    async def _root(_request: Any) -> Any:
        from starlette.responses import HTMLResponse

        return HTMLResponse(
            "<!doctype html><html lang=de><meta charset=utf-8>"
            "<title>Agentic-Firmenbuch.at — MCP-Server</title>"
            "<body style='font-family:system-ui,sans-serif;max-width:42rem;margin:4rem auto;"
            "padding:0 1rem;line-height:1.6;color:#1a1a1a'>"
            "<h1>Agentic-Firmenbuch.at — MCP-Server</h1>"
            "<p>Das ist der <strong>MCP-Endpunkt</strong>, kein Website. Er ist für "
            "KI-Tools (Claude, Cursor, Copilot …) gedacht, nicht für den Browser.</p>"
            "<p>Verbinde dein Tool mit <code>https://mcp.agentic-firmenbuch.at/mcp</code> "
            "und dem Header <code>X-API-Key</code>.</p>"
            "<p>→ <a href='https://www.agentic-firmenbuch.at/onboarding.html'>Anleitung &amp; "
            "API-Key anfordern</a></p>"
            "</body></html>",
            status_code=200,
        )

    @mcp.custom_route("/health", methods=["GET"])  # type: ignore[untyped-decorator]
    async def _health(_request: Any) -> Any:
        from starlette.responses import JSONResponse

        return JSONResponse({"status": "ok"})

    # --- MCP OAuth 2.1 (§8.10b) ----------------------------------------------------------
    # These endpoints let clients that cannot use the X-API-Key header (Claude Cowork,
    # claude.ai) attach by URL + login. Discovery + DCR are live now; /authorize and
    # /token follow in phase 3.

    # The authorization-base URL is the host root with the MCP path stripped. The metadata
    # endpoint MUST live at the root per RFC 8414 / MCP spec — and Cowork won't even try
    # the URL if this 404s.
    _base = os.environ.get("PUBLIC_BASE_URL", "https://mcp.agentic-firmenbuch.at").rstrip("/")
    from fbl_auth import email_sender_from_settings

    _settings = settings or get_settings()
    _email = email_sender_from_settings(_settings)

    @mcp.custom_route("/.well-known/oauth-authorization-server", methods=["GET"])  # type: ignore[untyped-decorator]
    async def _oauth_metadata(_request: Any) -> Any:
        from starlette.responses import JSONResponse

        return JSONResponse(
            {
                "issuer": _base,
                "authorization_endpoint": f"{_base}/authorize",
                "token_endpoint": f"{_base}/token",
                "registration_endpoint": f"{_base}/register",
                "response_types_supported": ["code"],
                "grant_types_supported": ["authorization_code", "refresh_token"],
                "code_challenge_methods_supported": ["S256"],  # OAuth 2.1: plain disallowed
                "token_endpoint_auth_methods_supported": ["none"],  # public client + PKCE
                "scopes_supported": ["mcp:read"],
            }
        )

    # RFC 9728 Protected Resource Metadata. THIS is what makes Cowork/claude.ai discover
    # OAuth: their first unauthenticated /mcp request gets a 401 carrying
    # `WWW-Authenticate: Bearer resource_metadata="<this url>"` (see _OAuthChallenge), they
    # fetch this document, read `authorization_servers`, and then hit the auth-server
    # metadata above. The SDK appends the resource path, so the canonical URL is
    # `/.well-known/oauth-protected-resource/mcp`; we also answer the bare path because
    # client implementations differ on which they request.
    def _protected_resource_metadata(_request: Any) -> Any:
        from starlette.responses import JSONResponse

        return JSONResponse(
            {
                "resource": f"{_base}/mcp",
                "authorization_servers": [_base],
                "scopes_supported": ["mcp:read"],
                "bearer_methods_supported": ["header"],
            }
        )

    @mcp.custom_route("/.well-known/oauth-protected-resource/mcp", methods=["GET"])  # type: ignore[untyped-decorator]
    async def _prm_suffixed(request: Any) -> Any:
        return _protected_resource_metadata(request)

    @mcp.custom_route("/.well-known/oauth-protected-resource", methods=["GET"])  # type: ignore[untyped-decorator]
    async def _prm_bare(request: Any) -> Any:
        return _protected_resource_metadata(request)

    @mcp.custom_route("/register", methods=["POST", "OPTIONS"])  # type: ignore[untyped-decorator]
    async def _oauth_register(request: Any) -> Any:
        from starlette.responses import JSONResponse

        if request.method == "OPTIONS":
            return JSONResponse({}, status_code=204)
        from fbl_auth import register_client

        try:
            body = await request.json()
        except Exception:
            body = {}
        redirect_uris = list(body.get("redirect_uris") or [])
        # OAuth 2.1 + MCP: only localhost (http) or HTTPS redirect URIs allowed.
        for uri in redirect_uris:
            if not (
                uri.startswith("https://")
                or uri.startswith("http://localhost")
                or uri.startswith("http://127.0.0.1")
            ):
                return JSONResponse(
                    {"error": "invalid_redirect_uri", "error_description": f"not allowed: {uri}"},
                    status_code=400,
                )
        client = register_client(
            cosmos, client_name=body.get("client_name"), redirect_uris=redirect_uris
        )
        return JSONResponse(
            {
                "client_id": client.client_id,
                "client_id_issued_at": 0,  # we don't track this precisely
                "client_name": client.client_name,
                "redirect_uris": client.redirect_uris,
                "grant_types": client.grant_types,
                "response_types": client.response_types,
                "token_endpoint_auth_method": client.token_endpoint_auth_method,
            },
            status_code=201,
        )

    def _page(title: str, body_html: str, status: int = 200) -> Any:
        from starlette.responses import HTMLResponse

        return HTMLResponse(
            "<!doctype html><html lang=de><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{html.escape(title)} – Agentic-Firmenbuch.at</title>"
            "<body style='margin:0;background:#0A0B0E;color:#EDEFF3;"
            "font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif'>"
            "<div style='max-width:30rem;margin:0 auto;padding:3rem 1.25rem'>"
            "<div style='font-weight:700;font-size:17px;margin-bottom:1.5rem'>"
            "<span style='color:#19C37D'>Agentic</span>-Firmenbuch.at</div>"
            f"{body_html}</div></body></html>",
            status_code=status,
        )

    def _validated_authz(params: dict[str, Any]) -> tuple[Any, str]:
        """Return ``(client, error)``: client is None + a message on failure. On a bad
        client/redirect we must NOT redirect (open-redirect guard) — the caller renders an
        error page instead."""
        from fbl_auth import get_client

        client_id = params.get("client_id", "")
        redirect_uri = params.get("redirect_uri", "")
        client = get_client(cosmos, client_id) if client_id else None
        if client is None:
            return None, "Unbekannter oder fehlender client_id."
        if redirect_uri not in client.redirect_uris:
            return None, "redirect_uri ist für diesen Client nicht registriert."
        if params.get("response_type", "code") != "code":
            return None, "Nur response_type=code wird unterstützt."
        if not params.get("code_challenge"):
            return None, "PKCE code_challenge fehlt (erforderlich)."
        if params.get("code_challenge_method", "S256") != "S256":
            return None, "Nur code_challenge_method=S256 wird unterstützt."
        return client, ""

    @mcp.custom_route("/authorize", methods=["GET", "POST"])  # type: ignore[untyped-decorator]
    async def _authorize(request: Any) -> Any:
        from fbl_auth import create_pending_auth, is_plausible_email

        params = dict(request.query_params)
        if request.method == "POST":
            form = await request.form()
            params = {**params, **{k: form[k] for k in form}}
        client, err = _validated_authz(params)
        if client is None:
            return _page("Fehler", f"<p>{html.escape(err)}</p>", status=400)

        hidden = "".join(
            f"<input type=hidden name={k} value='{html.escape(str(params.get(k, '')))}'>"
            for k in (
                "client_id",
                "redirect_uri",
                "code_challenge",
                "code_challenge_method",
                "state",
                "scope",
                "response_type",
            )
        )
        email = str(params.get("email", "")).strip()
        if request.method == "GET" or not email:
            cname = html.escape(client.client_name or "ein KI-Tool")
            return _page(
                "Verbinden",
                f"<p style='color:#9AA2AF'><strong style='color:#EDEFF3'>{cname}</strong> möchte "
                "sich mit Agentic-Firmenbuch.at verbinden. Gib deine E-Mail ein – du bekommst "
                "einen Bestätigungslink, kein Key nötig.</p>"
                "<form method=post style='margin-top:1.5rem'>"
                + hidden
                + "<input type=email name=email required placeholder='deine@email.at' "
                "style='width:100%;padding:12px 14px;border-radius:10px;border:1px solid #2a2f3a;"
                "background:#14161B;color:#EDEFF3;font-size:15px;box-sizing:border-box'>"
                "<button type=submit style='margin-top:12px;width:100%;padding:12px;border:0;"
                "border-radius:10px;background:#19C37D;color:#08130D;font-weight:700;font-size:15px;"
                "cursor:pointer'>Bestätigungslink senden</button></form>",
            )
        if not is_plausible_email(email):
            return _page("Fehler", "<p>Bitte eine gültige E-Mail-Adresse eingeben.</p>", status=400)

        pending = create_pending_auth(
            cosmos,
            client_id=str(params["client_id"]),
            redirect_uri=str(params["redirect_uri"]),
            code_challenge=str(params["code_challenge"]),
            state=params.get("state"),
            scope=str(params.get("scope") or "mcp:read"),
            email=email,
        )
        confirm_url = f"{_base}/authorize/confirm?grant={pending.grant_id}"
        # Delivery best-effort; never leak internals to the browser.
        with contextlib.suppress(Exception):
            _email.send_oauth_login(email, confirm_url, client.client_name or "ein KI-Tool")
        return _page(
            "E-Mail unterwegs",
            f"<p>Wir haben einen Bestätigungslink an <strong>{html.escape(email)}</strong> "
            "geschickt. Öffne ihn (gültig 15 Minuten), um die Verbindung abzuschließen. "
            "Du kannst dieses Fenster dann schließen.</p>"
            "<p style='margin-top:1rem;padding:12px 14px;background:#14161B;border-radius:10px;"
            "color:#9AA2AF;font-size:14px'><strong style='color:#EDEFF3'>Danach testen:</strong> "
            "Öffne einen neuen Chat und frage z. B. „Bist du mit Agentic-Firmenbuch.at verbunden? "
            "Was kannst du abfragen?“ – nennt der Agent die Firmenbuch-Werkzeuge, steht die "
            "Verbindung.</p>",
        )

    @mcp.custom_route("/authorize/confirm", methods=["GET"])  # type: ignore[untyped-decorator]
    async def _authorize_confirm(request: Any) -> Any:
        from starlette.responses import RedirectResponse

        from fbl_auth import consume_pending_auth, get_or_create_account_by_email, issue_code

        grant = request.query_params.get("grant", "")
        pending = consume_pending_auth(cosmos, grant) if grant else None
        if pending is None:
            return _page(
                "Link ungültig",
                "<p>Dieser Bestätigungslink ist abgelaufen oder wurde schon benutzt. "
                "Starte die Verbindung im KI-Tool einfach neu.</p>",
                status=400,
            )
        account = get_or_create_account_by_email(cosmos, pending.email)
        code = issue_code(
            cosmos,
            client_id=pending.client_id,
            account_id=account.id,
            redirect_uri=pending.redirect_uri,
            code_challenge=pending.code_challenge,
            scope=pending.scope,
        )
        q = {"code": code.code}
        if pending.state:
            q["state"] = pending.state
        sep = "&" if "?" in pending.redirect_uri else "?"
        return RedirectResponse(f"{pending.redirect_uri}{sep}{urlencode(q)}", status_code=302)

    @mcp.custom_route("/token", methods=["POST", "OPTIONS"])  # type: ignore[untyped-decorator]
    async def _token(request: Any) -> Any:
        from starlette.responses import JSONResponse

        cors = {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "*"}
        if request.method == "OPTIONS":
            return JSONResponse({}, status_code=204, headers=cors)

        from fbl_auth import (
            ACCESS_TTL_SEC,
            consume_code,
            consume_refresh,
            issue_token_pair,
            verify_pkce,
        )

        def _err(desc: str, code: str = "invalid_grant") -> Any:
            return JSONResponse(
                {"error": code, "error_description": desc}, status_code=400, headers=cors
            )

        form = await request.form()
        grant_type = str(form.get("grant_type", ""))

        if grant_type == "authorization_code":
            code_str = str(form.get("code", ""))
            verifier = str(form.get("code_verifier", ""))
            redirect_uri = str(form.get("redirect_uri", ""))
            rec = consume_code(cosmos, code_str) if code_str else None
            if rec is None:
                return _err("authorization code invalid or expired")
            if rec.redirect_uri != redirect_uri:
                return _err("redirect_uri mismatch")
            if not verifier or not verify_pkce(verifier, rec.code_challenge):
                return _err("PKCE verification failed")
            access, refresh = issue_token_pair(
                cosmos, client_id=rec.client_id, account_id=rec.account_id, scope=rec.scope
            )
            return JSONResponse(
                {
                    "access_token": access,
                    "token_type": "Bearer",
                    "expires_in": ACCESS_TTL_SEC,
                    "refresh_token": refresh,
                    "scope": rec.scope,
                },
                headers=cors,
            )

        if grant_type == "refresh_token":
            rotated = consume_refresh(cosmos, str(form.get("refresh_token", "")))
            if rotated is None:
                return _err("refresh token invalid or expired")
            account, client_id = rotated
            access, refresh = issue_token_pair(cosmos, client_id=client_id, account_id=account.id)
            return JSONResponse(
                {
                    "access_token": access,
                    "token_type": "Bearer",
                    "expires_in": ACCESS_TTL_SEC,
                    "refresh_token": refresh,
                    "scope": "mcp:read",
                },
                headers=cors,
            )

        return _err("unsupported grant_type", code="unsupported_grant_type")

    # The API key comes from the X-API-Key connection header (see _http_token); it is NOT a
    # tool argument, so the agent never has to know or pass it (and it never leaks into a
    # tool-call payload). `ctx: Context` is injected by FastMCP and excluded from the schema.
    @mcp.tool()
    def search_companies(
        ctx: ToolContext,
        filters: SearchFilters | None = None,
        sort: Sort | None = None,
        page: int = 1,
        page_size: int = 25,
    ) -> dict[str, Any]:
        """Filtered company search over the Austrian Firmenbuch.

        Returns a COMPACT summary card per company (name, legal_form, bundesland, size,
        Bilanzsumme, equity ratio, revenue, growth, has_guv) — NOT the full record. For one
        company's full profile call get_company_details; for everything we hold (full
        position taxonomy, per-year history, lineage) call get_full_record.
        Field reference: https://www.agentic-firmenbuch.at/felder.html
        """
        return svc.search_companies(_http_token(ctx), filters, sort, page, page_size)

    @mcp.tool()
    def get_company_details(ctx: ToolContext, fnr: str) -> dict[str, Any]:
        """Full served profile for one company by FNR (identity, location, financials with
        per-year Bilanz + GuV, all ratios, growth, employees, filings, management).
        Field reference: https://www.agentic-firmenbuch.at/felder.html
        """
        return svc.get_company_details(_http_token(ctx), fnr)

    @mcp.tool()
    def describe_fields(ctx: ToolContext) -> dict[str, Any]:
        """Catalog of every field the server can return, by tool tier (search card → full
        profile → full record), with code tables and availability/null rules. Call this to
        discover the full data shape before deciding which tool to use.
        Human-readable version: https://www.agentic-firmenbuch.at/felder.html"""
        return svc.describe_fields(_http_token(ctx))

    @mcp.tool()
    def get_company_history(
        ctx: ToolContext, fnr: str, metrics: list[str] | None = None
    ) -> dict[str, Any]:
        """Per-metric time series for one company."""
        return svc.get_company_history(_http_token(ctx), fnr, metrics)

    @mcp.tool()
    def get_full_record(ctx: ToolContext, fnr: str) -> dict[str, Any]:
        """Complete per-company record (superset of the served profile): every position's
        full history, unknown-code passthrough, completeness, guv_years (§5.1)."""
        return svc.get_full_record(_http_token(ctx), fnr)

    @mcp.tool()
    def get_document(ctx: ToolContext, doc_key: str) -> dict[str, Any]:
        """Resolve a filing document reference by key."""
        return svc.get_document(_http_token(ctx), doc_key)

    @mcp.tool()
    def list_sectors(ctx: ToolContext) -> dict[str, Any]:
        """Legal-form + size-class taxonomy with counts."""
        return svc.list_sectors(_http_token(ctx))

    @mcp.tool()
    def get_cohort_summary(ctx: ToolContext, dimension: str, value: str) -> dict[str, Any]:
        """Aggregate summary for a cohort (gkl / bundesland / legal_form)."""
        return svc.get_cohort_summary(_http_token(ctx), dimension, value)

    @mcp.tool()
    def find_peers(ctx: ToolContext, fnr: str, n: int = 10) -> dict[str, Any]:
        """Nearest companies by Bilanzsumme within the same size class."""
        return svc.find_peers(_http_token(ctx), fnr, n)

    @mcp.tool()
    def get_coverage(ctx: ToolContext) -> dict[str, Any]:
        """Internal coverage dashboard: XML vs PDF-only vs none, by format/status."""
        return svc.get_coverage(_http_token(ctx))

    @mcp.tool()
    def get_my_usage(ctx: ToolContext, window: str = "today") -> dict[str, Any]:
        """Your own API-key usage: call count and weighted compute-units, broken down
        per tool. window ∈ {today, yesterday, month_to_date, last_30_days, all}.
        Returns only your own key's usage — no other user's data, no e-mail."""
        return svc.get_my_usage(_http_token(ctx), window)

    return mcp


def _scope_credential(scope: dict[str, Any]) -> tuple[str, str] | None:
    """The auth credential the ASGI request carries, as ``(kind, token)``:
    ``("api_key", v)`` for an ``X-API-Key`` header (Claude Code / Copilot / Cursor),
    ``("bearer", v)`` for an ``Authorization: Bearer`` header (the OAuth path), or
    ``None`` if neither is present. Presence/shape only — the API-key value's validity is
    still checked later by ``McpService._authorize``; a bearer's validity is checked by the
    wrapper so an expired one can trigger a refresh-inducing 401 (see ``_OAuthChallenge``)."""
    for raw_key, raw_val in scope.get("headers", []):
        key = raw_key.decode("latin-1").lower()
        if key == "x-api-key" and raw_val.strip():
            return ("api_key", raw_val.decode("latin-1").strip())
        if key == "authorization":
            val = raw_val.decode("latin-1").strip()
            if val.lower().startswith("bearer "):
                return ("bearer", val[7:].strip())
    return None


# JSON-RPC methods that only reveal the server's PUBLIC shape (handshake + tool/prompt/
# resource catalog), never any company data. These are allowed through WITHOUT a credential
# so directory health checks (Glama etc.) and "preview the tools before connecting" work.
# Everything else — above all ``tools/call`` — still requires auth. The tool schemas are
# already fully public (felder.html, the MCP registry), so exposing the catalog leaks nothing.
_ANON_DISCOVERY_METHODS = frozenset(
    {
        "initialize",
        "notifications/initialized",
        "tools/list",
        "prompts/list",
        "resources/list",
        "resources/templates/list",
        "ping",
    }
)
_MAX_INSPECT_BYTES = 1_048_576  # MCP JSON-RPC requests are tiny; cap the buffered body at 1 MB.


def _is_anonymous_discovery(body: bytes) -> bool:
    """True iff *body* is a JSON-RPC request (or batch) whose every method is a public,
    data-free discovery method. Safe-by-default: unparseable / any other method -> False."""
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        return False
    items = data if isinstance(data, list) else [data]
    if not items:
        return False
    return all(isinstance(it, dict) and it.get("method") in _ANON_DISCOVERY_METHODS for it in items)


async def _read_body(receive: Any) -> bytes:
    """Drain the ASGI request body (bounded). Pairs with ``_replay`` to feed it to the app."""
    chunks: list[bytes] = []
    size = 0
    while True:
        msg = await receive()
        if msg.get("type") != "http.request":
            break
        chunk = msg.get("body", b"") or b""
        chunks.append(chunk)
        size += len(chunk)
        if size > _MAX_INSPECT_BYTES or not msg.get("more_body", False):
            break
    return b"".join(chunks)


def _replay(receive: Any, body: bytes) -> Any:
    """A receive() that yields the already-read *body* once, then delegates to *receive*."""
    sent = False

    async def replay() -> Any:
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return await receive()

    return replay


class _OAuthChallenge:
    """ASGI wrapper that makes the MCP endpoint a spec-compliant OAuth 2.0 protected resource.

    An OAuth client (Cowork, claude.ai) that tries an actual data call with no credential
    gets a ``401`` carrying ``WWW-Authenticate: Bearer resource_metadata="…"`` (RFC 9728),
    which triggers OAuth discovery (DCR -> /authorize -> /token).

    We challenge in two cases, both of which the OAuth client knows how to act on:
    * **no credential on a data call** (anything other than the public discovery methods in
      ``_ANON_DISCOVERY_METHODS``) -> first-time discovery (DCR -> /authorize -> /token).
    * **a Bearer that is expired/invalid/revoked** -> the client silently refreshes (it
      holds a 30-day refresh token) and retries. Without this, an expired access token
      reaches the tool, fails validation deep inside, and returns ``invalid or unknown
      token`` inside an HTTP 200 -- which the client reads as "the call succeeded", so it
      never refreshes and the connection silently dies ~1h after connecting (the 1h access
      TTL), permanently, until a manual reconnect. A 401 here is RFC 6750 ``invalid_token``.

    **Anonymous discovery is allowed** (handshake + tool/prompt/resource catalog) so directory
    health checks and tool previews work without a key; the OAuth challenge is simply deferred
    from connect-time to the first real ``tools/call``. The data itself is never exposed
    anonymously — every tool still calls ``_authorize`` internally.

    An invalid **X-API-Key** is left to flow to ``_authorize`` (those clients do not do OAuth
    and would not act on the challenge), so the existing header path is untouched.

    We intentionally do NOT enable FastMCP's native auth: that would *require* a Bearer on
    every request and lock out the X-API-Key clients.
    """

    def __init__(self, app: Any, base: str, cosmos: CosmosStoreLike) -> None:
        self._app = app
        self._cosmos = cosmos
        self._resource_metadata_url = f"{base.rstrip('/')}/.well-known/oauth-protected-resource/mcp"

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        is_mcp = scope.get("type") == "http" and scope.get("path", "").rstrip("/") == "/mcp"
        if not is_mcp:
            await self._app(scope, receive, send)
            return

        cred = _scope_credential(scope)
        if cred is not None:
            # A credential is present. A present-but-expired/invalid Bearer must 401 so the
            # client refreshes; an X-API-Key (valid or not) flows to _authorize as before.
            if cred[0] == "bearer" and validate_bearer(self._cosmos, cred[1]) is None:
                await self._challenge(send)
                return
            await self._app(scope, receive, send)
            return

        # No credential. Allow anonymous discovery (handshake + catalog) so directory health
        # checks and tool previews succeed; challenge anything that touches data so OAuth
        # clients still get their 401 on the first real call. Only POST carries a JSON-RPC
        # body to inspect; a no-credential GET/DELETE (SSE stream / session op) is challenged.
        if scope.get("method") != "POST":
            await self._challenge(send)
            return
        body = await _read_body(receive)
        if _is_anonymous_discovery(body):
            await self._app(scope, _replay(receive, body), send)
            return
        await self._challenge(send)

    async def _challenge(self, send: Any) -> None:
        www_auth = f'Bearer resource_metadata="{self._resource_metadata_url}"'
        body = b'{"error":"unauthorized","error_description":"authentication required"}'
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"www-authenticate", www_auth.encode("latin-1")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


def build_asgi_app(cosmos: CosmosStoreLike, settings: Settings | None = None) -> Any:
    """The production ASGI app: the FastMCP streamable-HTTP transport wrapped so unauthenticated
    ``/mcp`` requests trigger OAuth discovery (see ``_OAuthChallenge``). This is what
    ``__main__`` serves with uvicorn; tests drive it directly via Starlette's TestClient."""
    mcp = build_app(cosmos, settings)
    base = os.environ.get("PUBLIC_BASE_URL", "https://mcp.agentic-firmenbuch.at")
    return _OAuthChallenge(mcp.streamable_http_app(), base, cosmos)
