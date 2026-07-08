"""One-shot Stripe TEST-mode setup for the Pro subscription (Aufgabe 2 go-live prep).

Creates (idempotently) everything the billing backend needs, in TEST mode only:
  - Product  "Agentic-Firmenbuch Pro"
  - Price    EUR 79,00 / month, tax_behavior=inclusive, lookup_key="pro_monthly"
  - Webhook  endpoint for the events we handle (if --webhook-url is given), prints its secret

The 14-day trial is applied at checkout (subscription_data.trial_period_days), not on the price,
so the price needs no trial. Stripe Tax and the Customer Portal are dashboard toggles (printed
as next steps).

SAFETY: refuses to run unless STRIPE_SECRET_KEY starts with "sk_test_" — it will NEVER touch
the live account. Nothing here is committed; the key comes from the environment.

Usage:
    STRIPE_SECRET_KEY=sk_test_xxx uv run python scripts/stripe_test_setup.py
    STRIPE_SECRET_KEY=sk_test_xxx uv run python scripts/stripe_test_setup.py \
        --webhook-url https://<your-test-host>/api/billing/webhook
"""

from __future__ import annotations

import argparse
import os
import sys

from fbl_auth.billing import HANDLED_EVENTS

PRICE_LOOKUP_KEY = "pro_monthly"
PRICE_UNIT_AMOUNT = 7900  # EUR 79,00 in cents
PRODUCT_NAME = "Agentic-Firmenbuch Pro"
# H1: single source of truth — the exact events the webhook handler processes. Mirroring
# HANDLED_EVENTS (not a hand-maintained list) is what prevents the drift where the live endpoint
# was missing customer.subscription.updated, so scheduled cancellations / downgrades never fired.
WEBHOOK_EVENTS = sorted(HANDLED_EVENTS)


def main() -> None:
    parser = argparse.ArgumentParser(description="Set up Stripe TEST-mode billing objects.")
    parser.add_argument(
        "--webhook-url",
        default=None,
        help="public URL of the deployed /api/billing/webhook (test host); creates the endpoint",
    )
    args = parser.parse_args()

    key = os.environ.get("STRIPE_SECRET_KEY", "")
    if not key:
        sys.exit("STRIPE_SECRET_KEY is not set.")
    if not key.startswith("sk_test_"):
        sys.exit("Refusing to run: STRIPE_SECRET_KEY is not a TEST key (must start with sk_test_).")

    try:
        import stripe
    except ImportError:
        sys.exit("The 'stripe' package is not installed. Run: uv pip install stripe")

    stripe.api_key = key

    # --- Product (reuse by name) ---
    product = next(
        (p for p in stripe.Product.list(active=True).auto_paging_iter() if p.name == PRODUCT_NAME),
        None,
    )
    if product is None:
        product = stripe.Product.create(name=PRODUCT_NAME)
        print(f"created product {product.id} ({PRODUCT_NAME})")
    else:
        print(f"reusing product {product.id} ({PRODUCT_NAME})")

    # --- Price (reuse by lookup_key) ---
    existing = stripe.Price.list(lookup_keys=[PRICE_LOOKUP_KEY], active=True).data
    if existing:
        price = existing[0]
        print(f"reusing price {price.id} (lookup_key={PRICE_LOOKUP_KEY})")
    else:
        price = stripe.Price.create(
            product=product.id,
            currency="eur",
            unit_amount=PRICE_UNIT_AMOUNT,
            recurring={"interval": "month"},
            tax_behavior="inclusive",
            lookup_key=PRICE_LOOKUP_KEY,
        )
        print(f"created price {price.id} (EUR 79,00/month incl. VAT, key={PRICE_LOOKUP_KEY})")

    # --- Webhook endpoint (optional) ---
    if args.webhook_url:
        endpoint = stripe.WebhookEndpoint.create(
            url=args.webhook_url,
            enabled_events=WEBHOOK_EVENTS,
        )
        print(f"created webhook endpoint {endpoint.id} -> {args.webhook_url}")
        secret = getattr(endpoint, "secret", None)
        if secret:
            print("\n  IMPORTANT: set this as STRIPE_WEBHOOK_SECRET (test env / Key Vault):")
            print(f"    STRIPE_WEBHOOK_SECRET={secret}")

    print("\nDone (TEST mode). Next steps in the Stripe TEST dashboard:")
    print("  1. Enable the Customer Portal (Settings -> Billing -> Customer portal).")
    print("  2. Enable Stripe Tax if you want automatic VAT handling.")
    print("  3. Set ENV on the test API host:")
    print("       STRIPE_SECRET_KEY=<your sk_test_...>")
    print("       STRIPE_WEBHOOK_SECRET=<whsec_ from above or from the dashboard>")
    print("  4. Test flow: checkout with card 4242 4242 4242 4242 -> webhook -> plan 'pro'")
    print("     -> portal cancel -> immediate downgrade to 'free'.")


if __name__ == "__main__":
    main()
