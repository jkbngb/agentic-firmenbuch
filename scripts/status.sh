#!/usr/bin/env bash
# Live status of the pipeline Job + the 99_registry company count.
#   ./scripts/status.sh
set -euo pipefail
cd "$(dirname "$0")/.."
RG=${RG:-rg-firmenbuch-prod}
JOB=job-firmenbuch-pipeline

# Latest execution as JSON (parsed in Python — robust to missing endTime etc.).
EXEC_JSON=$(az containerapp job execution list -n "$JOB" -g "$RG" \
  --query "reverse(sort_by([],&properties.startTime))[0]" -o json 2>/dev/null)
ENDPOINT=$(az cosmosdb show -n "${COSMOS_ACCOUNT:-cosmos-firmenbuch-xbjux2hw}" -g "$RG" --query documentEndpoint -o tsv 2>/dev/null)

EXEC_JSON="$EXEC_JSON" ENDPOINT="$ENDPOINT" uv run python <<'PY'
import json
import os
from datetime import datetime, timezone
from azure.identity import DefaultAzureCredential
from azure.cosmos import CosmosClient

ex = json.loads(os.environ["EXEC_JSON"] or "{}")
props = ex.get("properties", {})
name, status = ex.get("name", "—"), props.get("status", "—")
start, end = props.get("startTime"), props.get("endTime")
finished = status in ("Succeeded", "Failed", "Stopped", "Degraded")


def parse(ts):
    return datetime.fromisoformat(ts) if ts else None


def fmt(d):
    s = int(d.total_seconds()); h, s = divmod(s, 3600); m, s = divmod(s, 60)
    return f"{h}h {m}m {s}s"


st, en = parse(start), parse(end)
if st and finished:
    end_disp = end or "(finished — no end timestamp from Azure)"
    runtime = fmt((en or datetime.now(timezone.utc)) - st) + " (total)"
elif st:
    end_disp = "— (still running)"
    runtime = fmt(datetime.now(timezone.utc) - st) + " (so far)"
else:
    end_disp, runtime = "—", "—"

print("== pipeline job (latest execution) ==")
print(f"execution : {name}")
print(f"status    : {status}")
print(f"start     : {start or '—'}")
print(f"end       : {end_disp}")
print(f"run time  : {runtime}")

print("\n== 99_registry ==")
cont = (
    CosmosClient(os.environ["ENDPOINT"], credential=DefaultAzureCredential())
    .get_database_client("firmenbuch")
    .get_container_client("99_registry")
)
# Cross-partition Cosmos only supports `SELECT VALUE COUNT(1)` aggregates (no GROUP BY),
# so count each status with its own VALUE-aggregate query.
def count(where: str = "") -> int:
    q = 'SELECT VALUE COUNT(1) FROM c WHERE NOT STARTSWITH(c.id, "__")' + where
    return list(cont.query_items(q, enable_cross_partition_query=True))[0]


# Count each status, then derive total = sum (consistent — no race against the live count).
by_status = {st: count(f" AND c.status = '{st}'") for st in ("active", "historical", "deleted")}
by_status["other"] = count(
    " AND (NOT IS_DEFINED(c.status) OR c.status NOT IN ('active','historical','deleted'))"
)
total = sum(by_status.values())
print(f"companies : {total:,}")
for st in ("active", "historical", "deleted", "other"):
    c = by_status[st]
    if c:
        print(f"  {st:<11}: {c:>9,}  ({(100 * c / total) if total else 0:4.1f}%)")

# Legal-form breakdown: DISTINCT VALUE is allowed cross-partition; count each form.
forms = [
    f for f in cont.query_items(
        "SELECT DISTINCT VALUE c.rechtsform FROM c WHERE NOT STARTSWITH(c.id, '__')",
        enable_cross_partition_query=True,
    )
]
if forms:
    pairs = []
    for f in forms:
        if f is None:
            n = count(" AND NOT IS_DEFINED(c.rechtsform)")
            label = "(none)"
        else:
            n = count(f" AND c.rechtsform = '{f}'")
            label = f
        pairs.append((label, n))
    # Percentages are relative to THIS section's own sum, not the status total: the two
    # sections are independent live snapshots, so during a running sync their totals can
    # differ by the rows inserted in between (otherwise a form could read >100%).
    rf_total = sum(n for _, n in pairs)
    print(f"\n== by rechtsform =={'' if rf_total == total else f'  (snapshot: {rf_total:,})'}")
    for label, n in sorted(pairs, key=lambda p: -p[1]):
        if n:
            print(f"  {label:<11}: {n:>9,}  ({(100 * n / rf_total) if rf_total else 0:4.1f}%)")
