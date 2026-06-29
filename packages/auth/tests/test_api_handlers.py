"""HTTP-decision tests for the signup Functions handlers (throttle, validation, Turnstile)."""

from __future__ import annotations

from fbl_auth import (
    regenerate_request,
    signup_request,
    unsubscribe_request,
    validate,
    verify_request,
)
from fbl_core.storage import InMemoryCosmosStore


class Sender:
    def __init__(self) -> None:
        self.verify: list[str] = []
        self.key: list[str] = []

    def send_verify(self, to: str, verify_url: str) -> bool:
        self.verify.append(to)
        return True

    def send_key(self, to: str, api_key: str) -> bool:
        self.key.append(to)
        return True

    def send_oauth_login(self, to: str, login_url: str, client_name: str) -> bool:
        return True

    def send_token(self, to: str, token: str) -> bool:
        return True

    def send_alert(self, to: str, subject: str, text: str) -> bool:
        return True


def _url(t: str) -> str:
    return f"https://x.at/api/verify?token={t}"


def test_signup_happy_path_returns_202_and_sends_verify() -> None:
    cosmos, sender = InMemoryCosmosStore(), Sender()
    status, body = signup_request(
        {"email": "a@b.at", "consent": True},
        "1.1.1.1",
        cosmos,
        email_sender=sender,
        verify_url=_url,
    )
    assert status == 202 and body["status"] == "pending"
    assert sender.verify == ["a@b.at"]


def test_signup_rejects_bad_email_and_missing_consent() -> None:
    cosmos, sender = InMemoryCosmosStore(), Sender()
    s1, _ = signup_request(
        {"email": "nope", "consent": True}, "1.1.1.1", cosmos, email_sender=sender, verify_url=_url
    )
    s2, _ = signup_request(
        {"email": "a@b.at", "consent": False},
        "1.1.1.1",
        cosmos,
        email_sender=sender,
        verify_url=_url,
    )
    assert s1 == 400 and s2 == 400
    assert sender.verify == []  # nothing sent


def test_signup_turnstile_gate_blocks_when_secret_set() -> None:
    cosmos, sender = InMemoryCosmosStore(), Sender()
    # secret configured + verifier returns False → 400
    status, body = signup_request(
        {"email": "a@b.at", "consent": True, "turnstile_token": "x"},
        "1.1.1.1",
        cosmos,
        email_sender=sender,
        verify_url=_url,
        turnstile_secret="sek",
        turnstile_verifier=lambda tok, ip: False,
    )
    assert status == 400 and body["error"] == "turnstile_failed"
    # passing verifier → 202
    status2, _ = signup_request(
        {"email": "a@b.at", "consent": True, "turnstile_token": "x"},
        "2.2.2.2",
        cosmos,
        email_sender=sender,
        verify_url=_url,
        turnstile_secret="sek",
        turnstile_verifier=lambda tok, ip: True,
    )
    assert status2 == 202


def test_signup_ip_throttle_returns_429() -> None:
    cosmos, sender = InMemoryCosmosStore(), Sender()
    ok = 0
    for _ in range(7):
        st, _ = signup_request(
            {"email": "a@b.at", "consent": True},
            "9.9.9.9",
            cosmos,
            email_sender=sender,
            verify_url=_url,
            ip_limit=5,
        )
        if st == 202:
            ok += 1
    assert ok == 5  # 6th/7th throttled


def test_verify_then_key_validates_end_to_end() -> None:
    cosmos, sender = InMemoryCosmosStore(), Sender()
    signup_request(
        {"email": "a@b.at", "consent": True},
        "1.1.1.1",
        cosmos,
        email_sender=sender,
        verify_url=_url,
    )
    # the verify token is the query param of the link the sender would have built — capture via flow
    # (here we re-derive it: request stored a pending doc; use regenerate to get a known token)
    from fbl_auth import request_verification

    tok = request_verification("a@b.at", cosmos, email_sender=sender, verify_url=_url, token="zz")
    status, body = verify_request(tok, cosmos, email_sender=sender)
    assert status == 200 and body["status"] == "verified"
    assert sender.key == ["a@b.at"]


def test_verify_bad_token_400() -> None:
    cosmos, sender = InMemoryCosmosStore(), Sender()
    assert verify_request("", cosmos, email_sender=sender)[0] == 400
    assert verify_request("bogus", cosmos, email_sender=sender)[0] == 400


def test_regenerate_and_unsubscribe_are_non_committal() -> None:
    cosmos, sender = InMemoryCosmosStore(), Sender()
    # regenerate for an unknown email still 202 (don't reveal existence)
    st, _ = regenerate_request(
        {"email": "ghost@b.at"}, "1.1.1.1", cosmos, email_sender=sender, verify_url=_url
    )
    assert st == 202
    # unsubscribe always 200
    st2, _ = unsubscribe_request({"email": "ghost@b.at"}, cosmos)
    assert st2 == 200


def test_unsubscribe_revokes_real_key() -> None:
    cosmos, sender = InMemoryCosmosStore(), Sender()
    from fbl_auth import request_verification, verify

    tok = request_verification("real@b.at", cosmos, email_sender=sender, verify_url=_url, token="k")
    key = verify(tok, cosmos, email_sender=sender)
    assert key and validate(key, cosmos) is not None
    unsubscribe_request({"email": "real@b.at"}, cosmos)
    assert validate(key, cosmos) is None
