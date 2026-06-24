#!/usr/bin/env bash
# Per-legal-form backfill progress: how many ACTIVE companies in each Rechtsform have been
# ingested (downloaded) so far, out of the total active in that form.
#   ./scripts/backfill-status.sh
# "Processed" = the FNR is in the backfill checkpoint's done-set
# (90-raw/_checkpoints/backfill_ingest.json). Reads the registry once — takes ~a minute.
set -euo pipefail
cd "$(dirname "$0")/.."
RG=${RG:-rg-firmenbuch-prod}
SA=${SA:-stfirmenbuchxbjux2hw}
JOB=job-firmenbuch-backfill-ingest

# Find the actually-ingesting execution. The hourly schedule spawns no-op "defer" executions
# that exit in <1 min while an EARLIER execution keeps ingesting under the run lock — so we must
# query Running executions explicitly (they get buried under dozens of recent defers), and only
# fall back to the latest-started when nothing is running.
RUNNING_JSON=$(az containerapp job execution list -n "$JOB" -g "$RG" \
  --query "[?properties.status=='Running']" -o json 2>/dev/null || echo '[]')
LATEST_JSON=$(az containerapp job execution list -n "$JOB" -g "$RG" \
  --query "reverse(sort_by([],&properties.startTime))[:1]" -o json 2>/dev/null || echo '[]')
ENDPOINT=$(az cosmosdb show -n "${COSMOS_ACCOUNT:-cosmos-firmenbuch-xbjux2hw}" -g "$RG" --query documentEndpoint -o tsv 2>/dev/null)

RUNNING_JSON="$RUNNING_JSON" LATEST_JSON="$LATEST_JSON" ENDPOINT="$ENDPOINT" SA="$SA" uv run python <<'PY'
import json
import os
from datetime import datetime, timezone

from azure.cosmos import CosmosClient
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient

cred = DefaultAzureCredential()

# --- backfill job execution (is it running?) ---------------------------------------------
running = json.loads(os.environ["RUNNING_JSON"] or "[]")
latest = json.loads(os.environ["LATEST_JSON"] or "[]")
ex = running[0] if running else (latest[0] if latest else {})
p = ex.get("properties", {})
print("== backfill job ==")
print(f"  execution : {ex.get('name', '—')}")
print(f"  status    : {p.get('status', '—')}"
      + ("" if running else "   (no execution actively ingesting right now)"))
print(f"  start     : {p.get('startTime', '—')}")

# --- the processed set: backfill checkpoint done-FNRs -------------------------------------
blob = BlobServiceClient(
    f"https://{os.environ['SA']}.blob.core.windows.net", credential=cred
).get_container_client("90-raw")
try:
    raw = blob.get_blob_client("_checkpoints/backfill_ingest.json").download_blob().readall()
    ck = json.loads(raw)
    done = set(ck.get("done_fnrs", []))
    updated = ck.get("updated_at", "—")
except Exception:
    done, updated = set(), None

if updated is None:
    print("\nBackfill has not produced a checkpoint yet (not started, or <200 companies in).")

# --- join active companies vs the done-set, tally per Rechtsform --------------------------
c = (
    CosmosClient(os.environ["ENDPOINT"], credential=cred)
    .get_database_client("firmenbuch")
    .get_container_client("99_registry")
)
total: dict[str, int] = {}
proc: dict[str, int] = {}
for d in c.query_items(
    "SELECT c.fnr, c.rechtsform FROM c WHERE c.status = 'active'",
    enable_cross_partition_query=True,
    max_item_count=2000,
):
    rf = d.get("rechtsform") or "(none)"
    total[rf] = total.get(rf, 0) + 1
    if d["fnr"] in done:
        proc[rf] = proc.get(rf, 0) + 1

# --- render ------------------------------------------------------------------------------
tot_all = sum(total.values())
proc_all = sum(proc.values())
print(f"\n== backfill progress (checkpoint updated: {updated}) ==")
print(f"  ACTIVE companies processed: {proc_all:,} / {tot_all:,}"
      f"  ({100 * proc_all / tot_all:.1f}%)" if tot_all else "  (no active companies)")
print("\n== per Rechtsform (processed / active) ==")
width = 24
for rf in sorted(total, key=lambda k: -total[k]):
    t = total[rf]
    pr = proc.get(rf, 0)
    pct = 100 * pr / t if t else 0
    bar = "█" * round(width * pct / 100)
    print(f"  {rf:<7} {pr:>7,} / {t:>7,}  {pct:5.1f}%  {bar}")
PY
