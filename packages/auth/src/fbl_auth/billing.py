"""Stripe billing -> plan changes on accounts (pure; no Stripe SDK import).

The Stripe I/O — webhook signature verification and Checkout/Portal session creation —
lives in the ASGI wiring (``api/asgi.py``). This module takes an ALREADY-VERIFIED event
dict and mutates accounts, plus builds the parameter dicts the wiring hands to Stripe. That
keeps it free of the ``stripe`` dependency and trivially unit-testable with constructed
payloads (no secrets, no network).

Rules (owner decisions, see gtm/output/STRIPE_BUILD_PLAN.md):
- ``checkout.session.completed`` -> account plan ``pro`` (+ store Stripe customer/subscription).
- ``customer.subscription.deleted`` / ``invoice.payment_failed`` -> plan ``free`` IMMEDIATELY
  (no grace period).
- Idempotent: every handled event id is recorded in ``00_billing_events`` so Stripe's
  at-least-once delivery can't double-apply.
- Account match: by ``client_reference_id`` (= our account id, set when WE start the checkout,
  so the paying e-mail/card need not match the account), falling back to the Stripe customer
  e-mail; on the subscription lifecycle events, by the stored ``stripe_customer_id``.
"""

from __future__ import annotations

import logging
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any, Protocol

from fbl_core.lineage import now_utc_z
from fbl_core.storage import CosmosStoreLike

from .accounts import ACCOUNTS_CONTAINER, Account


class _CancellationMailer(Protocol):
    """The narrow email capability billing needs (the full EmailSender satisfies it).

    Kept separate from EmailSender so the goodbye mail is an optional, injected dependency
    and adding it never forces every EmailSender implementer/stub to grow a method.
    """

    def send_subscription_canceled(self, to: str, access_until: str) -> bool: ...


BILLING_EVENTS_CONTAINER = "00_billing_events"

CHECKOUT_COMPLETED = "checkout.session.completed"
SUBSCRIPTION_UPDATED = "customer.subscription.updated"
SUBSCRIPTION_DELETED = "customer.subscription.deleted"
PAYMENT_FAILED = "invoice.payment_failed"
HANDLED_EVENTS = frozenset(
    {CHECKOUT_COMPLETED, SUBSCRIPTION_UPDATED, SUBSCRIPTION_DELETED, PAYMENT_FAILED}
)

logger = logging.getLogger(__name__)


def _period_end_ts(sub: dict[str, Any]) -> int | None:
    """The unix timestamp the current paid/trial period ends (access-until).

    Robust across Stripe API versions: prefer the subscription's ``current_period_end``
    (or ``cancel_at`` / ``trial_end``); fall back to the first item's period end.
    """
    for key in ("current_period_end", "cancel_at", "trial_end"):
        v = sub.get(key)
        if isinstance(v, (int, float)) and v > 0:
            return int(v)
    items = (sub.get("items") or {}).get("data") or []
    if items:
        v = items[0].get("current_period_end")
        if isinstance(v, (int, float)) and v > 0:
            return int(v)
    return None


def _iso_from_ts(ts: int) -> str:
    """ISO-8601 Z instant (stored on the account as the informational plan_expires_at)."""
    return datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _display_from_ts(ts: int) -> str:
    """Human date for the goodbye email, e.g. ``31.07.2026`` (locale-free)."""
    return datetime.fromtimestamp(ts, tz=UTC).strftime("%d.%m.%Y")


# --- account lookup / persistence ----------------------------------------------


def _account_by_id(cosmos: CosmosStoreLike, account_id: str | None) -> Account | None:
    if not account_id:
        return None
    doc = cosmos.get(ACCOUNTS_CONTAINER, account_id)
    return Account.model_validate(doc) if doc else None


def _account_by_email(cosmos: CosmosStoreLike, email: str | None) -> Account | None:
    if not email:
        return None
    rows = list(cosmos.query_by_field(ACCOUNTS_CONTAINER, "email", email.strip().lower()))
    active = [r for r in rows if r.get("status") == "active"]
    chosen = active[0] if active else (rows[0] if rows else None)
    return Account.model_validate(chosen) if chosen else None


