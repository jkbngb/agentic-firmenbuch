"""FI classifier tests (ROADMAP P2.1 — heuristic bank/insurer detection)."""

from __future__ import annotations

import pytest

from fbl_core.financial_institution import classify_financial_institution as clf


@pytest.mark.parametrize(
    "legal_form,name,kind,source",
    [
        # Legal form is decisive.
        ("SPA", "Irgendeine Sparkasse-Tochter", "bank", "legal_form"),
        ("VER", "Wiener Städtische Versicherungsverein", "insurer", "legal_form"),
        # The real regulated bank (the Volksbank NÖ AG case) — caught by name.
        ("AG", "Volksbank Niederösterreich AG", "bank", "name"),
        ("AG", "UniCredit Bank Austria AG", "bank", "name"),
        ("GEN", "Volksbank Kärnten eG", "bank", "name"),
        ("AG", "Erste Group Bank AG", "bank", "name"),
        ("GES", "Hypothekenbank Tirol GmbH", "bank", "name"),
        # Insurers by name.
        ("AG", "UNIQA Insurance Group AG", "insurer", "name"),
        ("AG", "Generali Versicherung AG", "insurer", "name"),
    ],
)
def test_classifies_financial_institutions(
    legal_form: str | None, name: str | None, kind: str, source: str
) -> None:
    fi = clf(legal_form, name)
    assert fi is not None
    assert fi.kind == kind
    assert fi.source == source
    assert fi.caveat  # non-empty German caveat


@pytest.mark.parametrize(
    "legal_form,name",
    [
        ("GES", "VB - REAL Volksbank NÖ GmbH"),  # whoops — this DOES contain 'volksbank'
    ],
)
def test_known_overcapture_is_documented(legal_form: str | None, name: str | None) -> None:
    # The real-estate subsidiary literally carries "Volksbank" in its name, so the name
    # heuristic flags it too. That is the accepted false-positive direction (ROADMAP P2.1):
    # the flag only suppresses UGB-ratio expectations, it never deletes the GmbH's real data.
    assert clf(legal_form, name) is not None  # flagged — acceptable over-capture


@pytest.mark.parametrize(
    "legal_form,name",
    [
        ("GES", "Mustermann Datenbank GmbH"),  # "bank" inside Datenbank — guarded
        ("GES", "Tischlerei Werkbank OG"),
        ("GES", "Spielbank Austria GmbH"),  # casino, not a credit institution
        ("GES", "Bäckerei Habernig GmbH"),
        ("AG", "OMV Aktiengesellschaft"),
        ("GES", None),
        (None, None),
        ("EU", ""),
    ],
)
def test_does_not_flag_ordinary_companies(legal_form: str | None, name: str | None) -> None:
    assert clf(legal_form, name) is None
