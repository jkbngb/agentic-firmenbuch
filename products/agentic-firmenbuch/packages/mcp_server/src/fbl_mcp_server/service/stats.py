"""Coverage dashboard + sector taxonomy, served O(1) from the precomputed ``__stats__`` doc.

Aggregates over the whole served universe (~340k docs) must never stream every document into a
request — that blows the timeout and drops the MCP connection (observed live on list_sectors).
This Cosmos SDK build also rejects GROUP BY, so the taxonomy is **precomputed** into a single
``__stats__`` doc by the pipeline (``store_stats``) and served O(1); ``_LIVE_SCAN_MAX`` gates the
Python fallback so the in-memory test store still computes live while production never scans inline.
"""

from __future__ import annotations

from typing import Any

from fbl_core.storage import CosmosStoreLike
from fbl_core_at.classification.industry import OENACE_2008_VERSION, OENACE_VERSION
from fbl_core_at.classification.taxonomy import load_oenace_tree
from fbl_core_at.models import PublicProvenance

from ._common import (
    CONSOLIDATED,
    PRESENTED,
    REGISTRY,
    _all_presented,
    _count_where,
    _g,
)

STATS_ID = "__stats__"
_LIVE_SCAN_MAX = 5000


def coverage_summary(cosmos: CosmosStoreLike) -> dict[str, Any]:
    """Universe coverage: XML vs PDF-only vs none, formats, status, presented count (§11).

    Answers "how many are PDF-only" directly. Read-only aggregation over the registry
    (`known_filings`) and the served layer.
    """
    total = with_xml = pdf_only = none = 0
    by_format: dict[str, int] = {}
    by_status: dict[str, int] = {}
    for doc in cosmos.iter_all(REGISTRY):
        if str(doc.get("id", "")).startswith("__"):  # skip watermark / run lock
            continue
        total += 1
        by_status[doc.get("status", "unknown")] = by_status.get(doc.get("status", "unknown"), 0) + 1
        filings = doc.get("known_filings", []) or []
        formats = {f.get("format") or f.get("dateiendung") for f in filings}
        for f in filings:
            fmt = f.get("format") or f.get("dateiendung") or "unknown"
            by_format[fmt] = by_format.get(fmt, 0) + 1
        if not filings:
            none += 1
        elif formats <= {"pdf"}:
            pdf_only += 1
        else:
            with_xml += 1
    # Count presented docs server-side — materialising all ~340k full presentation docs into a
    # list here OOM-killed the 2Gi refresh-stats job (silent since ~2026-06). Fall back to the
    # streaming count only on the in-memory test store (where the COUNT SQL is ignored).
    pc = _count_where(cosmos, PRESENTED, "NOT STARTSWITH(c.id, '__')", [])
    presented = pc if pc is not None else sum(1 for _ in _all_presented(cosmos))
    parse_success = _parse_success_rates(cosmos)
    return {
        "schema_version": "1.0",
        "result": {
            "total_companies": total,
            "with_xml": with_xml,
            "pdf_only": pdf_only,
            "no_filings": none,
            "filings_by_format": by_format,
            "companies_by_status": by_status,
            "presented_count": presented,
            # Parse-success rate by format/year — the metric that surfaces a silently
            # failing format (e.g. an unhandled schema variant) in production (§11).
            "parse_success_by_format": parse_success["by_format"],
            "parse_success_by_year": parse_success["by_year"],
        },
        "provenance": PublicProvenance().model_dump(mode="json"),
    }


