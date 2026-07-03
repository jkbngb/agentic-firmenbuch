"""Sync the financial-institution registers into ``00_directories`` (issues #15, #17).

Two register families feed the authoritative ``is_financial_institution`` flag:

* **Banks** — the OeNB MFI + NMFI lists (free CC-BY CSVs at a stable URL that **already carry
  the Firmenbuchnummer**). One download, read the FN column, done. Rock-solid.
* **Insurers** — there is no Austrian source that lists insurers *with* the FN, so it is a
  deterministic two-step bridge: the **EIOPA** register gives the AT insurer set + each one's
  **LEI**; **GLEIF** translates each LEI → FN (``entity.registeredAs`` gated on
  ``registeredAt.id == "RA000017"``, the Austrian Firmenbuch). EIOPA has no stable API — the
  register is a SharePoint WebForms app — so the fetch is a stateful POST-scrape.

Because both fetches (and especially the EIOPA scrape) are brittle, every source is wrapped in
the same robustness contract:

* **Snapshot fallback** — a successful, sanity-passing fetch is archived dated to
  ``90-raw/_directories/{source}/{day}.csv`` (lossless history). If a later fetch fails *or*
  fails the row-count sanity gate, we fall back to the most recent archived snapshot instead of
  serving a truncated/empty set. The archive IS the snapshot — it self-heals after one success.
* **Mass-deactivation guard** — a source's entries are only deactivated when that source
  refreshed *and* the drop is small; a sudden mass-vanish (almost always a bad upstream fetch)
  is refused and alerted, never applied.
* **Alerts** — any fetch failure, sanity-gate trip, degraded (snapshot) run, or refused
  mass-deactivation calls the injected ``alert`` hook (an email in production) and is recorded
  in ``report["errors"]`` so the job can exit non-zero.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable

import httpx

from fbl_core.directories import (
    DIRECTORIES_CONTAINER,
    FinancialInstitutionRecord,
    load_fi_directory,
    parse_eiopa_at,
    parse_oenb_list,
)
from fbl_core.lineage import now_utc_z
from fbl_core.logging import get_logger
from fbl_core.storage import RAW_CONTAINER, BlobStoreLike, CosmosStoreLike

__all__ = [
    "DIRECTORIES_CONTAINER",
    "OENB_SOURCES",
    "fetch_eiopa_at",
    "fetch_url",
    "load_fi_directory",
    "resolve_fns_via_gleif",
    "sync_directories",
]

log = get_logger("ingest.directories")

# OeNB bulk lists (monthly). Banks (MFI) + non-MFI BWG credit institutions, both FB-Nr-keyed.
OENB_SOURCES: tuple[tuple[str, str], ...] = (
    ("oenb_mfi", "https://www.oenb.at/docroot/downloads_observ/MFI.csv"),
    ("oenb_nmfi", "https://www.oenb.at/docroot/downloads_observ/NMFI.csv"),
)

EIOPA_REGISTER_URL = "https://register.eiopa.europa.eu/registers/register-of-insurance-undertakings"
GLEIF_RECORDS_URL = "https://api.gleif.org/api/v1/lei-records"
AT_FIRMENBUCH_RA = "RA000017"  # GLEIF registrationAuthority id for the Austrian Firmenbuch

# Row-count sanity gates: a fetch returning fewer joinable rows than this is treated as a bad
# (truncated/empty) fetch → fall back to the last snapshot rather than wiping the flag.
MIN_ROWS: dict[str, int] = {"oenb_mfi": 300, "oenb_nmfi": 25, "eiopa_at": 40}
# Never deactivate more than this fraction of a source's active set in one run (a mass-drop is a
# bad upstream fetch, not real delistings). Small absolute drops are always allowed.
MAX_DEACTIVATE_FRACTION = 0.10
_MIN_DEACTIVATE_ABS = 5

Fetcher = Callable[[], bytes]
Resolver = Callable[[Iterable[str]], dict[str, str]]
Alert = Callable[[str, str], None]
Parsed = tuple[list[FinancialInstitutionRecord], str | None]


def fetch_url(url: str) -> bytes:
    """Plain HTTP GET of a bulk CSV (the OeNB lists; no key needed)."""
    with httpx.Client(timeout=httpx.Timeout(60.0, connect=15.0)) as client:
        resp = client.get(url)
        resp.raise_for_status()
        return resp.content


def fetch_eiopa_at() -> bytes:
    """Fetch the EIOPA insurance-undertakings register, filtered to Home Country = AT.

    EIOPA exposes no API/OData/static file: the register is an ASP.NET WebForms grid whose
    "Export to CSV" is a postback. So we GET the page, harvest the anti-tamper tokens
    (``__VIEWSTATE``/``__EVENTVALIDATION``) and the export control + country-dropdown names, then
    POST them back with the country set to AT. Verified to return the full AT subset as a
    semicolon CSV. Brittle to any EIOPA redeploy — the caller falls back to a snapshot on error."""
    with httpx.Client(timeout=httpx.Timeout(90.0, connect=15.0), follow_redirects=True) as client:
        page = client.get(EIOPA_REGISTER_URL)
        page.raise_for_status()
        html = page.text

        def hidden(field: str) -> str:
            m = re.search(rf'id="{field}"\s+value="([^"]*)"', html) or re.search(
                rf'name="{field}"\s+value="([^"]*)"', html
            )
            if m is None:
                raise RuntimeError(f"EIOPA page: hidden field {field} not found")
            return m.group(1)

        # The country dropdown + CSV-export link share the same control prefix
        # (ctl00$ctl34$g_<GUID>$…). Derive the export postback target from the dropdown name so we
        # don't depend on the exact doPostBack JS form (it uses WebForm_DoPostBackWithOptions).
        country_m = re.search(r'name="(ctl00\$[^"]*\$ddlCountry)"', html)
        if country_m is None:
            raise RuntimeError("EIOPA page: country dropdown not found")
        country_name = country_m.group(1)
        prefix = country_name[: -len("ddlCountry")]
        export_target = prefix + "lkbtnExport"

        form = {
            "__EVENTTARGET": export_target,
            "__EVENTARGUMENT": "",
            "__VIEWSTATE": hidden("__VIEWSTATE"),
            "__VIEWSTATEGENERATOR": hidden("__VIEWSTATEGENERATOR"),
            "__EVENTVALIDATION": hidden("__EVENTVALIDATION"),
            country_name: "AT",
        }
        resp = client.post(EIOPA_REGISTER_URL, data=form)
        resp.raise_for_status()
        return resp.content


def resolve_fns_via_gleif(leis: Iterable[str]) -> dict[str, str]:
    """Map ``{LEI: Firmenbuchnummer}`` via the free GLEIF API, in batches of 200.

    Accepts an FN only when GLEIF reports the registering authority as the Austrian Firmenbuch
    (``entity.registeredAt.id == "RA000017"``) — never a guess. LEIs that don't resolve (blank,
    lapsed, or non-AT authority) simply get no FN and drop out of the joinable set."""
    out: dict[str, str] = {}
    unique = [lei for lei in dict.fromkeys(leis) if lei]
    with httpx.Client(timeout=httpx.Timeout(60.0, connect=15.0)) as client:
        for i in range(0, len(unique), 200):
            batch = unique[i : i + 200]
            resp = client.get(
                GLEIF_RECORDS_URL,
                params={"filter[lei]": ",".join(batch), "page[size]": 200},
                headers={"Accept": "application/vnd.api+json"},
            )
            resp.raise_for_status()
            for rec in resp.json().get("data", []):
                entity = (rec.get("attributes") or {}).get("entity") or {}
                lei = rec.get("id")
                fn = entity.get("registeredAs")
                ra = (entity.get("registeredAt") or {}).get("id")
                if lei and fn and ra == AT_FIRMENBUCH_RA:
                    out[str(lei)] = _normalize_fn(str(fn))
    return out


def _normalize_fn(value: str) -> str:
    """GLEIF ``registeredAs`` for AT is inconsistent — sometimes the bare FN (``31532x``),
    sometimes prefixed (``FN 67427h``). Normalise to the bare lowercase form the rest of the
    system uses (matching the OeNB FB-Nr)."""
    return re.sub(r"^fn\s*", "", value.strip(), flags=re.IGNORECASE).strip().lower()


def _archive_path(source: str, day: str) -> str:
    return f"_directories/{source}/{day}.csv"


def _latest_snapshot(blob: BlobStoreLike, source: str) -> bytes | None:
    """The most recent archived CSV for a source (the fallback when a live fetch is bad)."""
    paths = sorted(p for p in blob.list_paths(RAW_CONTAINER, f"_directories/{source}/"))
    return blob.get_bytes(RAW_CONTAINER, paths[-1]) if paths else None


def _acquire(
    blob: BlobStoreLike,
    source: str,
    fetch: Fetcher,
    parse: Callable[[bytes], Parsed],
    *,
    day: str,
    min_rows: int,
) -> tuple[list[FinancialInstitutionRecord], str | None, bool]:
    """Return ``(records, stand, fresh)`` for a source, with snapshot fallback.

    Tries the live fetch; if it succeeds AND clears the row-count sanity gate, the raw bytes are
    archived (dated) and returned as *fresh*. If the fetch raises OR the gate trips, falls back to
    the most recent archived snapshot (``fresh=False``). Raises only if there is no snapshot to
    fall back to."""
    try:
        data = fetch()
        records, stand = parse(data)
        joinable = [r for r in records if r.fnr]
        if len(joinable) < min_rows:
            raise ValueError(
                f"sanity gate: {len(joinable)} joinable rows < {min_rows} (suspect truncated fetch)"
            )
        blob.put_bytes(RAW_CONTAINER, _archive_path(source, day), data)
        return joinable, stand, True
    except Exception as live_err:
        snap = _latest_snapshot(blob, source)
        if snap is None:
            raise RuntimeError(f"{source}: live fetch failed and no snapshot exists") from live_err
        records, stand = parse(snap)
        log.warning(
            "directories source degraded to snapshot",
            extra={"context": {"source": source, "error": str(live_err)[:200]}},
        )
        return [r for r in records if r.fnr], stand, False


def sync_directories(
    blob: BlobStoreLike,
    cosmos: CosmosStoreLike,
    *,
    fetch: Callable[[str], bytes] = fetch_url,
    today: str | None = None,
    sources: tuple[tuple[str, str], ...] = OENB_SOURCES,
    eiopa_fetch: Fetcher | None = None,
    gleif: Resolver | None = None,
    alert: Alert | None = None,
    min_rows: dict[str, int] | None = None,
) -> dict[str, object]:
    """Download + archive + parse the registers, then robustly reconcile ``00_directories``.

    Banks come from ``sources`` (OeNB). Insurers are synced only when ``eiopa_fetch`` (and
    ``gleif``) are provided — in production the orchestrator wires the live scrape + GLEIF; tests
    inject fakes, so existing OeNB-only tests are unaffected. ``alert`` is called on any anomaly.
    Returns counts + ``degraded``/``errors`` lists; a non-empty ``errors`` means the job should
    exit non-zero."""
    day = today or now_utc_z()[:10]
    gate = min_rows or MIN_ROWS
    seen: dict[str, dict[str, object]] = {}
    refreshed: set[str] = set()
    degraded: list[str] = []
    errors: list[str] = []

    def fail(source: str, msg: str) -> None:
        line = f"{source}: {msg}"
        errors.append(line)
        log.error("directories source failed", extra={"context": {"source": source, "error": msg}})
        if alert is not None:
            alert("[firmenbuch] directory sync FAILED", line)

    def ingest(source: str, fetcher: Fetcher, parse: Callable[[bytes], Parsed]) -> None:
        try:
            records, stand, fresh = _acquire(
                blob, source, fetcher, parse, day=day, min_rows=gate.get(source, 1)
            )
        except Exception as exc:  # no snapshot to fall back to → hard fail for this source
            fail(source, str(exc))
            return
        refreshed.add(source)
        if not fresh:
            degraded.append(source)
            if alert is not None:
                alert(
                    "[firmenbuch] directory sync DEGRADED",
                    f"{source}: live fetch unusable, served the last good snapshot.",
                )
        for rec in records:
            assert rec.fnr is not None  # _acquire returns only joinable rows
            seen[rec.fnr] = {**rec.model_dump(mode="json"), "stand": stand}

    # 1) Banks — OeNB MFI/NMFI. Typed closures (not bare lambdas) so each captures its own
    #    source/url and mypy can infer the Fetcher / parser signatures.
    def ingest_oenb(source: str, url: str) -> None:
        def fetcher() -> bytes:
            return fetch(url)

        def parse(data: bytes) -> Parsed:
            return _parse_oenb(data, source)

        ingest(source, fetcher, parse)

    for source, url in sources:
        ingest_oenb(source, url)

    # 2) Insurers — EIOPA + GLEIF (only when wired).
    if eiopa_fetch is not None:
        resolver = gleif or resolve_fns_via_gleif

        def parse_eiopa(data: bytes) -> Parsed:
            return _parse_eiopa(data, resolver)

        ingest("eiopa_at", eiopa_fetch, parse_eiopa)

    # 3) Reconcile against what's already stored.
    existing = {str(d["fnr"]): d for d in cosmos.iter_all(DIRECTORIES_CONTAINER) if d.get("fnr")}
    new = 0
    for fnr, row in seen.items():
        prev = existing.get(fnr)
        cosmos.upsert(
            DIRECTORIES_CONTAINER,
            {
                **row,
                "id": fnr,
                "fnr": fnr,
                "active": True,
                "first_seen": (prev.get("first_seen") if prev else day) or day,
                "last_seen": day,
            },
        )
        if prev is None:
            new += 1

    # 4) Deactivate delisted entries — ONLY within a source that refreshed this run, and only if
    #    the drop is small (the mass-deactivation guard refuses a suspicious wipe).
    deactivated = 0
    for source in refreshed:
        active_prev = [
            d for d in existing.values() if d.get("source") == source and d.get("active")
        ]
        gone = [d for d in active_prev if str(d["fnr"]) not in seen]
        if (
            active_prev
            and len(gone) > _MIN_DEACTIVATE_ABS
            and len(gone) / len(active_prev) > MAX_DEACTIVATE_FRACTION
        ):
            fail(
                source,
                f"refusing to deactivate {len(gone)}/{len(active_prev)} entries "
                f"(> {int(MAX_DEACTIVATE_FRACTION * 100)}%) — suspect bad fetch; kept active",
            )
            continue
        for d in gone:
            d["active"] = False
            d["deactivated_at"] = day
            cosmos.upsert(DIRECTORIES_CONTAINER, d)
            deactivated += 1

    return {
        "active": len(seen),
        "new": new,
        "deactivated": deactivated,
        "banks": sum(1 for r in seen.values() if r.get("kind") == "bank"),
        "insurers": sum(1 for r in seen.values() if r.get("kind") == "insurer"),
        "degraded": degraded,
        "errors": errors,
    }


def _parse_oenb(data: bytes, source: str) -> Parsed:
    parsed = parse_oenb_list(data, source=source)
    return parsed.records, parsed.stand


def _parse_eiopa(data: bytes, resolver: Resolver) -> Parsed:
    """Parse the EIOPA AT export, then resolve each insurer's LEI → FN via GLEIF."""
    parsed = parse_eiopa_at(data, source="eiopa_at")
    fn_by_lei = resolver(r.lei for r in parsed.records if r.lei)
    resolved = [
        r.model_copy(update={"fnr": fn_by_lei[r.lei]})
        for r in parsed.records
        if r.lei and r.lei in fn_by_lei
    ]
    return resolved, parsed.stand
