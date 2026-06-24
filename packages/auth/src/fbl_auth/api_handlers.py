"""Pure request handlers for the four signup Functions (Distribution §4–§6).

Each returns ``(http_status, json_payload)`` from plain inputs (parsed body, client IP,
a Cosmos store, an email sender, a Turnstile verifier) so the HTTP decisions — throttle,
validation, Turnstile gate — are unit-tested here, and the Azure Functions layer
(``api/function_app.py``) only translates ``HttpRequest`` ↔ these calls.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from fbl_core.storage import CosmosStoreLike

from .email import EmailSender
from .signup_flow import (
    check_ip_throttle,
    is_plausible_email,
    regenerate,
    request_verification,
    unsubscribe,
    verify,
)

# (token, remote_ip) -> True iff Turnstile accepts the challenge.
TurnstileVerifier = Callable[[str, str | None], bool]

_PENDING_MSG = "Bitte bestätige deine E-Mail-Adresse über den Link, den wir dir geschickt haben."


def _turnstile_ok(
    token: str, ip: str | None, secret: str | None, verifier: TurnstileVerifier | None
) -> bool:
    """No secret configured (local/dev) → skip the gate; otherwise the verifier must pass."""
    if not secret:
        return True
    if verifier is None:
        return False
    return verifier(token or "", ip)


def signup_request(
    payload: dict[str, Any],
    ip: str | None,
    cosmos: CosmosStoreLike,
    *,
    email_sender: EmailSender,
    verify_url: Callable[[str], str],
    turnstile_secret: str | None = None,
    turnstile_verifier: TurnstileVerifier | None = None,
    ip_limit: int = 5,
    ttl_hours: int = 24,
    now: datetime | None = None,
) -> tuple[int, dict[str, str]]:
    """POST /api/signup → create a pending signup + send the verify mail (Distribution §4)."""
    if ip and not check_ip_throttle(ip, cosmos, limit=ip_limit, now=now):
        return 429, {"error": "rate_limited", "message": "Zu viele Anfragen. Bitte später erneut."}
    email = str(payload.get("email", "")).strip().lower()
    if not is_plausible_email(email):
        return 400, {
            "error": "invalid_email",
            "message": "Bitte gib eine gültige E-Mail-Adresse ein.",
        }
    if not payload.get("consent"):
        return 400, {
            "error": "consent_required",
            "message": "Bitte stimme der Datenschutzerklärung zu.",
        }
    if not _turnstile_ok(
        str(payload.get("turnstile_token", "")), ip, turnstile_secret, turnstile_verifier
    ):
        return 400, {"error": "turnstile_failed", "message": "Sicherheitsprüfung fehlgeschlagen."}

    consent = {
        "text_version": str(payload.get("consent_text_version", "v1")),
        "ip": ip or "",
    }
    request_verification(
        email,
        cosmos,
        email_sender=email_sender,
        verify_url=verify_url,
        consent=consent,
        ttl_hours=ttl_hours,
        now=now,
    )
    return 202, {"status": "pending", "message": _PENDING_MSG}


def verify_request(
    token: str,
    cosmos: CosmosStoreLike,
    *,
    email_sender: EmailSender,
    now: datetime | None = None,
) -> tuple[int, dict[str, str]]:
    """GET /api/verify?token=… → issue + email the API key (Distribution §4)."""
    if not token:
        return 400, {"error": "missing_token", "message": "Ungültiger Bestätigungslink."}
    key = verify(token, cosmos, email_sender=email_sender, now=now)
    if key is None:
        return 400, {"error": "invalid_or_expired", "message": "Link ungültig oder abgelaufen."}
    return 200, {"status": "verified", "message": "E-Mail bestätigt – dein API-Key ist unterwegs."}


def regenerate_request(
    payload: dict[str, Any],
    ip: str | None,
    cosmos: CosmosStoreLike,
    *,
    email_sender: EmailSender,
    verify_url: Callable[[str], str],
    ip_limit: int = 5,
    ttl_hours: int = 24,
    now: datetime | None = None,
) -> tuple[int, dict[str, str]]:
    """POST /api/regenerate → re-send a verify link; new key issued (old revoked) on verify (§5)."""
    if ip and not check_ip_throttle(ip, cosmos, limit=ip_limit, now=now):
        return 429, {"error": "rate_limited", "message": "Zu viele Anfragen. Bitte später erneut."}
    email = str(payload.get("email", "")).strip().lower()
    if not is_plausible_email(email):
        return 400, {
            "error": "invalid_email",
            "message": "Bitte gib eine gültige E-Mail-Adresse ein.",
        }
    regenerate(
        email,
        cosmos,
        email_sender=email_sender,
        verify_url=verify_url,
        ttl_hours=ttl_hours,
        now=now,
    )
    # Always 202 (don't reveal whether the email exists).
    return 202, {"status": "pending", "message": _PENDING_MSG}


def unsubscribe_request(
    payload: dict[str, Any], cosmos: CosmosStoreLike
) -> tuple[int, dict[str, str]]:
    """POST /api/unsubscribe → revoke key + remove PII for the email (right to deletion, §7)."""
    email = str(payload.get("email", "")).strip().lower()
    if not is_plausible_email(email):
        return 400, {
            "error": "invalid_email",
            "message": "Bitte gib eine gültige E-Mail-Adresse ein.",
        }
    unsubscribe(email, cosmos)
    # Always 200 (don't reveal whether the email existed).
    return 200, {"status": "removed", "message": "Dein Zugang wurde entfernt."}
