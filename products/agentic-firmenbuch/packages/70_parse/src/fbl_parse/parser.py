"""``parse_filing`` — raw filing XML → canonical :class:`ParsedFiling` (§8.4).

Pure function of the input bytes (idempotent). Auto-detects the format, maps every
recognized position to its canonical name, computes the age feature, records the
quality checks and lineage, and never crashes on bad input: an unparseable filing
returns a dead-letter stub carrying an ``error`` field (§15b-7).
"""

from __future__ import annotations

from lxml import etree

from fbl_core.lineage import new_doc_id, stamp
from fbl_core.models.meta import LineageRef, Meta
from fbl_core_at.mapping import (
    BILANZ_FIELD_TO_CANONICAL,
    EBT_CANONICAL,
    GUV_FIELD_TO_CANONICAL,
    INTEREST_EXPENSE_CANONICAL,
    MAPPING_VERSION,
    load_taxonomy,
)
from fbl_core_at.models.filing import (
    Bilanz,
    FieldProvenance,
    FilingFormat,
    GuV,
    ParsedFiling,
    RevenueBasis,
    Signatory,
)

from .people import extract_employees, extract_signatories, primary_signatory
from .positions import ExtractResult, extract_hgb, extract_v4
from .variant import XmlVariant, as_filing_format, detect_variant
from .xml_common import first_descendant, glue_name, local_name, text_of

PRODUCER = "parse@1.0.0"
_AKTIVA_TOLERANCE = 1.0  # absolute euro tolerance for aktiva == passiva

# Canonical positions in any GuV category (for the §8.4 tag-presence has_guv rule).
_GUV_CANONICALS = frozenset(
    p.canonical
    for p in load_taxonomy().positions
    if p.category.startswith("guv") or any(c.startswith("guv") for c in p.extra_categories)
)


def _has_guv(extract: ExtractResult) -> bool:
    """GuV present iff any GuV-category position appears (spec §8.4 tag-presence rule).

    Covers GuV filings whose only positions are outside the 7 typed GuV fields (e.g.
    carried only in the passthrough as ``HGB_231*``/``GUV*`` codes).
    """
    if _GUV_CANONICALS & extract.canonical_values.keys():
        return True
    return any(code.startswith("HGB_231") or "GUV" in code for code in extract.passthrough)


def parse_filing(
    raw_xml: bytes,
    *,
    run_id: str = "adhoc",
    raw_ref: LineageRef | None = None,
    fnr_hint: str | None = None,
    stichtag_hint: str | None = None,
) -> ParsedFiling:
    """Parse one Jahresabschluss filing into a :class:`ParsedFiling`."""
    try:
        root = etree.fromstring(raw_xml)
    except etree.XMLSyntaxError as exc:
        return _error_stub(run_id, raw_ref, fnr_hint, stichtag_hint, error=f"xml_syntax: {exc}")

    try:
        return _parse_root(root, run_id=run_id, raw_ref=raw_ref)
    except Exception as exc:
        return _error_stub(run_id, raw_ref, fnr_hint, stichtag_hint, error=f"parse: {exc}")


def parse_pdf_only(
    fnr: str,
    stichtag: str,
    *,
    run_id: str = "adhoc",
    raw_ref: LineageRef | None = None,
) -> ParsedFiling:
    """Build a parsed record for a PDF-only filing: financials empty, document linked."""
    meta = _new_meta(fnr, stichtag, run_id, raw_ref)
    pf = ParsedFiling(
        fnr=fnr,
        stichtag=stichtag,
        format="pdf",
        parsed=False,
        field_provenance=FieldProvenance(format="pdf"),
        meta=meta,
    )
    _finalize(pf)
    return pf


# --- internals -----------------------------------------------------------------


def _parse_root(root: etree._Element, *, run_id: str, raw_ref: LineageRef | None) -> ParsedFiling:
    variant = detect_variant(root)
    fnr = _extract_fnr(root)
    name = _extract_name(root)
    legal_form = _extract_legal_form(root)
    gj_beginn, gj_ende = _extract_fiscal_year(root, variant)
    stichtag = gj_ende or "unknown"
    currency = _currency(root)
    gkl = _extract_gkl(root)

    extract = _extract_positions(root, variant)
    bilanz = _build_bilanz(extract)
    has_bilanz = any(v is not None for v in bilanz.model_dump().values())
    guv = _build_guv(extract)
    has_guv = guv is not None or _has_guv(extract)

    # Guardrail (§15b-2): a semantic JAb 4.0 filing that yields NO positions is a parse
    # failure (e.g. an unhandled schema variant), not an empty company — dead-letter it
    # loudly instead of silently serving empty/stale financials.
    if variant == "jab40_semantic" and not extract.canonical_values:
        return _error_stub(
            run_id,
            raw_ref,
            fnr,
            stichtag,
            error="jab40_semantic: no positions extracted (unhandled schema?)",
            fmt="jab40_semantic",
        )

    employees = extract_employees(root)
    signatories = extract_signatories(root)
    primary = primary_signatory(signatories)

    provenance = _build_provenance(variant, extract, bilanz, guv, employees, primary)

    meta = _new_meta(fnr, stichtag, run_id, raw_ref)
    meta.checks = _checks(extract, bilanz)

    pf = ParsedFiling(
        fnr=fnr,
        stichtag=stichtag,
        name=name,
        legal_form=legal_form,
        gj_beginn=gj_beginn,
        gj_ende=gj_ende,
        currency=currency,
        gkl=gkl,
        format=as_filing_format(variant),
        parsed=True,
        has_bilanz=has_bilanz,
        has_guv=has_guv,
        bilanz=bilanz,
        guv=guv,
        positions=dict(extract.canonical_values),
        positions_prior_year=dict(extract.prior_year_values),
        position_codes={k: list(v) for k, v in extract.source_codes.items()},
        employees=employees,
        signatory=primary,
        signatories=signatories,
        field_provenance=provenance,
        meta=meta,
    )
    _finalize(pf)
    return pf


