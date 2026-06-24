"""OAuth 2.1 data model + bearer-token validation (§8.10b phase 1)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fbl_auth import (
    OAUTH_TOKENS,
    consume_code,
    consume_refresh,
    get_client,
    hash_token,
    issue_code,
    issue_token_pair,
    register_client,
    revoke,
    signup,
    validate_bearer,
)
from fbl_core.storage import InMemoryCosmosStore


def _store_with_account() -> tuple[InMemoryCosmosStore, str]:
    cosmos = InMemoryCosmosStore()
    rec = signup("u@example.com", cosmos)
    return cosmos, rec.account.id


def test_register_client_is_public_pkce_only() -> None:
    cosmos, _ = _store_with_account()
    c = register_client(cosmos, client_name="Test Client", redirect_uris=["http://localhost:42/cb"])
    assert c.client_id and c.client_id == c.id  # opaque id, no separate secret
    assert c.token_endpoint_auth_method == "none"  # public client (PKCE)
    assert get_client(cosmos, c.client_id) is not None


def test_authorization_code_is_one_shot() -> None:
    cosmos, account_id = _store_with_account()
    c = register_client(cosmos, client_name="X", redirect_uris=["http://localhost/cb"])
    rec = issue_code(
        cosmos,
        client_id=c.client_id,
        account_id=account_id,
        redirect_uri="http://localhost/cb",
        code_challenge="abc123",
    )
    # First redemption succeeds
    redeemed = consume_code(cosmos, rec.code)
    assert redeemed is not None and redeemed.account_id == account_id
    # Second is rejected (one-shot)
    assert consume_code(cosmos, rec.code) is None


def test_expired_code_rejected() -> None:
    cosmos, account_id = _store_with_account()
    c = register_client(cosmos, client_name="X", redirect_uris=["http://localhost/cb"])
    rec = issue_code(
        cosmos,
        client_id=c.client_id,
        account_id=account_id,
        redirect_uri="http://localhost/cb",
        code_challenge="abc",
    )
    # Force expiry by rewriting the doc
    stale = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    doc = cosmos.get("00_oauth_codes", rec.code)
    assert doc is not None
    doc["expires_at"] = stale
    cosmos.upsert("00_oauth_codes", doc)
    assert consume_code(cosmos, rec.code) is None


def test_bearer_validate_returns_same_account_as_api_key_path() -> None:
    # The whole point of bearer auth: downstream rate-limit/metering is account-keyed,
    # so a bearer token resolves to the SAME Account as the X-API-Key would for the
    # same user. Then check_rate_limit/record_usage stay unchanged.
    cosmos, account_id = _store_with_account()
    c = register_client(cosmos, client_name="X", redirect_uris=["http://localhost/cb"])
    access, refresh = issue_token_pair(cosmos, client_id=c.client_id, account_id=account_id)
    assert access and refresh and access != refresh

    acc = validate_bearer(cosmos, access)
    assert acc is not None and acc.id == account_id


def test_bearer_validate_rejects_unknown_revoked_or_refresh() -> None:
    cosmos, account_id = _store_with_account()
    c = register_client(cosmos, client_name="X", redirect_uris=["http://localhost/cb"])
    access, refresh = issue_token_pair(cosmos, client_id=c.client_id, account_id=account_id)
    # Unknown token
    assert validate_bearer(cosmos, "totally-not-a-token") is None
    # Refresh tokens must NOT validate as access tokens (different `kind`)
    assert validate_bearer(cosmos, refresh) is None
    # Revoked access is rejected
    revoke(cosmos, hash_token(access))
    assert validate_bearer(cosmos, access) is None


def test_refresh_rotates_and_kills_paired_access() -> None:
    cosmos, account_id = _store_with_account()
    c = register_client(cosmos, client_name="X", redirect_uris=["http://localhost/cb"])
    access, refresh = issue_token_pair(cosmos, client_id=c.client_id, account_id=account_id)
    # Access works before rotation
    assert validate_bearer(cosmos, access) is not None
    # Consume the refresh -> the paired access must be revoked, refresh itself remains
    # usable conceptually but should be one-shot via /token's policy (next phase).
    rotated = consume_refresh(cosmos, refresh)
    assert rotated is not None
    account, client_id = rotated
    assert account.id == account_id and client_id == c.client_id
    # Old access is dead
    assert validate_bearer(cosmos, access) is None


def test_bearer_stored_as_hash_not_plaintext() -> None:
    # The plaintext token must NEVER appear in storage; only the sha256 hash, same
    # rule as the X-API-Key path.
    cosmos, account_id = _store_with_account()
    c = register_client(cosmos, client_name="X", redirect_uris=["http://localhost/cb"])
    access, _ = issue_token_pair(cosmos, client_id=c.client_id, account_id=account_id)
    for doc in cosmos.iter_all(OAUTH_TOKENS):
        assert access not in str(doc), "plaintext bearer token leaked into storage"
