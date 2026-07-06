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
from typing import Any

from fbl_core.lineage import now_utc_z
from fbl_core.storage import CosmosStoreLike

from .accounts import ACCOUNTS_CONTAINER, Account

BILLING_EVENTS_CONTAINER = "00_billing_events"

CHECKOUT_COMPLETED = "checkout.session.completed"
SUBSCRIPTION_DELETED = "customer.subscription.deleted"
PAYMENT_FAILED = "invoice.payment_failed"
HANDLED_EVENTS = frozenset({CHECKOUT_COMPLETED, SUBSCRIPTION_DELETED, PAYMENT_FAILED})

logger = logging.getLogger(__name__)


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


def handle_event(cosmos: CosmosStoreLike, event: dict[str, Any]) -> dict[str, Any]:
    """Dispatch a verified Stripe event to the right handler, idempotently.

    Returns a small status dict for logging/response. Unhandled event types are ignored;
    duplicates (by event id, already in ``00_billing_events``) are a no-op.
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
    else:
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
