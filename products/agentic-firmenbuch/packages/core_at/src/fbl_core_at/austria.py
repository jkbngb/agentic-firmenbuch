"""Austria-specific helpers (Bundesland from postal code).

The first digit of an Austrian PLZ maps to a Bundesland region. This is the coarse
mapping the product filters on (single-letter Bundesland codes used in §9).
"""

from __future__ import annotations

# First PLZ digit -> Bundesland letter.
# 1 Wien, 2 Niederösterreich/Burgenland(east), 3 Niederösterreich, 4 Oberösterreich,
# 5 Salzburg, 6 Tirol/Vorarlberg, 7 Burgenland, 8 Steiermark, 9 Kärnten.
_PLZ_FIRST_DIGIT = {
    "1": "W",  # Wien
    "2": "N",  # Niederösterreich (+ parts of Burgenland)
    "3": "N",  # Niederösterreich
    "4": "O",  # Oberösterreich
    "5": "S",  # Salzburg
    "6": "T",  # Tirol / Vorarlberg
    "7": "B",  # Burgenland
    "8": "St",  # Steiermark
    "9": "K",  # Kärnten
}


def bundesland_from_plz(plz: str | None) -> str | None:
    """Return the Bundesland letter for an Austrian postal code, or None."""
    if not plz:
        return None
    digits = plz.strip()
    if not digits or not digits[0].isdigit():
        return None
    return _PLZ_FIRST_DIGIT.get(digits[0])
