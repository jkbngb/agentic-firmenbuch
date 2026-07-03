"""Coverage dashboard + sector taxonomy, served O(1) from the precomputed ``__stats__`` doc.

Aggregates over the whole served universe (~340k docs) must never stream every document into a
request — that blows the timeout and drops the MCP connection (observed live on list_sectors).
This Cosmos SDK build also rejects GROUP BY, so the taxonomy is **precomputed** into a single
``__stats__`` doc by the pipeline (``store_stats``) and served O(1); ``_LIVE_SCAN_MAX`` gates the
Python fallback so the in-memory test store still computes live while production never scans inline.
"""

from __future__ import annotations

from typing import Any

from fbl_core.models import PublicProvenance
from fbl_core.storage import CosmosStoreLike

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
    presented = sum(1 for _ in _all_presented(cosmos))
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


def _compute_sectors(cosmos: CosmosStoreLike) -> dict[str, dict[str, int]]:
    """Legal-form + size-class counts via a lean two-field projection (no GROUP BY). Heavy
    (touches every served doc) — run offline by store_stats, never in a request."""
    legal_forms: dict[str, int] = {}
    size_classes: dict[str, int] = {}
    sql = (
        "SELECT c.identity.legal_form AS lf, c.size.gkl AS gkl "
        'FROM c WHERE NOT STARTSWITH(c.id, "__")'
    )
    for row in cosmos.query(PRESENTED, sql):
        if str(row.get("id", "")).startswith("__"):  # in-memory store: row is a full doc
            continue
        lf = row.get("lf") if "lf" in row else _g(row, "identity", "legal_form")
        gkl = row.get("gkl") if "gkl" in row else _g(row, "size", "gkl")
        if lf:
            legal_forms[str(lf)] = legal_forms.get(str(lf), 0) + 1
        if gkl:
            size_classes[str(gkl)] = size_classes.get(str(gkl), 0) + 1
    return {"legal_forms": legal_forms, "size_classes": size_classes}


def _load_stats(cosmos: CosmosStoreLike) -> dict[str, Any] | None:
    doc = cosmos.get(PRESENTED, STATS_ID)
    return (doc or {}).get("stats") if doc else None


def store_stats(cosmos: CosmosStoreLike, *, include_coverage: bool = True) -> dict[str, Any]:
    """Materialise the expensive aggregates into the ``__stats__`` doc so the read tools serve
    them O(1). Called by the pipeline (and a one-off populate); never from a request path."""
    from fbl_core.lineage import now_utc_z

    stats: dict[str, Any] = {"sectors": _compute_sectors(cosmos)}
    if include_coverage:
        stats["coverage"] = coverage_summary(cosmos)["result"]
    cosmos.upsert(
        PRESENTED,
        {"id": STATS_ID, "fnr": STATS_ID, "stats": stats, "computed_at": now_utc_z()},
    )
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


def list_sectors(cosmos: CosmosStoreLike) -> dict[str, Any]:
    """v1 taxonomy: legal forms + size classes with counts. Served from the precomputed
    ``__stats__`` doc (O(1)); falls back to a live scan only on a small/test store."""
    stats = _load_stats(cosmos)
    if stats and stats.get("sectors"):
        sectors = stats["sectors"]
    elif (_count_where(cosmos, PRESENTED, "NOT STARTSWITH(c.id, '__')", []) or 0) <= _LIVE_SCAN_MAX:
        sectors = _compute_sectors(cosmos)
    else:  # production, stats doc missing — never scan inline
        sectors = {"legal_forms": {}, "size_classes": {}, "pending": True}
    return {
        "schema_version": "1.0",
        "result": {
            **sectors,
            "size_class_labels": {"W": "Mikro/Kleinst", "K": "Klein", "M": "Mittel", "G": "Groß"},
        },
        "provenance": PublicProvenance().model_dump(mode="json"),
    }
