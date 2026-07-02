#!/usr/bin/env python
"""Backfill operating_result + ebit_strict + ebit_strict_margin onto served docs (#6).

The values already exist in the derived layer (financials.guv.ebit = Betriebserfolg;
financials.positions.{ergebnis_vor_steuern, zinsen_und_aehnliche_aufwendungen}), so we compute
the new fields from there and patch 10_presentation directly — no re-parse. Series are enriched
with the same growth helper the pipeline uses, so a backfilled doc matches a freshly-presented
one. Idempotent; companies re-presented later by the daily pipeline get the identical result.
"""
from __future__ import annotations

import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from azure.cosmos import CosmosClient

from fbl_core.models import MetricSeries
from fbl_derive.growth import compute_growth

HERE = Path(__file__).resolve().parent
HORIZONS = [1, 3, 5]

key = subprocess.check_output(
    ["az", "cosmosdb", "keys", "list", "-n", "cosmos-firmenbuch-xbjux2hw",
     "-g", "rg-firmenbuch-prod", "--query", "primaryMasterKey", "-o", "tsv"], text=True
).strip()
db = CosmosClient("https://cosmos-firmenbuch-xbjux2hw.documents.azure.com:443/", key).get_database_client("firmenbuch")
derived = db.get_container_client("30_derived")
presented = db.get_container_client("10_presentation")


def _hist(series: dict | None) -> dict[int, float]:
    if not series:
        return {}
    return {int(y): float(v) for y, v in (series.get("history") or {}).items() if v is not None}


def _enriched(history: dict[int, float]) -> dict | None:
    if not history:
        return None
    ly = max(history)
    ms = MetricSeries(latest=history[ly], latest_year=ly, history=history)
    return compute_growth(ms, HORIZONS).model_dump(mode="json")


def build(doc: dict) -> dict | None:
    fin = doc.get("financials") or {}
    guv = fin.get("guv") or {}
    positions = fin.get("positions") or {}
    ops = {int(y): float(v) for y, v in ((guv.get("ebit") or {}).get("history") or {}).items()
           if v is not None}
    if not ops:
        return None  # no GuV -> nothing to do
    ebt = _hist(positions.get("ergebnis_vor_steuern"))
    interest = _hist(positions.get("zinsen_und_aehnliche_aufwendungen"))
    umsatz = _hist(guv.get("umsatzerloese"))

    ops_block = {y: ops[y] for y in ops}
    strict = {y: ebt[y] - interest[y] for y in ebt if y in interest}
    margin = {y: strict[y] / umsatz[y] for y in strict if umsatz.get(y)}

    return {
        "fnr": doc["fnr"],
        "operating_result": _enriched(ops_block),
        "ebit_strict": _enriched(strict),
        "ebit_strict_margin": _enriched(margin),
    }


def patch(rec: dict) -> str:
    ops: list[dict] = [
        {"op": "set", "path": "/financials/guv/operating_result", "value": rec["operating_result"]}
    ]
    if rec["ebit_strict"] is not None:
        ops.append({"op": "set", "path": "/financials/guv/ebit_strict", "value": rec["ebit_strict"]})
    if rec["ebit_strict_margin"] is not None:
        ops.append(
            {"op": "set", "path": "/ratios/ebit_strict_margin", "value": rec["ebit_strict_margin"]}
        )
    presented.patch_item(item=rec["fnr"], partition_key=rec["fnr"], patch_operations=ops)
    return str(rec["fnr"])


def main() -> None:
    done = set()
    jf = HERE / "backfill_ebit.jsonl"
    if jf.exists():
        done = {json.loads(x)["fnr"] for x in jf.open()}
    q = "SELECT c.fnr, c.financials FROM c WHERE IS_DEFINED(c.financials.guv.ebit)"
    recs = []
    for doc in derived.query_items(q, enable_cross_partition_query=True):
        if doc["fnr"] in done:
            continue
        r = build(doc)
        if r:
            recs.append(r)
    print(f"zu patchen: {len(recs)} (mit ebit_strict: {sum(1 for r in recs if r['ebit_strict'])})")
    out = jf.open("a")
    n = 0
    with ThreadPoolExecutor(max_workers=16) as ex:
        for fnr in ex.map(patch, recs):
            out.write(json.dumps({"fnr": fnr}) + "\n")
            n += 1
            if n % 2000 == 0:
                out.flush()
                print(f"  {n}/{len(recs)}", flush=True)
    out.close()
    print(f"fertig: {n} Firmen gepatcht")


if __name__ == "__main__":
    main()