PY

# Walk proof-of-life (§15a.1): the company count plateaus while the grind chews dense/empty
# prefixes — so it can look "stuck" when it isn't. The real liveness signal is the checkpoint
# blob's last-modified (rewritten every 500 prefixes) + the latest prefixes_processed log.
echo
echo "== grind proof-of-life =="
SA=${SA:-stfirmenbuchxbjux2hw}
LM=$(az storage blob show --account-name "$SA" --container-name 90-raw \
  --name "_checkpoints/sync_registry_walk.json" --auth-mode login \
  --query "properties.lastModified" -o tsv 2>/dev/null || true)
if [ -n "${LM:-}" ]; then
  # LM is ISO-8601 UTC ("...+00:00"). Parse it AS UTC on both BSD (macOS, -u) and GNU date,
  # else a local-vs-UTC mismatch invents a phantom offset (e.g. CEST = +120 min).
  LMEPOCH=$(date -u -j -f "%Y-%m-%dT%H:%M:%S" "${LM%%+*}" +%s 2>/dev/null || date -u -d "$LM" +%s 2>/dev/null)
  AGE=$(( ( $(date -u +%s) - LMEPOCH ) / 60 ))
  echo "checkpoint  : updated ${LM} (~${AGE} min ago)"
  [ "$AGE" -le 15 ] && echo "verdict     : ALIVE (checkpoint fresh — walk is progressing even if the count is flat)" \
                    || echo "verdict     : STALE >15min — investigate (logs / execution status)"
  # Real "you are here": which legal-form phases are done, and how far through the current
  # form's alphabet (the walk sweeps each Rechtsform, drilling first-letters z->a). This is a
  # meaningful progress view — unlike the raw frontier tail, which hops around as a stack.
  az storage blob download --account-name "$SA" --container-name 90-raw \
    --name "_checkpoints/sync_registry_walk.json" --auth-mode login --file /tmp/fbl_ckpt.json -o none 2>/dev/null \
  && uv run python - <<'PY'
import collections
import json
import string

try:
    _d = json.load(open("/tmp/fbl_ckpt.json"))
except Exception:
    _d = {}
done, f = _d.get("done", []), _d.get("frontier", [])
FORM = {"GES": "GmbH", "AKT": "AG", "KEG": "KG", "OHG": "OHG", "EGE": "EWIV", "EGU": "EU", "": "all-forms"}
done_rf = collections.Counter(k.split("|", 1)[0] for k in done)
front_rf = collections.Counter(rf for rf, _ in f)
# A form is finished once it has done-prefixes and nothing left in the frontier.
finished = sorted(rf for rf in done_rf if rf and rf not in front_rf)
print("phases done : " + (", ".join(f"{rf}({FORM.get(rf, rf)})" for rf in finished) or "—"))
# Current form = the non-catch-all form still being walked (e.g. GES).
cur = next((rf for rf, _ in f if rf), "")
if cur:
    rem = sorted({p[:1].lower() for rf, p in f if rf == cur and p[:1].lower() in string.ascii_lowercase})
    dn = sorted({k.split("|", 1)[1][:1].lower() for k in done if k.startswith(cur + "|")
                 and len(k.split("|", 1)) > 1 and k.split("|", 1)[1][:1].lower() in string.ascii_lowercase})
    pct = round(100 * len(dn) / 26)
    print(f"current     : {cur} ({FORM.get(cur, cur)}) sweep — ~{pct}% of the alphabet done")
    print(f"  letters   : done {''.join(dn) or '—'}  |  REMAINING {' '.join(rem) or '—'} (+digits/symbols)")
pend_forms = [FORM.get(rf, rf) for rf in front_rf if not rf]
if pend_forms:
    print("then        : final all-forms sweep")
print(f"prefixes    : {len(done):,} done cumulative, {len(f):,} pending  (survives restarts)")
if f:
    rf, p = f[-1]
    print(f"position    : {rf} | {p!r}  (raw current query — hops around, not a progress bar)")
PY
else
  echo "checkpoint  : none yet (walk <500 prefixes in, or already completed + cleared)"
fi
