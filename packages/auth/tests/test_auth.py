"""Auth tests: tokens hashed, validate, rate limit, metering (§8.10 DoD)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from fbl_auth import (
    ACCOUNTS_CONTAINER,
    NullEmailSender,
    check_rate_limit,
    email_sender_from_settings,
    hash_token,
    quota_for,
    record_usage,
    signup,
    validate,
)
from fbl_auth.email import AcsEmailSender
from fbl_core.config import Settings
from fbl_core.storage import InMemoryCosmosStore


def test_signup_stores_token_hashed_only() -> None:
    cosmos = InMemoryCosmosStore()
    rec = signup("a@example.test", cosmos)
    assert rec.token  # plaintext returned once for email delivery
    stored = cosmos.get(ACCOUNTS_CONTAINER, hash_token(rec.token))
    assert stored is not None
    # plaintext token must never be stored
    assert rec.token not in json.dumps(stored)
    assert stored["token_hash"] == hash_token(rec.token)


def test_validate_roundtrip_and_unknown() -> None:
    cosmos = InMemoryCosmosStore()
    rec = signup("a@example.test", cosmos)
    acct = validate(rec.token, cosmos)
    assert acct is not None and acct.email == "a@example.test"
    assert validate("wrong-token", cosmos) is None


def test_validate_inactive_returns_none() -> None:
    cosmos = InMemoryCosmosStore()
    rec = signup("a@example.test", cosmos)
    acct = rec.account
    acct.status = "disabled"
    cosmos.upsert(ACCOUNTS_CONTAINER, acct.model_dump(mode="json"))
    assert validate(rec.token, cosmos) is None


def test_rate_limit_per_minute() -> None:
    cosmos = InMemoryCosmosStore()
    acct = signup("a@example.test", cosmos).account
    now = datetime(2026, 6, 16, 10, 0, 0, tzinfo=UTC)
    for _ in range(3):
        assert check_rate_limit(acct, per_min=3, per_day=100, now=now).allowed
        record_usage(acct, "search_companies", cosmos, now=now)
    decision = check_rate_limit(acct, per_min=3, per_day=100, now=now)
    assert decision.allowed is False
    assert decision.reason == "rate_limited_minute"
    # next minute resets
    later = datetime(2026, 6, 16, 10, 1, 0, tzinfo=UTC)
    assert check_rate_limit(acct, per_min=3, per_day=100, now=later).allowed


def test_rate_limit_per_day() -> None:
    cosmos = InMemoryCosmosStore()
    acct = signup("a@example.test", cosmos).account
    base = datetime(2026, 6, 16, 10, 0, 0, tzinfo=UTC)
    for i in range(5):
        minute = base.replace(minute=i)
        record_usage(acct, "x", cosmos, now=minute)
    decision = check_rate_limit(acct, per_min=100, per_day=5, now=base.replace(minute=6))
    assert decision.allowed is False and decision.reason == "rate_limited_day"


def test_quota_for_maps_tier_to_quota() -> None:
    # free falls back to the base limits; a configured paid tier uses its override (§8.10).
    settings = Settings(rate_limit_per_min=60, rate_limit_per_day=5000)
    assert quota_for("free", settings) == (60, 5000)
    assert quota_for("unknown", settings) == (60, 5000)
    assert quota_for("pro", settings) == (600, 100_000)
    assert quota_for("enterprise", settings) == (3_000, 1_000_000)
    # purely a config change — no code edit
    custom = Settings(tier_quotas={"vip": [1, 2]})
    assert quota_for("vip", custom) == (1, 2)


def test_signup_delivers_token_via_email_sender() -> None:
    cosmos = InMemoryCosmosStore()
    sent: list[tuple[str, str]] = []

    class RecordingSender:
        def send_token(self, to: str, token: str) -> bool:
            sent.append((to, token))
            return True

        def send_verify(self, to: str, verify_url: str) -> bool:
            return True

        def send_key(self, to: str, api_key: str) -> bool:
            return True

        def send_oauth_login(self, to: str, login_url: str, client_name: str) -> bool:
            return True

        def send_alert(self, to: str, subject: str, text: str) -> bool:
            return True

    rec = signup("a@example.test", cosmos, email_sender=RecordingSender())
    assert sent == [("a@example.test", rec.token)]


def test_signup_survives_email_delivery_failure() -> None:
    cosmos = InMemoryCosmosStore()

    class BoomSender:
        def send_token(self, to: str, token: str) -> bool:
            raise RuntimeError("ACS down")

        def send_verify(self, to: str, verify_url: str) -> bool:
            raise RuntimeError("ACS down")

        def send_key(self, to: str, api_key: str) -> bool:
            raise RuntimeError("ACS down")

        def send_oauth_login(self, to: str, login_url: str, client_name: str) -> bool:
            raise RuntimeError("ACS down")

        def send_alert(self, to: str, subject: str, text: str) -> bool:
            raise RuntimeError("ACS down")

    rec = signup("a@example.test", cosmos, email_sender=BoomSender())
    assert rec.token  # token still returned; account still stored
    assert validate(rec.token, cosmos) is not None


def test_email_sender_from_settings_selects_implementation() -> None:
    assert isinstance(email_sender_from_settings(Settings()), NullEmailSender)
    configured = Settings(
        acs_connection_string="endpoint=https://x;accesskey=y",
        acs_sender_address="DoNotReply@example.test",
    )
    assert isinstance(email_sender_from_settings(configured), AcsEmailSender)


def test_record_usage_persists_counters() -> None:
    cosmos = InMemoryCosmosStore()
    rec = signup("a@example.test", cosmos)
    now = datetime(2026, 6, 16, 10, 0, 0, tzinfo=UTC)
    record_usage(rec.account, "list_sectors", cosmos, now=now)
    reloaded = validate(rec.token, cosmos)
    assert reloaded is not None
    assert reloaded.usage.total == 1
    assert reloaded.usage.last_tool == "list_sectors"
