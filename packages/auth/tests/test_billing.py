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


def _sub_updated_event(
    event_id: str,
    customer: str,
    *,
    cancel_at_period_end: bool = False,
    status: str | None = None,
    period_end: int = 1793491200,
) -> dict[str, Any]:
    obj: dict[str, Any] = {
        "object": "subscription",
        "customer": customer,
        "cancel_at_period_end": cancel_at_period_end,
        "current_period_end": period_end,
    }
    if status is not None:
        obj["status"] = status
    return {
        "id": event_id,
        "type": "customer.subscription.updated",
        "data": {"object": obj},
    }


class _RecordingEmail:
    """Minimal EmailSender double: records only the cancellation goodbye calls."""

    def __init__(self) -> None:
        self.canceled: list[tuple[str, str]] = []

    def send_subscription_canceled(self, to: str, access_until: str) -> bool:
        self.canceled.append((to, access_until))
        return True


def _make_pro(cosmos: InMemoryCosmosStore, customer: str, sub: str = "s") -> str:
    """Sign up + run a checkout so the account is pro and bound to *customer*. Returns token."""
    token = signup("buyer@example.test", cosmos).token
    handle_event(
        cosmos,
        _checkout_event(
            "evt_setup_" + customer,
            client_reference_id=None,
            email="buyer@example.test",
            customer=customer,
            sub=sub,
        ),
    )
    return token


def test_scheduled_cancel_keeps_pro_until_period_end_and_emails_once() -> None:
    cosmos = InMemoryCosmosStore()
    token = _make_pro(cosmos, "cus_c1")
    assert validate(token, cosmos).tier == "pro"  # type: ignore[union-attr]
    email = _RecordingEmail()

    ev1 = _sub_updated_event("evt_u1", "cus_c1", cancel_at_period_end=True)
    out = handle_event(cosmos, ev1, email_sender=email)
    assert out["outcome"] == "cancel_scheduled"
    acct = validate(token, cosmos)
    assert acct.tier == "pro"  # type: ignore[union-attr]  # access kept until period end
    assert acct.plan_expires_at is not None  # type: ignore[union-attr]  # end recorded
    assert email.canceled == [("buyer@example.test", "01.11.2026")]  # goodbye with date

    # a second identical updated event must NOT re-send the goodbye
    ev2 = _sub_updated_event("evt_u2", "cus_c1", cancel_at_period_end=True)
    out2 = handle_event(cosmos, ev2, email_sender=email)
    assert out2["outcome"] == "cancel_scheduled_dup"
    assert len(email.canceled) == 1


def test_scheduled_cancel_then_deleted_downgrades_at_period_end() -> None:
    cosmos = InMemoryCosmosStore()
    token = _make_pro(cosmos, "cus_c2")
    ev3 = _sub_updated_event("evt_u3", "cus_c2", cancel_at_period_end=True)
    handle_event(cosmos, ev3, email_sender=_RecordingEmail())
    assert validate(token, cosmos).tier == "pro"  # type: ignore[union-attr]
    out = handle_event(cosmos, _sub_deleted_event("evt_del", "cus_c2"))
    assert out["outcome"] == "downgraded"
    acct = validate(token, cosmos)
    assert acct.tier == "free"  # type: ignore[union-attr]
    assert acct.plan_expires_at is None  # type: ignore[union-attr]


def test_cancel_reversed_clears_scheduled_end() -> None:
    cosmos = InMemoryCosmosStore()
    token = _make_pro(cosmos, "cus_c3")
    ev4 = _sub_updated_event("evt_u4", "cus_c3", cancel_at_period_end=True)
    handle_event(cosmos, ev4, email_sender=_RecordingEmail())
    ev5 = _sub_updated_event("evt_u5", "cus_c3", cancel_at_period_end=False)
    out = handle_event(cosmos, ev5)
    assert out["outcome"] == "cancel_reversed"
    acct = validate(token, cosmos)
    assert acct.tier == "pro"  # type: ignore[union-attr]  # still pro, cancellation undone
    assert acct.plan_expires_at is None  # type: ignore[union-attr]


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


def test_payment_failed_does_not_downgrade_c3() -> None:
    # C3: a failed payment must NOT drop the customer — Stripe Smart Retries recover a one-day
    # card hiccup. The plan is driven by subscription status / deletion, not payment events.
    cosmos = InMemoryCosmosStore()
    token = _make_pro(cosmos, "cus_6", sub="s6")
    out = handle_event(cosmos, _payment_failed_event("evt_d", "cus_6"))
    assert out["outcome"] == "payment_failed_noted"
    assert validate(token, cosmos).tier == "pro"  # type: ignore[union-attr]  # still pro

    # dunning exhausted → subscription.updated(status=unpaid) is what actually ends access
    out2 = handle_event(cosmos, _sub_updated_event("evt_unpaid", "cus_6", status="unpaid"))
    assert out2["outcome"] == "downgraded"
    assert validate(token, cosmos).tier == "free"  # type: ignore[union-attr]