def _account_by_customer(cosmos: CosmosStoreLike, customer_id: str | None) -> Account | None:
    if not customer_id:
        return None
    rows = list(cosmos.query_by_field(ACCOUNTS_CONTAINER, "stripe_customer_id", customer_id))
    return Account.model_validate(rows[0]) if rows else None


def _save(cosmos: CosmosStoreLike, account: Account) -> Account:
    cosmos.upsert(ACCOUNTS_CONTAINER, account.model_dump(mode="json"))
    return account


# --- idempotency ---------------------------------------------------------------


def already_processed(cosmos: CosmosStoreLike, event_id: str | None) -> bool:
    if not event_id:
        return False
    return cosmos.get(BILLING_EVENTS_CONTAINER, event_id) is not None


def _mark_processed(
    cosmos: CosmosStoreLike, event_id: str, event_type: str, outcome: str, account_id: str | None
) -> None:
    cosmos.upsert(
        BILLING_EVENTS_CONTAINER,
        {
            "id": event_id,
            "event_type": event_type,
            "outcome": outcome,
            "account_id": account_id,
            "processed_at": now_utc_z(),
        },
    )


# --- event application ---------------------------------------------------------


def _checkout_email(session: dict[str, Any]) -> str | None:
    """The buyer e-mail from a checkout.session (top-level or nested customer_details)."""
    return session.get("customer_email") or (session.get("customer_details") or {}).get("email")


def apply_checkout_completed(cosmos: CosmosStoreLike, session: dict[str, Any]) -> Account | None:
    """A paid/trialing checkout completed -> set the matched account to ``pro``.

    Returns the upgraded account, or ``None`` if no account matched (recorded as ``unmatched``
    for owner reconciliation — rare, since our own checkout endpoint always sets
    ``client_reference_id``).
    """
    account = _account_by_id(cosmos, session.get("client_reference_id")) or _account_by_email(
        cosmos, _checkout_email(session)
    )
    if account is None:
        logger.warning(
            "billing: checkout.session.completed matched no account (customer=%s)",
            session.get("customer"),
        )
        return None
    account.tier = "pro"
    account.plan_expires_at = None
    customer = session.get("customer")
    if isinstance(customer, str):
        account.stripe_customer_id = customer
    subscription = session.get("subscription")
    if isinstance(subscription, str):
        account.stripe_subscription_id = subscription
    return _save(cosmos, account)


def apply_subscription_ended(cosmos: CosmosStoreLike, obj: dict[str, Any]) -> Account | None:
    """A subscription was canceled or a payment failed -> downgrade the account to ``free``.

    ``obj`` is a subscription (``…deleted``) or an invoice (``…payment_failed``); both carry
    ``customer``. Only a currently-``pro`` account is downgraded, so a legacy/guest account
    that happens to share a customer id is never affected.
    """
    account = _account_by_customer(cosmos, obj.get("customer"))
    if account is None or account.tier != "pro":
        return None
    account.tier = "free"
    account.plan_expires_at = None
    return _save(cosmos, account)


def apply_subscription_scheduled_cancel(
    cosmos: CosmosStoreLike, sub: dict[str, Any], *, email_sender: _CancellationMailer | None = None
) -> tuple[str, Account | None]:
    """Handle ``customer.subscription.updated``: a portal cancellation schedules the end for the
    period end (``cancel_at_period_end``) — the user keeps full Pro access until then, so we do
    NOT downgrade here. We record the end date on the account and send the goodbye email ONCE.

    Returns ``(outcome, account)``. ``.updated`` fires for many reasons; we act only on a
    newly-set / cleared cancellation and dedupe the email via the stored ``plan_expires_at``.
    """
    account = _account_by_customer(cosmos, sub.get("customer"))
    if account is None or account.tier != "pro":
        return "no_change", account

    if sub.get("cancel_at_period_end"):
        ts = _period_end_ts(sub)
        end_iso = _iso_from_ts(ts) if ts else None
        if account.plan_expires_at == end_iso:
            return "cancel_scheduled_dup", account  # already recorded + emailed this cancellation
        account.plan_expires_at = end_iso  # informational for a pro account (see effective_plan)
        _save(cosmos, account)
        if email_sender is not None and account.email and ts:
            with suppress(Exception):  # a mail failure must never fail the webhook
                email_sender.send_subscription_canceled(account.email, _display_from_ts(ts))
        return "cancel_scheduled", account

    # cancel_at_period_end is false: if we had a pending cancellation recorded, it was reversed.
    if account.plan_expires_at is not None:
        account.plan_expires_at = None
        _save(cosmos, account)
        return "cancel_reversed", account
    return "no_change", account


