"""Double-opt-in signup → verify → API-key lifecycle (Distribution Spez §4–§6).

Pure, store-backed logic for the four signup Functions; the Azure Functions layer
(``api/asgi.py``) only does HTTP, Turnstile and wiring. Everything lives in
``00_accounts`` (partitioned by ``token_hash``):

* **pending** doc — keyed by ``hash_token(verify_token)``; created on signup, emailed a
  verify link, expires after ``ttl_hours``. Status ``pending`` → can't be used as an API key
  (``validate`` only returns ``active`` accounts).
* **active** doc — the existing :class:`~fbl_auth.accounts.Account`, keyed by
  ``hash_token(api_key)`` with ``status="active"`` — so the already-built MCP ``validate()``
  keeps working unchanged. Issued on verify; a regenerate revokes the previous one.

There is no hard delete in :class:`CosmosStoreLike`, so consumed/old/removed docs are marked
(``consumed`` / ``revoked`` / ``deleted``) rather than deleted; unsubscribe also blanks the
email so no PII remains (hard-delete is a backlog item once the store grows a ``delete``).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, Field

from fbl_core.lineage import now_utc_z
from fbl_core.storage import CosmosStoreLike

from .accounts import ACCOUNTS_CONTAINER, Account, hash_token, issue_token
from .email import EmailSender
from .invites import get_invite, invite_is_valid, mark_redeemed
from .metrics import bump_metric

_FMT = "%Y-%m-%dT%H:%M:%SZ"

# Block obvious throwaway-inbox domains (double-opt-in already blocks most bots; this trims noise).
DISPOSABLE_DOMAINS = frozenset(
    {
        "mailinator.com",
        "guerrillamail.com",
        "10minutemail.com",
        "tempmail.com",
        "trashmail.com",
        "yopmail.com",
        "getnada.com",
        "sharklasers.com",
        "dispostable.com",
        "maildrop.cc",
        "fakeinbox.com",
        "throwawaymail.com",
    }
)


class PendingSignup(BaseModel):
    """A pending double-opt-in signup (Distribution §4). Keyed by the verify-token hash."""

    id: str  # == token_hash == hash_token(verify_token)
    token_hash: str  # partition key (/token_hash)
    kind: str = "pending_signup"
    email: str
    status: str = "pending"  # pending | consumed
    verify_token_hash: str
    verify_expires_at: str
    consent: dict[str, str] = Field(default_factory=dict)
    created_at: str = Field(default_factory=now_utc_z)
    # Cosmos per-item TTL (seconds): auto-purge consumed/expired pending docs after 30 days (M6,
    # GDPR data-minimization + container hygiene). Ignored until the 00_accounts container has
    # DefaultTimeToLive enabled (bicep) — harmless to carry until then.
    ttl: int = 30 * 24 * 3600


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.strptime(ts, _FMT).replace(tzinfo=UTC)
    except ValueError:
        return None


def is_plausible_email(email: str) -> bool:
    """Cheap sanity + disposable-domain screen (full validation is the double-opt-in itself)."""
    email = (email or "").strip().lower()
    if email.count("@") != 1:
        return False
    local, _, domain = email.partition("@")
    if not local or "." not in domain or len(email) > 254:
        return False
    return domain not in DISPOSABLE_DOMAINS


def find_accounts_by_email(email: str, cosmos: CosmosStoreLike) -> list[dict[str, object]]:
    """All ``00_accounts`` docs for an email (active + pending). Used by regenerate/unsubscribe."""
    return list(cosmos.query_by_field(ACCOUNTS_CONTAINER, "email", email.strip().lower()))


def request_verification(
    email: str,
    cosmos: CosmosStoreLike,
    *,
    email_sender: EmailSender,
    verify_url: Callable[[str], str],
    consent: dict[str, str] | None = None,
    ttl_hours: int = 24,
    now: datetime | None = None,
    token: str | None = None,
) -> str:
    """Create/refresh a pending signup and email the verify link. Returns the verify token.

    Idempotent per email at the doc level: a new verify token supersedes any prior pending
    one (the old pending doc simply expires unused). Used for both signup and regenerate.
    """
    now = now or datetime.now(UTC)
    token = token or issue_token()
    vth = hash_token(token)
    expires = (now + timedelta(hours=ttl_hours)).strftime(_FMT)
    doc = PendingSignup(
        id=vth,
        token_hash=vth,
        email=email.strip().lower(),
        verify_token_hash=vth,
        verify_expires_at=expires,
        consent=consent or {},
    )
    cosmos.upsert(ACCOUNTS_CONTAINER, doc.model_dump(mode="json"))
    email_sender.send_verify(doc.email, verify_url(token))
    return token


def verify(
    token: str,
    cosmos: CosmosStoreLike,
    *,
    email_sender: EmailSender,
    now: datetime | None = None,
    api_key: str | None = None,
) -> str | None:
    """Consume a verify token → issue + email an API key. Returns the key, or None if invalid.

    Revokes any previously-active key for the same email (so regenerate truly invalidates the old).
    """
    now = now or datetime.now(UTC)
    raw = cosmos.get(ACCOUNTS_CONTAINER, hash_token(token))
    if raw is None or raw.get("kind") != "pending_signup" or raw.get("status") != "pending":
        return None
    pending = PendingSignup.model_validate(raw)
    expires = _parse(pending.verify_expires_at)
    if expires is None or expires <= now:
        return None

    # Revoke prior active keys for this email, CARRYING their billing state (C1). A paying
    # customer who loses their key and uses "Neuen Key anfordern" (or re-signs-up) must keep
    # Pro + the Stripe linkage — otherwise they silently drop to free, keep being billed, and
    # the orphaned (revoked) doc keeps the customer id so future subscription webhooks resolve
    # to the wrong account and cancellations never reach the live one.
    def _s(value: object) -> str | None:
        return value if isinstance(value, str) else None

    carried_tier: str | None = None
    carried_expiry: str | None = None
    carried_cust: str | None = None
    carried_sub: str | None = None
    for other in find_accounts_by_email(pending.email, cosmos):
        if other.get("status") == "active" and not other.get("kind"):
            if other.get("tier") in ("pro", "legacy", "enterprise", "guest"):
                carried_tier = _s(other.get("tier"))
                carried_expiry = _s(other.get("plan_expires_at"))
            carried_cust = _s(other.get("stripe_customer_id")) or carried_cust
            carried_sub = _s(other.get("stripe_subscription_id")) or carried_sub
            other["status"] = "revoked"
            cosmos.upsert(ACCOUNTS_CONTAINER, other)

    # Guest invite (Aufgabe 3): if this signup carried a still-valid invite code, issue the key
    # on the time-boxed ``guest`` plan (full Pro for N days, then auto-free) instead of ``free``.
    tier = "free"
    plan_expires_at: str | None = None
    invite = get_invite(cosmos, pending.consent.get("invite_code"))
    granted_by_invite = False
    if invite is not None and invite_is_valid(invite, now):
        granted_by_invite = True
        tier = "guest"
        plan_expires_at = (now + timedelta(days=invite.guest_days)).strftime(_FMT)

    # A carried PAID/existing plan always wins over the free/guest default (never demote a payer).
    if carried_tier is not None:
        tier = carried_tier
        plan_expires_at = carried_expiry

    api_key = api_key or issue_token()
    akh = hash_token(api_key)
    account = Account(
        id=akh,
        token_hash=akh,
        email=pending.email,
        tier=tier,
        plan_expires_at=plan_expires_at,
        status="active",
        stripe_customer_id=carried_cust,
        stripe_subscription_id=carried_sub,
    )
    cosmos.upsert(ACCOUNTS_CONTAINER, account.model_dump(mode="json"))

    pending.status = "consumed"  # one-time use (no hard delete in the store)
    cosmos.upsert(ACCOUNTS_CONTAINER, pending.model_dump(mode="json"))
    # Only consume the invite if it actually granted guest (a carried paid plan didn't win).
    if granted_by_invite and invite is not None and tier == "guest" and carried_tier is None:
        mark_redeemed(cosmos, invite, pending.email, now=now)  # single-use

    email_sender.send_key(pending.email, api_key)
    bump_metric(cosmos, "signups_verified", now=now)  # privacy-friendly daily counter
    return api_key


def regenerate(
    email: str,
    cosmos: CosmosStoreLike,
    *,
    email_sender: EmailSender,
    verify_url: Callable[[str], str],
    ttl_hours: int = 24,
    now: datetime | None = None,
    token: str | None = None,
) -> str:
    """Re-send a verify link; on verify a new key is issued and the old one revoked."""
    return request_verification(
        email,
        cosmos,
        email_sender=email_sender,
        verify_url=verify_url,
        consent={"regenerate": "true"},
        ttl_hours=ttl_hours,
        now=now,
        token=token,
    )


def request_guest_access(
    email: str,
    code: str,
    cosmos: CosmosStoreLike,
    *,
    email_sender: EmailSender,
    verify_url: Callable[[str], str],
    now: datetime | None = None,
    token: str | None = None,
) -> str | None:
    """Redeem a guest invite code (Aufgabe 3): validate the code, then send the normal
    double-opt-in verify link carrying the code. On verify the key is issued on the ``guest``
    plan. Returns the verify token, or ``None`` if the code is invalid/expired/already used.

    The code is re-checked at verify time too, so a code consumed between request and click
    still can't grant guest (it falls back to a normal free key).
    """
    invite = get_invite(cosmos, code)
    if invite is None or not invite_is_valid(invite, now):
        return None
    return request_verification(
        email,
        cosmos,
        email_sender=email_sender,
        verify_url=verify_url,
        consent={"invite_code": invite.code},
        now=now,
        token=token,
    )


def unsubscribe(email: str, cosmos: CosmosStoreLike) -> int:
    """Revoke the key + remove PII for an email (right to deletion). Returns docs affected."""
    n = 0
    for doc in find_accounts_by_email(email, cosmos):
        doc["status"] = "deleted"
        doc["email"] = ""  # blank the only PII; hard delete is a backlog item
        cosmos.upsert(ACCOUNTS_CONTAINER, doc)
        n += 1
    return n


def check_ip_throttle(
    ip: str, cosmos: CosmosStoreLike, *, limit: int = 5, now: datetime | None = None
) -> bool:
    """Best-effort per-IP-per-minute throttle for /api/signup. True if allowed, else False."""
    now = now or datetime.now(UTC)
    minute = now.strftime("%Y-%m-%dT%H:%M")
    key = hash_token(f"ipthrottle:{ip}:{minute}")
    doc = cosmos.get(ACCOUNTS_CONTAINER, key) or {
        "id": key,
        "token_hash": key,
        "kind": "ip_throttle",
        "ip": ip,
        "minute": minute,
        "count": 0,
        "ttl": 24 * 3600,  # M6: purge throttle counters after a day (Cosmos per-item TTL)
    }
    if int(doc.get("count", 0)) >= limit:
        return False
    doc["count"] = int(doc.get("count", 0)) + 1
    cosmos.upsert(ACCOUNTS_CONTAINER, doc)
    return True
