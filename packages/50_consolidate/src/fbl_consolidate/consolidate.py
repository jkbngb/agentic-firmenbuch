"""``consolidate`` — merge a company's parsed filings + master → ConsolidatedCompany (§8.5).

Builds per-line ``MetricSeries.history`` (facts only; growth is added in ``derive``),
GuV rollups, and the management block. Deterministic: identical inputs → identical
``content_hash``. On rebuild, ``supersedes`` points at the prior doc and ``data_version``
is bumped.
"""

from __future__ import annotations

from collections.abc import Callable

from fbl_core.lineage import lineage_ref, new_doc_id, stamp
from fbl_core.mapping import (
    BILANZ_FIELD_TO_CANONICAL,
    GUV_FIELD_TO_CANONICAL,
    paragraph_ref,
    paragraph_ref_for_canonical,
)
from fbl_core.models import (
    Bilanz,
    CompanyMaster,
    ConsolidatedCompany,
    Court,
    FilingRef,
    Financials,
    GuV,
    Identity,
    Location,
    Management,
    Manager,
    MasterData,
    Meta,
    MetricSeries,
    Money,
    ParsedFiling,
    Size,
)
from fbl_core.models.meta import LineageRef

PRODUCER = "consolidate@1.0.0"

_BILANZ_FIELDS = list(Bilanz.model_fields)
_GUV_FIELDS = [f for f in GuV.model_fields if f != "revenue_basis"]


def _year(stichtag: str | None) -> int | None:
    if not stichtag or len(stichtag) < 4 or not stichtag[:4].isdigit():
        return None
    return int(stichtag[:4])


def _dedupe_latest_per_stichtag(filings: list[ParsedFiling]) -> list[ParsedFiling]:
    """Keep one filing per Stichtag — the last submitted wins (resubmissions, §15b-11)."""
    by_stichtag: dict[str, ParsedFiling] = {}
    for f in filings:
        by_stichtag[f.stichtag] = f  # later entries overwrite earlier (input order = arrival)
    return [by_stichtag[k] for k in sorted(by_stichtag)]


def consolidate(
    fnr: str,
    filings: list[ParsedFiling],
    master: MasterData | None,
    prev: ConsolidatedCompany | None,
    *,
    run_id: str = "adhoc",
) -> ConsolidatedCompany:
    """Consolidate all filings for *fnr* into one company document."""
    parsed = _dedupe_latest_per_stichtag([f for f in filings if f.parsed])
    years = [y for f in parsed if (y := _year(f.stichtag)) is not None]
    latest_year = max(years) if years else None

    bilanz_series = _series_by_field(
        parsed, _BILANZ_FIELDS, lambda f: f.bilanz, BILANZ_FIELD_TO_CANONICAL
    )
    guv_series = _series_by_field(parsed, _GUV_FIELDS, lambda f: f.guv, GUV_FIELD_TO_CANONICAL)

    guv_years = sorted(y for f in parsed if f.has_guv and (y := _year(f.stichtag)) is not None)
    latest_filing = _latest(parsed)
    has_guv_latest = latest_filing.has_guv if latest_filing is not None else False
    revenue_basis = latest_filing.guv.revenue_basis if latest_filing and latest_filing.guv else None

    financials = Financials(
        currency=latest_filing.currency if latest_filing else "EUR",
        latest_year=latest_year,
        has_bilanz=any(f.has_bilanz for f in parsed),
        has_guv=any(f.has_guv for f in parsed),
        has_guv_latest=has_guv_latest,
        guv_years=guv_years,
        has_xml=any(f.format != "pdf" for f in filings),
        has_pdf_only=bool(filings) and all(f.format == "pdf" for f in filings),
        revenue_basis=revenue_basis,
        completeness=_completeness(parsed),
        bilanz=bilanz_series,
        guv=guv_series,
        positions=_positions_series(parsed),
        passthrough=_passthrough_series(parsed),
    )

    employees = _employees_series(parsed)
    management = _management(parsed, master)
    identity, location, company = _master_blocks(fnr, parsed, master, years)

    meta = _build_meta(fnr, parsed, master, prev, run_id)

    company_doc = ConsolidatedCompany(
        identity=identity,
        location=location,
        company=company,
        size=_size_placeholder(latest_filing),
        financials=financials,
        employees=employees,
        management=management,
        filings=[_filing_ref(f) for f in sorted(parsed, key=lambda x: x.stichtag, reverse=True)],
        events=list(master.events) if master else [],
        meta=meta,
    )
    stamp(company_doc.meta, company_doc.model_dump(mode="json"), stage_time_key="consolidated_at")
    # Idempotency: a rebuild that produces identical content keeps the prior
    # data_version (no bump) so re-runs are true no-ops; only a real change bumps
    # data_version and records `supersedes` (§7, §8.8).
    if prev is not None and prev.meta.content_hash == company_doc.meta.content_hash:
        company_doc.meta.data_version = prev.meta.data_version
        company_doc.meta.supersedes = prev.meta.supersedes
    return company_doc


