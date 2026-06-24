"""Demo loader: run a SMALL set of real companies through the whole pipeline into Azure.

Seeds ~N companies (via a narrow ``sucheFirma`` query), then runs ingest → parse →
consolidate → derive → present against the **deployed** Blob + Cosmos so you can inspect
the result in Cosmos DB. NOT the full backfill.

Env (read from the process env, falling back to the repo-root .env):
    FIRMENBUCH_API_KEY, JUSTIZONLINE_API_URL, COSMOS_ENDPOINT, BLOB_ACCOUNT_URL
Auth to Azure is via DefaultAzureCredential (your `az login`).

Usage:
    COSMOS_ENDPOINT=... BLOB_ACCOUNT_URL=... \
        uv run python scripts/demo_load.py --count 20 --prefix a
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from fbl_core.storage import BlobStore, CosmosStore
from fbl_firmenbuch_client import JustizOnlineClient
from fbl_ingest import run_ingest
from fbl_orchestration import PipelineContext, process_set
from fbl_registry import Registry

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _env(key: str, default: str | None = None) -> str | None:
    if os.environ.get(key):
        return os.environ[key]
    env_file = _REPO_ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                name, value = line.split("=", 1)
                if name.strip() == key and value.strip():
                    return value.strip()
    return default


def main() -> int:
    parser = argparse.ArgumentParser(prog="demo_load")
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument("--prefix", default="a", help="sucheFirma name prefix to seed from")
    parser.add_argument("--rechtsform", default="GES")
    args = parser.parse_args()

    api_url = _env("JUSTIZONLINE_API_URL", "https://justizonline.gv.at/jop/api/at.gv.justiz.fbw/ws")
    api_key = _env("FIRMENBUCH_API_KEY")
    cosmos_endpoint = _env("COSMOS_ENDPOINT")
    blob_url = _env("BLOB_ACCOUNT_URL")
    if not (api_key and cosmos_endpoint and blob_url):
        raise SystemExit("need FIRMENBUCH_API_KEY, COSMOS_ENDPOINT, BLOB_ACCOUNT_URL")

    assert api_url is not None
    client = JustizOnlineClient(api_url, api_key)
    cosmos = CosmosStore(cosmos_endpoint, "firmenbuch")
    blob = BlobStore(blob_url)
    registry = Registry(cosmos)
    ctx = PipelineContext(blob=blob, cosmos=cosmos, source=client, registry=registry)

    # Seed: first N distinct FNRs from a narrow query (deterministic: sorted by FNR).
    hits = client.suche_firma(
        f"{args.prefix}*", rechtsform=args.rechtsform, suchbereich=1, exaktesuche=True
    )
    fnrs = sorted({h.fnr for h in hits})[: args.count]
    print(f"seeding {len(fnrs)} companies: {fnrs}")
    for fnr in fnrs:
        registry.ensure(fnr, source="demo_load")

    print("ingesting raw -> 90-raw ...")
    ing = run_ingest(client, registry, blob, run_id="demo", fnrs=fnrs)
    print("  ", ing.model_dump())

    print("processing 50 -> 30 -> 10 ...")
    rep = process_set(ctx, "demo", fnrs)
    print("  ", rep.model_dump())

    print("\nDone. Inspect Cosmos: 99_registry, 50_consolidated, 30_derived, 10_presented")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
