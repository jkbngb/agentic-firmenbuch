"""Cutover migration: grandfather every EXISTING account onto the ``legacy`` plan.

Owner decision (gtm/output/STRIPE_BUILD_PLAN.md): when the paid model goes live, all
existing API keys keep full access, free, forever. Only NEW signups start on ``free``. The
``legacy`` plan has full-access feature gates (see fbl_mcp_server.plans) and Pro-level rate
quotas (fbl_core.config.tier_quotas), so grandfathered users are unaffected.

This flips every active account whose plan is NOT already a paid/guest/legacy plan
(i.e. ``free`` and anything unknown) to ``legacy``. It never touches ``pro`` (a real Stripe
subscriber), ``guest`` (a time-boxed trial), or already-``legacy`` accounts. Idempotent.

Run it ONCE, at cutover, AFTER the billing code is deployed but BEFORE announcing the paid
model. Dry-run by default; pass --commit to write.

Env (from process env, falling back to repo-root .env):
    COSMOS_ENDPOINT, COSMOS_DATABASE
Auth to Azure Cosmos is via DefaultAzureCredential (your ``az login``).

Usage:
    COSMOS_ENDPOINT=... uv run python scripts/migrate_accounts_to_legacy.py          # dry run
    COSMOS_ENDPOINT=... uv run python scripts/migrate_accounts_to_legacy.py --commit  # apply
"""

from __future__ import annotations

import argparse

from pydantic import ValidationError

from fbl_auth.accounts import ACCOUNTS_CONTAINER, Account
from fbl_core.config import get_settings
from fbl_core.storage import CosmosStore

# Plans we must NOT overwrite: real subscribers, active trials, and the target itself.
_KEEP = frozenset({"pro", "guest", "legacy", "enterprise"})


def main() -> None:
    parser = argparse.ArgumentParser(description="Grandfather existing accounts onto 'legacy'.")
    parser.add_argument("--commit", action="store_true", help="write changes (default: dry run)")
    args = parser.parse_args()

    settings = get_settings()
    if not settings.cosmos_endpoint:
        raise SystemExit("COSMOS_ENDPOINT is not set — refusing to run.")
    cosmos = CosmosStore(settings.cosmos_endpoint, settings.cosmos_database)

    scanned = migrated = skipped = non_account = 0
    for raw in cosmos.iter_all(ACCOUNTS_CONTAINER):
        scanned += 1
        try:
            account = Account.model_validate(raw)
        except ValidationError:
            # 00_accounts also holds non-account records (e.g. OAuth/usage docs keyed by
            # sha256:… without an email) — anything that doesn't parse as an Account is skipped.
            non_account += 1
            continue
        # Only active accounts; leave pending/unsubscribed as-is.
        if account.status != "active" or account.tier in _KEEP:
            skipped += 1
            continue
        old = account.tier
        account.tier = "legacy"
        migrated += 1
        label = account.id[:16]
        if args.commit:
            cosmos.upsert(ACCOUNTS_CONTAINER, account.model_dump(mode="json"))
            print(f"  migrated {label}… {old} -> legacy")
        else:
            print(f"  would migrate {label}… {old} -> legacy")

    mode = "APPLIED" if args.commit else "DRY RUN (no writes)"
    print(
        f"\n{mode}: scanned={scanned} migrated={migrated} "
        f"skipped={skipped} non_account={non_account}"
    )
    if not args.commit and migrated:
        print("Re-run with --commit to apply.")


if __name__ == "__main__":
    main()
