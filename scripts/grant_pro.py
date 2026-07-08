"""Grant time-boxed full Pro (comp access) to one or more people, by email.

Sets ``tier="guest"`` + ``plan_expires_at`` on the account. ``guest`` has full access to
every tool and Pro-level rate limits, and reverts to ``free`` automatically once the date
passes -- no Stripe, no charge, nothing to cancel. Idempotent by email: an existing account
is upgraded in place; an unknown email gets a fresh account and its API key is printed here
(this script can't send mail -- forward the key, or the person just logs in via Cowork/OAuth
with the same email and lands on the same account).

Usage (from the agentic-firmenbuch repo root):

    COSMOS_ENDPOINT=https://cosmos-firmenbuch-xbjux2hw.documents.azure.com:443/ \
    COSMOS_DATABASE=firmenbuch \
    uv run python scripts/grant_pro.py a.huber@ai-bd.at other@example.com --until 2026-09-15

Auth to Cosmos is DefaultAzureCredential (your ``az login``). Read-modify-write only on the
named accounts; no other data is touched.
"""

from __future__ import annotations

import argparse

from fbl_auth import signup
from fbl_auth.accounts import ACCOUNTS_CONTAINER, Account, account_by_email
from fbl_core.config import get_settings
from fbl_core.storage import CosmosStore


def _grant(cosmos: CosmosStore, email: str, expires: str) -> tuple[str, str | None]:
    """Upgrade or create ``email`` as guest until ``expires``. Returns (status, new_key)."""
    email = email.strip().lower()
    acct = account_by_email(cosmos, email)
    if acct is not None:
        acct.tier = "guest"
        acct.plan_expires_at = expires
        cosmos.upsert(ACCOUNTS_CONTAINER, acct.model_dump(mode="json"))
        return "upgraded", None
    rec = signup(email, cosmos)  # no email_sender -> no mail; we print the key below
    new = rec.account
    new.tier = "guest"
    new.plan_expires_at = expires
    cosmos.upsert(ACCOUNTS_CONTAINER, new.model_dump(mode="json"))
    return "created", rec.token


def main() -> None:
    ap = argparse.ArgumentParser(description="Grant time-boxed full Pro (guest) by email.")
    ap.add_argument("emails", nargs="+", help="one or more email addresses")
    ap.add_argument(
        "--until",
        required=True,
        metavar="YYYY-MM-DD",
        help="access lasts through end of this day (UTC), then auto-reverts to free",
    )
    args = ap.parse_args()

    expires = f"{args.until}T23:59:59Z"
    settings = get_settings()
    if not settings.cosmos_endpoint:
        raise SystemExit("COSMOS_ENDPOINT is not set.")
    cosmos = CosmosStore(settings.cosmos_endpoint, settings.cosmos_database)

    print(f"\n  Grant guest (full Pro) until {expires}\n  " + "-" * 60)
    for email in args.emails:
        status, key = _grant(cosmos, email, expires)
        # Re-read to prove the stored state.
        chk = account_by_email(cosmos, email)
        assert chk is not None
        line = f"  {email:34} {status:9} tier={chk.tier} bis {chk.plan_expires_at}"
        print(line)
        if key:
            print(f"       API-KEY (weiterleiten oder Cowork-Login per E-Mail): {key}")
    print()


if __name__ == "__main__":
    main()
