"""Privacy-friendly daily counters + the signup/playground hooks (Distribution §14.8)."""

from __future__ import annotations

from datetime import UTC, datetime

from fbl_auth import bump_metric, read_metric, request_verification, verify
from fbl_core.storage import InMemoryCosmosStore


class _Sender:
    def send_verify(self, to: str, verify_url: str) -> bool:
        return True

    def send_key(self, to: str, api_key: str) -> bool:
        return True

    def send_oauth_login(self, to: str, login_url: str, client_name: str) -> bool:
        return True

    def send_token(self, to: str, token: str) -> bool:
        return True


def test_bump_and_read_metric_is_daily() -> None:
    cosmos = InMemoryCosmosStore()
    d1 = datetime(2026, 6, 18, 9, 0, 0, tzinfo=UTC)
    d2 = datetime(2026, 6, 19, 9, 0, 0, tzinfo=UTC)
    bump_metric(cosmos, "x", now=d1)
    bump_metric(cosmos, "x", now=d1)
    bump_metric(cosmos, "x", now=d2)
    assert read_metric(cosmos, "x", "2026-06-18") == 2
    assert read_metric(cosmos, "x", "2026-06-19") == 1
    assert read_metric(cosmos, "x", "2026-06-20") == 0


def test_verify_increments_signups_metric() -> None:
    cosmos, sender = InMemoryCosmosStore(), _Sender()
    now = datetime(2026, 6, 18, 9, 0, 0, tzinfo=UTC)
    request_verification(
        "a@b.at", cosmos, email_sender=sender, verify_url=lambda t: t, token="t", now=now
    )
    verify("t", cosmos, email_sender=sender, now=now)
    assert read_metric(cosmos, "signups_verified", "2026-06-18") == 1