def _extract_gkl(root: etree._Element) -> str | None:
    """Size class: ALLG_JUSTIZ ``EINSTUFUNG`` (legacy/fb2025) or ``GROESSENKLASSE`` (jab40)."""
    block = first_descendant(root, "ALLG_JUSTIZ")
    if block is not None:
        attr = block.get("EINSTUFUNG") or block.get("EINORDNUNG")
        if attr and attr.strip():
            einstufung: str = attr.strip()
            return einstufung
    gk = first_descendant(root, "GROESSENKLASSE")  # JAb 4.0 (under GESCHAEFTSJAHR)
    if gk is not None and gk.text and gk.text.strip():
        groessenklasse: str = gk.text.strip()
        return groessenklasse
    return None


def _currency(root: etree._Element) -> str:
    for tag in ("WAEHRUNG", "BILANZ_WAEHRUNG"):  # legacy / jab40
        elem = first_descendant(root, tag)
        if elem is not None and elem.text and elem.text.strip():
            currency: str = elem.text.strip()
            return currency
    return "EUR"


def _extract_positions(root: etree._Element, variant: XmlVariant) -> ExtractResult:
    if variant == "legacy_finanzonline":
        return extract_hgb(root, value_mode="postenzeile_betrag", year_block="GJ")
    if variant == "firmenbuch_2025":
        return extract_hgb(root, value_mode="betrag_gj", year_block="GESCHAEFTSJAHR")
    return extract_v4(root)


def _extract_name(root: etree._Element) -> str | None:
    """Company name from the filing, glued from its multi-line segments (§15b-5).

    Legacy/fb2025 carry ``FIRMA/F_NAME`` with ``<Z>`` line segments; JAb 4.0 carries
    ``FIRMENWORTLAUT`` with ``<ZEILE SORTIERUNG=…>`` segments (ordered by SORTIERUNG).
    A line that splits a word ends in ``-`` and is glued without a space.
    """
    fname = first_descendant(root, "F_NAME")
    if fname is not None:
        segs = [z.text or "" for z in fname if isinstance(z.tag, str) and local_name(z) == "Z"]
        glued = glue_name(segs if segs else [fname.text or ""])
        if glued:
            return glued
    wortlaut = first_descendant(root, "FIRMENWORTLAUT")
    if wortlaut is not None:
        zeilen = [z for z in wortlaut if isinstance(z.tag, str) and local_name(z) == "ZEILE"]
        zeilen.sort(key=lambda z: _sort_key(z.get("SORTIERUNG")))
        glued = glue_name([z.text or "" for z in zeilen])
        if glued:
            return glued
    return None


def _extract_legal_form(root: etree._Element) -> str | None:
    """Rechtsform carried in the filing, if present (JAb 4.0 ``GESCHAEFTSJAHR/RECHTSFORM``).

    A filing-level fallback for ``identity.legal_form`` when master (``auszug``) is absent;
    the authoritative source remains the master extract. Legacy filings rarely carry it.
    """
    elem = first_descendant(root, "RECHTSFORM")
    if elem is not None and elem.text and elem.text.strip():
        legal_form: str = elem.text.strip()
        return legal_form
    return None


def _sort_key(raw: str | None) -> int:
    """Numeric SORTIERUNG order; unparseable/absent sort last (deterministic)."""
    if raw and raw.strip().isdigit():
        return int(raw.strip())
    return 1_000_000


def _extract_fnr(root: etree._Element) -> str:
    for tag in ("FNR", "FIRMENBUCHNUMMER"):  # legacy/fb2025 / jab40
        elem = first_descendant(root, tag)
        if elem is not None and elem.text and elem.text.strip():
            fnr: str = elem.text.strip()
            return fnr
    return "unknown"


def _extract_fiscal_year(
    root: etree._Element, variant: XmlVariant
) -> tuple[str | None, str | None]:
    block = "GESCHAEFTSJAHR" if variant == "firmenbuch_2025" else "GJ"
    gj = first_descendant(root, block)
    if gj is None and variant == "jab40_semantic":
        gj = first_descendant(root, "GESCHAEFTSJAHR")
        if gj is None:
            gj = first_descendant(root, "GJ")
    if gj is None:
        return None, None
    return text_of(gj, "BEGINN"), text_of(gj, "ENDE")


