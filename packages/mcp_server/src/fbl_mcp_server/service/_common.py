"""Shared support layer for the MCP read tools (§9).

Generic document helpers, container names, the Bundesland/legal-form code maps, the
financial-institution flag, the search-card builder, and the low-level Cosmos query
primitives. Every tool submodule (search/records/documents/cohort/stats) imports from here;
this module imports from no sibling, so the dependency graph stays acyclic.
"""

from __future__ import annotations

from typing import Any

from fbl_core.classification.industry import (
    build_industry_block,
    industry_from_legacy_branch,
)
from fbl_core.financial_institution import classify_financial_institution
from fbl_core.models import CompanyCard, PublicProvenance
from fbl_core.storage import CosmosStoreLike

from ..errors import NotFound

PRESENTED = "10_presentation"
CONSOLIDATED = "50_consolidated"
DERIVED = "30_derived"
REGISTRY = "99_registry"
MAX_PAGE_SIZE = 100


def _g(doc: dict[str, Any], *path: str) -> Any:
    cur: Any = doc
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _provenance(doc: dict[str, Any]) -> PublicProvenance:
    prov = doc.get("provenance") or {}
    return PublicProvenance(
        data_version=prov.get("data_version"),
        built_at=prov.get("built_at"),
        schema_version=prov.get("schema_version", "1.0"),
    )


def _in_range(value: Any, lo: float | None, hi: float | None) -> bool:
    if lo is None and hi is None:
        return True
    if value is None:
        return False
    if lo is not None and value < lo:
        return False
    return not (hi is not None and value > hi)


def _all_presented(cosmos: CosmosStoreLike) -> list[dict[str, Any]]:
    return [d for d in cosmos.iter_all(PRESENTED) if not str(d.get("id", "")).startswith("__")]


def _require(cosmos: CosmosStoreLike, fnr: str) -> dict[str, Any]:
    doc = cosmos.get(PRESENTED, fnr)
    if doc is None:
        raise NotFound(f"company {fnr!r} not found")
    return doc


def _strip_internal(doc: dict[str, Any]) -> dict[str, Any]:
    # drop the lineage block, the internal event-derivation baseline (issue #16), and Cosmos
    # system fields (_rid/_self/_etag/_ts/_attachments)
    return {
        k: v
        for k, v in doc.items()
        if k not in ("meta", "event_baseline") and not k.startswith("_")
    }


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    mid = len(values) // 2
    if len(values) % 2:
        return values[mid]
    return (values[mid - 1] + values[mid]) / 2


def _scalar(cosmos: CosmosStoreLike, container: str, sql: str, params: list[dict[str, Any]]) -> Any:
    """First row of a query — an int for ``SELECT VALUE COUNT(1)`` on real Cosmos, or a whole
    doc on the in-memory store (which ignores SQL). Callers branch on ``isinstance(_, int)``."""
    return next(iter(cosmos.query(container, sql, params)), 0)


def _count_where(
    cosmos: CosmosStoreLike, container: str, where: str, params: list[dict[str, Any]]
) -> int | None:
    """Server-side ``COUNT(1)``; ``None`` signals the in-memory store (SQL ignored) so the
    caller can fall back to Python over its small dataset."""
    raw = _scalar(cosmos, container, f"SELECT VALUE COUNT(1) FROM c WHERE {where}", params)
    return raw if isinstance(raw, int) else None


def _scalar_floats(
    cosmos: CosmosStoreLike, container: str, sql: str, params: list[dict[str, Any]]
) -> list[float]:
    """Numeric values from a ``SELECT VALUE`` projection (the query Protocol is typed for docs,
    so each value is widened to ``Any`` before the numeric check)."""
    out: list[float] = []
    for v in cosmos.query(container, sql, params):
        val: Any = v
        if isinstance(val, int | float) and not isinstance(val, bool):
            out.append(float(val))
    return out


# --- Bundesland + legal-form code maps -----------------------------------------

_STATUS_SQL = {
    "active": "c.identity.status = 'active'",
    "inactive": "c.identity.status IN ('historical', 'deleted')",
}

# Presentation stores Bundesland as the official one/two-letter code; search filters and the
# UI speak full names. Map both ways so "Wien" matches the stored "W" and cards read "Wien".
_BL_NAME_TO_CODE = {
    "Burgenland": "B",
    "Kärnten": "K",
    "Niederösterreich": "N",
    "Oberösterreich": "O",
    "Salzburg": "S",
    "Steiermark": "St",
    "Tirol": "T",
    "Vorarlberg": "V",
    "Wien": "W",
}
_BL_CODE_TO_NAME = {code: name for name, code in _BL_NAME_TO_CODE.items()}

# Presentation stores the granular Firmenbuch Rechtsform code; the GmbH family is the "GE…"
# prefix (GES is ~99.7% of it). Filters/UI speak "GmbH"; map both directions.
_GMBH_NAMES = {"gmbh", "ges.m.b.h.", "ges.m.b.h", "gesellschaft mit beschränkter haftung"}


def _is_gmbh_filter(value: str | None) -> bool:
    return value is not None and value.strip().lower() in _GMBH_NAMES


