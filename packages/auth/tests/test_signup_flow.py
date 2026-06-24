"""Double-opt-in signup → verify → key lifecycle + guards (Distribution §4–§6)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from fbl_auth import (
    ACCOUNTS_CONTAINER,
    check_ip_throttle,
    find_accounts_by_email,
    hash_token,
    is_plausible_email,
    regenerate,
    request_verification,
    unsubscribe,
    validate,
    verify,
)
from fbl_core.storage import InMemoryCosmosStore


class RecordingSender:
    """Captures every email the flow would send."""

    def __init__(self) -> None:
        self.verify: list[tuple[str, str]] = []
        self.key: list[tuple[str, str]] = []

    def send_verify(self, to: str, verify_url: str) -> bool:
        self.verify.append((to, verify_url))
        return True

    def send_key(self, to: str, api_key: str) -> bool:
        self.key.append((to, api_key))
        return True

    def send_oauth_login(self, to: str, login_url: str, client_name: str) -> bool:
        return True

    def send_token(self, to: str, token: str) -> bool:  # legacy, unused here
        return True


def _url(token: str) -> str:
    return f"https://agentic-firmenbuch.at/api/verify?token={token}"


def test_signup_then_verify_issues_a_validatable_key() -> None:
    cosmos, sender = InMemoryCosmosStore(), RecordingSender()
    vtoken = request_verification(
        "User@Firma.AT",
        cosmos,
        email_sender=sender,
        verify_url=_url,
        consent={"text_version": "v1", "at": "2026-06-18T00:00:00Z"},
        token="vtok",
    )
    # verify mail sent with the link; nothing stored in plaintext
    assert sender.verify == [("user@firma.at", _url("vtok"))]
    pending = cosmos.get(ACCOUNTS_CONTAINER, hash_token(vtoken))
    assert pending is not None and pending["status"] == "pending"
    assert "vtok" not in json.dumps(pending)  # only the hash is stored

    # a pending verify token can NOT be used as an API key
    assert validate(vtoken, cosmos) is None

    key = verify(vtoken, cosmos, email_sender=sender)
    assert key is not None
    assert sender.key == [("user@firma.at", key)]
    acct = validate(key, cosmos)
    assert acct is not None and acct.email == "user@firma.at" and acct.status == "active"
    # pending doc consumed (one-time)
    consumed = cosmos.get(ACCOUNTS_CONTAINER, hash_token(vtoken))
    assert consumed is not None and consumed["status"] == "consumed"


def test_verify_rejects_unknown_and_expired() -> None:
    cosmos, sender = InMemoryCosmosStore(), RecordingSender()
    assert verify("never-issued", cosmos, email_sender=sender) is None

    t0 = datetime(2026, 6, 18, 9, 0, 0, tzinfo=UTC)
    request_verification(
        "a@b.at", cosmos, email_sender=sender, verify_url=_url, ttl_hours=24, now=t0, token="t"
    )
    # 25h later → expired
    assert verify("t", cosmos, email_sender=sender, now=t0 + timedelta(hours=25)) is None


def test_regenerate_issues_new_key_and_revokes_old() -> None:
    cosmos, sender = InMemoryCosmosStore(), RecordingSender()
    request_verification("a@b.at", cosmos, email_sender=sender, verify_url=_url, token="t1")
    key1 = verify("t1", cosmos, email_sender=sender)
    assert key1 and validate(key1, cosmos) is not None

    regenerate("a@b.at", cosmos, email_sender=sender, verify_url=_url, token="t2")
    key2 = verify("t2", cosmos, email_sender=sender)
    assert key2 and key2 != key1
    assert validate(key2, cosmos) is not None  # new key works
    assert validate(key1, cosmos) is None  # old key revoked


def test_unsubscribe_revokes_key_and_blanks_email() -> None:
    cosmos, sender = InMemoryCosmosStore(), RecordingSender()
    request_verification("gone@b.at", cosmos, email_sender=sender, verify_url=_url, token="t")
    key = verify("t", cosmos, email_sender=sender)
    assert key and validate(key, cosmos) is not None

    affected = unsubscribe("gone@b.at", cosmos)
    assert affected >= 1
    assert validate(key, cosmos) is None  # key no longer valid
    assert find_accounts_by_email("gone@b.at", cosmos) == []  # email blanked → no PII left


def test_ip_throttle_blocks_after_limit_then_resets() -> None:
    cosmos = InMemoryCosmosStore()
    now = datetime(2026, 6, 18, 9, 0, 0, tzinfo=UTC)
    assert check_ip_throttle("1.2.3.4", cosmos, limit=2, now=now) is True
    assert check_ip_throttle("1.2.3.4", cosmos, limit=2, now=now) is True
    assert check_ip_throttle("1.2.3.4", cosmos, limit=2, now=now) is False  # over limit
    # a different minute resets
    assert check_ip_throttle("1.2.3.4", cosmos, limit=2, now=now + timedelta(minutes=1)) is True


def test_is_plausible_email_screens_garbage_and_disposable() -> None:
    assert is_plausible_email("a@firma.at")
    assert not is_plausible_email("not-an-email")
    assert not is_plausible_email("a@@b.at")
    assert not is_plausible_email("a@localhost")  # no dot in domain
    assert not is_plausible_email("x@mailinator.com")  # disposable