def test_c3_status_drives_plan() -> None:
    # past_due keeps Pro (grace); canceled ends it; active restores it (recovery / self-heal).
    cosmos = InMemoryCosmosStore()
    token = _make_pro(cosmos, "cus_st", sub="s_st")
    handle_event(cosmos, _sub_updated_event("e_pd", "cus_st", status="past_due"))
    assert validate(token, cosmos).tier == "pro"  # type: ignore[union-attr]  # grace, not dropped
    handle_event(cosmos, _sub_updated_event("e_can", "cus_st", status="canceled"))
    assert validate(token, cosmos).tier == "free"  # type: ignore[union-attr]
    handle_event(cosmos, _sub_updated_event("e_act", "cus_st", status="active"))
    assert validate(token, cosmos).tier == "pro"  # type: ignore[union-attr]  # restored


def test_c1_regenerate_preserves_pro_and_stripe_ids() -> None:
    # C1: a paying customer who loses their key and regenerates must keep Pro + Stripe linkage,
    # and the old key must stop working (revoked).
    from fbl_auth import account_by_email
    from fbl_auth.email import NullEmailSender
    from fbl_auth.signup_flow import request_verification, verify

    cosmos = InMemoryCosmosStore()
    old_token = _make_pro(cosmos, "cus_carry", sub="sub_carry")
    old = validate(old_token, cosmos)
    assert old is not None and old.tier == "pro"

    sender = NullEmailSender()
    vtok = request_verification(
        old.email, cosmos, email_sender=sender, verify_url=lambda t: "x/" + t
    )
    new_key = verify(vtok, cosmos, email_sender=sender)
    assert new_key is not None
    new = validate(new_key, cosmos)
    assert new is not None and new.tier == "pro"  # carried, not demoted to free
    assert new.stripe_customer_id == "cus_carry" and new.stripe_subscription_id == "sub_carry"
    assert validate(old_token, cosmos) is None  # old key revoked
    # a later subscription.deleted for that customer downgrades the NEW (active) account
    assert account_by_email(cosmos, old.email).id == new.id  # type: ignore[union-attr]


def test_c2_pending_and_kind_docs_never_resolve_as_account() -> None:
    # C2: pending_signup / ip_throttle / invite docs (they carry a `kind`) must never be treated
    # as an account, and a checkout bound to one must be UNMATCHED (not corrupt the pending doc).
    from fbl_auth import account_by_email
    from fbl_auth.accounts import hash_token
    from fbl_auth.email import NullEmailSender
    from fbl_auth.signup_flow import request_verification

    cosmos = InMemoryCosmosStore()
    vtok = request_verification(
        "p@example.test", cosmos, email_sender=NullEmailSender(), verify_url=lambda t: "x"
    )
    assert account_by_email(cosmos, "p@example.test") is None  # pending is not a real account
    pending_id = hash_token(vtok)
    ev = _checkout_event(
        "evt_c2",
        client_reference_id=pending_id,
        email="p@example.test",
        customer="cus_c2",
        sub="s2",
    )
    assert handle_event(cosmos, ev)["outcome"] == "unmatched"  # guard rejects the pending id
    doc = cosmos.get(ACCOUNTS_CONTAINER, pending_id)
    assert doc is not None and doc["status"] == "pending" and doc["kind"] == "pending_signup"


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


def test_checkout_params_promo_no_trial_cardless() -> None:
    # Promo ("3 Monate gratis"): no trial (would burn the coupon window), card-less at €0,
    # tagged in metadata so promo subs are queryable in Stripe.
    p = checkout_session_params(
        None,
        price_id="price_x",
        success_url="s",
        cancel_url="c",
        trial_days=14,
        email="promo@b.test",
        promo=True,
    )
    assert p["payment_method_collection"] == "if_required"
    assert p["allow_promotion_codes"] is True
    assert "trial_period_days" not in p["subscription_data"]  # no trial
    assert p["subscription_data"]["metadata"]["promo"] == "GRATIS3M"
    assert p["customer_email"] == "promo@b.test"


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


def test_checkout_params_new_buyer_binds_by_email() -> None:
    # No account yet (new buyer): bind by the e-mail Stripe collects, no client_reference_id.
    # The account is created on payment (webhook), so nothing is produced without a sale.
    p = checkout_session_params(
        None, price_id="price_x", success_url="s", cancel_url="c", trial_days=14, email="new@b.test"
    )
    assert "client_reference_id" not in p
    assert p["customer_email"] == "new@b.test"
    assert p["subscription_data"] == {"trial_period_days": 14}
    assert p["mode"] == "subscription"


def test_portal_params_none_without_customer() -> None:
    acc = Account(id="acct:3", token_hash="acct:3", email="a@b.test")
    assert portal_session_params(acc, return_url="r") is None
    acc.stripe_customer_id = "cus_p"
    assert portal_session_params(acc, return_url="r") == {
        "customer": "cus_p",
        "return_url": "r",
    }