def _legal_form_label(code: str | None) -> str | None:
    if code and code.startswith("GE"):
        return "GmbH"
    return code


def _legal_form_matches(doc: dict[str, Any], wanted: str | None) -> bool:
    if wanted is None:
        return True
    code = _g(doc, "identity", "legal_form") or ""
    return code.startswith("GE") if _is_gmbh_filter(wanted) else code == wanted


# --- financial-institution flag + search card ----------------------------------

# Caveat per register kind. Generic fallback for the less common financial-institution types.
_FI_CAVEAT = {
    "bank": "Bank: Rechnungslegung nach BWG (§§43-58), nicht UGB; strukturierte UGB-Kennzahlen "
    "liegen daher nicht vor — der amtliche Jahresabschluss ist als PDF einzusehen.",
    "insurer": "Versicherung: Rechnungslegung nach VAG (§§136-167), nicht UGB; strukturierte "
    "UGB-Kennzahlen liegen daher nicht vor — der amtliche Jahresabschluss ist als PDF einzusehen.",
}


def _fi_caveat(kind: str) -> str:
    return _FI_CAVEAT.get(
        kind,
        f"Reguliertes Finanzinstitut ({kind}): es gelten Sondervorschriften, UGB-Kennzahlen sind "
        "nicht ohne Weiteres vergleichbar.",
    )


def _financial_institution(
    doc: dict[str, Any], directory: dict[str, str] | None = None
) -> dict[str, Any] | None:
    """The served ``financial_institution`` block, or ``None`` for an ordinary company.

    Register-first (ROADMAP P2 / issue #15): if the FN is in *directory* — the authoritative
    OeNB/EIOPA register set (``00_directories``) — use that (``source="register"``, exact kind).
    Only fall back to the name heuristic (``source="heuristic"``) for entries not in any register
    (e.g. foreign branches), so banks the heuristic missed (BAWAG, Oberbank) are still right."""
    fnr = doc.get("fnr")
    kind = (directory or {}).get(str(fnr)) if fnr else None
    if kind is not None:
        return {"kind": kind, "source": "register", "caveat": _fi_caveat(kind)}
    fi = classify_financial_institution(
        _g(doc, "identity", "legal_form"), _g(doc, "identity", "name")
    )
    if fi is None:
        return None
    return {"kind": fi.kind, "source": fi.source, "caveat": fi.caveat}


def industry_block(doc: dict[str, Any]) -> dict[str, Any] | None:
    """The served ``industry`` block (v2, #34): prefer the stored v2 block, translate a
    stored legacy v1 ``branch`` block into the v2 shape during the transition, and as a
    last resort serve the free text alone with ``oenace``/``nace`` = null. Codes are
    NEVER guessed at serve time (the v1 keyword fallback served 2008-lettered sections
    next to 2025-lettered stored ones — an inconsistent contract; gone)."""
    stored = doc.get("industry")
    if isinstance(stored, dict):
        return stored
    legacy = industry_from_legacy_branch(doc.get("branch"))
    if legacy is not None:
        return legacy
    gz = _g(doc, "company", "description")
    if not gz:
        return None
    return build_industry_block(gz, None, "llm")


def _card(doc: dict[str, Any], directory: dict[str, str] | None = None) -> CompanyCard:
    gz = _g(doc, "company", "description")
    # Serve section/division/group + German labels from the same label-correct block the
    # detail view uses (v2 stored → legacy v1 branch → free text); never a serve-time code
    # guess. Symmetric with the oenace_* search filters (#35).
    oenace = (industry_block(doc) or {}).get("oenace") or {}
    return CompanyCard(
        fnr=doc["fnr"],
        name=_g(doc, "identity", "name") or doc["fnr"],
        legal_form=_legal_form_label(_g(doc, "identity", "legal_form")),
        bundesland=_BL_CODE_TO_NAME.get(
            _g(doc, "location", "bundesland"), _g(doc, "location", "bundesland")
        ),
        postal_code=_g(doc, "location", "postal_code"),
        city=_g(doc, "location", "city"),
        street=_g(doc, "location", "street"),
        size_gkl=_g(doc, "size", "gkl"),
        bilanzsumme_band=_g(doc, "size", "bilanzsumme_band"),
        bilanzsumme_latest=_g(doc, "financials", "latest", "bilanzsumme"),
        equity_ratio_latest=_g(doc, "ratios", "equity_ratio", "latest"),
        revenue_latest=_g(doc, "financials", "latest", "revenue"),
        growth_profile=_g(doc, "growth", "profile"),
        has_guv_latest=bool(_g(doc, "financials", "has_guv_latest")),
        manager_name=_g(doc, "management", "primary_manager_name"),
        is_financial_institution=_financial_institution(doc, directory) is not None,
        geschaeftszweig=_g(doc, "industry", "geschaeftszweig")
        or _g(doc, "branch", "geschaeftszweig")
        or gz,
        industry_section=oenace.get("section"),
        oenace_division=oenace.get("division"),
        oenace_division_label=oenace.get("division_label_de"),
        oenace_group=oenace.get("group"),
        oenace_group_label=oenace.get("group_label_de"),
    )
