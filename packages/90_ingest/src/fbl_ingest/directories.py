"""Sync the OeNB financial-institution registers into ``00_directories`` (issue #15).

Downloads the OeNB MFI + NMFI lists (free CC-BY bulk CSVs), archives each verbatim + **dated**
(lossless history, §5.1 — never overwritten), parses them (``fbl_core.directories``), and
reconciles ``00_directories``: every currently-listed institution is upserted **active**; one
that dropped off the list is marked **inactive** (licence lost) but KEPT for history. The MCP
serves the ``is_financial_institution`` flag from the active rows — authoritative and
Firmenbuchnummer-keyed, replacing the lossy name heuristic.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx

from fbl_core.directories import DIRECTORIES_CONTAINER, load_fi_directory, parse_oenb_list
from fbl_core.lineage import now_utc_z
from fbl_core.storage import RAW_CONTAINER, BlobStoreLike, CosmosStoreLike

__all__ = [
    "DIRECTORIES_CONTAINER",
    "OENB_SOURCES",
    "fetch_url",
    "load_fi_directory",
    "sync_directories",
]

# OeNB bulk lists (monthly). Banks (MFI) + non-MFI BWG credit institutions, both FB-Nr-keyed.
OENB_SOURCES: tuple[tuple[str, str], ...] = (
    ("oenb_mfi", "https://www.oenb.at/docroot/downloads_observ/MFI.csv"),
    ("oenb_nmfi", "https://www.oenb.at/docroot/downloads_observ/NMFI.csv"),
)

Fetcher = Callable[[str], bytes]


def fetch_url(url: str) -> bytes:
    """Default fetcher: a plain HTTP GET of the bulk CSV (no key, no API needed)."""
    with httpx.Client(timeout=httpx.Timeout(60.0, connect=15.0)) as client:
        resp = client.get(url)
        resp.raise_for_status()
        return resp.content


def sync_directories(
    blob: BlobStoreLike,
    cosmos: CosmosStoreLike,
    *,
    fetch: Fetcher = fetch_url,
    today: str | None = None,
    sources: tuple[tuple[str, str], ...] = OENB_SOURCES,
) -> dict[str, int]:
    """Download + archive + parse the OeNB lists, then full-reconcile ``00_directories``.

    Returns counts ``{active, new, deactivated}``. ``fetch``/``today``/``sources`` are injectable
    so this unit-tests offline."""
    day = today or now_utc_z()[:10]

    # 1) download + archive verbatim (dated, lossless) + parse → the current active set by FN.
    seen: dict[str, dict[str, object]] = {}
    for source, url in sources:
        data = fetch(url)
        blob.put_bytes(RAW_CONTAINER, f"_directories/{source}/{day}.csv", data)
        parsed = parse_oenb_list(data, source=source)
        for rec in parsed.records:
            if rec.fnr is None:
                continue  # no Firmenbuch entry → can't join to a company (still in the raw archive)
            seen[rec.fnr] = {**rec.model_dump(mode="json"), "stand": parsed.stand}

    # 2) reconcile against what's already stored.
    existing = {str(d["fnr"]): d for d in cosmos.iter_all(DIRECTORIES_CONTAINER) if d.get("fnr")}
    report = {"active": 0, "new": 0, "deactivated": 0}

    for fnr, row in seen.items():
        prev = existing.get(fnr)
        doc = {
            **row,
            "id": fnr,
            "fnr": fnr,
            "active": True,
            "first_seen": (prev.get("first_seen") if prev else day) or day,
            "last_seen": day,
        }
        cosmos.upsert(DIRECTORIES_CONTAINER, doc)
        report["active"] += 1
        if prev is None:
            report["new"] += 1

    # 3) licence lost: was active, no longer listed → deactivate (kept for history).
    for fnr, prev in existing.items():
        if fnr not in seen and prev.get("active"):
            prev["active"] = False
            prev["deactivated_at"] = day
            cosmos.upsert(DIRECTORIES_CONTAINER, prev)
            report["deactivated"] += 1

    return report
