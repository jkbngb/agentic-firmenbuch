"""Per-company record tools: ``get_company_details``, ``get_company_history``,
``get_full_record``, and the self-describing ``describe_fields`` catalog (§9)."""

from __future__ import annotations

from typing import Any

from fbl_core.storage import CosmosStoreLike
from fbl_core_at.classification.taxonomy import load_oenace_tree
from fbl_core_at.directories import load_fi_directory_cached
from fbl_core_at.models import CompanyCard, PublicProvenance

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
    industry_block,
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
    fi = _financial_institution(doc, load_fi_directory_cached(cosmos))
    if fi is not None:
        # Surface the flag at the top of the record so an agent reads it before the (absent)
        # UGB figures, and doesn't mistake "no Bilanz" for "no data" (ROADMAP P2.1).
        result["financial_institution"] = fi
    # Industry (v2, #34): the stored v2 block, or the legacy v1 branch translated into the
    # v2 shape during the transition. The legacy `branch` key itself is no longer served —
    # a single, consistent contract (breaking change, announced in felder.html).
    result["industry"] = industry_block(doc)
    result.pop("branch", None)
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
# Pydantic models (fbl_core_at.models.mcp) and the prose reference at FIELD_REFERENCE_URL /
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
    "operating_result",
    "ebit_strict",
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
    "ebit_strict_margin",
    "ebitda_margin",
    "net_margin",
    "personalkostenquote",
    "materialaufwandsquote",
    "roa",
    "roe",
    "capital_profile",
]


def _oenace_divisions() -> list[dict[str, str]]:
    """The ÖNACE 2025 division catalog ``[{division, label_de}]`` (~87 entries), sourced from the
    bundled classification tree — NEVER hand-typed. This is what lets the LLM turn an industry
    CONCEPT ("Metallverarbeiter", "tech companies") into the right ``oenace_division`` filter
    instead of guessing at the free-text ``geschaeftszweig``. (T7)"""
    tree = load_oenace_tree(2025)
    return [{"division": code, "label_de": tree.nodes[code].title_de} for code in tree.codes_at(2)]


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
                    "events[]": [
                        "register changes from 2026-07-01: {date, type "
                        "(name_change/seat_change/legal_form_change/capital_change/"
                        "management_change), description, source}",
                        "capital_from/capital_to (capital_change); managers_added[]/"
                        "managers_removed[] as 'ROLE Name' (management_change)",
                    ],
                    "industry": [
                        "geschaeftszweig (Firmenbuch free text, never dropped)",
                        "oenace.{section,division,group} + {level}_label_de/_label_en on every "
                        "level + version (ÖNACE 2025)",
                        "nace.{section,division,group} + {level}_label (EN) + version "
                        "(NACE Rev. 2.1; codes identical to oenace by construction)",
                        "oenace_2008.{section,division,group,class} + {level}_label_de/_label_en "
                        "+ version (ÖNACE 2008) — the vintage the classifier predicted in, "
                        "expanded symmetrically to oenace via the official Statistik Austria "
                        "table; motor-vehicle trade is division 45 here (46/47 in ÖNACE 2025)",
                        "code_2008 (assigned ÖNACE 2008 class), source (lexicon/llm), "
                        "classified_from (geschaeftszweig/name)",
                    ],
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
            "list_events": {
                "returns": "cross-company feed of register changes (Pro), newest first",
                "filters": [
                    "types[] (name_change/seat_change/legal_form_change/capital_change/"
                    "management_change)",
                    "since/until (ISO date; default last 30 days)",
                    "bundesland, oenace_section, oenace_division, legal_form (search facets)",
                    "fnrs[] (watchlist), page, page_size",
                ],
                "event_fields": [
                    "fnr, name, date, type, description, source",
                    "capital_from, capital_to, managers_added[], managers_removed[]",
                    "bundesland, legal_form, industry_section",
                ],
                "note": "derived from the daily change feed; forward-only from 2026-07-01",
            },
            "get_event_stats": {
                "returns": "counts by type and by Bundesland over a window (Pro)",
                "filters": ["since/until (default 30 days)", "same facets as list_events"],
            },
        },
        "codes": {
            "bundesland": _BL_CODE_TO_NAME,
            "gkl": {"W": "Kleinst/Mikro", "K": "Klein", "M": "Mittel", "G": "Groß"},
            "legal_form": "profile carries the raw Firmenbuch code; GmbH family = 'GE…' prefix "
            "(GES ≈ 99.7%); the search card labels it 'GmbH'",
            # ÖNACE 2025 divisions (2-digit) with German titles — pass the `division` value as the
            # search_companies `oenace_division` filter to screen an industry as a CONCEPT (T7).
            "oenace_divisions": _oenace_divisions(),
        },
        "metric_definitions": {
            "ebit": "The UGB operating result (Betriebserfolg, §231 Abs 2), BEFORE financial "
            "result and taxes. Kept as a documented alias of operating_result; it is NOT strict "
            "EBIT. Prefer operating_result (this same figure, correctly named) or ebit_strict "
            "(true EBIT) depending on what you need.",
            "operating_result": "Betriebserfolg (§231 Abs 2), the operating result before the "
            "financial result and taxes. Identical value to ebit, served under its correct name.",
            "ebit_strict": "True EBIT = Ergebnis vor Steuern (pre-tax result) + Zinsaufwand "
            "(interest expense). Includes the financial result, unlike operating_result. Null "
            "when the GuV does not disclose the pre-tax result and interest expense (~7% of GuV "
            "filers). For entities with material financial / participation income (e.g. holdings) "
            "ebit_strict and operating_result can differ materially.",
            "ebitda": "operating_result plus depreciation & amortisation (abschreibungen). "
            "Operating-result basis (like operating_result / ebit), NOT strict-EBIT basis.",
            "ebit_margin": "ebit / umsatzerloese (operating-result basis, = operating_result).",
            "ebit_strict_margin": "ebit_strict / umsatzerloese (true-EBIT basis); null when "
            "ebit_strict is null.",
            "ebitda_margin": "ebitda / umsatzerloese (operating-result basis).",
        },
        "availability_rules": [
            "search_companies returns a summary card, not all data — escalate to "
            "get_company_details / get_full_record for the full field set.",
            "guv positions + revenue are present only when has_guv is true (small companies "
            "often file a Bilanz only).",
            "employees is frequently null — the Firmenbuch reports headcount only sparsely.",
            "growth.profile is null until at least 2 comparable years exist.",
            "ÖNACE codes are ÖNACE 2025 (= NACE Rev. 2.1). The oenace_section/oenace_division/"
            "oenace_group search filters match BOTH ÖNACE 2025 AND ÖNACE 2008, so a query in "
            "either vintage returns results — no dead end for using the older codes. Note the "
            "vintages differ: motor-vehicle trade is division 45 in ÖNACE 2008 but 46/47 in "
            "2025; the old Information section split J→J+K, shifting later section letters. Call "
            "list_sectors to see which divisions exist in each vintage, or filter by the "
            "geschaeftszweig free text.",
            "GDPR: officer names ARE served (public Firmenbuch data, per-query lookup); birth "
            "data is year only (birth_year / age) — never month or day.",
        ],
        "provenance": PublicProvenance().model_dump(mode="json"),
    }
