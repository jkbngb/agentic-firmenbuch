"""T-VERIFY — measure search latency + RU against the live ``10_presentation`` container.

Parameterized copies of the 2026-07-12 measurement queries (name-CONTAINS count/page,
indexed-filter page, and — once T12 lands — a radius query), printing latency + RU per
query and a before/after markdown table. This is the gate for the whole search-quality
program (TODO_search-quality.md): run it after every phase and paste the table into the PR.

Baseline numbers to beat (measured 2026-07-12, serverless, Germany West Central):

    | Query                                    | Latency   | RU          |
    |------------------------------------------|-----------|-------------|
    | COUNT with CONTAINS(LOWER(name), …)      | 5.7-7.4 s | 4,200-6,500 |
    | Same CONTAINS on an indexed text path    | 0.5 s     | 334         |
    | Full name-search triple (2 COUNTs + page)| 11-16 s   | ~10,600     |
    | Filter page (PLZ prefix + range + ORDER) | 0.19 s    | 187         |

Auth: ``DefaultAzureCredential`` (your ``az login``). Endpoint from ``COSMOS_ENDPOINT``
(default ``https://cosmos-firmenbuch-xbjux2hw.documents.azure.com:443/``).

Usage:
    COSMOS_ENDPOINT=... uv run python scripts/measure_search_perf.py
    uv run python scripts/measure_search_perf.py --name bau --json baseline.json
    uv run python scripts/measure_search_perf.py --compare baseline.json   # A/B table
"""

from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import asdict, dataclass
from typing import Any

DEFAULT_ENDPOINT = "https://cosmos-firmenbuch-xbjux2hw.documents.azure.com:443/"
DATABASE = "firmenbuch"
CONTAINER = "10_presentation"


@dataclass
class Measurement:
    """One query's wall-clock + server-reported RU + a small result signal."""

    label: str
    latency_s: float
    ru: float
    signal: str  # e.g. the COUNT value or the number of rows on the page


def _run(container: Any, label: str, sql: str, params: list[dict[str, Any]]) -> Measurement:
    """Execute one query, draining every page so the RU charge is complete, and sum the
    per-page ``x-ms-request-charge`` the server reports on ``last_response_headers``."""
    started = time.monotonic()
    ru_total = 0.0
    rows: list[Any] = []
    pager = container.query_items(
        query=sql, parameters=params, enable_cross_partition_query=True
    ).by_page()
    for page in pager:
        rows.extend(list(page))
        charge = container.client_connection.last_response_headers.get("x-ms-request-charge", 0)
        ru_total += float(charge)
    latency = time.monotonic() - started
    signal = str(rows[0]) if len(rows) == 1 else f"{len(rows)} rows"
    return Measurement(
        label=label, latency_s=round(latency, 3), ru=round(ru_total, 1), signal=signal
    )


def _where_name_contains_lower(name: str) -> tuple[str, list[dict[str, Any]]]:
    # The OLD (pre-T2) non-index-friendly form.
    return "CONTAINS(LOWER(c.identity.name), @n)", [{"name": "@n", "value": name.lower()}]


def _where_name_contains_ci(name: str) -> tuple[str, list[dict[str, Any]]]:
    # The NEW (T2) 3-arg, index-friendly form.
    return "CONTAINS(c.identity.name, @n, true)", [{"name": "@n", "value": name}]


def measure(container: Any, name: str) -> list[Measurement]:
    """The standard battery. Runs each name query in both the LOWER() and 3-arg forms so a
    single run shows the T1/T2 effect side by side."""
    base = "NOT STARTSWITH(c.id, '__')"
    out: list[Measurement] = []

    for tag, (frag, params) in (
        ("lower()", _where_name_contains_lower(name)),
        ("3-arg ci", _where_name_contains_ci(name)),
    ):
        where = f"{base} AND {frag}"
        count_sql = f"SELECT VALUE COUNT(1) FROM c WHERE {where}"
        out.append(_run(container, f"COUNT name~'{name}' [{tag}]", count_sql, params))
        page_sql = (
            f"SELECT c.fnr, c.identity.name FROM c WHERE {where} "
            f"ORDER BY c.financials.latest.bilanzsumme DESC OFFSET 0 LIMIT 25"
        )
        out.append(_run(container, f"PAGE  name~'{name}' [{tag}]", page_sql, params))

    # An already-indexed filter page (the fast reference path — PLZ prefix + range + ORDER BY).
    filt_where = (
        f"{base} AND STARTSWITH(c.location.postal_code, @plz) "
        "AND c.financials.latest.bilanzsumme >= @lo"
    )
    filt_params = [{"name": "@plz", "value": "10"}, {"name": "@lo", "value": 1_000_000}]
    out.append(
        _run(
            container,
            "PAGE  PLZ 10xx + Bilanzsumme>=1M (indexed ref)",
            f"SELECT c.fnr FROM c WHERE {filt_where} "
            "ORDER BY c.financials.latest.bilanzsumme DESC OFFSET 0 LIMIT 25",
            filt_params,
        )
    )
    return out


def _table(rows: list[Measurement]) -> str:
    lines = ["| Query | Latency (s) | RU | Signal |", "|---|---|---|---|"]
    for m in rows:
        lines.append(f"| {m.label} | {m.latency_s} | {m.ru} | {m.signal} |")
    return "\n".join(lines)


def _compare_table(before: list[dict[str, Any]], after: list[Measurement]) -> str:
    by_label = {m.label: m for m in after}
    lines = [
        "| Query | Before lat/RU | After lat/RU | Δ latency | Δ RU |",
        "|---|---|---|---|---|",
    ]
    for b in before:
        a = by_label.get(b["label"])
        if a is None:
            continue
        dl = round(a.latency_s - b["latency_s"], 3)
        dr = round(a.ru - b["ru"], 1)
        lines.append(
            f"| {b['label']} | {b['latency_s']}/{b['ru']} | {a.latency_s}/{a.ru} | {dl} | {dr} |"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Measure search latency + RU on live Cosmos.")
    parser.add_argument("--name", default="bau", help="substring for the name battery")
    parser.add_argument("--endpoint", default=os.environ.get("COSMOS_ENDPOINT", DEFAULT_ENDPOINT))
    parser.add_argument("--json", help="write raw measurements to this file (for later --compare)")
    parser.add_argument("--compare", help="a prior --json file; print an A/B table instead")
    args = parser.parse_args()

    from azure.cosmos import CosmosClient
    from azure.identity import DefaultAzureCredential

    client = CosmosClient(args.endpoint, credential=DefaultAzureCredential())
    container = client.get_database_client(DATABASE).get_container_client(CONTAINER)

    rows = measure(container, args.name)

    if args.compare:
        with open(args.compare) as fh:
            before = json.load(fh)
        print(_compare_table(before, rows))
    else:
        print(_table(rows))

    if args.json:
        with open(args.json, "w") as fh:
            json.dump([asdict(m) for m in rows], fh, indent=2)
        print(f"\nwrote {args.json}")


if __name__ == "__main__":
    main()
