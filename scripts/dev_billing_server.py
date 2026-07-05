"""Local dev server to test the Stripe billing round-trip end-to-end — NO Azure, NO Cosmos.

Wires the REAL billing handlers (checkout/portal/webhook) to an in-memory store plus one
seeded test account, so you can run a real TEST-mode checkout and watch the webhook upgrade
the account to 'pro' and a portal cancel downgrade it to 'free'. Everything on localhost.

Run it together with the Stripe CLI (which forwards live test events to your laptop):

  Terminal 1:  stripe listen --forward-to localhost:8787/api/billing/webhook
               -> copy the printed  whsec_...

  Terminal 2:  STRIPE_SECRET_KEY=sk_test_... STRIPE_WEBHOOK_SECRET=whsec_... \
                 uv run --with stripe --with uvicorn python scripts/dev_billing_server.py

  Browser:     open http://localhost:8787  ->  "Start Pro checkout"
               pay with card 4242 4242 4242 4242 (any future date / any CVC)
               -> /status shows plan = pro; cancel in the portal -> plan = free

Only a TEST secret key is accepted (sk_test_), so this can never touch live money.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from fbl_auth import (
    checkout_session_params,
    handle_event,
    portal_session_params,
    signup,
)
from fbl_auth.accounts import ACCOUNTS_CONTAINER, Account
from fbl_core.storage import InMemoryCosmosStore

PORT = int(os.environ.get("PORT", "8787"))
BASE = f"http://localhost:{PORT}"

_key = os.environ.get("STRIPE_SECRET_KEY", "")
if not _key.startswith("sk_test_"):
    sys.exit("Set STRIPE_SECRET_KEY to a TEST key (sk_test_...). Refusing to run otherwise.")
WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET")

import stripe  # noqa: E402  (imported after the key guard on purpose)

stripe.api_key = _key

# One in-memory store + one seeded free account for the whole session.
cosmos = InMemoryCosmosStore()
_rec = signup("dev-tester@example.test", cosmos)  # default plan: free
ACCOUNT_ID = _rec.account.id


def _account() -> Account:
    return Account.model_validate(cosmos.get(ACCOUNTS_CONTAINER, ACCOUNT_ID))


async def home(_req: Request) -> Response:
    acc = _account()
    return HTMLResponse(
        f"<h1>Billing dev server</h1>"
        f"<p>Seeded account plan: <b>{acc.tier}</b></p>"
        f"<p><a href='/checkout'>Start Pro checkout (test)</a> &middot; "
        f"<a href='/portal'>Open customer portal</a> &middot; "
        f"<a href='/status'>Status (JSON)</a></p>"
        f"<p>Pay with card <code>4242 4242 4242 4242</code>, any future date, any CVC.</p>"
    )


async def status(_req: Request) -> Response:
    acc = _account()
    return JSONResponse(
        {
            "plan": acc.tier,
            "stripe_customer_id": acc.stripe_customer_id,
            "plan_expires_at": acc.plan_expires_at,
        }
    )


async def checkout(_req: Request) -> Response:
    prices = stripe.Price.list(lookup_keys=["pro_monthly"], expand=["data.product"])
    if not prices.data:
        return JSONResponse({"error": "no price for lookup_key 'pro_monthly'"}, status_code=500)
    params = checkout_session_params(
        _account(), price_id=prices.data[0].id, success_url=BASE + "/status",
        cancel_url=BASE + "/", trial_days=14,
    )
    session = stripe.checkout.Session.create(**params)
    return RedirectResponse(session.url, status_code=303)


async def portal(_req: Request) -> Response:
    params = portal_session_params(_account(), return_url=BASE + "/")
    if params is None:
        return JSONResponse({"error": "no Stripe customer yet — buy first"}, status_code=409)
    session = stripe.billing_portal.Session.create(**params)
    return RedirectResponse(session.url, status_code=303)


async def webhook(req: Request) -> Response:
    if not WEBHOOK_SECRET:
        return JSONResponse({"error": "STRIPE_WEBHOOK_SECRET not set"}, status_code=503)
    payload = await req.body()
    sig = req.headers.get("stripe-signature", "")
    try:
        event = stripe.Webhook.construct_event(payload, sig, WEBHOOK_SECRET)
    except Exception as exc:
        print(f"[webhook] SIGNATURE FAILED: {exc}")
        return JSONResponse({"error": "bad signature"}, status_code=400)
    event_dict: dict[str, Any] = json.loads(str(event))
    result = handle_event(cosmos, event_dict)
    print(f"[webhook] {event_dict.get('type')} -> {result} | plan now: {_account().tier}")
    return JSONResponse(result)


app = Starlette(
    routes=[
        Route("/", home),
        Route("/status", status),
        Route("/checkout", checkout),
        Route("/portal", portal),
        Route("/api/billing/webhook", webhook, methods=["POST"]),
    ]
)


if __name__ == "__main__":
    print("=" * 64)
    print(f"Billing dev server:  {BASE}")
    print(f"Seeded account id:   {ACCOUNT_ID}  (used as client_reference_id)")
    print(f"Webhook secret set:  {bool(WEBHOOK_SECRET)}")
    print(f"Open {BASE} and click 'Start Pro checkout'. Card: 4242 4242 4242 4242")
    print("=" * 64)
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=PORT)
