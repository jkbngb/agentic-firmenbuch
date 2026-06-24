#!/usr/bin/env bash
# Progress of the bulk backfill-PROCESS (raw → consolidated → derived → presented). The form
# scope is auto-detected from the job (PROCESS_RECHTSFORMEN — now all Rechtsformen). Shows
# both phases, percentages, a live rate, and an ETA.
#   ./scripts/process-status.sh            # 30s sample window for the rate
#   ./scripts/process-status.sh 60         # longer window = steadier rate
#
# Two-phase by design (§15a.1): Phase A consolidates ALL companies, THEN Phase B derives +
# presents them — so `10_presentation` stays 0 until Phase A is complete, then fills. The
# meaningful progress signal is therefore the checkpoint counts, not the served layer.
# Reads the process checkpoint (90-raw/_checkpoints/backfill_process.json) twice, SAMPLE
# seconds apart, to estimate the current rate.
set -euo pipefail
cd "$(dirname "$0")/.."
RG=${RG:-rg-firmenbuch-prod}
SA=${SA:-stfirmenbuchxbjux2hw}
JOB=job-firmenbuch-backfill-process
SAMPLE=${1:-30}

RUNNING_JSON=$(az containerapp job execution list -n "$JOB" -g "$RG" \
  --query "[?properties.status=='Running']" -o json 2>/dev/null || echo '[]')
LATEST_JSON=$(az containerapp job execution list -n "$JOB" -g "$RG" \
  --query "reverse(sort_by([],&properties.startTime))[:1]" -o json 2>/dev/null || echo '[]')
ENDPOINT=$(az cosmosdb show -n "${COSMOS_ACCOUNT:-cosmos-firmenbuch-xbjux2hw}" -g "$RG" --query documentEndpoint -o tsv 2>/dev/null)
# Auto-detect the form scope from the job so the denominator is always right (it widened
# from GES-only to all Rechtsformen); falls back to the env var, then GES.
FORMS=$(az containerapp job show -n "$JOB" -g "$RG" --query "properties.template.containers[0].env[?name=='PROCESS_RECHTSFORMEN'].value | [0]" -o tsv 2>/dev/null)

RUNNING_JSON="$RUNNING_JSON" LATEST_JSON="$LATEST_JSON" ENDPOINT="$ENDPOINT" SA="$SA" SAMPLE="$SAMPLE" \
PROCESS_RECHTSFORMEN="${PROCESS_RECHTSFORMEN:-${FORMS:-GES}}" \
uv run python <<'PY'
import json
import os
import time

from azure.cosmos import CosmosClient
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient

cred = DefaultAzureCredential()
sample = int(os.environ["SAMPLE"])

# --- process job execution (is it running?) ----------------------------------------------
running = json.loads(os.environ["RUNNING_JSON"] or "[]")
latest = json.loads(os.environ["LATEST_JSON"] or "[]")
ex = running[0] if running else (latest[0] if latest else {})
p = ex.get("properties", {})
print("== backfill-process job ==")
print(f"  execution : {ex.get('name', '—')}")
print(f"  status    : {p.get('status', '—')}"
      + ("" if running else "   (no run actively processing right now)"))
print(f"  start     : {p.get('startTime', '—')}")

db = CosmosClient(os.environ["ENDPOINT"], credential=cred).get_database_client("firmenbuch")
reg = db.get_container_client("99_registry")

# --- worklist size: active GES (GmbH) ----------------------------------------------------
forms = [f.strip() for f in os.environ.get("PROCESS_RECHTSFORMEN", "GES").split(",") if f.strip()]
qmarks = " OR ".join(f"c.rechtsform = '{f}'" for f in forms)
N = list(reg.query_items(
    f"SELECT VALUE COUNT(1) FROM c WHERE c.status = 'active' AND ({qmarks})",
    enable_cross_partition_query=True,
))[0]

blob = BlobServiceClient(
    f"https://{os.environ['SA']}.blob.core.windows.net", credential=cred
).get_container_client("90-raw")