def _build_bilanz(extract: ExtractResult) -> Bilanz:
    values = {
        field: extract.canonical_values.get(canonical)
        for field, canonical in BILANZ_FIELD_TO_CANONICAL.items()
    }
    return Bilanz(**values)


def _build_guv(extract: ExtractResult) -> GuV | None:
    present = {
        field: extract.canonical_values.get(canonical)
        for field, canonical in GUV_FIELD_TO_CANONICAL.items()
    }
    if not any(v is not None for v in present.values()):
        return None  # ~96.7% of companies: Bilanz only, no GuV (§15b-9)

    umsatz = present.get("umsatzerloese")
    revenue_basis: RevenueBasis | None
    if umsatz is not None:
        revenue_basis = "umsatzerloese"
    elif present.get("rohergebnis") is not None:
        revenue_basis = "rohergebnis"
    else:
        revenue_basis = None
    ebit = present.get("ebit")
    abschreibungen = present.get("abschreibungen")
    ebitda = ebit - abschreibungen if (ebit is not None and abschreibungen is not None) else None

    # True EBIT (#6): Ergebnis vor Steuern + Zinsaufwand. Interest expense is stored negative,
    # so EBIT = EBT - zinsen_und_aehnliche_aufwendungen. Null unless both lines are present.
    ebt = extract.canonical_values.get(EBT_CANONICAL)
    interest = extract.canonical_values.get(INTEREST_EXPENSE_CANONICAL)
    ebit_strict = ebt - interest if (ebt is not None and interest is not None) else None

    return GuV(
        revenue_basis=revenue_basis,
        umsatzerloese=umsatz,
        rohergebnis=present.get("rohergebnis"),
        materialaufwand=present.get("materialaufwand"),
        personalaufwand=present.get("personalaufwand"),
        abschreibungen=abschreibungen,
        ebit=ebit,
        ebitda=ebitda,
        operating_result=present.get("operating_result"),  # = Betriebserfolg, correctly named
        ebit_strict=ebit_strict,
        jahresueberschuss=present.get("jahresueberschuss"),
    )


def _build_provenance(
    variant: XmlVariant,
    extract: ExtractResult,
    bilanz: Bilanz,
    guv: GuV | None,
    employees: int | None,
    primary: Signatory | None,
) -> FieldProvenance:
    field_map: dict[str, str] = {}
    for field, canonical in BILANZ_FIELD_TO_CANONICAL.items():
        if getattr(bilanz, field) is not None and canonical in extract.provenance:
            field_map[f"bilanz.{field}"] = extract.provenance[canonical]
    if guv is not None:
        for field, canonical in GUV_FIELD_TO_CANONICAL.items():
            if getattr(guv, field, None) is not None and canonical in extract.provenance:
                field_map[f"guv.{field}"] = extract.provenance[canonical]
    return FieldProvenance(
        format=as_filing_format(variant),
        mapping_version=MAPPING_VERSION,
        scaling={"wert_tsd_applied": extract.wert_tsd_applied},
        map=field_map,
        passthrough=dict(extract.passthrough),
    )


def _checks(extract: ExtractResult, bilanz: Bilanz) -> dict[str, bool]:
    aktiva = extract.canonical_values.get("aktiva")
    passiva = extract.canonical_values.get("passiva")
    aktiva_equals_passiva = (
        aktiva is not None and passiva is not None and abs(aktiva - passiva) <= _AKTIVA_TOLERANCE
    )
    negative_equity = bilanz.eigenkapital is not None and bilanz.eigenkapital < 0
    return {
        "aktiva_equals_passiva": aktiva_equals_passiva,
        "negative_equity": negative_equity,
        "wert_tsd_applied": extract.wert_tsd_applied,
    }


def _new_meta(fnr: str, stichtag: str, run_id: str, raw_ref: LineageRef | None) -> Meta:
    meta = Meta(
        doc_id=new_doc_id(),
        entity_id=f"{fnr}/{stichtag}",
        stage="parsed",
        producer=PRODUCER,
        run_id=run_id,
    )
    if raw_ref is not None:
        meta.lineage = [raw_ref]
        if raw_ref.created_at:
            meta.timestamps["ingested_at"] = raw_ref.created_at
    return meta


def _finalize(pf: ParsedFiling) -> None:
    stamp(pf.meta, pf.model_dump(mode="json"), stage_time_key="parsed_at")


def _error_stub(
    run_id: str,
    raw_ref: LineageRef | None,
    fnr_hint: str | None,
    stichtag_hint: str | None,
    *,
    error: str,
    fmt: FilingFormat = "legacy_finanzonline",
) -> ParsedFiling:
    fnr = fnr_hint or "unknown"
    stichtag = stichtag_hint or "unknown"
    meta = _new_meta(fnr, stichtag, run_id, raw_ref)
    pf = ParsedFiling(
        fnr=fnr,
        stichtag=stichtag,
        format=fmt,
        parsed=False,
        error=error,
        field_provenance=FieldProvenance(format=fmt),
        meta=meta,
    )
    _finalize(pf)
    return pf
