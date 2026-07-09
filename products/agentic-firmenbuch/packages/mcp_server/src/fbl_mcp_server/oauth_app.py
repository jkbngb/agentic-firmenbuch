"""MCP OAuth 2.1 for clients that cannot send the X-API-Key header (Cowork, claude.ai) (§8.10b).

Split out of ``app.py`` to keep that file focused on the MCP tools. Two parts:
* ``register_oauth_endpoints`` -- the discovery / DCR / authorize / token HTTP routes, wired onto
  the FastMCP server by ``app.build_app``.
* ``_OAuthChallenge`` (+ helpers) -- the ASGI wrapper that turns ``/mcp`` into a spec-compliant
  OAuth protected resource, used by ``app.build_asgi_app``.

This module never imports ``app`` (no circular dependency): ``app`` imports from here.
"""

from __future__ import annotations

import contextlib
import html
import json
from typing import Any
from urllib.parse import urlencode

from fbl_auth import validate_bearer
from fbl_core.storage import CosmosStoreLike


def register_oauth_endpoints(
    mcp: Any, cosmos: CosmosStoreLike, base: str, email_sender: Any
) -> None:
    """Register the OAuth 2.1 discovery / DCR / authorize / token routes on *mcp* (§8.10b).

    These let clients that cannot use the X-API-Key header (Claude Cowork, claude.ai) attach by
    URL + email login. ``base`` is the authorization-base URL (host root, MCP path stripped);
    ``email_sender`` sends the magic-link confirmation mail.
    """

    @mcp.custom_route("/.well-known/oauth-authorization-server", methods=["GET"])  # type: ignore[untyped-decorator]
    async def _oauth_metadata(_request: Any) -> Any:
        from starlette.responses import JSONResponse

        return JSONResponse(
            {
                "issuer": base,
                "authorization_endpoint": f"{base}/authorize",
                "token_endpoint": f"{base}/token",
                "registration_endpoint": f"{base}/register",
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
                "resource": f"{base}/mcp",
                "authorization_servers": [base],
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
        confirm_url = f"{base}/authorize/confirm?grant={pending.grant_id}"
        # Delivery best-effort; never leak internals to the browser.
        with contextlib.suppress(Exception):
            email_sender.send_oauth_login(email, confirm_url, client.client_name or "ein KI-Tool")
        return _page(
            "E-Mail unterwegs",
            f"<p>Wir haben einen Bestätigungslink an <strong>{html.escape(email)}</strong> "
            "geschickt. Öffne ihn (gültig 60 Minuten) und klicke auf „Verbindung bestätigen“. "
            "Du kannst dieses Fenster dann schließen.</p>"
            "<p style='margin-top:1rem;padding:12px 14px;background:#14161B;border-radius:10px;"
            "color:#9AA2AF;font-size:14px'><strong style='color:#EDEFF3'>Danach testen:</strong> "
            "Öffne einen neuen Chat und frage z. B. „Bist du mit Agentic-Firmenbuch.at verbunden? "
            "Was kannst du abfragen?“ – nennt der Agent die Firmenbuch-Werkzeuge, steht die "
            "Verbindung.</p>",
        )

    @mcp.custom_route("/authorize/confirm", methods=["GET", "POST"])  # type: ignore[untyped-decorator]
    async def _authorize_confirm(request: Any) -> Any:
        from starlette.responses import RedirectResponse

        from fbl_auth import consume_pending_auth, get_or_create_account_by_email, issue_code

        # Two-step so corporate mail link-scanners (Microsoft 365 Safe Links, Proofpoint, …) can't
        # silently consume the one-time magic link: the email link is a GET that ONLY shows a
        # confirm button — it consumes nothing. Scanners do the GET (harmless); the human clicks
        # the button → POST → actual consent. Without this, a Safe-Links GET marked the grant
        # "used" before the user, so every M365 user got "Link abgelaufen/benutzt" and no token.
        if request.method == "GET":
            grant = request.query_params.get("grant", "")
            if not grant:
                return _page(
                    "Link ungültig", "<p>Dieser Bestätigungslink ist ungültig.</p>", status=400
                )
            return _page(
                "Verbindung bestätigen",
                "<p style='color:#9AA2AF'>Fast geschafft — klicke, um die Verbindung mit "
                "Agentic-Firmenbuch.at abzuschließen.</p>"
                "<form method=post style='margin-top:1.5rem'>"
                f"<input type=hidden name=grant value='{html.escape(grant)}'>"
                "<button type=submit style='width:100%;padding:12px;border:0;border-radius:10px;"
                "background:#19C37D;color:#08130D;font-weight:700;font-size:15px;cursor:pointer'>"
                "Verbindung bestätigen</button></form>"
                "<p style='margin-top:1.25rem;padding:11px 13px;background:#14161B;"
                "border-radius:10px;color:#9AA2AF;font-size:13px;line-height:1.5'>"
                "<strong style='color:#EDEFF3'>Mehrere Claude-Konten?</strong> Öffne diesen Link "
                "im selben Browser bzw. Claude-Konto, mit dem du den Connector hinzugefügt hast — "
                "sonst kann Claude die Verbindung nicht abschließen.</p>",
            )
        form = await request.form()
        grant = str(form.get("grant", "")).strip()
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

    def __init__(
        self,
        app: Any,
        base: str,
        cosmos: CosmosStoreLike,
        *,
        anonymous_discovery: bool = False,
    ) -> None:
        self._app = app
        self._cosmos = cosmos
        # Directory-compliant default: challenge EVERY unauthenticated /mcp request (incl. the
        # first `initialize`) with a 401. When True, the handshake + tool catalog are allowed
        # through without a credential (registry health checks / anonymous tool preview).
        self._anonymous_discovery = anonymous_discovery
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

        # No credential. Directory-compliant default (anonymous_discovery=False): challenge
        # EVERYTHING, including the first `initialize`, so the Anthropic reviewer's
        # unauthenticated probe gets its 401 + WWW-Authenticate on the handshake. When
        # anonymous_discovery is enabled, allow the handshake + catalog (data-free discovery
        # methods) through so registry health checks / tool previews work; still challenge
        # anything that touches data. Only POST carries a JSON-RPC body to inspect; a
        # no-credential GET/DELETE (SSE stream / session op) is always challenged.
        if not self._anonymous_discovery:
            await self._challenge(send)
            return
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
