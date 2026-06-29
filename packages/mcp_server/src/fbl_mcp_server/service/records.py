"""Per-company record tools: ``get_company_details``, ``get_company_history``,
``get_full_record``, and the self-describing ``describe_fields`` catalog (§9)."""

from __future__ import annotations

from typing import Any

from fbl_core.directories import load_fi_directory
from fbl_core.models import CompanyCard, PublicProvenance
from fbl_core.storage import CosmosStoreLike

from ..errors import NotFound
from ._common import (
    _BL_CODE_TO_NAME,
    CONSOLIDATED,
    DERIVED,
    _financial_institution,
    _g,
    _provenance,
    _require,
    _strip_internal,
)


def get_company_details(cosmos: CosmosStoreLike, fnr: str) -> dict[str, Any]:
    """Full served document for one company (internal hash chain omitted, §8.7)."""
    doc = _require(cosmos, fnr)
    result = _strip_internal(doc)
    # Serve-time: stamp each filing with a resolvable document_ref the agent can pass straight to
    # get_document for a signed download link — derived from data already in the record (no
    # re-grind, mirrors the FI flag). One filing per Stichtag, so {fnr}:{stichtag} is unique.
    for f in result.get("filings", []):
        if isinstance(f, dict) and f.get("stichtag"):
            f["document_ref"] = f"{fnr}:{f['stichtag']}"
    fi = _financial_institution(doc, load_fi_directory(cosmos))
    if fi is not None:
        # Surface the flag at the top of the record so an agent reads it before the (absent)
        # UGB figures, and doesn't mistake "no Bilanz" for "no data" (ROADMAP P2.1).
        result["financial_institution"] = fi
    return {
        "schema_version": doc.get("schema_version", "1.0"),
        "data_version": _g(doc, "provenance", "data_version"),
        "result": result,
        "provenance": _provenance(doc).model_dump(mode="json"),
    }


# Card field name -> stored history key, so agents can ask for the name they saw on the card.
_METRIC_ALIASES = {"revenue": "umsatzerloese"}


def get_company_history(
    cosmos: CosmosStoreLike, fnr: str, metrics: list[str] | None = None
) -> dict[str, Any]:
    """Per-metric histories (absolutes + ratios) for one company."""
    doc = _require(cosmos, fnr)
    bilanz = _g(doc, "financials", "bilanz") or {}
    guv = _g(doc, "financials", "guv") or {}
    ratios = doc.get("ratios") or {}
    available = {**bilanz, **guv, **{k: v for k, v in ratios.items() if isinstance(v, dict)}}
    # Accept the search-card metric names as aliases for the stored UGB keys (the eval found
    # `revenue` returned nothing because the stored series is `umsatzerloese`).
    for alias, stored in _METRIC_ALIASES.items():
        if alias not in available and stored in available:
            available[alias] = available[stored]
    wanted = metrics or list(available)
    out = {}
    for name in wanted:
        ms = available.get(name)
        if not isinstance(ms, dict):
            continue
        # Expose the official UGB code + §-ref alongside the series (Part A.3).
        out[name] = {
            "history": ms.get("history", {}),
            "source_codes": ms.get("source_codes", []),
            "source_codes_by_year": ms.get("source_codes_by_year", {}),
            "ugb_paragraph": ms.get("paragraph_ref"),
        }
    return {
        "schema_version": doc.get("schema_version", "1.0"),
        "data_version": _g(doc, "provenance", "data_version"),
        "result": {"fnr": fnr, "metrics": out},
        "provenance": _provenance(doc).model_dump(mode="json"),
    }


def get_full_record(
    cosmos: CosmosStoreLike, fnr: str, *, expose_personal_data: bool = False
) -> dict[str, Any]:
    """The COMPLETE per-company record (Part B §5.1): the derived layer, a superset of the
    served document — every position's full year history (`financials.positions`), the
    unknown-code `passthrough`, `completeness`, and `guv_years`.

    Falls back to the consolidated layer if derived is absent. The internal hash chain
    (`meta`) is stripped; officer names stay withheld unless ``expose_personal_data`` is
    set (a documented lawful basis, §8.7) — names are the only allowlisted field NOT
    retrievable here.
    """
    doc = cosmos.get(DERIVED, fnr) or cosmos.get(CONSOLIDATED, fnr)
    if doc is None:
        raise NotFound(f"company {fnr!r} not found")
    record = _strip_internal(doc)
    if not expose_personal_data:
        _redact_officer_names(record)
    data_version = _g(doc, "meta", "data_version")
    return {
        "schema_version": _g(doc, "meta", "schema_version") or "1.0",
        "data_version": data_version,
        "result": record,
        "provenance": PublicProvenance(data_version=data_version).model_dump(mode="json"),
    }


def _redact_officer_names(record: dict[str, Any]) -> None:
    """Strip officer first/last name from a full record (GDPR §8.7); keep birth_year/age."""
    gf = _g(record, "management", "primary_gf")
    if isinstance(gf, dict):
        gf.pop("first_name", None)
        gf.pop("last_name", None)


FIELD_REFERENCE_URL = "https://www.agentic-firmenbuch.at/felder.html"

