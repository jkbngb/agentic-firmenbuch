"""``present`` — assemble the public document; scope, gating, attribution (§8.7).

GDPR (§8.7): officer **names are withheld** by default; **age_at_signing, current age,
and birth_year (year only)** ARE exposed. Never serve the name, month, or day. Filter
fields are denormalized to shallow indexed paths (§4.1). The internal hash chain is kept
in ``meta`` (stored) but omitted from the served body by the MCP server.
"""

from __future__ import annotations

from fbl_core.lineage import lineage_ref, new_doc_id, stamp
from fbl_core.models import (
    DerivedCompany,
    Meta,
    PresentedCompany,
    PresentedFinancials,
    PresentedManagement,
    PresentedManager,
    PublicProvenance,
)

PRODUCER = "present@1.0.0"

# Bilanz items denormalized into financials.latest for cheap indexed queries (§4.1).
_LATEST_BILANZ = ("bilanzsumme", "eigenkapital", "verbindlichkeiten", "anlagevermoegen")

# Strict no-loss carry-forward (Part B §5.1): `present` is a curated PROJECTION of the
# derived layer. This allowlist is the EXHAUSTIVE, justified set of derived fields that
# are intentionally NOT surfaced in the served document. The layer-completeness test
# (tests/test_layer_completeness.py) fails if any derived leaf is dropped that is NOT
# listed here. Everything on this list except officer names is retrievable in full via
# the MCP `get_full_record` tool (which returns the derived/consolidated record).
PRESENTED_ALLOWLIST: dict[str, str] = {
    # Internal lineage / hash chain — stored in Cosmos, omitted from the served body (§8.7).
    "meta": "internal lineage + content-hash chain; stored, never served",
    # GDPR (§8.7): officer names are withheld unless a documented lawful basis is set.
    "management.primary_gf.first_name": "officer name withheld (GDPR §8.7)",
    "management.primary_gf.last_name": "officer name withheld (GDPR §8.7)",
    # Full-taxonomy detail — curated out of the served doc, retrievable via get_full_record.
    "financials.positions": "full 317-taxonomy position map; retrievable via get_full_record",
    "financials.passthrough": "unknown source codes + history; retrievable via get_full_record",
    "financials.completeness": "per-year item-count QA metric; retrievable via get_full_record",
    "financials.guv_years": "list of GuV years (has_guv_latest is surfaced); via get_full_record",
    # Headline management figures are surfaced (n_signatories_latest, signatories_stable_years);
    # the full per-year count history is curated out, retrievable via get_full_record.
    "management.signatories_history": "per-year signatory-count series; via get_full_record",
    # The ratio/growth formula registry is static documentation, carried on the derived record.
    "derivations": "metrics_version + formula registry; retrievable via get_full_record",
}


def present(
    company: DerivedCompany,
    *,
    expose_personal_data: bool = False,
    status: str | None = None,
    run_id: str = "adhoc",
    current_year: int | None = None,
) -> PresentedCompany:
    """Build the served ``PresentedCompany`` from a ``DerivedCompany``."""
    identity = company.identity.model_dump(mode="json")
    if status is not None:
        identity["status"] = status  # registry is the source of truth for status

    doc = PresentedCompany(
        id=company.identity.fnr,
        fnr=company.identity.fnr,
        schema_version=company.meta.schema_version,
        identity=identity,
        location=company.location.model_dump(mode="json"),
        company=company.company.model_dump(mode="json"),
        size=company.size.model_dump(mode="json"),
        financials=_financials(company),
        ratios=company.ratios.model_dump(mode="json"),
        growth=company.growth.model_dump(mode="json"),
        employees=company.employees.model_dump(mode="json") if company.employees else None,
        filings=[f.model_dump(mode="json") for f in company.filings],
        events=[e.model_dump(mode="json") for e in company.events],
        management=_management(company, expose_personal_data, current_year),
        provenance=PublicProvenance(
            data_version=company.meta.data_version,
            schema_version=company.meta.schema_version,
        ),
        meta=_meta(company, run_id),
    )
    payload = doc.model_dump(mode="json")
    assert doc.meta is not None
    stamp(doc.meta, payload, stage_time_key="presented_at")
    doc.provenance.built_at = doc.meta.timestamps.get("presented_at")
    return doc


