"""Austria-specific helpers (Bundesland from postal code).

The Bundesland is resolved from an **exact PLZ→Bundesland table** (``mapping/plz_bundesland.json``,
2501 real Austrian postal codes) rather than the postal code's leading digit. Leading-digit
mapping is too coarse: PLZ region 6 is split between Tirol (60xx–66xx) and **Vorarlberg**
(67xx–69xx), and Osttirol (99xx) belongs to Tirol, not Kärnten — a first-digit rule silently
folds Vorarlberg into Tirol and Osttirol into Kärnten. The table is derived from the official
Gemeindekennziffer (Statistik Austria; its leading digit encodes the Bundesland) joined to the
PLZ, with Wien (exclusive leading digit 1) filled in and postal-region interpolation for the few
PLZ that carry no municipal seat. Codes are the single letters used across §9 (V = Vorarlberg,
St = Steiermark, W = Wien, …).

For a PLZ absent from the table (e.g. a brand-new one) we fall back to a leading-digit heuristic
that at least honours the Tirol/Vorarlberg split, so the coarse case can never again hide V.
"""

from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources

# Leading-digit fallback for PLZ not present in the exact table. Only region 6 is ambiguous:
# 67xx–69xx is Vorarlberg, everything else in region 6 is Tirol.
_PLZ_FIRST_DIGIT = {
    "1": "W",  # Wien (owns leading digit 1 exclusively)
    "2": "N",  # Niederösterreich (+ parts of Burgenland)
    "3": "N",  # Niederösterreich
    "4": "O",  # Oberösterreich
    "5": "S",  # Salzburg
    "7": "B",  # Burgenland
    "8": "St",  # Steiermark
    "9": "K",  # Kärnten (Osttirol 99xx is Tirol, but those PLZ are all in the exact table)
}


@lru_cache(maxsize=1)
def _plz_table() -> dict[str, str]:
    """The exact PLZ→Bundesland lookup, loaded once from the bundled mapping."""
    raw = resources.files("fbl_core_at.mapping").joinpath("plz_bundesland.json").read_text("utf-8")
    data: dict[str, str] = json.loads(raw)
    return data


def _normalize_plz(plz: str) -> str | None:
    """Return the 4-digit Austrian PLZ token, or None if it doesn't look like one."""
    digits = "".join(ch for ch in plz.strip() if ch.isdigit())
    return digits[:4] if len(digits) >= 4 else None


def bundesland_from_plz(plz: str | None) -> str | None:
    """Return the Bundesland letter for an Austrian postal code, or None.

    Exact lookup against the bundled table; for an unknown PLZ, a leading-digit fallback that
    still splits Vorarlberg (67xx–69xx) from Tirol.
    """
    if not plz:
        return None
    key = _normalize_plz(plz)
    if key is None:
        return None
    hit = _plz_table().get(key)
    if hit is not None:
        return hit
    if key[0] == "6":
        return "V" if key[:2] in {"67", "68", "69"} else "T"
    return _PLZ_FIRST_DIGIT.get(key[0])
