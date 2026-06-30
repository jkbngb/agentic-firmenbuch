"""OAuth phase-2 HTTP endpoints (metadata + DCR + stub authorize/token) on the FastMCP
streamable-HTTP ASGI app. Cowork-class clients discover and register against these."""

from __future__ import annotations

import json

from starlette.testclient import TestClient

from fbl_auth import issue_token_pair, register_client, signup
from fbl_core.config import Settings
from fbl_core.storage import InMemoryCosmosStore
from fbl_mcp_server import build_app, build_asgi_app


def _client() -> TestClient:
    app = build_app(InMemoryCosmosStore(), Settings())
    return TestClient(app.streamable_http_app())


def _asgi_client(cosmos: InMemoryCosmosStore | None = None) -> TestClient:
    # The production ASGI app: streamable-HTTP transport + the OAuth-challenge wrapper.
    return TestClient(build_asgi_app(cosmos or InMemoryCosmosStore(), Settings()))


def test_unauthenticated_data_call_triggers_oauth_discovery() -> None:
    # A no-credential *data* call (tools/call) must return 401 + WWW-Authenticate pointing at
    # the protected-resource metadata (RFC 9728) — this is what triggers OAuth discovery for
    # Cowork/claude.ai. (The challenge is deferred from connect-time to the first real call;
    # discovery methods like initialize/tools-list are now allowed anonymously, below.)
    r = _asgi_client().post(
        "/mcp",
        content=json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "search_companies", "arguments": {}},
            }
        ),
        headers={
            "content-type": "application/json",
            "accept": "application/json, text/event-stream",
        },
    )
    assert r.status_code == 401
    www = r.headers["www-authenticate"]
    assert www.startswith("Bearer ")
    assert "resource_metadata=" in www
    assert "/.well-known/oauth-protected-resource/mcp" in www


def test_anonymous_discovery_is_allowed() -> None:
    # Directory health checks (Glama etc.) and "preview the tools before connecting" must
    # work without a key: a no-credential initialize / tools/list passes the wrapper (the
    # tool *catalog* is already fully public; only tools/call exposes data and stays gated).
    for method in ("initialize", "tools/list"):
        with _asgi_client() as client:
            r = client.post(
                "/mcp",
                content=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": {}}),
                headers={
                    "content-type": "application/json",
                    "accept": "application/json, text/event-stream",
                },
            )
        assert r.status_code != 401, f"{method} must not be challenged"


def test_unauthenticated_malformed_body_is_challenged() -> None:
    # Safe-by-default: an unparseable / non-discovery no-credential POST is still challenged.
    r = _asgi_client().post(
        "/mcp",
        content=b"not json",
        headers={"content-type": "application/json", "accept": "application/json"},
    )
    assert r.status_code == 401


def test_api_key_request_is_not_challenged() -> None:
    # An X-API-Key client (Claude Code/Copilot/Cursor) must pass the wrapper untouched —
    # it must NOT get a 401 OAuth challenge (that would break the existing header path).
    # `with` so the streamable-HTTP transport's lifespan/task-group actually starts.
    with _asgi_client() as client:
        r = client.post(
            "/mcp",
            content=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
            headers={
                "content-type": "application/json",
                "accept": "application/json, text/event-stream",
                "x-api-key": "whatever",
            },
        )
    assert r.status_code != 401


def test_expired_or_invalid_bearer_triggers_oauth_challenge() -> None:
    # The hourly silent-death bug: when a Bearer token is present but expired/invalid, the
    # server must return 401 + WWW-Authenticate (RFC 6750 `invalid_token`) so the OAuth
    # client refreshes (it holds a 30-day refresh token) instead of reading the deep
    # `invalid or unknown token` tool-error as an HTTP-200 success and never refreshing.
    r = _asgi_client().post(
        "/mcp",
        content=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
        headers={
            "content-type": "application/json",
            "accept": "application/json, text/event-stream",
            "authorization": "Bearer not-a-real-token",
        },
    )
    assert r.status_code == 401
    assert "resource_metadata=" in r.headers["www-authenticate"]


