"""Admin: mint a guest invite code (Aufgabe 3).

Creates a single-use code that, when redeemed at ``/try?code=…`` (double-opt-in), grants a
key on the ``guest`` plan — full Pro access for the trial window, then automatic ``free``.
No Stripe involved. Hand the code to an interested tester (LinkedIn etc.).

Env (from process env, falling back to repo-root .env):
    COSMOS_ENDPOINT, COSMOS_DATABASE
Auth to Azure Cosmos is via DefaultAzureCredential (your ``az login``).

Usage:
    COSMOS_ENDPOINT=... uv run python scripts/create_invite.py --label "Max / LinkedIn"
    COSMOS_ENDPOINT=... uv run python scripts/create_invite.py --label "Beta" --guest-days 30 \
        --valid-days 60 --code SPECIAL-2026
"""

from __future__ import annotations

import argparse

from fbl_auth import create_invite
from fbl_core.config import get_settings
from fbl_core.storage import CosmosStore


def main() -> None:
    parser = argparse.ArgumentParser(description="Mint a guest invite code.")
    parser.add_argument("--label", default="", help="who it's for (free text, for your records)")
    parser.add_argument("--guest-days", type=int, default=14, help="trial length once redeemed")
    parser.add_argument(
        "--valid-days", type=int, default=30, help="how long the code is redeemable"
    )
    parser.add_argument("--code", default=None, help="use a specific code instead of a random one")
    args = parser.parse_args()

    settings = get_settings()
    if not settings.cosmos_endpoint:
        raise SystemExit("COSMOS_ENDPOINT is not set — refusing to run.")
    cosmos = CosmosStore(settings.cosmos_endpoint, settings.cosmos_database)

    invite = create_invite(
        cosmos,
        label=args.label,
        guest_days=args.guest_days,
        valid_days=args.valid_days,
        code=args.code,
    )
    base = settings.site_base_url.rstrip("/")
    print(f"Code:        {invite.code}")
    print(f"Label:       {invite.label or '(none)'}")
    print(f"Guest days:  {invite.guest_days}")
    print(f"Valid until: {invite.expires_at}")
    print(f"Redeem link: {base}/try?code={invite.code}")


if __name__ == "__main__":
    main()