def _series_by_field(
    filings: list[ParsedFiling],
    fields: list[str],
    accessor: Callable[[ParsedFiling], Bilanz | GuV | None],
    field_to_canonical: dict[str, str],
) -> dict[str, MetricSeries]:
    series: dict[str, MetricSeries] = {}
    for field in fields:
        history: dict[int, float] = {}
        codes_by_year: dict[int, list[str]] = {}
        canonical = field_to_canonical.get(field)
        for f in filings:
            obj = accessor(f)
            if obj is None:
                continue
            year = _year(f.stichtag)
            value = getattr(obj, field, None)
            if year is not None and value is not None:
                history[year] = value
                if canonical and (codes := f.position_codes.get(canonical)):
                    codes_by_year[year] = list(codes)
        if history:
            latest_year = max(history)
            ms = MetricSeries(latest=history[latest_year], latest_year=latest_year, history=history)
            _attach_codes(ms, canonical, codes_by_year)
            series[field] = ms
    return series


def _attach_codes(
    series: MetricSeries, canonical: str | None, codes_by_year: dict[int, list[str]]
) -> None:
    """Attach the official source code(s) + §-reference to a position series (§-traceability).

    ``source_codes`` is the union across years; ``source_codes_by_year`` is populated only
    when the code set differs across years (Part A). The §-ref comes from the canonical's
    official HGB code (appendix), falling back to the parsed code for unmapped canonicals.
    """
    if codes_by_year:
        union = sorted({c for codes in codes_by_year.values() for c in codes})
        series.source_codes = union
        distinct = {tuple(sorted(set(codes))) for codes in codes_by_year.values()}
        if len(distinct) > 1:
            series.source_codes_by_year = {y: sorted(set(c)) for y, c in codes_by_year.items()}
    ref = paragraph_ref_for_canonical(canonical) if canonical else None
    if ref is None and series.source_codes:
        ref = paragraph_ref(series.source_codes[0])
    series.paragraph_ref = ref


def _positions_series(filings: list[ParsedFiling]) -> dict[str, MetricSeries]:
    """Full-taxonomy superset: a year-history series for EVERY recognized canonical (Part B).

    Keyed by canonical name (the typed Bilanz/GuV maps are an ergonomic subset of this).
    Nothing recognized is reduced — every position the parser found is carried up with its
    complete history and official source code(s).
    """
    series: dict[str, MetricSeries] = {}
    for canonical in sorted({c for f in filings for c in f.positions}):
        history: dict[int, float] = {}
        codes_by_year: dict[int, list[str]] = {}
        for f in filings:
            year = _year(f.stichtag)
            value = f.positions.get(canonical)
            if year is not None and value is not None:
                history[year] = value
                if codes := f.position_codes.get(canonical):
                    codes_by_year[year] = list(codes)
        if history:
            latest_year = max(history)
            ms = MetricSeries(latest=history[latest_year], latest_year=latest_year, history=history)
            _attach_codes(ms, canonical, codes_by_year)
            series[canonical] = ms
    return series