def _parse_success_rates(cosmos: CosmosStoreLike) -> dict[str, dict[str, dict[str, float | int]]]:
    """Parse-success counts by filing format and by fiscal year (§11).

    Reads the consolidated layer, where each ``FilingRef`` records ``parsed`` (False for
    dead-lettered / empty-extract filings). A format whose ``rate`` falls toward 0 is the
    early-warning signal a schema variant has stopped extracting — what would have caught
    the JAb 4.0 regression before it reached the served layer.
    """
    by_format: dict[str, dict[str, float | int]] = {}
    by_year: dict[str, dict[str, float | int]] = {}

    def bump(bucket: dict[str, dict[str, float | int]], key: str, ok: bool) -> None:
        slot = bucket.setdefault(key, {"total": 0, "parsed": 0})
        slot["total"] = int(slot["total"]) + 1
        if ok:
            slot["parsed"] = int(slot["parsed"]) + 1

    for doc in cosmos.iter_all(CONSOLIDATED):
        if str(doc.get("id", "")).startswith("__"):
            continue
        for f in doc.get("filings", []) or []:
            fmt = f.get("format") or "unknown"
            stichtag = f.get("stichtag") or ""
            year = stichtag[:4] if stichtag[:4].isdigit() else "unknown"
            ok = bool(f.get("parsed"))
            bump(by_format, fmt, ok)
            bump(by_year, year, ok)

    for bucket in (by_format, by_year):
        for slot in bucket.values():
            total = int(slot["total"])
            slot["rate"] = round(int(slot["parsed"]) / total, 4) if total else 0.0
    return {"by_format": by_format, "by_year": by_year}


def _field(row: dict[str, Any], alias: str, *path: str) -> Any:
    """Read a projected alias (real Cosmos) or fall back to the full-doc path (in-memory store,
    which ignores the SELECT projection and returns whole documents)."""
    return row.get(alias) if alias in row else _g(row, *path)


def _compute_sectors(cosmos: CosmosStoreLike) -> dict[str, Any]:
    """Legal-form + size-class + ÖNACE-division counts via a lean projection (no GROUP BY).
    Heavy (touches every served doc) — run offline by store_stats, never in a request.

    ÖNACE divisions are counted for BOTH vintages so a caller can discover which divisions
    actually exist before filtering (and see that e.g. 2025 has no division 45 but 2008 does).
    The 2008 division comes from the ``oenace_2008`` twin when present, else the 2-digit prefix
    of the stored 4-digit ``code_2008`` — so it is populated even before the #34 re-grind."""
    legal_forms: dict[str, int] = {}
    size_classes: dict[str, int] = {}
    div_2025: dict[str, int] = {}
    div_2008: dict[str, int] = {}
    sql = (
        "SELECT c.identity.legal_form AS lf, c.size.gkl AS gkl, "
        "c.industry.oenace.division AS d25, c.branch.oenace.division AS d25b, "
        "c.industry.oenace_2008.division AS d08, c.industry.code_2008 AS c08, "
        "c.branch.code_2008 AS c08b "
        'FROM c WHERE NOT STARTSWITH(c.id, "__")'
    )
    for row in cosmos.query(PRESENTED, sql):
        if str(row.get("id", "")).startswith("__"):  # in-memory store: row is a full doc
            continue
        lf = _field(row, "lf", "identity", "legal_form")
        gkl = _field(row, "gkl", "size", "gkl")
        if lf:
            legal_forms[str(lf)] = legal_forms.get(str(lf), 0) + 1
        if gkl:
            size_classes[str(gkl)] = size_classes.get(str(gkl), 0) + 1
        d25 = _field(row, "d25", "industry", "oenace", "division") or _field(
            row, "d25b", "branch", "oenace", "division"
        )
        if d25:
            div_2025[str(d25)] = div_2025.get(str(d25), 0) + 1
        d08 = _field(row, "d08", "industry", "oenace_2008", "division")
        if not d08:
            code = _field(row, "c08", "industry", "code_2008") or _field(
                row, "c08b", "branch", "code_2008"
            )
            d08 = code.split(".")[0] if isinstance(code, str) and code else None
        if d08:
            div_2008[str(d08)] = div_2008.get(str(d08), 0) + 1
    return {
        "legal_forms": legal_forms,
        "size_classes": size_classes,
        "oenace_divisions_2025": div_2025,
        "oenace_divisions_2008": div_2008,
    }


def _load_stats(cosmos: CosmosStoreLike) -> dict[str, Any] | None:
    doc = cosmos.get(PRESENTED, STATS_ID)
    return (doc or {}).get("stats") if doc else None


