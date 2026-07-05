"""Guest invite codes (Aufgabe 3) — full Pro access for a limited trial, no Stripe.

Personalized, single-use codes handed out (LinkedIn etc.) so someone can try the product.
Redeeming a code runs the SAME double-opt-in as a normal signup (see ``signup_flow``), but
issues a key on the ``guest`` plan with a ``plan_expires_at`` N days out; after that the
account automatically becomes ``free`` (resolved at request time by ``fbl_mcp_server.plans``).

Codes live in ``00_accounts`` (like pending signups / ip-throttle docs) keyed by
``invite:<CODE>`` so no new container is needed. This module is pure data/CRUD — the redeem
flow (email verification -> guest account) lives in ``signup_flow``.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, Field

from fbl_core.lineage import now_utc_z
from fbl_core.storage import CosmosStoreLike

from .accounts import ACCOUNTS_CONTAINER

INVITE_KIND = "invite_code"
_FMT = "%Y-%m-%dT%H:%M:%SZ"


def normalize_code(code: str) -> str:
    """Codes are case-insensitive and trimmed; stored/compared upper-case."""
    return (code or "").strip().upper()


def invite_id(code: str) -> str:
    return f"invite:{normalize_code(code)}"


def generate_code(prefix: str = "TRY") -> str:
    """A readable, hard-to-guess single-use code, e.g. ``TRY-9F3A2B7C``."""
    return f"{normalize_code(prefix)}-{secrets.token_hex(4).upper()}"


class InviteCode(BaseModel):
    """A single-use guest invite (Aufgabe 3). Keyed by ``invite:<CODE>``."""

    id: str  # == invite_id(code)
    token_hash: str  # == id (partition key /token_hash)
    kind: str = INVITE_KIND
    code: str  # normalized, human-readable
    label: str = ""  # who it's for (free text, e.g. "Max Mustermann / LinkedIn")
    guest_days: int = 14  # length of the Pro trial once redeemed
    status: str = "unused"  # unused | redeemed
    expires_at: str  # redeem-by instant (code validity), ISO-8601 Z
    redeemed_by_email: str | None = None
    redeemed_at: str | None = None
    created_at: str = Field(default_factory=now_utc_z)


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.strptime(ts, _FMT).replace(tzinfo=UTC)
    except ValueError:
        return None


def create_invite(
    cosmos: CosmosStoreLike,
    *,
    label: str = "",
    guest_days: int = 14,
    valid_days: int = 30,
    code: str | None = None,
    now: datetime | None = None,
) -> InviteCode:
    """Create + persist a new invite code (default: valid 30 days, 14-day guest trial)."""
    now = now or datetime.now(UTC)
    normalized = normalize_code(code) if code else generate_code()
    expires = (now + timedelta(days=valid_days)).strftime(_FMT)
    invite = InviteCode(
        id=invite_id(normalized),
        token_hash=invite_id(normalized),
        code=normalized,
        label=label,
        guest_days=guest_days,
        expires_at=expires,
    )
    cosmos.upsert(ACCOUNTS_CONTAINER, invite.model_dump(mode="json"))
    return invite


def get_invite(cosmos: CosmosStoreLike, code: str | None) -> InviteCode | None:
    if not code:
        return None
    raw = cosmos.get(ACCOUNTS_CONTAINER, invite_id(code))
    if raw is None or raw.get("kind") != INVITE_KIND:
        return None
    return InviteCode.model_validate(raw)


def invite_is_valid(invite: InviteCode, now: datetime | None = None) -> bool:
    """True if the code is unused and not past its redeem-by date."""
    if invite.status != "unused":
        return False
    expires = _parse(invite.expires_at)
    return expires is not None and expires > (now or datetime.now(UTC))


def mark_redeemed(
    cosmos: CosmosStoreLike, invite: InviteCode, email: str, now: datetime | None = None
) -> InviteCode:
    """Bind the code to the redeeming email and persist (single-use)."""
    now = now or datetime.now(UTC)
    invite.status = "redeemed"
    invite.redeemed_by_email = email.strip().lower()
    invite.redeemed_at = now.strftime(_FMT)
    cosmos.upsert(ACCOUNTS_CONTAINER, invite.model_dump(mode="json"))
    return invite