def _passthrough_series(filings: list[ParsedFiling]) -> dict[str, MetricSeries]:
    """Year-history series for every UNKNOWN source code (Part B): never dropped (§5.1).

    Keyed by the raw code/element name (no canonical). The §-ref is derived structurally
    where the code carries a numeric paragraph; otherwise None.
    """
    series: dict[str, MetricSeries] = {}
    codes = {code for f in filings for code in f.field_provenance.passthrough}
    for code in sorted(codes):
        history: dict[int, float] = {}
        for f in filings:
            year = _year(f.stichtag)
            value = f.field_provenance.passthrough.get(code)
            if year is not None and value is not None:
                history[year] = value
        if history:
            latest_year = max(history)
            series[code] = MetricSeries(
                latest=history[latest_year],
                latest_year=latest_year,
                history=history,
                source_codes=[code],
                paragraph_ref=paragraph_ref(code),
            )
    return series


def _latest(filings: list[ParsedFiling]) -> ParsedFiling | None:
    dated = [(y, f) for f in filings if (y := _year(f.stichtag)) is not None]
    return max(dated, key=lambda t: t[0])[1] if dated else None


def _completeness(filings: list[ParsedFiling]) -> dict[int, dict[str, int]]:
    out: dict[int, dict[str, int]] = {}
    for f in filings:
        year = _year(f.stichtag)
        if year is None:
            continue
        bilanz_items = sum(1 for v in f.bilanz.model_dump().values() if v is not None)
        guv_items = (
            sum(1 for k, v in f.guv.model_dump().items() if k != "revenue_basis" and v is not None)
            if f.guv
            else 0
        )
        out[year] = {"bilanz_items": bilanz_items, "guv_items": guv_items}
    return out


def _employees_series(filings: list[ParsedFiling]) -> MetricSeries | None:
    history: dict[int, float] = {}
    for f in filings:
        year = _year(f.stichtag)
        if year is not None and f.employees is not None:
            history[year] = float(f.employees)
    if not history:
        return None
    latest_year = max(history)
    return MetricSeries(latest=history[latest_year], latest_year=latest_year, history=history)


def _management(filings: list[ParsedFiling], master: MasterData | None) -> Management | None:
    latest = _latest(filings)
    sig_history: dict[int, float] = {}
    for f in filings:
        year = _year(f.stichtag)
        if year is not None and f.signatories:
            sig_history[year] = float(len(f.signatories))

    primary: Manager | None = None
    if master and master.persons:
        primary = master.persons[0]
    elif latest and latest.signatory:
        s = latest.signatory
        primary = Manager(
            first_name=s.first_name,
            last_name=s.last_name,
            birth_year=s.birth_year,
            age_at_signing=s.age_at_signing,
            role_label="Geschäftsführer",
        )
    if primary is None and not sig_history:
        return None

    n_latest = int(sig_history[max(sig_history)]) if sig_history else None
    stability = _stability_years(sig_history)
    sig_series = (
        MetricSeries(
            latest=sig_history[max(sig_history)], latest_year=max(sig_history), history=sig_history
        )
        if sig_history
        else None
    )
    return Management(
        primary_gf=primary,
        n_signatories_latest=n_latest,
        signatories_stable_years=stability,
        signatories_history=sig_series,
    )


def _stability_years(sig_history: dict[int, float]) -> int | None:
    """Consecutive most-recent years with the same signatory count."""
    if not sig_history:
        return None
    years = sorted(sig_history, reverse=True)
    latest_count = sig_history[years[0]]
    stable = 0
    for y in years:
        if sig_history[y] == latest_count:
            stable += 1
        else:
            break
    return stable


def _master_blocks(
    fnr: str, filings: list[ParsedFiling], master: MasterData | None, years: list[int]
) -> tuple[Identity, Location, CompanyMaster]:
    identity = Identity(
        fnr=fnr,
        register_id=f"AT_{fnr}",
        # Name precedence: master (auszug) → the name carried in the filing (§15b-5) → fnr.
        name=(master.name if master and master.name else None)
        or _name_from_filings(filings)
        or fnr,
        # Legal-form precedence: master (auszug) → Rechtsform carried in the filing (jab40).
        legal_form=(master.legal_form if master and master.legal_form else None)
        or _legal_form_from_filings(filings),
        status=master.status if master and master.status else "active",
        court=master.court if master else Court(),
    )
    location = master.location if master and master.location else Location()
    company = CompanyMaster(
        stammkapital=master.stammkapital if master else _stammkapital_from_filings(filings),
        first_filing_year=min(years) if years else None,
        last_filing_year=max(years) if years else None,
        filing_years_available=len(set(years)) if years else 0,
        founded_year=master.founded_year if master else None,
        description=master.description if master else None,
    )
    return identity, location, company