# Catalog of every field the server can return, by tool tier. Kept in sync with the served
# Pydantic models (fbl_core.models.mcp) and the prose reference at FIELD_REFERENCE_URL /
# docs/FIELD_REFERENCE.md. Lets an agent discover the full field set programmatically instead
# of guessing from a single search card.
_BILANZ_POSITIONS = [
    "bilanzsumme",
    "eigenkapital",
    "verbindlichkeiten",
    "anlagevermoegen",
    "umlaufvermoegen",
    "sachanlagen",
    "finanzanlagen",
    "vorraete",
    "forderungen",
    "cash",
    "rueckstellungen",
    "stammkapital",
    "kapitalruecklagen",
    "gewinnruecklagen",
    "bilanzgewinn_verlust",
]
_GUV_POSITIONS = [
    "umsatzerloese",
    "materialaufwand",
    "personalaufwand",
    "abschreibungen",
    "ebit",
    "ebitda",
    "jahresueberschuss",
]
_RATIOS = [
    "equity_ratio",
    "debt_ratio",
    "debt_to_equity",
    "working_capital_ratio",
    "anlagedeckungsgrad_1",
    "ebit_margin",
    "ebitda_margin",
    "net_margin",
    "personalkostenquote",
    "materialaufwandsquote",
    "roa",
    "roe",
    "capital_profile",
]


def describe_fields() -> dict[str, Any]:
    """Self-describing catalog of every field the server can return (§9).

    Static schema doc — no per-company lookup. Tells an agent which fields exist at each
    tier (search card → full profile → full record), the code tables, and the availability
    rules (when a field is null). Per-company availability is exposed on the records
    themselves: ``has_guv_latest``, ``employees`` (null when unknown), ``filing_years_available``.
    """
    return {
        "schema_version": "1.0",
        "reference_url": FIELD_REFERENCE_URL,
        "tiers": {
            "search_companies": {
                "returns": "compact summary card per hit — NOT the full record",
                "fields": list(CompanyCard.model_fields.keys()),
            },
            "get_company_details": {
                "returns": "full served profile for one company",
                "sections": {
                    "identity": ["fnr", "register_id", "name", "legal_form", "status", "court"],
                    "location": ["country", "bundesland", "city", "postal_code", "street"],
                    "company": [
                        "stammkapital",
                        "first_filing_year",
                        "last_filing_year",
                        "filing_years_available",
                        "founded_year",
                        "founded_source",
                        "description",
                    ],
                    "size": ["gkl", "bilanzsumme_band", "peer_percentiles"],
                    "financials": {
                        "scalars": ["latest_year", "has_guv_latest", "revenue_basis", "latest"],
                        "bilanz_positions": _BILANZ_POSITIONS,
                        "guv_positions": _GUV_POSITIONS,
                    },
                    "ratios": _RATIOS,
                    "growth": ["profile", "method"],
                    "employees": ["latest", "latest_year", "history"],
                    "filings[]": ["stichtag", "format", "parsed", "gkl", "eingereicht", "doc_key"],
                    "events[]": ["registered events (V1: usually empty)"],
                    "management": [
                        "n_signatories_latest",
                        "signatories_stable_years",
                        "primary_manager.age",
                        "primary_manager.birth_year",
                        "primary_manager.role_label",
                        "primary_manager.vertretung",
                    ],
                },
            },
            "get_full_record": {
                "returns": "superset of the profile",
                "adds": [
                    "financials.positions (full 317-position UGB taxonomy)",
                    "financials.passthrough (unknown source codes + history)",
                    "financials.completeness (per-year QA metric)",
                    "financials.guv_years",
                    "management.signatories_history",
                    "derivations (metrics_version + formula registry)",
                ],
            },
        },
        "codes": {
            "bundesland": _BL_CODE_TO_NAME,
            "gkl": {"W": "Kleinst/Mikro", "K": "Klein", "M": "Mittel", "G": "Groß"},
            "legal_form": "profile carries the raw Firmenbuch code; GmbH family = 'GE…' prefix "
            "(GES ≈ 99.7%); the search card labels it 'GmbH'",
        },
        "metric_definitions": {
            "ebit": "Operating result (Betriebserfolg, the §231 Abs 2 UGB operating subtotal) "
            "BEFORE financial result and taxes. The UGB GuV reports no EBIT line, so this is a "
            "simplified approximation; it is NOT strict EBIT (earnings before interest and taxes, "
            "which includes the financial result). For entities with material financial / "
            "participation income (e.g. holdings) the two can differ materially.",
            "ebitda": "ebit (= Betriebserfolg) plus depreciation & amortisation (abschreibungen). "
            "Same operating-result basis and caveat as ebit.",
            "ebit_margin": "ebit / umsatzerloese (operating-result basis, see ebit).",
            "ebitda_margin": "ebitda / umsatzerloese (operating-result basis, see ebit).",
        },
        "availability_rules": [
            "search_companies returns a summary card, not all data — escalate to "
            "get_company_details / get_full_record for the full field set.",
            "guv positions + revenue are present only when has_guv is true (small companies "
            "often file a Bilanz only).",
            "employees is frequently null — the Firmenbuch reports headcount only sparsely.",
            "growth.profile is null until at least 2 comparable years exist.",
            "GDPR: officer names ARE served (public Firmenbuch data, per-query lookup); birth "
            "data is year only (birth_year / age) — never month or day.",
        ],
        "provenance": PublicProvenance().model_dump(mode="json"),
    }
