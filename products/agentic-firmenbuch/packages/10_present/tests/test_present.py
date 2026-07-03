"""Present-stage tests: scope, GDPR gating, denormalized index fields (§8.7 DoD)."""

from __future__ import annotations

from typing import Any, cast

from fbl_core.lineage import content_hash
from fbl_core.models import Meta, MetricSeries
from fbl_core_at.models import (
    CompanyMaster,
    Derivations,
    DerivedCompany,
    Financials,
    Growth,
    Identity,
    Location,
    Management,
    Manager,
    Ratios,
    Size,
)
from fbl_present import present, present_status_only


def _derived() -> DerivedCompany:
    return DerivedCompany(
        identity=Identity(
            fnr="093450b",
            register_id="AT_093450b",
            name="Schubert CleanTech GmbH",
            legal_form="GES",
            status="active",
        ),
        location=Location(bundesland="N", city="Ober-Grafendorf", postal_code="3200"),
        company=CompanyMaster(first_filing_year=2020, last_filing_year=2025),
        size=Size(gkl="M", bilanzsumme_band="medium", peer_percentiles={"bilanzsumme": 82.9}),
        financials=Financials(
            latest_year=2025,
            has_bilanz=True,
            has_guv=True,
            has_guv_latest=True,
            revenue_basis="rohergebnis",
            bilanz={
                "bilanzsumme": MetricSeries(latest=23492979.69, history={2025: 23492979.69}),
                "eigenkapital": MetricSeries(latest=6393383.95, history={2025: 6393383.95}),
            },
            guv={"rohergebnis": MetricSeries(latest=24424100.52, history={2025: 24424100.52})},
        ),
        employees=MetricSeries(latest=95, history={2024: 91, 2025: 95}),
        management=Management(
            primary_gf=Manager(
                first_name="Claus",
                last_name="Benedict",
                birth_year=1972,
                age_at_signing=53.6,
                role_label="Geschäftsführer",
            ),
            n_signatories_latest=1,
            signatories_stable_years=5,
        ),
        ratios=Ratios(
            equity_ratio=MetricSeries(latest=0.2721, history={2025: 0.2721}),
            capital_profile="balanced",
        ),
        growth=Growth(profile="fast_growing", method="rohergebnis"),
        derivations=Derivations(),
        meta=Meta(
            doc_id="der-1",
            entity_id="093450b",
            stage="derived",
            producer="derive@1.0.0",
            run_id="t",
            data_version=7,
            metrics_version="1.0",
        ),
    )


def test_names_withheld_by_default() -> None:
    doc = present(_derived(), run_id="t", current_year=2026)
    assert doc.management is not None
    mgr = doc.management.primary_manager
    assert mgr is not None
    # age + birth_year exposed; NO name
    assert mgr.birth_year == 1972
    assert mgr.age_at_signing == 53.6
    assert mgr.age == 54  # 2026 - 1972
    assert mgr.role_label == "Geschäftsführer"
    assert doc.management.primary_manager_name is None
    # the full name must not leak anywhere in the served body
    assert "Claus" not in doc.model_dump_json()
    assert "Benedict" not in doc.model_dump_json()


def test_names_exposed_only_with_flag() -> None:
    doc = present(_derived(), expose_personal_data=True, run_id="t", current_year=2026)
    assert doc.management is not None
    assert doc.management.primary_manager_name == "Claus Benedict"


def test_birth_data_is_year_only_no_month_or_day_field_exists() -> None:
    # The hard GDPR invariant (§8.7): names may be served, but birth data is YEAR ONLY —
    # never month/day. Guard it structurally so a future schema change can't introduce a
    # finer-grained birth field unnoticed. Covers both the internal and the served models.
    from fbl_core_at.models import PresentedManager
    from fbl_core_at.models.company import Manager

    forbidden = ("month", "day", "dob", "date_of_birth", "geburtstag", "geburtsdatum")
    for model in (Manager, PresentedManager):
        for fname in model.model_fields:
            assert not any(bad in fname.lower() for bad in forbidden), f"{model.__name__}.{fname}"
        # the only birth-related field is the year
        birth_fields = {f for f in model.model_fields if "birth" in f.lower()}
        assert birth_fields == {"birth_year"}, f"{model.__name__}: {birth_fields}"


def test_denormalized_index_fields() -> None:
    doc = present(_derived(), run_id="t", current_year=2026)
    # shallow indexed paths (§4.1)
    assert doc.identity["status"] == "active"
    assert doc.identity["legal_form"] == "GES"
    assert doc.location["bundesland"] == "N"
    assert doc.size["gkl"] == "M"
    assert doc.financials.has_guv_latest is True
    assert doc.financials.latest["bilanzsumme"] == 23492979.69
    assert doc.financials.latest["revenue"] == 24424100.52  # rohergebnis basis
    assert cast(dict[str, Any], doc.ratios["equity_ratio"])["latest"] == 0.2721
    assert doc.growth["profile"] == "fast_growing"
    assert doc.employees is not None and doc.employees["latest"] == 95
    assert doc.company["last_filing_year"] == 2025


def test_reserved_groups_null_and_provenance() -> None:
    doc = present(_derived(), run_id="t", current_year=2026)
    assert doc.sector is None and doc.enrichment is None and doc.score is None
    assert doc.summary is None and doc.observations is None
    assert doc.provenance.license == "CC-BY-4.0"
    assert doc.provenance.data_version == 7
    assert doc.provenance.built_at is not None
    assert "Firmenbuch" in doc.provenance.attribution


def test_status_override_from_registry() -> None:
    doc = present(_derived(), status="deleted", run_id="t", current_year=2026)
    assert doc.identity["status"] == "deleted"


def test_status_only_refresh() -> None:
    base = present(_derived(), run_id="t", current_year=2026)
    refreshed = present_status_only(base, "historical", run_id="t2")
    assert refreshed.identity["status"] == "historical"
    # everything else unchanged
    assert refreshed.financials.latest == base.financials.latest


def test_present_is_idempotent() -> None:
    a = present(_derived(), run_id="run-a", current_year=2026)
    b = present(_derived(), run_id="run-b", current_year=2026)
    assert content_hash(a.model_dump(mode="json")) == content_hash(b.model_dump(mode="json"))


def test_id_equals_fnr() -> None:
    doc = present(_derived(), run_id="t", current_year=2026)
    assert doc.id == doc.fnr == "093450b"
