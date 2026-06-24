"""Bounded LIVE smoke of stage 1 (sync-registry): seed a slice of 99_registry for real.

Mirrors the prefix-walk query (`sucheFirma("{prefix}*", suchbereich=1, exaktesuche=True)`)
for a small set of prefixes, then upserts the hits into the deployed Cosmos `99_registry`
with the exact seed logic (status mapping + name), capped at --limit companies. This proves
the live enumeration → registry write path (incl. the new `name` field) before the full,
hours-long all-Rechtsformen sweep. Idempotent: a later full `--mode sync-registry` reconciles.

    uv run python scripts/sync_registry_smoke.py --endpoint <cosmos> --limit 120
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
PREFIXES = ["Wester", "Aae", "Sonnen", "Alpen", "Tiroler", "Donau", "Berg", "Wiener Stadt"]


def main() -> int:
    ap = argparse.ArgumentParser(prog="sync_registry_smoke")
    ap.add_argument("--endpoint", default=os.environ.get("COSMOS_ENDPOINT", ""))
    ap.add_argument("--database", default="firmenbuch")
    ap.add_argument("--rechtsform", default="GES")
    ap.add_argument("--prefixes", nargs="*", default=PREFIXES)
    ap.add_argument("--limit", type=int, default=120)
    ap.add_argument("--source-label", default="sucheFirma_smoke")
    args = ap.parse_args()

    env = dotenv_values(str(ROOT / ".env"))
    os.environ.update({k: v for k, v in env.items() if v})
    endpoint = args.endpoint or os.environ.get("COSMOS_ENDPOINT", "")
    if not endpoint:
        raise SystemExit("need --endpoint or COSMOS_ENDPOINT (the deployed Cosmos)")

    from fbl_core.storage import CosmosStore
    from fbl_firmenbuch_client import JustizOnlineClient
    from fbl_ingest import status_from_result
    from fbl_registry import Registry

    client = JustizOnlineClient(
        os.environ["JUSTIZONLINE_API_URL"], os.environ["FIRMENBUCH_API_KEY"]
    )
    registry = Registry(CosmosStore(endpoint, args.database))

    # 1) Bounded live enumeration — one sucheFirma call per prefix, stop at --limit.
    found: dict[str, object] = {}
    for prefix in args.prefixes:
        if len(found) >= args.limit:
            break
        results = client.suche_firma(
            f"{prefix}*", suchbereich=1, rechtsform=args.rechtsform, exaktesuche=True
        )
        for r in results:
            found.setdefault(r.fnr, r)
        print(f"  sucheFirma {prefix!r}*  -> {len(results):>4} hits  (unique so far: {len(found)})")
    chosen = list(found.items())[: args.limit]
    print(f"\nseeding {len(chosen)} companies into {endpoint} / {args.database} / 99_registry")

    # 2) Upsert with the seed logic (status mapping + name), idempotent.
    seeded = updated = 0
    for fnr, r in chosen:
        status = status_from_result(r)  # type: ignore[arg-type]
        existing = registry.get(fnr)
        if existing is None:
            registry.ensure(fnr, status=status, source=args.source_label, name=r.name)  # type: ignore[attr-defined]
            seeded += 1
        else:
            existing.status = status
            if r.name:  # type: ignore[attr-defined]
                existing.name = r.name  # type: ignore[attr-defined]
            registry.put(existing)
            updated += 1

    # 3) Read back from prod Cosmos to prove the write (fnr + status + name).
    docs = [registry.get(fnr) for fnr, _ in chosen[:15]]
    total = len(registry.all_fnrs())
    print(f"\nseeded={seeded} updated={updated}; 99_registry now holds {total} companies")
    print("\nsample of what landed in 99_registry (read back from Cosmos):")
    print(f"{'FNR':<10} {'STATUS':<11} NAME")
    for d in docs:
        if d is not None:
            print(f"{d.fnr:<10} {d.status:<11} {d.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