def handle_event(
    cosmos: CosmosStoreLike,
    event: dict[str, Any],
    *,
    email_sender: _CancellationMailer | None = None,
) -> dict[str, Any]:
    """Dispatch a verified Stripe event to the right handler, idempotently.

    Returns a small status dict for logging/response. Unhandled event types are ignored;
    duplicates (by event id, already in ``00_billing_events``) are a no-op. ``email_sender``
    (optional) sends the cancellation-confirmation goodbye email on a scheduled cancellation.
    """
    event_type = event.get("type", "")
    event_id = event.get("id")
    if event_type not in HANDLED_EVENTS:
        return {"status": "ignored", "type": event_type}
    if already_processed(cosmos, event_id):
        return {"status": "duplicate", "id": event_id, "type": event_type}

    obj = (event.get("data") or {}).get("object") or {}
    if event_type == CHECKOUT_COMPLETED:
        account = apply_checkout_completed(cosmos, obj)
        outcome = "upgraded" if account else "unmatched"
    elif event_type == SUBSCRIPTION_UPDATED:
        outcome, account = apply_subscription_scheduled_cancel(
            cosmos, obj, email_sender=email_sender
        )
    else:  # SUBSCRIPTION_DELETED (period end reached) or PAYMENT_FAILED → access truly ends
        account = apply_subscription_ended(cosmos, obj)
        outcome = "downgraded" if account else "no_change"

    account_id = account.id if account else None
    if event_id:
        _mark_processed(cosmos, event_id, event_type, outcome, account_id)
    return {"status": "ok", "type": event_type, "outcome": outcome, "account_id": account_id}


# --- Stripe request builders (pure; the wiring passes these to the Stripe SDK) --


def checkout_session_params(
    account: Account | None,
    *,
    price_id: str,
    success_url: str,
    cancel_url: str,
    trial_days: int,
    email: str | None = None,
) -> dict[str, Any]:
    """Kwargs for ``stripe.checkout.Session.create`` for a Pro subscription checkout.

    For an existing *account*, binds the purchase via ``client_reference_id`` so the webhook can
    match it regardless of which e-mail/card pays. For a **new buyer** (``account is None``) it
    is bound by the ``email`` Stripe collects — the account is created on payment (webhook), so
    no account/e-mail is ever produced without a real checkout. Reuses a known Stripe customer.
    """
    params: dict[str, Any] = {
        "mode": "subscription",
        "line_items": [{"price": price_id, "quantity": 1}],
        "success_url": success_url,
        "cancel_url": cancel_url,
        "allow_promotion_codes": True,
    }
    if trial_days > 0:
        params["subscription_data"] = {"trial_period_days": trial_days}
    if account is not None:
        params["client_reference_id"] = account.id
        if account.stripe_customer_id:
            params["customer"] = account.stripe_customer_id
        elif account.email:
            params["customer_email"] = account.email
    elif email:
        params["customer_email"] = email
    return params


def portal_session_params(account: Account, *, return_url: str) -> dict[str, Any] | None:
    """Kwargs for ``stripe.billing_portal.Session.create``, or ``None`` if the account has no
    Stripe customer yet (nothing to manage)."""
    if not account.stripe_customer_id:
        return None
    return {"customer": account.stripe_customer_id, "return_url": return_url}