def test_valid_bearer_passes_the_challenge() -> None:
    # A live access token must NOT be challenged — it reaches the tool layer normally.
    cosmos = InMemoryCosmosStore()
    account_id = signup("u@example.com", cosmos).account.id
    client = register_client(cosmos, client_name="Cowork", redirect_uris=["https://claude.ai/cb"])
    access, _ = issue_token_pair(cosmos, client_id=client.client_id, account_id=account_id)
    with _asgi_client(cosmos) as c:
        r = c.post(
            "/mcp",
            content=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
            headers={
                "content-type": "application/json",
                "accept": "application/json, text/event-stream",
                "authorization": f"Bearer {access}",
            },
        )
    assert r.status_code != 401


def test_protected_resource_metadata_points_at_auth_server() -> None:
    # RFC 9728 document the client fetches after the 401.
    for path in (
        "/.well-known/oauth-protected-resource/mcp",
        "/.well-known/oauth-protected-resource",
    ):
        r = _client().get(path)
        assert r.status_code == 200, path
        meta = r.json()
        assert meta["resource"].endswith("/mcp")
        assert meta["authorization_servers"]  # non-empty; client derives auth-server metadata
        assert meta["bearer_methods_supported"] == ["header"]


def test_oauth_metadata_advertises_required_endpoints() -> None:
    # Cowork hits this on first attach; it must list authorize/token/register and PKCE-S256.
    r = _client().get("/.well-known/oauth-authorization-server")
    assert r.status_code == 200
    meta = r.json()
    assert meta["authorization_endpoint"].endswith("/authorize")
    assert meta["token_endpoint"].endswith("/token")
    assert meta["registration_endpoint"].endswith("/register")
    assert "S256" in meta["code_challenge_methods_supported"]
    # OAuth 2.1 + public PKCE client => no auth method at /token
    assert meta["token_endpoint_auth_methods_supported"] == ["none"]
    assert "authorization_code" in meta["grant_types_supported"]
    assert "refresh_token" in meta["grant_types_supported"]


def test_dcr_issues_a_public_client() -> None:
    c = _client()
    body = {"client_name": "ClaudeCowork", "redirect_uris": ["https://claude.ai/oauth/cb"]}
    r = c.post("/register", content=json.dumps(body), headers={"content-type": "application/json"})
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["client_id"]
    assert out["token_endpoint_auth_method"] == "none"
    assert out["redirect_uris"] == body["redirect_uris"]


def test_dcr_rejects_non_https_non_localhost_redirect() -> None:
    # OAuth 2.1: redirect URIs must be https OR localhost. http://evil.example must be refused.
    c = _client()
    r = c.post(
        "/register",
        content=json.dumps({"redirect_uris": ["http://evil.example/cb"]}),
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_redirect_uri"


def test_dcr_accepts_localhost_for_local_clients() -> None:
    # Claude Code-class clients register a loopback redirect; must work.
    c = _client()
    r = c.post(
        "/register",
        content=json.dumps({"redirect_uris": ["http://localhost:7777/cb"]}),
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 201


def test_authorize_rejects_unknown_client() -> None:
    # Open-redirect guard: a bad/unknown client must NOT redirect, just show an error.
    r = _client().get("/authorize?client_id=nope&redirect_uri=https://x/cb&code_challenge=abc")
    assert r.status_code == 400
    assert "client_id" in r.text


def test_authorize_requires_pkce() -> None:
    c = _client()
    reg = c.post(
        "/register",
        content=json.dumps({"redirect_uris": ["https://claude.ai/cb"]}),
        headers={"content-type": "application/json"},
    ).json()
    # Missing code_challenge => rejected (OAuth 2.1 requires PKCE).
    r = c.get(f"/authorize?client_id={reg['client_id']}&redirect_uri=https://claude.ai/cb")
    assert r.status_code == 400
    assert "PKCE" in r.text


def test_authorize_get_renders_email_form() -> None:
    c = _client()
    reg = c.post(
        "/register",
        content=json.dumps({"client_name": "Cowork", "redirect_uris": ["https://claude.ai/cb"]}),
        headers={"content-type": "application/json"},
    ).json()
    r = c.get(
        f"/authorize?client_id={reg['client_id']}&redirect_uri=https://claude.ai/cb"
        "&code_challenge=abc123&code_challenge_method=S256&state=xyz"
    )
    assert r.status_code == 200
    assert "name=email" in r.text and "Cowork" in r.text


def test_token_rejects_garbage() -> None:
    # No valid code => invalid_grant (not a 503 stub anymore).
    r = _client().post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": "nope",
            "code_verifier": "v",
            "redirect_uri": "https://claude.ai/cb",
        },
    )
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_grant"