def store_stats(cosmos: CosmosStoreLike, *, include_coverage: bool = True) -> dict[str, Any]:
    """Materialise the expensive aggregates into the ``__stats__`` doc so the read tools serve
    them O(1). Called by the pipeline (and a one-off populate); never from a request path."""
    from fbl_core.lineage import now_utc_z

    def _persist(stats: dict[str, Any]) -> None:
        cosmos.upsert(
            PRESENTED,
            {"id": STATS_ID, "fnr": STATS_ID, "stats": stats, "computed_at": now_utc_z()},
        )

    # Persist the cheap sectors aggregate (legal-form/size taxonomy + ÖNACE-division discovery
    # surface) in its OWN upsert first, keeping any prior coverage, so it lands even if the heavy
    # coverage scan below is slow or fails. Coverage is then computed and merged in a second pass.
    existing = _load_stats(cosmos) or {}
    stats: dict[str, Any] = {"sectors": _compute_sectors(cosmos)}
    if existing.get("coverage"):
        stats["coverage"] = existing["coverage"]
    _persist(stats)
    if include_coverage:
        stats["coverage"] = coverage_summary(cosmos)["result"]
        _persist(stats)
    return stats


def coverage(cosmos: CosmosStoreLike) -> dict[str, Any]:
    """Coverage dashboard served from the precomputed ``__stats__`` doc (O(1)). The live
    computation (``coverage_summary``) triple-scans ~340k docs, so it only runs on a
    small/test store; in production a missing stats doc returns ``pending`` rather than
    stalling the request."""
    stats = _load_stats(cosmos)
    if stats and stats.get("coverage"):
        result: dict[str, Any] = stats["coverage"]
    elif (_count_where(cosmos, REGISTRY, "NOT STARTSWITH(c.id, '__')", []) or 0) <= _LIVE_SCAN_MAX:
        result = coverage_summary(cosmos)["result"]
    else:
        result = {"pending": True}
    return {
        "schema_version": "1.0",
        "result": result,
        "provenance": PublicProvenance().model_dump(mode="json"),
    }


def _divisions_with_labels(counts: dict[str, int], year: int) -> dict[str, dict[str, Any]]:
    """Attach the official DE/EN division titles to the counted divisions (sorted by code)."""
    tree = load_oenace_tree(year)
    out: dict[str, dict[str, Any]] = {}
    for div, n in sorted(counts.items()):
        node = tree.get(div)
        out[div] = {
            "count": n,
            "label_de": node.title_de if node else None,
            "label_en": node.title_en if node else None,
        }
    return out


def list_sectors(cosmos: CosmosStoreLike) -> dict[str, Any]:
    """Taxonomy: legal forms + size classes + ÖNACE divisions (per vintage) with counts. Served
    from the precomputed ``__stats__`` doc (O(1)); falls back to a live scan only on a small/test
    store. The ÖNACE-division listing is the discovery surface for the ``oenace_*`` filters —
    it shows which divisions actually exist in EACH vintage, so a caller can see that division
    45 (motor-vehicle trade) lives in ÖNACE 2008 and maps to 46/47 in ÖNACE 2025."""
    stats = _load_stats(cosmos)
    if stats and stats.get("sectors"):
        sectors = stats["sectors"]
    elif (_count_where(cosmos, PRESENTED, "NOT STARTSWITH(c.id, '__')", []) or 0) <= _LIVE_SCAN_MAX:
        sectors = _compute_sectors(cosmos)
    else:  # production, stats doc missing — never scan inline
        sectors = {"legal_forms": {}, "size_classes": {}, "pending": True}
    result: dict[str, Any] = {
        "legal_forms": sectors.get("legal_forms", {}),
        "size_classes": sectors.get("size_classes", {}),
        "size_class_labels": {"W": "Mikro/Kleinst", "K": "Klein", "M": "Mittel", "G": "Groß"},
        "oenace_divisions": {
            "note": "ÖNACE divisions present in the served universe, per classification vintage. "
            "The oenace_division/oenace_group/oenace_section filters match BOTH vintages, so "
            "filtering by either code works. Motor-vehicle trade is division 45 in ÖNACE 2008 "
            "and is split across divisions 46/47 in ÖNACE 2025.",
            "2025": {
                "version": OENACE_VERSION,
                "divisions": _divisions_with_labels(sectors.get("oenace_divisions_2025", {}), 2025),
            },
            "2008": {
                "version": OENACE_2008_VERSION,
                "divisions": _divisions_with_labels(sectors.get("oenace_divisions_2008", {}), 2008),
            },
        },
    }
    if sectors.get("pending"):
        result["pending"] = True
    return {
        "schema_version": "1.0",
        "result": result,
        "provenance": PublicProvenance().model_dump(mode="json"),
    }
