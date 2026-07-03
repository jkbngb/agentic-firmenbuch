#!/usr/bin/env python
"""Targeted repair: companies that were uploaded with industry.oenace = null in the first
grind pass (before their text was classified) got journalled as 'done', so the resumed
upload skipped them. Re-patch exactly those whose text now has a class. Idempotent."""
from __future__ import annotations

import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from azure.cosmos import CosmosClient

from fbl_core_at.classification.industry import build_industry_block

HERE = Path(__file__).resolve().parent


def norm(t: str) -> str:
    import re

    return re.sub(r"\s+", " ", t.strip()).casefold()


key = subprocess.check_output(
    ["az", "cosmosdb", "keys", "list", "-n", "cosmos-firmenbuch-xbjux2hw",
     "-g", "rg-firmenbuch-prod", "--query", "primaryMasterKey", "-o", "tsv"], text=True
).strip()
cont = (
    CosmosClient("https://cosmos-firmenbuch-xbjux2hw.documents.azure.com:443/", key)
    .get_database_client("firmenbuch").get_container_client("10_presentation")
)

text_cls = {r["key"]: r["cls08"] for r in (json.loads(x) for x in (HERE / "texts2.jsonl").open())}

# the null-but-has-description companies, straight from Cosmos (authoritative)
q = (
    "SELECT c.fnr, c.company.description AS gz FROM c "
    "WHERE IS_DEFINED(c.company.description) AND c.company.description != null "
    "AND (NOT IS_DEFINED(c.industry.oenace) OR c.industry.oenace = null)"
)
targets = list(cont.query_items(q, enable_cross_partition_query=True))
print(f"null-mit-Geschäftszweig: {len(targets):,}")

block_cache: dict[str, dict | None] = {}
todo = []
no_class = 0
for r in targets:
    gz = (r.get("gz") or "").strip()
    k = norm(gz)
    cls = text_cls.get(k)
    if not cls:
        no_class += 1
        continue
    if k not in block_cache:
        block_cache[k] = build_industry_block(gz, cls, "llm")
    todo.append((r["fnr"], block_cache[k]))
print(f"patchbar (Text jetzt klassifiziert): {len(todo):,}; bleibt null (Text unklassifizierbar): {no_class:,}")

done = 0
out = (HERE / "patched_nulls.jsonl").open("a")


def patch(item: tuple[str, dict | None]) -> str:
    fnr, block = item
    cont.patch_item(item=fnr, partition_key=fnr,
                    patch_operations=[{"op": "set", "path": "/industry", "value": block}])
    return fnr


with ThreadPoolExecutor(max_workers=16) as ex:
    for fnr in ex.map(patch, todo):
        out.write(fnr + "\n")
        done += 1
        if done % 5000 == 0:
            out.flush()
            print(f"  {done:,}/{len(todo):,}")
out.close()
print(f"fertig: {done:,} gepatcht")
