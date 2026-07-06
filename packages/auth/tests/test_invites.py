"""Guest invite codes: creation, validity, and the redeem -> guest-plan flow (Aufgabe 3)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fbl_auth import (
    create_invite,
    get_invite,
    invite_is_valid,
    request_guest_access,
    request_verification,
    verify,
)
from fbl_auth.accounts import validate
from fbl_auth.email import NullEmailSender
from fbl_auth.invites import generate_code, normalize_code
from fbl_core.storage import InMemoryCosmosStore

_SENDER = NullEmailSender()


def _verify_url(token: str) -> str:
    return f"https://x.test/api/verify?token={token}"


def test_generate_and_normalize_code() -> None:
    code = generate_code("try")
    assert code.startswith("TRY-") and code == normalize_code(code)
    assert normalize_code("  try-abc ") == "TRY-ABC"


def test_create_and_fetch_invite() -> None:
    cosmos = InMemoryCosmosStore()
    inv = create_invite(cosmos, label="Max / LinkedIn", guest_days=14, valid_days=30)
    assert inv.status == "unused" and inv.guest_days == 14
    fetched = get_invite(cosmos, inv.code.lower())  # case-insensitive lookup
    assert fetched is not None and fetched.code == inv.code and fetched.label == "Max / LinkedIn"


def test_invite_validity_rules() -> None:
    cosmos = InMemoryCosmosStore()
    now = datetime(2026, 7, 5, tzinfo=UTC)
    inv = create_invite(cosmos, valid_days=10, now=now)
    assert invite_is_valid(inv, now)
    assert not invite_is_valid(inv, now + timedelta(days=11))  # expired
    inv.status = "redeemed"
    assert not invite_is_valid(inv, now)  # already used


def test_redeem_invite_grants_guest_plan_with_expiry() -> None:
    cosmos = InMemoryCosmosStore()
    now = datetime(2026, 7, 5, 12, 0, 0, tzinfo=UTC)
    inv = create_invite(cosmos, guest_days=14, now=now)

    token = request_guest_access(
        "tester@example.test",
        inv.code,
        cosmos,
        email_sender=_SENDER,
        verify_url=_verify_url,
        now=now,
    )
    assert token is not None
    key = verify(token, cosmos, email_sender=_SENDER, now=now)
    assert key is not None

    account = validate(key, cosmos)
    assert account is not None
    assert account.tier == "guest"
    assert account.plan_expires_at == "2026-07-19T12:00:00Z"  # now + 14 days
    # code is now single-use
    assert get_invite(cosmos, inv.code).status == "redeemed"  # type: ignore[union-attr]


def test_redeem_invalid_code_returns_none() -> None:
    cosmos = InMemoryCosmosStore()
    assert (
        request_guest_access(
            "t@example.test", "NOPE-0000", cosmos, email_sender=_SENDER, verify_url=_verify_url
        )
        is None
    )


def test_expired_code_cannot_be_redeemed() -> None:
    cosmos = InMemoryCosmosStore()
    now = datetime(2026, 7, 5, tzinfo=UTC)
    inv = create_invite(cosmos, valid_days=5, now=now)
    later = now + timedelta(days=6)
    assert (
        request_guest_access(
            "t@example.test",
            inv.code,
            cosmos,
            email_sender=_SENDER,
            verify_url=_verify_url,
            now=later,
        )
        is None
    )


def test_used_code_second_redeem_falls_back_to_free() -> None:
    # If a code is consumed between request and click, verify must NOT grant guest again.
    cosmos = InMemoryCosmosStore()
    now = datetime(2026, 7, 5, tzinfo=UTC)
    inv = create_invite(cosmos, now=now)
    t1 = request_guest_access(
        "first@example.test",
        inv.code,
        cosmos,
        email_sender=_SENDER,
        verify_url=_verify_url,
        now=now,
    )
    assert verify(t1, cosmos, email_sender=_SENDER, now=now) is not None  # type: ignore[arg-type]
    # a second pending signup that still carries the (now redeemed) code
    t2 = request_verification(
        "second@example.test",
        cosmos,
        email_sender=_SENDER,
        verify_url=_verify_url,
        consent={"invite_code": inv.code},
        now=now,
    )
    key2 = verify(t2, cosmos, email_sender=_SENDER, now=now)
    assert key2 is not None
    acct2 = validate(key2, cosmos)
    assert acct2 is not None and acct2.tier == "free"
