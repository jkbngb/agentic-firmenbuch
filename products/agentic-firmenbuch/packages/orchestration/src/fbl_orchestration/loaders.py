"""Load inputs for the processing stages from Blob/Cosmos (§8.8)."""

from __future__ import annotations

from typing import Any

from fbl_core.models import ConsolidatedCompany, MasterData, ParsedFiling
from fbl_core.storage import PARSED_CONTAINER, RAW_CONTAINER, BlobStoreLike, CosmosStoreLike
from fbl_parse import PRODUCER, parse_filing, parse_pdf_only


def parse_all(blob: BlobStoreLike, fnr: str, *, run_id: str = "adhoc") -> list[ParsedFiling]:
    """Parse every raw filing for *fnr* from ``90-raw`` into ``ParsedFiling``s.

    **Write-through ``70-parsed`` cache (§3.4, §5.1).** Each parsed filing is persisted to the
    ``70-parsed`` layer as ``{fnr}/{stichtag}.json``. On a re-run a cached doc produced by the
    **current parser version** (``meta.producer == PRODUCER``) is reused, skipping the XML
    re-parse — so reprocessing (re-consolidate/derive after a logic change) doesn't re-parse
    200k+ filings. A parser-version bump (or a corrupt/old-schema cache entry) invalidates it
    and re-parses + overwrites. The layer is always re-derivable from the immutable raw, so it
    is safe to drop and rebuild. (Prefers the XML per Stichtag; a PDF-only Stichtag → linked stub.)
    """
    paths = blob.list_paths(RAW_CONTAINER, f"{fnr}/")
    by_stichtag: dict[str, dict[str, str]] = {}
    for path in paths:
        parts = path.split("/")
        if len(parts) != 3 or parts[1] == "master":
            continue  # skip master/ and manifests at other depths
        _, stichtag, filename = parts
        if filename.startswith("_"):
            continue  # manifest
        ext = filename.rsplit(".", 1)[-1].lower()
        by_stichtag.setdefault(stichtag, {})[ext] = path

    filings: list[ParsedFiling] = []
    for stichtag, arts in sorted(by_stichtag.items()):
        cache_path = f"{fnr}/{stichtag}.json"
        cached = blob.get_json(PARSED_CONTAINER, cache_path)
        if cached is not None and cached.get("meta", {}).get("producer") == PRODUCER:
            try:
                filings.append(ParsedFiling.model_validate(cached))
                continue  # cache hit (current parser version) — no re-parse
            except Exception:  # schema drift in an old cache entry → fall through and re-parse
                pass
        if "xml" in arts:
            data = blob.get_bytes(RAW_CONTAINER, arts["xml"])
            if data is None:
                continue
            pf = parse_filing(data, run_id=run_id, stichtag_hint=stichtag, fnr_hint=fnr)
        elif "pdf" in arts:
            pf = parse_pdf_only(fnr, stichtag, run_id=run_id)
        else:
            continue
        blob.put_json(PARSED_CONTAINER, cache_path, pf.model_dump(mode="json"))
        filings.append(pf)
    return filings


def load_master(blob: BlobStoreLike, fnr: str) -> MasterData | None:
    """Load the most recent archived ``auszug`` extract as canonical MasterData."""
    paths = sorted(
        p for p in blob.list_paths(RAW_CONTAINER, f"{fnr}/master/") if p.endswith(".json")
    )
    if not paths:
        return None
    raw = blob.get_json(RAW_CONTAINER, paths[-1])
    if raw is None:
        return None
    return _auszug_json_to_master(raw, fnr)


def _auszug_json_to_master(raw: dict[str, Any], fnr: str) -> MasterData:
    """Map an archived AuszugKurz JSON to MasterData without importing the API package."""
    from fbl_core.austria import bundesland_from_plz
    from fbl_core.models import Location, Manager, Money

    plz = raw.get("postal_code")
    persons = [
        Manager(
            first_name=p.get("first_name"),
            last_name=p.get("last_name"),
            birth_year=p.get("birth_year"),
            role_label=p.get("function_text"),
        )
        for p in raw.get("persons") or []
        if isinstance(p, dict)
    ]
    stammkapital = raw.get("stammkapital")
    return MasterData(
        fnr=fnr,
        name=raw.get("name"),
        legal_form=raw.get("rechtsform_code"),
        location=Location(
            country=raw.get("country") or "AT",
            bundesland=bundesland_from_plz(plz if isinstance(plz, str) else None),
            city=raw.get("city"),
            postal_code=plz if isinstance(plz, str) else None,
        ),
        stammkapital=(
            Money(amount=stammkapital, currency=raw.get("currency") or "EUR")
            if isinstance(stammkapital, int | float)
            else None
        ),
        description=raw.get("geschaeftszweig"),
        persons=persons,
    )


def load_prev(cosmos: CosmosStoreLike, fnr: str) -> ConsolidatedCompany | None:
    """Load the previous consolidated doc for supersedes/data_version chaining."""
    raw = cosmos.get("50_consolidated", fnr)
    return ConsolidatedCompany.model_validate(raw) if raw is not None else None
