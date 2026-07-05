"""HTTP handler for /api/try (guest invite redemption, Aufgabe 3)."""

from __future__ import annotations

from fbl_auth import create_invite, try_guest_request
from fbl_auth.email import NullEmailSender
from fbl_core.storage import InMemoryCosmosStore

_SENDER = NullEmailSender()


def _verify_url(token: str) -> str:
    return f"https://x.test/api/verify?token={token}"


def _try(cosmos: InMemoryCosmosStore, **payload: object) -> tuple[int, dict[str, str]]:
    return try_guest_request(
        dict(payload), "1.2.3.4", cosmos, email_sender=_SENDER, verify_url=_verify_url
    )


def test_valid_code_accepted() -> None:
    cosmos = InMemoryCosmosStore()
    inv = create_invite(cosmos)
    status, payload = _try(cosmos, email="t@example.test", code=inv.code, consent=True)
    assert status == 202 and payload["status"] == "pending"


def test_invalid_code_rejected() -> None:
    cosmos = InMemoryCosmosStore()
    status, payload = _try(cosmos, email="t@example.test", code="NOPE-0000", consent=True)
    assert status == 400 and payload["error"] == "invalid_code"


def test_missing_code_rejected() -> None:
    cosmos = InMemoryCosmosStore()
    status, payload = _try(cosmos, email="t@example.test", consent=True)
    assert status == 400 and payload["error"] == "missing_code"


def test_invalid_email_rejected() -> None:
    cosmos = InMemoryCosmosStore()
    inv = create_invite(cosmos)
    status, payload = _try(cosmos, email="nope", code=inv.code, consent=True)
    assert status == 400 and payload["error"] == "invalid_email"


def test_consent_required() -> None:
    cosmos = InMemoryCosmosStore()
    inv = create_invite(cosmos)
    status, payload = _try(cosmos, email="t@example.test", code=inv.code)
    assert status == 400 and payload["error"] == "consent_required"


def test_code_not_consumed_on_request_only_on_verify() -> None:
    # Requesting must NOT consume the code (only clicking the verify link does).
    cosmos = InMemoryCosmosStore()
    inv = create_invite(cosmos)
    _try(cosmos, email="t@example.test", code=inv.code, consent=True)
    from fbl_auth import get_invite

    assert get_invite(cosmos, inv.code).status == "unused"  # type: ignore[union-attr]