def _financials(company: DerivedCompany) -> PresentedFinancials:
    fin = company.financials
    latest: dict[str, float] = {}
    for field in _LATEST_BILANZ:
        ms = fin.bilanz.get(field)
        if ms is not None and ms.latest is not None:
            latest[field] = ms.latest
    revenue = _revenue_latest(company)
    if revenue is not None:
        latest["revenue"] = revenue
    return PresentedFinancials(
        latest_year=fin.latest_year,
        currency=fin.currency,
        has_bilanz=fin.has_bilanz,
        has_guv=fin.has_guv,
        has_guv_latest=fin.has_guv_latest,
        has_xml=fin.has_xml,
        has_pdf_only=fin.has_pdf_only,
        revenue_basis=fin.revenue_basis,
        latest=latest,
        bilanz={k: v.model_dump(mode="json") for k, v in fin.bilanz.items()},
        guv={k: v.model_dump(mode="json") for k, v in fin.guv.items()},
    )


def _revenue_latest(company: DerivedCompany) -> float | None:
    """Latest revenue: Umsatzerlöse if present, else Rohergebnis (records the basis)."""
    guv = company.financials.guv
    for field in ("umsatzerloese", "rohergebnis"):
        ms = guv.get(field)
        if ms is not None and ms.latest is not None:
            return ms.latest
    return None


def _management(
    company: DerivedCompany, expose_personal_data: bool, current_year: int | None
) -> PresentedManagement | None:
    mgmt = company.management
    if mgmt is None:
        return None
    primary = None
    name = None
    if mgmt.primary_gf is not None:
        gf = mgmt.primary_gf
        age = None
        if gf.birth_year is not None and current_year is not None:
            age = current_year - gf.birth_year
        primary = PresentedManager(
            age_at_signing=gf.age_at_signing,
            age=age,
            birth_year=gf.birth_year,  # YEAR ONLY — never month/day
            role_label=gf.role_label,
            vertretung=gf.vertretung,
        )
        if expose_personal_data:  # only with a documented lawful basis (§14)
            parts = [p for p in (gf.first_name, gf.last_name) if p]
            name = " ".join(parts) if parts else None
    return PresentedManagement(
        n_signatories_latest=mgmt.n_signatories_latest,
        signatories_stable_years=mgmt.signatories_stable_years,
        primary_manager=primary,
        primary_manager_name=name,
    )


def _meta(company: DerivedCompany, run_id: str) -> Meta:
    meta = Meta(
        doc_id=new_doc_id(),
        entity_id=company.identity.fnr,
        stage="presented",
        producer=PRODUCER,
        run_id=run_id,
        data_version=company.meta.data_version,
        metrics_version=company.meta.metrics_version,
        lineage=[lineage_ref(company.meta)],
    )
    if company.meta.timestamps:
        meta.timestamps.update(company.meta.timestamps)
    return meta


def present_status_only(
    prev: PresentedCompany, status: str, *, run_id: str = "adhoc"
) -> PresentedCompany:
    """Cheap refresh: update only the denormalized status (dirty_reason=status_change, §15a).

    Re-runs from the existing presented doc — no re-parse/consolidate/derive.
    """
    doc = prev.model_copy(deep=True)
    doc.identity = {**doc.identity, "status": status}
    if doc.meta is not None:
        doc.meta.doc_id = new_doc_id()
        doc.meta.run_id = run_id
        stamp(doc.meta, doc.model_dump(mode="json"), stage_time_key="presented_at")
        doc.provenance.built_at = doc.meta.timestamps.get("presented_at")
    return doc
