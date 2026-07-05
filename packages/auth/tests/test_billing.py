"""Billing webhook -> plan changes (pure handler, constructed events; no Stripe SDK/secrets)."""

from __future__ import annotations

from typing import Any

from fbl_auth import handle_event, signup
from fbl_auth.accounts import ACCOUNTS_CONTAINER, Account, validate
from fbl_auth.billing import (
    BILLING_EVENTS_CONTAINER,
    checkout_session_params,
    portal_session_params,
)
from fbl_core.storage import InMemoryCosmosStore


def _checkout_event(
    event_id: str, *, client_reference_id: str | None, email: str | None, customer: str, sub: str
) -> dict[str, Any]:
    return {
        "id": event_id,
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "object": "checkout_session",
                "client_reference_id": client_reference_id,
                "customer_email": email,
                "customer": customer,
                "subscription": sub,
            }
        },
    }


def _sub_deleted_event(event_id: str, customer: str) -> dict[str, Any]:
    return {
        "id": event_id,
        "type": "customer.subscription.deleted",
        "data": {"object": {"object": "subscription", "customer": customer}},
    }


def _payment_failed_event(event_id: str, customer: str) -> dict[str, Any]:
    return {
        "id": event_id,
        "type": "invoice.payment_failed",
        "data": {"object": {"object": "invoice", "customer": customer}},
    }


def test_checkout_completed_upgrades_by_client_reference_id() -> None:
    cosmos = InMemoryCosmosStore()
    rec = signup("buyer@example.test", cosmos)  # default free
    token = rec.token
    account_id = rec.account.id

    # buyer paid with a DIFFERENT e-mail — match must still work via client_reference_id
    ev = _checkout_event(
        "evt_1",
        client_reference_id=account_id,
        email="someone-else@paid.test",
        customer="cus_123",
        sub="sub_123",
    )
    out = handle_event(cosmos, ev)
    assert out["outcome"] == "upgraded" and out["account_id"] == account_id

    acct = validate(token, cosmos)
    assert acct is not None and acct.tier == "pro"
    assert acct.stripe_customer_id == "cus_123" and acct.stripe_subscription_id == "sub_123"


def test_checkout_completed_matches_by_email_fallback() -> None:
    cosmos = InMemoryCosmosStore()
    token = signup("buyer@example.test", cosmos).token
    ev = _checkout_event(
        "evt_2", client_reference_id=None, email="buyer@example.test", customer="cus_9", sub="sub_9"
    )
    assert handle_event(cosmos, ev)["outcome"] == "upgraded"
    assert validate(token, cosmos).tier == "pro"  # type: ignore[union-attr]


def test_checkout_completed_unmatched_is_recorded_not_applied() -> None:
    cosmos = InMemoryCosmosStore()
    ev = _checkout_event(
        "evt_3",
        client_reference_id="acct:nope",
        email="ghost@x.test",
        customer="cus_x",
        sub="sub_x",
    )
    out = handle_event(cosmos, ev)
    assert out["outcome"] == "unmatched" and out["account_id"] is None


def test_subscription_deleted_downgrades_immediately() -> None:
    cosmos = InMemoryCosmosStore()
    token = signup("buyer@example.test", cosmos).token
    handle_event(
        cosmos,
        _checkout_event(
            "evt_a",
            client_reference_id=None,
            email="buyer@example.test",
            customer="cus_5",
            sub="s5",
        ),
    )
    assert validate(token, cosmos).tier == "pro"  # type: ignore[union-attr]

    out = handle_event(cosmos, _sub_deleted_event("evt_b", "cus_5"))
    assert out["outcome"] == "downgraded"
    assert validate(token, cosmos).tier == "free"  # type: ignore[union-attr]


def test_payment_failed_downgrades_immediately() -> None:
    cosmos = InMemoryCosmosStore()
    token = signup("buyer@example.test", cosmos).token
    handle_event(
        cosmos,
        _checkout_event(
            "evt_c",
            client_reference_id=None,
            email="buyer@example.test",
            customer="cus_6",
            sub="s6",
        ),
    )
    out = handle_event(cosmos, _payment_failed_event("evt_d", "cus_6"))
    assert out["outcome"] == "downgraded"
    assert validate(token, cosmos).tier == "free"  # type: ignore[union-attr]


def test_downgrade_leaves_non_pro_accounts_untouched() -> None:
    # A legacy account that somehow shares a customer id must NOT be downgraded.
    cosmos = InMemoryCosmosStore()
    acc = Account(
        id="acct:legacy",
        token_hash="acct:legacy",
        email="old@example.test",
        tier="legacy",
        stripe_customer_id="cus_leg",
    )
    cosmos.upsert(ACCOUNTS_CONTAINER, acc.model_dump(mode="json"))
    out = handle_event(cosmos, _sub_deleted_event("evt_e", "cus_leg"))
    assert out["outcome"] == "no_change"
    reloaded = Account.model_validate(cosmos.get(ACCOUNTS_CONTAINER, "acct:legacy"))
    assert reloaded.tier == "legacy"


def test_duplicate_event_is_idempotent() -> None:
    cosmos = InMemoryCosmosStore()
    signup("buyer@example.test", cosmos)
    ev = _checkout_event(
        "evt_dup", client_reference_id=None, email="buyer@example.test", customer="cus_7", sub="s7"
    )
    assert handle_event(cosmos, ev)["status"] == "ok"
    # replay the same event id -> no-op
    assert handle_event(cosmos, ev)["status"] == "duplicate"
    # exactly one processed-event record
    assert cosmos.get(BILLING_EVENTS_CONTAINER, "evt_dup") is not None


def test_unhandled_event_type_is_ignored() -> None:
    cosmos = InMemoryCosmosStore()
    out = handle_event(cosmos, {"id": "evt_z", "type": "customer.created", "data": {"object": {}}})
    assert out["status"] == "ignored"


def test_checkout_params_bind_account_and_trial() -> None:
    acc = Account(id="acct:1", token_hash="acct:1", email="a@b.test")
    p = checkout_session_params(
        acc, price_id="price_x", success_url="s", cancel_url="c", trial_days=14
    )
    assert p["mode"] == "subscription"
    assert p["client_reference_id"] == "acct:1"
    assert p["customer_email"] == "a@b.test"  # no stripe customer yet
    assert p["subscription_data"] == {"trial_period_days": 14}
    assert p["line_items"] == [{"price": "price_x", "quantity": 1}]


def test_checkout_params_reuse_existing_customer() -> None:
    acc = Account(
        id="acct:2", token_hash="acct:2", email="a@b.test", stripe_customer_id="cus_known"
    )
    p = checkout_session_params(acc, price_id="p", success_url="s", cancel_url="c", trial_days=0)
    assert p["customer"] == "cus_known" and "customer_email" not in p
    assert "subscription_data" not in p  # trial_days=0 -> no trial block


def test_portal_params_none_without_customer() -> None:
    acc = Account(id="acct:3", token_hash="acct:3", email="a@b.test")
    assert portal_session_params(acc, return_url="r") is None
    acc.stripe_customer_id = "cus_p"
    assert portal_session_params(acc, return_url="r") == {
        "customer": "cus_p",
        "return_url": "r",
    }
