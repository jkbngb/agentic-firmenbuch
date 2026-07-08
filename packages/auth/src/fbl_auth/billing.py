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

from .accounts import ACCOUNTS_CONTAINER, Account, account_by_email


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
    # Never resolve a pending_signup/ip_throttle/invite doc (they carry a `kind`) as an account,
    # even if a stale client_reference_id points at one (C2).
    if doc is None or doc.get("kind"):
        return None
    return Account.model_validate(doc)


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
    account = _account_by_id(cosmos, session.get("client_reference_id")) or account_by_email(
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
    """``customer.subscription.deleted`` — the definitive end of a subscription → downgrade the
    account to ``free``.

    ``obj`` is the deleted subscription (carries ``customer``). Only a currently-``pro`` account
    is downgraded, so a legacy/guest account that happens to share a customer id is never
    affected. (``invoice.payment_failed`` no longer routes here — see C3 in ``handle_event``.)
    """
    account = _account_by_customer(cosmos, obj.get("customer"))
    if account is None or account.tier != "pro":
        return None
    account.tier = "free"
    account.plan_expires_at = None
    return _save(cosmos, account)


# Subscription statuses that keep access, and the terminal ones that end it (C3). We drive the
# plan from the subscription's *status*, not from payment events, so Stripe Smart Retries / a
# card update in the portal transparently restore Pro, and ``past_due`` is a grace period while
# dunning runs (the definitive end is ``subscription.deleted``).
_ALIVE_STATUSES = frozenset({"active", "trialing", "past_due"})
_DEAD_STATUSES = frozenset({"unpaid", "canceled", "incomplete_expired"})


def apply_subscription_updated(
    cosmos: CosmosStoreLike, sub: dict[str, Any], *, email_sender: _CancellationMailer | None = None
) -> tuple[str, Account | None]:
    """Handle ``customer.subscription.updated`` — the load-bearing lifecycle event (C3).

    Drives the account plan from the subscription ``status`` (recovery restores Pro, terminal
    states end it, ``past_due`` keeps Pro during dunning), and separately keeps the scheduled-
    cancellation bookkeeping (``cancel_at_period_end`` → record end date + one-shot goodbye mail).
    Returns ``(outcome, account)``.
    """
    account = _account_by_customer(cosmos, sub.get("customer"))
    if account is None:
        return "no_change", None
    status = sub.get("status")

    # Terminal: dunning exhausted / canceled / never-completed → access ends now.
    if status in _DEAD_STATUSES:
        if account.tier == "pro":
            account.tier = "free"
            account.plan_expires_at = None
            _save(cosmos, account)
            return "downgraded", account
        return "no_change", account

    # Alive: restore Pro if a prior failure/missed-event left a Stripe customer on ``free``
    # (self-heals; never clobbers legacy/guest/enterprise). ``past_due`` stays Pro = grace.
    if status in _ALIVE_STATUSES and account.tier == "free":
        account.tier = "pro"
        account.plan_expires_at = None
        _save(cosmos, account)

    if account.tier != "pro":
        return "no_change", account

    # Scheduled-cancellation bookkeeping + one-shot goodbye mail (deduped via plan_expires_at).
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
        outcome, account = apply_subscription_updated(cosmos, obj, email_sender=email_sender)
    elif event_type == SUBSCRIPTION_DELETED:  # the definitive end of access
        account = apply_subscription_ended(cosmos, obj)
        outcome = "downgraded" if account else "no_change"
    else:  # PAYMENT_FAILED — do NOT downgrade (C3). Stripe Smart Retries + subscription.updated
        # drive the plan, so a one-day card hiccup recovers without the customer losing access;
        # the terminal end always arrives as subscription.updated(unpaid) / subscription.deleted.
        account = _account_by_customer(cosmos, obj.get("customer"))
        outcome = "payment_failed_noted"

    account_id = account.id if account else None
    if event_id:
        _mark_processed(cosmos, event_id, event_type, outcome, account_id)
    return {"status": "ok", "type": event_type, "outcome": outcome, "account_id": account_id}


# --- Stripe request builders (pure; the wiring passes these to the Stripe SDK) --


PROMO_METADATA_KEY = "promo"
PROMO_CODE = "GRATIS3M"  # the live promotion code (100 % off, 3 monthly invoices)


def checkout_session_params(
    account: Account | None,
    *,
    price_id: str,
    success_url: str,
    cancel_url: str,
    trial_days: int = 0,
    email: str | None = None,
    promo: bool = False,
) -> dict[str, Any]:
    """Kwargs for ``stripe.checkout.Session.create`` for a Pro subscription checkout.

    For an existing *account*, binds the purchase via ``client_reference_id`` so the webhook can
    match it regardless of which e-mail/card pays. For a **new buyer** (``account is None``) it
    is bound by the ``email`` Stripe collects — the account is created on payment (webhook), so
    no account/e-mail is ever produced without a real checkout. Reuses a known Stripe customer.

    Card collection is ``payment_method_collection="if_required"`` on EVERY checkout: a normal
    purchase (€79 due now) still collects a card, but when the buyer types a 100 %-off promo code
    into Stripe's field (``allow_promotion_codes``) the first invoice is €0 and NO card is asked
    for. After the 3 free invoices, invoice #4 is €79 with no card on file → ``past_due`` → Stripe
    dunning → cancel (C3): a promo user is never charged unless they add a card. This is why there
    is no default trial — a trial would make €0 due now and skip the card for *paying* users too;
    the promo code IS the free-trial path. ``promo=True`` only tags the subscription in
    ``metadata`` so promo subs stay queryable in Stripe.
    """
    params: dict[str, Any] = {
        "mode": "subscription",
        "line_items": [{"price": price_id, "quantity": 1}],
        "success_url": success_url,
        "cancel_url": cancel_url,
        "allow_promotion_codes": True,  # the buyer types the promo code into Stripe's field
        # Collect a card only when money is due NOW, so a 100 %-off code = no card entry at all.
        "payment_method_collection": "if_required",
        # The submit button label ("Zahlungspflichtig abonnieren") is Stripe-controlled and legally
        # mandated for a subscription that becomes chargeable — it cannot be changed. This is the
        # one piece of copy next to it that we CAN set, to reassure promo buyers it is €0 today.
        "custom_text": {
            "submit": {
                "message": (
                    "Mit einem Gutschein-Code sind die ersten 3 Monate gratis: heute wird nichts "
                    "abgebucht und keine Kreditkarte benoetigt. Jederzeit kuendbar."
                )
            }
        },
    }
    if promo:
        params["subscription_data"] = {"metadata": {PROMO_METADATA_KEY: PROMO_CODE}}
    elif trial_days > 0:
        # NOTE: with if_required a trial makes €0 due now, so the card is NOT collected during the
        # trial (a card-less trial that will not auto-convert). Off by default for that reason.
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
