"""operating_result (Betriebserfolg, correctly named) and ebit_strict (true EBIT) — #6.

ebit was overloaded: it is the UGB operating result, NOT strict EBIT. These serve the
operating result under its own name and compute true EBIT (Ergebnis vor Steuern + interest
expense) where the GuV discloses those lines, null otherwise.
"""

from fbl_parse.parser import _build_guv
from fbl_parse.positions import ExtractResult


def test_operating_result_equals_ebit_and_strict_ebit_adds_back_interest() -> None:
    ex = ExtractResult(
        canonical_values={
            "umsatzerloese": 1000.0,
            "zwischensumme_betriebserfolg": 100.0,  # Betriebserfolg (operating result)
            "abschreibungen": -20.0,  # expense, stored negative
            "ergebnis_vor_steuern": 60.0,  # EBT: operating 100 + financial result -40
            "zinsen_und_aehnliche_aufwendungen": -30.0,  # interest expense, negative
            "jahresueberschuss_jahresfehlbetrag": 45.0,
        }
    )
    guv = _build_guv(ex)
    assert guv is not None
    # operating_result is the Betriebserfolg under its correct name — identical to ebit
    assert guv.ebit == 100.0
    assert guv.operating_result == 100.0
    assert guv.ebitda == 120.0  # operating result + D&A (100 - (-20))
    # true EBIT = EBT + interest expense = 60 - (-30) = 90 (differs from operating result)
    assert guv.ebit_strict == 90.0


def test_strict_ebit_is_null_when_positions_absent() -> None:
    """Companies whose GuV lacks the pre-tax result / interest line get ebit_strict = null,
    never a wrong guess. operating_result is still served."""
    ex = ExtractResult(
        canonical_values={
            "umsatzerloese": 1000.0,
            "zwischensumme_betriebserfolg": 100.0,
            "jahresueberschuss_jahresfehlbetrag": 45.0,
        }
    )
    guv = _build_guv(ex)
    assert guv is not None
    assert guv.operating_result == 100.0
    assert guv.ebit == 100.0
    assert guv.ebit_strict is None


def test_strict_ebit_null_if_only_ebt_present() -> None:
    ex = ExtractResult(
        canonical_values={
            "zwischensumme_betriebserfolg": 100.0,
            "ergebnis_vor_steuern": 60.0,  # EBT present but interest missing -> cannot compute
        }
    )
    guv = _build_guv(ex)
    assert guv is not None and guv.ebit_strict is None