def _legal_form_from_filings(filings: list[ParsedFiling]) -> str | None:
    """Rechtsform from the latest filing that carries one (jab40 fallback when no master)."""
    for f in sorted(filings, key=lambda x: x.stichtag, reverse=True):
        if f.legal_form:
            return f.legal_form
    return None


def _name_from_filings(filings: list[ParsedFiling]) -> str | None:
    """Company name from the latest filing that carries one (§15b-5 fallback)."""
    for f in sorted(filings, key=lambda x: x.stichtag, reverse=True):
        if f.name:
            return f.name
    return None


def _stammkapital_from_filings(filings: list[ParsedFiling]) -> Money | None:
    latest = _latest(filings)
    if latest and latest.bilanz.stammkapital is not None:
        return Money(amount=latest.bilanz.stammkapital, currency=latest.currency)
    return None


def _filing_ref(f: ParsedFiling) -> FilingRef:
    return FilingRef(stichtag=f.stichtag, format=f.format, parsed=f.parsed, gkl=f.gkl)


# Canonical positions cross-checked between consecutive filings (the headline totals).
_RECONCILE_CANONICALS = ("aktiva", "eigenkapital")
_RECONCILE_REL_TOL = 0.01  # 1% (resubmissions / rounding)


def _prior_year_reconciled(filings: list[ParsedFiling]) -> bool:
    """Cross-check each filing's prior-year column against the previous filing (§8.5).

    For consecutive fiscal years, filing[Y]'s prior-year value (the Y-1 column) should
    match filing[Y-1]'s own current value. Returns False on any mismatch beyond a 1%
    tolerance; True when all checkable pairs agree (or there are none to compare).
    """
    by_year = {y: f for f in filings if (y := _year(f.stichtag)) is not None}
    for year, filing in by_year.items():
        prev = by_year.get(year - 1)
        if prev is None:
            continue
        for canonical in _RECONCILE_CANONICALS:
            prior = filing.positions_prior_year.get(canonical)
            current = prev.positions.get(canonical)
            if prior is None or current is None:
                continue
            if abs(prior - current) > max(1.0, _RECONCILE_REL_TOL * abs(current)):
                return False
    return True


def _size_placeholder(latest: ParsedFiling | None) -> Size:
    return Size()  # gkl/band/percentiles filled by derive


def _build_meta(
    fnr: str,
    filings: list[ParsedFiling],
    master: MasterData | None,
    prev: ConsolidatedCompany | None,
    run_id: str,
) -> Meta:
    inputs: list[LineageRef] = [lineage_ref(f.meta) for f in filings]
    if master is not None:
        inputs.append(
            LineageRef(
                stage="master",
                doc_id=new_doc_id(),
                content_hash="",
                created_at="",
                source="auszug",
                entity_id=fnr,
            )
        )
    meta = Meta(
        doc_id=new_doc_id(),
        entity_id=fnr,
        stage="consolidated",
        producer=PRODUCER,
        run_id=run_id,
        data_version=(prev.meta.data_version or 0) + 1 if prev else 1,
        inputs=inputs,
        checks={
            "all_inputs_present": bool(filings),
            "prior_year_reconciled": _prior_year_reconciled(filings),
        },
    )
    if prev is not None:
        meta.supersedes = LineageRef(
            stage="consolidated",
            doc_id=prev.meta.doc_id,
            content_hash=prev.meta.content_hash or "",
            created_at=(prev.meta.timestamps.get("consolidated_at", "")),
        )
        if prev.meta.timestamps:
            meta.timestamps.update(prev.meta.timestamps)
    return meta
