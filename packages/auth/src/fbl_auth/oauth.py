"""MCP OAuth 2.1 data model + storage + bearer-token validation (§8.10b).

Cowork and claude.ai cannot use the X-API-Key header (sandboxed clients with no
free-form header field in their Connector UI). The MCP spec (2025-03-26) requires
OAuth 2.1 with PKCE + RFC 8414 metadata discovery + RFC 7591 Dynamic Client Registration.
This module provides the storage primitives and the bearer-token validator; the HTTP
endpoints (metadata, /register, /authorize, /token) live in ``fbl_mcp_server`` and call
into here.

Three persistent doc kinds, all in container ``00_oauth_*`` (partitioned by /id):

* ``OAuthClient``  -- registered MCP client (DCR). Stored under ``00_oauth_clients``.
* ``AuthorizationCode`` -- short-lived code returned from /authorize, redeemed at /token.
  Stored under ``00_oauth_codes``; auto-expires after 600 s.
* ``BearerToken`` -- long-lived access + refresh tokens issued at /token. Stored under
  ``00_oauth_tokens`` as the SHA-256 hash of the plaintext token (same scheme as the
  X-API-Key path), linked to an existing ``Account``.

A bearer token resolves to the same ``Account`` as an API key would, so the rate-limit
and metering pipeline downstream (``check_rate_limit`` / ``record_usage``) is unchanged.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field

from fbl_core.lineage import now_utc_z
from fbl_core.storage import CosmosStoreLike

from .accounts import Account, hash_token

OAUTH_CLIENTS = "00_oauth_clients"
OAUTH_CODES = "00_oauth_codes"
OAUTH_TOKENS = "00_oauth_tokens"
OAUTH_PENDING = "00_oauth_pending"
ACCOUNTS = "00_accounts"
PENDING_TTL_SEC = 3600  # 60 min for the user to click the magic link (mail may be read later)


def verify_pkce(code_verifier: str, code_challenge: str) -> bool:
    """OAuth 2.1 PKCE S256 check: ``challenge == base64url(sha256(verifier))`` (no padding)."""
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return secrets.compare_digest(computed, code_challenge)


def get_or_create_account_by_email(cosmos: CosmosStoreLike, email: str) -> Account:
    """Return the Account for ``email``, creating an OAuth-only one if none exists.

    Idempotent by email: an existing account (e.g. from an API-key signup) is reused so a
    user who has both a key and an OAuth connector shares one account / rate-limit bucket.
    A new OAuth-only account gets a RANDOM id (not a hash of any guessable string), so the
    X-API-Key path can never accidentally resolve to it.
    """
    email = email.strip().lower()
    existing = list(cosmos.query_by_field(ACCOUNTS, "email", email))
    if existing:
        return Account.model_validate(existing[0])
    rid = "acct:" + secrets.token_urlsafe(24)
    acc = Account(id=rid, token_hash=rid, email=email)
    cosmos.upsert(ACCOUNTS, acc.model_dump(mode="json"))
    return acc


class PendingAuth(BaseModel):
    """A half-finished /authorize flow: the user gave their email; we emailed a magic link.
    Clicking the link (``consume_pending_auth``) completes consent and issues the code."""

    id: str  # == grant_id; the only thing the magic link carries
    grant_id: str
    client_id: str
    redirect_uri: str
    code_challenge: str  # PKCE challenge from the original client request
    state: str | None = None
    scope: str = "mcp:read"
    email: str
    expires_at: str = Field(default_factory=lambda: _exp(PENDING_TTL_SEC))
    used: bool = False


def create_pending_auth(
    cosmos: CosmosStoreLike,
    *,
    client_id: str,
    redirect_uri: str,
    code_challenge: str,
    state: str | None,
    scope: str,
    email: str,
) -> PendingAuth:
    gid = secrets.token_urlsafe(32)
    rec = PendingAuth(
        id=gid,
        grant_id=gid,
        client_id=client_id,
        redirect_uri=redirect_uri,
        code_challenge=code_challenge,
        state=state,
        scope=scope,
        email=email.strip().lower(),
    )
    cosmos.upsert(OAUTH_PENDING, rec.model_dump(mode="json"))
    return rec


def consume_pending_auth(cosmos: CosmosStoreLike, grant_id: str) -> PendingAuth | None:
    """One-shot: load + mark used. None if unknown, expired, or already clicked."""
    doc = cosmos.get(OAUTH_PENDING, grant_id)
    if doc is None:
        return None
    rec = PendingAuth.model_validate(doc)
    if rec.used or _expired(rec.expires_at):
        return None
    rec.used = True
    cosmos.upsert(OAUTH_PENDING, rec.model_dump(mode="json"))
    return rec


# Lifetimes — short codes (one-shot, exchanged within seconds), medium access tokens
# (long enough to avoid constant refresh chatter), long refresh tokens (matches typical
# OAuth defaults; MCP clients refresh out-of-band).
CODE_TTL_SEC = 600  # 10 minutes (MCP spec example)
ACCESS_TTL_SEC = 3600  # 1 hour
REFRESH_TTL_SEC = 30 * 24 * 3600  # 30 days


def _exp(ttl_sec: int) -> str:
    """ISO-8601 UTC timestamp ``ttl_sec`` seconds from now."""
    return (datetime.now(UTC) + timedelta(seconds=ttl_sec)).isoformat()


def _expired(expires_at: str) -> bool:
    return bool(expires_at < now_utc_z())


# --- Dynamic Client Registration (RFC 7591) ----------------------------------------------


class OAuthClient(BaseModel):
    """An MCP client registered via DCR. Public client (PKCE), no secret."""

    id: str  # == client_id; opaque random
    client_id: str
    client_name: str | None = None
    redirect_uris: list[str] = Field(default_factory=list)
    grant_types: list[str] = Field(default_factory=lambda: ["authorization_code", "refresh_token"])
    response_types: list[str] = Field(default_factory=lambda: ["code"])
    token_endpoint_auth_method: Literal["none"] = "none"  # public client
    created_at: str = Field(default_factory=now_utc_z)


def register_client(
    cosmos: CosmosStoreLike, *, client_name: str | None, redirect_uris: list[str]
) -> OAuthClient:
    """Persist a new public MCP client and return it. The plaintext ``client_id`` is the
    only credential the caller gets back; we never issue a secret (public PKCE client)."""
    cid = secrets.token_urlsafe(24)
    client = OAuthClient(
        id=cid, client_id=cid, client_name=client_name, redirect_uris=redirect_uris
    )
    cosmos.upsert(OAUTH_CLIENTS, client.model_dump(mode="json"))
    return client


def get_client(cosmos: CosmosStoreLike, client_id: str) -> OAuthClient | None:
    doc = cosmos.get(OAUTH_CLIENTS, client_id)
    return OAuthClient.model_validate(doc) if doc else None


# --- Authorization codes (one-shot, PKCE-bound) ------------------------------------------


class AuthorizationCode(BaseModel):
    """A one-shot code issued from /authorize, redeemed once at /token."""

    id: str  # == code; opaque random, never reused
    code: str
    client_id: str
    account_id: str  # which 00_accounts row this code grants on redemption
    redirect_uri: str
    code_challenge: str  # the PKCE challenge the client supplied at /authorize
    code_challenge_method: Literal["S256"] = "S256"  # OAuth 2.1 disallows "plain"
    scope: str = "mcp:read"
    expires_at: str = Field(default_factory=lambda: _exp(CODE_TTL_SEC))
    used: bool = False  # one-shot — set true on first redemption


def issue_code(
    cosmos: CosmosStoreLike,
    *,
    client_id: str,
    account_id: str,
    redirect_uri: str,
    code_challenge: str,
    scope: str = "mcp:read",
) -> AuthorizationCode:
    code = secrets.token_urlsafe(32)
    rec = AuthorizationCode(
        id=code,
        code=code,
        client_id=client_id,
        account_id=account_id,
        redirect_uri=redirect_uri,
        code_challenge=code_challenge,
        scope=scope,
    )
    cosmos.upsert(OAUTH_CODES, rec.model_dump(mode="json"))
    return rec


def consume_code(cosmos: CosmosStoreLike, code: str) -> AuthorizationCode | None:
    """Atomically take + mark used. Returns None if unknown, expired, or already redeemed."""
    doc = cosmos.get(OAUTH_CODES, code)
    if doc is None:
        return None
    rec = AuthorizationCode.model_validate(doc)
    if rec.used or _expired(rec.expires_at):
        return None
    rec.used = True
    cosmos.upsert(OAUTH_CODES, rec.model_dump(mode="json"))
    return rec


# --- Bearer tokens (access + refresh) ----------------------------------------------------


class BearerToken(BaseModel):
    """An access or refresh token issued at /token. Stored under the SHA-256 hash of the
    plaintext (same scheme as API keys), so the plaintext exists only in transit."""

    id: str  # == sha256:<hex>
    token_hash: str
    kind: Literal["access", "refresh"]
    client_id: str
    account_id: str  # the 00_accounts row this token authenticates
    scope: str = "mcp:read"
    expires_at: str
    created_at: str = Field(default_factory=now_utc_z)
    revoked: bool = False
    # For a refresh token, the access we issued from it (so the latest exchange can revoke
    # the prior access on rotation).
    paired_access_hash: str | None = None


def _new_bearer(
    cosmos: CosmosStoreLike, *, kind: Literal["access", "refresh"], **fields: object
) -> tuple[str, BearerToken]:
    plaintext = secrets.token_urlsafe(32)
    th = hash_token(plaintext)
    ttl = ACCESS_TTL_SEC if kind == "access" else REFRESH_TTL_SEC
    rec = BearerToken(id=th, token_hash=th, kind=kind, expires_at=_exp(ttl), **fields)  # type: ignore[arg-type]
    cosmos.upsert(OAUTH_TOKENS, rec.model_dump(mode="json"))
    return plaintext, rec


def issue_token_pair(
    cosmos: CosmosStoreLike, *, client_id: str, account_id: str, scope: str = "mcp:read"
) -> tuple[str, str]:
    """Issue (access_token, refresh_token) and pair them so refresh can rotate the access."""
    access_plain, access_rec = _new_bearer(
        cosmos, kind="access", client_id=client_id, account_id=account_id, scope=scope
    )
    refresh_plain, _ = _new_bearer(
        cosmos,
        kind="refresh",
        client_id=client_id,
        account_id=account_id,
        scope=scope,
        paired_access_hash=access_rec.token_hash,
    )
    return access_plain, refresh_plain


def revoke(cosmos: CosmosStoreLike, token_hash: str) -> None:
    doc = cosmos.get(OAUTH_TOKENS, token_hash)
    if doc is None:
        return
    rec = BearerToken.model_validate(doc)
    rec.revoked = True
    cosmos.upsert(OAUTH_TOKENS, rec.model_dump(mode="json"))


def validate_bearer(cosmos: CosmosStoreLike, plaintext: str) -> Account | None:
    """Return the active Account for a bearer access token, or None if invalid/expired."""
    doc = cosmos.get(OAUTH_TOKENS, hash_token(plaintext))
    if doc is None:
        return None
    rec = BearerToken.model_validate(doc)
    if rec.revoked or rec.kind != "access" or _expired(rec.expires_at):
        return None
    acc_doc = cosmos.get(ACCOUNTS, rec.account_id)
    if acc_doc is None:
        return None
    account = Account.model_validate(acc_doc)
    return account if account.status == "active" else None


def consume_refresh(cosmos: CosmosStoreLike, refresh_plain: str) -> tuple[Account, str] | None:
    """Rotate: validate a refresh token, return (account, client_id), and revoke the
    paired access. Caller then mints a new pair. Returns None if refresh is invalid."""
    th = hash_token(refresh_plain)
    doc = cosmos.get(OAUTH_TOKENS, th)
    if doc is None:
        return None
    rec = BearerToken.model_validate(doc)
    if rec.revoked or rec.kind != "refresh" or _expired(rec.expires_at):
        return None
    acc_doc = cosmos.get(ACCOUNTS, rec.account_id)
    if acc_doc is None:
        return None
    account = Account.model_validate(acc_doc)
    if account.status != "active":
        return None
    if rec.paired_access_hash:  # rotate: kill the access this refresh last issued
        revoke(cosmos, rec.paired_access_hash)
    return account, rec.client_id
