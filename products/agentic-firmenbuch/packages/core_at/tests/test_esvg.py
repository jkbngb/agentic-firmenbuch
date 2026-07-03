"""ESVG sector-key mapping tests (ROADMAP P2 / issue #15)."""

from __future__ import annotations

import pytest

from fbl_core_at.esvg import ESVG_LABELS, esvg_kind, esvg_label


@pytest.mark.parametrize(
    "code,kind",
    [
        ("1210", "bank"),  # Zentralbank
        ("1220A", "bank"),  # MFIs - CRD
        ("1280B", "insurer"),  # Lebensversicherungen
        ("1280A", "insurer"),  # Rückversicherungen
        ("1290", "pensionskasse"),  # Pensionskassen
        ("1250B", "vorsorgekasse"),  # Mitarbeitervorsorgekassen
        ("1240E", "fund"),  # Aktienfonds
        ("1260A", "other_financial"),  # Kredit-/Versicherungshilfstätigkeiten
        ("1100", "other"),  # Nicht-finanzielle Unternehmen
        (None, "other"),
        ("", "other"),
    ],
)
def test_esvg_kind(code: str | None, kind: str) -> None:
    assert esvg_kind(code) == kind


def test_esvg_label_is_official_and_complete() -> None:
    assert esvg_label("1220A") == "MFIs - CRD - MiRe-pflichtig"
    assert esvg_label("1280") == "Versicherungsgesellschaften"
    assert esvg_label("nope") is None and esvg_label(None) is None
    # the full legend was loaded verbatim (50 codes incl. banks, funds, insurers, pensions)
    assert len(ESVG_LABELS) == 50
    assert {"1220A", "1280B", "1290", "1250B"} <= set(ESVG_LABELS)