# Live counts straight from Cosmos — these update per company (every upsert), unlike the
# checkpoint which only saves every 200, so they give an accurate short-window rate.
def count(container: str) -> int:
    return list(db.get_container_client(container).query_items(
        "SELECT VALUE COUNT(1) FROM c", enable_cross_partition_query=True))[0]


def ckpt_updated() -> str:
    try:
        raw = blob.get_blob_client("_checkpoints/backfill_process.json").download_blob().readall()
        return json.loads(raw).get("updated_at", "—")
    except Exception:
        return "—"


c0, p0 = count("50_consolidated"), count("10_presentation")
updated = ckpt_updated()

# Which counter is the LIVE frontier? Dead-letters mean 50_consolidated never reaches N exactly,
# so don't insist on c0 == N — once presentation has started (p0 > 0) or consolidation is within
# tolerance of N, Phase B (10_presentation) is what's actually climbing. Tracking the frozen
# consolidation counter here was the "no movement" false alarm.
CONS_DONE_TOL = 300
consolidating = (p0 == 0) and (c0 < N - CONS_DONE_TOL)
phase = "A · Konsolidierung" if consolidating else "B · Aufbereitung (derive+present)"
label = "consolidated" if consolidating else "presented"

print(f"\n  sampling {sample}s for a live rate …")
time.sleep(sample)
c1, p1 = count("50_consolidated"), count("10_presentation")
dc, dp = max(0, c1 - c0), max(0, p1 - p0)
moved = (dc > 0) or (dp > 0)  # progress if EITHER frontier advanced
delta = dc if consolidating else dp
base = c0 if consolidating else p0
remaining = max(0, (N - c1) if consolidating else (N - p1))
rate_min = delta / (sample / 60.0)  # companies/minute
served1 = p1

# --- render ------------------------------------------------------------------------------
def bar(x: int, total: int, width: int = 30) -> str:
    pct = (100 * x / total) if total else 0
    return f"{x:>7,} / {total:>7,}  {pct:5.1f}%  " + "█" * round(width * pct / 100)

print(f"\n== progress ({', '.join(forms)} · checkpoint {updated}) ==")
print(f"  Phase            : {phase}")
print(f"  Phase A (konsol.): {bar(c1, N)}")
print(f"  Phase B (served) : {bar(p1, N)}")
print(f"  10_presentation  : {served1:,}  (the live served layer — fills during Phase B)")

print("\n== rate & ETA ==")
status = p.get("status", "")
if p1 >= N - CONS_DONE_TOL:
    print(f"  ✅ Essentially DONE — 10_presentation ≈ the full universe ({p1:,} / {N:,}). "
          "The small remainder are genuine dead-letters (invalid FNR / no fetchable docs).")
elif not moved:
    # Genuinely nothing moved in the window. Distinguish 'running but checkpoint-quiet' from 'idle'.
    if status == "Running":
        print(f"  No change in {sample}s, but the job is RUNNING — counts move per-upsert, so a "
              "short window can still catch a quiet moment. Re-run, or use a longer window "
              "(`process-status.sh 90`).")
    else:
        print("  No run is processing right now — idle between hourly ticks. The hourly cron "
              "picks it back up; or start one: az containerapp job start -n "
              "job-firmenbuch-backfill-process -g rg-firmenbuch-prod")
else:
    eta_min = (remaining / rate_min) if rate_min > 0 else 0
    eta_h = eta_min / 60.0
    pretty = f"{eta_min:,.0f} min" if eta_min < 90 else f"{eta_h:,.1f} h"
    print(f"  current rate     : {rate_min:,.0f} companies/min  ({label})")
    print(f"  remaining        : {remaining:,}")
    print(f"  ETA              : ~{pretty}")
    if not consolidating:
        print("  note: Phase B is filling 10_presentation now — the playground/MCP go live as this climbs.")
    else:
        print(f"  note: Phase B (derive+present of all {N:,}) follows after consolidation completes.")
PY