def test_full_oauth_flow_end_to_end(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """DCR -> /authorize (email) -> [simulate email click] -> /authorize/confirm -> code ->
    /token (PKCE) -> Bearer -> the token authenticates an MCP tool call. The whole point."""
    import base64
    import hashlib

    from fbl_auth import signup, validate_bearer

    cosmos = InMemoryCosmosStore()
    # A user already has an account (API-key signup); OAuth must reuse it by email.
    signup("user@example.at", cosmos)
    app = build_app(cosmos, Settings())
    c = TestClient(app.streamable_http_app())

    # 1) DCR
    reg = c.post(
        "/register",
        content=json.dumps({"client_name": "Cowork", "redirect_uris": ["https://claude.ai/cb"]}),
        headers={"content-type": "application/json"},
    ).json()
    client_id = reg["client_id"]

    # 2) PKCE params
    verifier = "verifier-0123456789-abcdefghijklmnopqrstuvwxyz"
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )

    # 3) /authorize POST with email -> creates a pending grant (email send is no-op in tests).
    r = c.post(
        "/authorize",
        data={
            "client_id": client_id,
            "redirect_uri": "https://claude.ai/cb",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "st-1",
            "email": "user@example.at",
        },
    )
    assert r.status_code == 200 and "unterwegs" in r.text

    # 4) The magic link is GET -> it ONLY shows a confirm button and consumes NOTHING, so a
    #    corporate mail link-scanner (Microsoft Safe Links) prefetching the URL can't burn the
    #    grant. Two GETs (a scanner + the human opening it) both leave it usable; only the POST
    #    (the human clicking the button) consumes + redirects with the code.
    from fbl_auth import OAUTH_PENDING

    pend = next(iter(cosmos.iter_all(OAUTH_PENDING)))
    gid = pend["grant_id"]
    g1 = c.get(f"/authorize/confirm?grant={gid}")
    assert g1.status_code == 200 and "bestätigen" in g1.text.lower()
    assert c.get(f"/authorize/confirm?grant={gid}").status_code == 200  # scanner prefetch: no burn
    confirm = c.post("/authorize/confirm", data={"grant": gid}, follow_redirects=False)
    assert confirm.status_code == 302
    loc = confirm.headers["location"]
    assert loc.startswith("https://claude.ai/cb?") and "state=st-1" in loc
    code = loc.split("code=")[1].split("&")[0]
    # and a re-POST of the now-consumed grant fails (still one-shot at the real consent step)
    assert c.post("/authorize/confirm", data={"grant": gid}).status_code == 400

    # 5) /token exchange with the verifier
    tok = c.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": "https://claude.ai/cb",
            "code_verifier": verifier,
            "client_id": client_id,
        },
    )
    assert tok.status_code == 200, tok.text
    payload = tok.json()
    assert (
        payload["token_type"] == "Bearer" and payload["access_token"] and payload["refresh_token"]
    )

    # 6) The Bearer token resolves to the SAME account as the email signup.
    acc = validate_bearer(cosmos, payload["access_token"])
    assert acc is not None and acc.email == "user@example.at"

    # 7) PKCE enforced: a wrong verifier is rejected (re-run authorize->confirm for a fresh code).
    c.post(
        "/authorize",
        data={
            "client_id": client_id,
            "redirect_uri": "https://claude.ai/cb",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "email": "user@example.at",
        },
    )
    pend2 = next(p for p in cosmos.iter_all(OAUTH_PENDING) if not p["used"])
    r2 = c.post("/authorize/confirm", data={"grant": pend2["grant_id"]}, follow_redirects=False)
    loc2 = r2.headers["location"]
    code2 = loc2.split("code=")[1].split("&")[0]
    bad = c.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code2,
            "redirect_uri": "https://claude.ai/cb",
            "code_verifier": "WRONG",
            "client_id": client_id,
        },
    )
    assert bad.status_code == 400 and bad.json()["error"] == "invalid_grant"
