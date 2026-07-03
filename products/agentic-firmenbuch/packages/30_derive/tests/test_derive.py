"""End-to-end derive on a synthetic ConsolidatedCompany + profile/size rules (§8.6)."""

from __future__ import annotations

from typing import Any

from fbl_core.models import (
    CompanyMaster,
    ConsolidatedCompany,
    Financials,
    Identity,
    Location,
    Meta,
    MetricSeries,
    Size,
)
from fbl_derive import build_cohort_stats, derive


def _hist(block: dict[str, Any]) -> dict[int, float]:
    return {int(k): v for k, v in block["history"].items()}


def _company_from(example: dict[str, Any]) -> ConsolidatedCompany:
    fin = example["financials"]
    bilanz = {
        name: MetricSeries(history=_hist(block))
        for name, block in fin["bilanz"].items()
        if block.get("history")
    }
    guv = {
        name: MetricSeries(history=_hist(block))
        for name, block in (fin.get("guv") or {}).items()
        if isinstance(block, dict) and block.get("history")
    }
    latest_year = max(bilanz["bilanzsumme"].history)
    return ConsolidatedCompany(
        identity=Identity(fnr=example["fnr"], name=example["name"]),
        location=Location(),
        company=CompanyMaster(last_filing_year=latest_year),
        size=Size(gkl=example["size"]["band"]),
        financials=Financials(latest_year=latest_year, has_bilanz=True, bilanz=bilanz, guv=guv),
        meta=Meta(
            doc_id="c",
            entity_id=example["fnr"],
            stage="consolidated",
            producer="consolidate@1.0.0",
            run_id="t",
            data_version=3,
        ),
    )


def test_derive_grama_shrinking_capital_profile(grama: dict[str, Any]) -> None:
    company = _company_from(grama)
    der = derive(company, cohort_stats=build_cohort_stats([company]), run_id="t")
    # Bilanz-only -> growth profile from bilanzsumme, which is shrinking here.
    assert der.growth.method == "bilanzsumme"
    assert der.growth.profile == "shrinking"
    # equity_ratio ~0.81 -> over_capitalized
    assert der.ratios.capital_profile == "over_capitalized"
    assert der.ratios.equity_ratio is not None
    assert der.ratios.equity_ratio.latest == grama["ratios"]["equity_ratio"]["latest"]
    assert der.meta.stage == "derived" and der.meta.data_version == 3
    assert der.meta.lineage[0].doc_id == "c"


def test_derive_schubert_fast_growing_rohergebnis(schubert: dict[str, Any]) -> None:
    company = _company_from(schubert)
    der = derive(company, cohort_stats=build_cohort_stats([company]), run_id="t")
    # has GuV with rohergebnis -> profile method is rohergebnis
    assert der.growth.method == "rohergebnis"
    assert der.growth.profile == "fast_growing"
    assert der.ratios.capital_profile == "balanced"  # equity_ratio ~0.27


def test_derive_size_band_effective() -> None:
    company = _company_from(
        {
            "fnr": "x",
            "name": "X",
            "size": {"band": "M"},
            "financials": {"bilanz": {"bilanzsumme": {"history": {"2024": 23492979.69}}}},
        }
    )
    der = derive(company, run_id="t")
    assert der.size.gkl == "M"
    assert der.size.bilanzsumme_band == "medium"  # 23.5M -> medium (>=6.25M, <25M)


def test_derive_is_idempotent(grama: dict[str, Any]) -> None:
    from fbl_core.lineage import content_hash

    company = _company_from(grama)
    cohort = build_cohort_stats([company])
    a = derive(company, cohort_stats=cohort, run_id="run-a")
    b = derive(company, cohort_stats=cohort, run_id="run-b")
    assert content_hash(a.model_dump(mode="json")) == content_hash(b.model_dump(mode="json"))
