"""Peer-percentile cohort tests (Appendix C.3)."""

from __future__ import annotations

from typing import Literal

from fbl_core.models import Meta, MetricSeries
from fbl_core_at.models import (
    CompanyMaster,
    ConsolidatedCompany,
    Financials,
    Identity,
    Location,
    Size,
)
from fbl_derive import build_cohort_stats


def _company(fnr: str, gkl: Literal["W", "K", "M", "G"], bilanzsumme: float) -> ConsolidatedCompany:
    return ConsolidatedCompany(
        identity=Identity(fnr=fnr, name=fnr),
        location=Location(),
        company=CompanyMaster(),
        size=Size(gkl=gkl),
        financials=Financials(
            has_bilanz=True,
            bilanz={"bilanzsumme": MetricSeries(latest=bilanzsumme, history={2024: bilanzsumme})},
        ),
        meta=Meta(doc_id=fnr, entity_id=fnr, stage="consolidated", producer="c", run_id="t"),
    )


def test_percentile_rank_within_band() -> None:
    companies = [
        _company("a", "K", 100.0),
        _company("b", "K", 200.0),
        _company("c", "K", 300.0),
        _company("d", "K", 400.0),
        _company("z", "M", 9999.0),  # different band, must not affect K
    ]
    cohort = build_cohort_stats(companies)
    # 300 is greater than 100 and 200 (2 of 4) -> mean rank 50%
    assert cohort.percentile("K", "bilanzsumme", 300.0) == 62.5
    assert cohort.percentile("K", "bilanzsumme", 100.0) == 12.5  # smallest
    assert cohort.percentile("K", "bilanzsumme", 400.0) == 87.5  # largest
    # band isolation
    assert cohort.percentile("M", "bilanzsumme", 9999.0) == 50.0


def test_percentile_none_when_unknown() -> None:
    cohort = build_cohort_stats([_company("a", "K", 100.0)])
    assert cohort.percentile(None, "bilanzsumme", 100.0) is None
    assert cohort.percentile("G", "bilanzsumme", 100.0) is None  # empty band
    assert cohort.percentile("K", "bilanzsumme", None) is None
