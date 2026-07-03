"""Model round-trip tests (Technische Spezifikation §6, §8.1 DoD).

DoD for `core`: models round-trip (serialize/parse) the golden fixtures and the
``content_hash`` is stable across runs for identical input.
"""

from __future__ import annotations

import json
from pathlib import Path

from fbl_core.lineage import content_hash, stamp
from fbl_core.models import Meta, MetricSeries
from fbl_core_at.models import (
    Bilanz,
    ConsolidatedCompany,
    Derivations,
    DerivedCompany,
    FieldProvenance,
    Financials,
    Growth,
    GuV,
    Identity,
    Location,
    ParsedFiling,
    Ratios,
    Signatory,
    Size,
)
from fbl_core_at.models.company import CompanyMaster

FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "fixtures"


def _sample_parsed() -> ParsedFiling:
    """The Stage 1 golden sample (Schubert CleanTech, 093450b) as a ParsedFiling."""
    return ParsedFiling(
        fnr="093450b",
        stichtag="2025-12-31",
        gj_beginn="2025-01-01",
        gj_ende="2025-12-31",
        format="jab40_semantic",
        parsed=True,
        has_bilanz=True,
        has_guv=True,
        bilanz=Bilanz(
            bilanzsumme=23492979.69,
            eigenkapital=6393383.95,
            verbindlichkeiten=9749100.41,
            anlagevermoegen=3425134.31,
            umlaufvermoegen=19454205.89,
        ),
        guv=GuV(
            revenue_basis="rohergebnis",
            rohergebnis=24424100.52,
            personalaufwand=-17070434.71,
            abschreibungen=-834467.61,
            ebit=1523815.88,
            ebitda=2358283.49,
            jahresueberschuss=1214303.44,
        ),
        employees=95,
        signatory=Signatory(
            first_name="Claus", last_name="Benedict", birth_year=1972, signed_at="2026-04-09"
        ),
        field_provenance=FieldProvenance(
            format="jab40_semantic",
            map={"bilanz.bilanzsumme": "UEBERMITTLUNG/BILANZ/BILANZ_AKTIVA"},
        ),
        meta=Meta(
            doc_id="7c3d9a51-44b2-4f0e-9bb1-1c9d2a55e004",
            entity_id="093450b/2025-12-31",
            stage="parsed",
            producer="parse@1.0.0",
            run_id="2026-06-16-daily-0003",
        ),
    )


def test_parsed_filing_round_trips() -> None:
    pf = _sample_parsed()
    dumped = pf.model_dump(mode="json")
    again = ParsedFiling.model_validate(dumped)
    assert again == pf
    # JSON-string round trip too
    assert ParsedFiling.model_validate_json(pf.model_dump_json()) == pf


def test_parsed_filing_content_hash_stable() -> None:
    pf = _sample_parsed()
    h1 = content_hash(pf.model_dump(mode="json"))
    # A second identical filing differing only in volatile meta hashes the same.
    pf2 = _sample_parsed()
    pf2.meta.doc_id = "different-uuid"
    pf2.meta.timestamps["parsed_at"] = "2099-01-01T00:00:00Z"
    h2 = content_hash(pf2.model_dump(mode="json"))
    assert h1 == h2


def test_metric_series_history_int_keys() -> None:
    # JSON object keys are strings; Pydantic coerces them back to int.
    ms = MetricSeries.model_validate(
        {"latest": 23492979.69, "latest_year": 2025, "history": {"2020": 10777460.87}}
    )
    assert ms.history == {2020: 10777460.87}


def test_consolidated_example_financials_load() -> None:
    """The prototype consolidated examples have per-line history blocks we can read."""
    raw = json.loads((FIXTURES / "consolidated_examples" / "grama_trade_032616s.json").read_text())
    bilanzsumme = MetricSeries.model_validate(raw["financials"]["bilanz"]["bilanzsumme"])
    assert bilanzsumme.latest == 1137155.73
    assert bilanzsumme.history[2020] == 1454398.93


def test_consolidated_company_round_trips() -> None:
    cons = ConsolidatedCompany(
        identity=Identity(fnr="093450b", name="Schubert CleanTech GmbH", legal_form="gmbh"),
        location=Location(bundesland="N", city="Ober-Grafendorf", postal_code="3200"),
        company=CompanyMaster(first_filing_year=2020, last_filing_year=2025),
        size=Size(gkl="M", bilanzsumme_band="medium"),
        financials=Financials(
            latest_year=2025,
            has_bilanz=True,
            has_guv=True,
            has_guv_latest=True,
            guv_years=[2020, 2025],
            bilanz={"bilanzsumme": MetricSeries(latest=23492979.69, history={2025: 23492979.69})},
        ),
        employees=MetricSeries(history={2024: 91, 2025: 95}),
        meta=Meta(
            doc_id="e90a7733-9f1c-4d2b-bb6e-2a0f4471c2da",
            entity_id="093450b",
            stage="consolidated",
            producer="consolidate@1.0.0",
            run_id="2026-06-16-daily-0003",
            data_version=7,
        ),
    )
    again = ConsolidatedCompany.model_validate(cons.model_dump(mode="json"))
    assert again == cons
    assert again.sector is None and again.score is None  # reserved v1 fields stay null


def test_derived_company_extends_consolidated() -> None:
    der = DerivedCompany(
        identity=Identity(fnr="093450b", name="Schubert CleanTech GmbH"),
        location=Location(),
        company=CompanyMaster(),
        size=Size(gkl="M"),
        financials=Financials(),
        ratios=Ratios(
            equity_ratio=MetricSeries(latest=0.2721, trend="declining"),
            capital_profile="balanced",
        ),
        growth=Growth(profile="fast_growing", method="rohergebnis"),
        derivations=Derivations(formulas={"ratios.equity_ratio": "eigenkapital / bilanzsumme"}),
        meta=Meta(
            doc_id="44ff1290-7ac3-4e51-8d22-9b3a51c7e6b1",
            entity_id="093450b",
            stage="derived",
            producer="derive@1.0.0",
            run_id="2026-06-16-daily-0003",
            metrics_version="1.0",
        ),
    )
    again = DerivedCompany.model_validate(der.model_dump(mode="json"))
    assert again == der
    assert again.ratios.equity_ratio is not None
    assert again.ratios.equity_ratio.latest == 0.2721


def test_stamp_on_real_model() -> None:
    pf = _sample_parsed()
    payload = pf.model_dump(mode="json")
    stamp(pf.meta, payload, stage_time_key="parsed_at")
    assert pf.meta.content_hash is not None
    assert pf.meta.timestamps["parsed_at"].endswith("Z")
