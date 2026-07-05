"""Signup → token; validate; rate-limit; meter usage (§8.10).

Tokens are opaque and stored **hashed** (sha256) in ``00_accounts`` (partitioned by
``token_hash``). Tiers/quotas are config so a paid tier is a config change.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from fbl_core.config import Settings
from fbl_core.lineage import now_utc_z
from fbl_core.storage import CosmosStoreLike

from .email import EmailSender

ACCOUNTS_CONTAINER = "00_accounts"

logger = logging.getLogger(__name__)


def quota_for(tier: str, settings: Settings) -> tuple[int, int]:
    """Resolve ``(per_min, per_day)`` for a tier (§8.10).

    A tier listed in ``settings.tier_quotas`` uses its override; any other tier
    (including ``free``) falls back to the base ``rate_limit_per_min``/``per_day``.
    So enabling a paid tier for an account is a pure config change — no code edit.
    """
    override = settings.tier_quotas.get(tier)
    if override and len(override) == 2:
        return int(override[0]), int(override[1])
    return settings.rate_limit_per_min, settings.rate_limit_per_day


def hash_token(token: str) -> str:
    """Return ``sha256:<hex>`` of an opaque token (never store the plaintext)."""
    return f"sha256:{hashlib.sha256(token.encode('utf-8')).hexdigest()}"


class Usage(BaseModel):
    minute_window: str | None = None  # "YYYY-MM-DDTHH:MM"
    minute_count: int = 0
    day_window: str | None = None  # "YYYY-MM-DD"
    day_count: int = 0
    total: int = 0
    last_tool: str | None = None
    last_used_at: str | None = None


class Account(BaseModel):
    id: str  # == token_hash
    token_hash: str
    email: str
    # The plan in force. Values: free, pro, guest, legacy (grandfathered), enterprise.
    # Stored under the historical name ``tier`` (== plan) so no data migration is needed;
    # ``quota_for`` maps it to rate-limit quotas, ``plans`` maps it to feature gates.
    tier: str = "free"
    # For time-boxed plans (``guest``): ISO-8601 Z instant at which the plan reverts to
    # ``free``. ``None`` for open-ended plans (free/pro/legacy). Stripe never sets this;
    # the subscription lifecycle drives pro up/downgrades via the plan field instead.
    plan_expires_at: str | None = None
    # Stripe linkage (set by the billing webhook; None for free/guest/legacy accounts that
    # never bought). The customer id lets us open the portal and match subscription events
    # back to this account regardless of which e-mail/card paid.
    stripe_customer_id: str | None = None
    stripe_subscription_id: str | None = None
    status: str = "active"
    created_at: str = Field(default_factory=now_utc_z)
    usage: Usage = Field(default_factory=Usage)


class RateDecision(BaseModel):
    allowed: bool
    reason: str | None = None
    retry_after_sec: int | None = None


class TokenRecord(BaseModel):
    """Returned by signup: the plaintext token (shown once) + the account."""

    token: str
    account: Account


def issue_token() -> str:
    """Generate a new opaque URL-safe token."""
    return secrets.token_urlsafe(32)


def signup(
    email: str,
    cosmos: CosmosStoreLike,
    *,
    tier: str = "free",
    token: str | None = None,
    email_sender: EmailSender | None = None,
) -> TokenRecord:
    """Create an account and return the plaintext token (delivered by email via ACS).

    The token is stored only as a hash; the plaintext is returned once for delivery.
    When an ``email_sender`` is provided (the ACS sender in production), the token is
    emailed to ``email``; delivery failures are logged but never fail signup — the
    plaintext token is still returned for an alternate hand-off.
    """
    token = token or issue_token()
    token_hash = hash_token(token)
    account = Account(id=token_hash, token_hash=token_hash, email=email, tier=tier)
    cosmos.upsert(ACCOUNTS_CONTAINER, account.model_dump(mode="json"))
    if email_sender is not None:
        try:
            email_sender.send_token(email, token)
        except Exception:  # delivery is best-effort; the token is still returned
            logger.exception("token email delivery failed for %s", email)
    return TokenRecord(token=token, account=account)


def validate(token: str, cosmos: CosmosStoreLike) -> Account | None:
    """Return the active Account for a token, or None if unknown/inactive."""
    doc = cosmos.get(ACCOUNTS_CONTAINER, hash_token(token))
    if doc is None:
        return None
    account = Account.model_validate(doc)
    return account if account.status == "active" else None


def check_rate_limit(
    account: Account,
    *,
    per_min: int,
    per_day: int,
    now: datetime | None = None,
) -> RateDecision:
    """Per-minute + per-day limits (config-driven). Pure: does not mutate the account."""
    now = now or datetime.now(UTC)
    minute = now.strftime("%Y-%m-%dT%H:%M")
    day = now.strftime("%Y-%m-%d")
    u = account.usage
    if u.day_window == day and u.day_count >= per_day:
        return RateDecision(allowed=False, reason="rate_limited_day", retry_after_sec=3600)
    if u.minute_window == minute and u.minute_count >= per_min:
        return RateDecision(allowed=False, reason="rate_limited_minute", retry_after_sec=60)
    return RateDecision(allowed=True)


def record_usage(
    account: Account, tool: str, cosmos: CosmosStoreLike, *, now: datetime | None = None
) -> Account:
    """Increment the rolling counters and persist (call after a successful tool run)."""
    now = now or datetime.now(UTC)
    minute = now.strftime("%Y-%m-%dT%H:%M")
    day = now.strftime("%Y-%m-%d")
    u = account.usage
    u.minute_count = u.minute_count + 1 if u.minute_window == minute else 1
    u.minute_window = minute
    u.day_count = u.day_count + 1 if u.day_window == day else 1
    u.day_window = day
    u.total += 1
    u.last_tool = tool
    u.last_used_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    cosmos.upsert(ACCOUNTS_CONTAINER, account.model_dump(mode="json"))
    return account
