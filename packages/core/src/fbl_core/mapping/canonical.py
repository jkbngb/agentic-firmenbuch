"""Canonical position taxonomy loader (Technische Spezifikation §8.4, Appendix C/D).

Loads ``appendix_position_mapping.json`` (317 canonical positions, copied verbatim
from the prototype) and builds the lookup tables the parser needs:

* ``CanonicalPosition`` — one taxonomy entry.
* ``hgb_to_canonical`` / ``v4_to_canonical`` — source code/element → canonical name.
* ``canonical_codes`` / ``canonical_v4`` — canonical name → its source codes/elements.

The ``Bilanz``/``GuV`` Pydantic model fields use short ergonomic names; the
``BILANZ_FIELD_TO_CANONICAL`` / ``GUV_FIELD_TO_CANONICAL`` tables bind each model
field to its canonical taxonomy entry, so the parser can resolve the exact source
codes for that field in either format.
"""

from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources
from typing import Any

from pydantic import BaseModel

MAPPING_VERSION = "1.0"


class CanonicalPosition(BaseModel):
    """One canonical taxonomy entry from the appendix."""

    canonical: str
    label_de: str
    category: str
    extra_categories: list[str] = []
    hgb_codes: list[str] = []
    v4_elements: list[str] = []


class Taxonomy(BaseModel):
    """The full taxonomy plus the derived reverse lookups."""

    positions: list[CanonicalPosition]
    hgb_to_canonical: dict[str, str]
    v4_to_canonical: dict[str, str]
    canonical_codes: dict[str, list[str]]
    canonical_v4: dict[str, list[str]]
    # codes/elements that appear under more than one canonical (ambiguous)
    hgb_collisions: dict[str, list[str]]
    v4_collisions: dict[str, list[str]]

    def by_canonical(self, name: str) -> CanonicalPosition | None:
        # Linear scan over 317 entries — not hot-path. (Previously an @lru_cache over
        # ``self``, which raises ``TypeError: unhashable type`` because a Pydantic model
        # is not hashable; it only ever worked because nothing called it.)
        return next((p for p in self.positions if p.canonical == name), None)


def _raw_mapping() -> dict[str, Any]:
    data = resources.files("fbl_core.mapping").joinpath("appendix_position_mapping.json")
    with data.open("r", encoding="utf-8") as fh:
        loaded: dict[str, Any] = json.load(fh)
    return loaded


@lru_cache(maxsize=1)
def load_taxonomy() -> Taxonomy:
    """Load and index the canonical taxonomy (cached for the process)."""
    raw = _raw_mapping()
    positions = [CanonicalPosition(**entry) for entry in raw["positions"]]

    hgb_to_canonical: dict[str, str] = {}
    v4_to_canonical: dict[str, str] = {}
    canonical_codes: dict[str, list[str]] = {}
    canonical_v4: dict[str, list[str]] = {}
    hgb_collisions: dict[str, list[str]] = {}
    v4_collisions: dict[str, list[str]] = {}

    for pos in positions:
        canonical_codes[pos.canonical] = list(pos.hgb_codes)
        canonical_v4[pos.canonical] = list(pos.v4_elements)
        for code in pos.hgb_codes:
            if code in hgb_to_canonical and hgb_to_canonical[code] != pos.canonical:
                hgb_collisions.setdefault(code, [hgb_to_canonical[code]]).append(pos.canonical)
            else:
                hgb_to_canonical[code] = pos.canonical
        for elem in pos.v4_elements:
            if elem in v4_to_canonical and v4_to_canonical[elem] != pos.canonical:
                v4_collisions.setdefault(elem, [v4_to_canonical[elem]]).append(pos.canonical)
            else:
                v4_to_canonical[elem] = pos.canonical

    return Taxonomy(
        positions=positions,
        hgb_to_canonical=hgb_to_canonical,
        v4_to_canonical=v4_to_canonical,
        canonical_codes=canonical_codes,
        canonical_v4=canonical_v4,
        hgb_collisions=hgb_collisions,
        v4_collisions=v4_collisions,
    )


# --- Model field → canonical taxonomy name -------------------------------------
# The Bilanz/GuV models (§6) use short names; bind each to its canonical entry.

BILANZ_FIELD_TO_CANONICAL: dict[str, str] = {
    "bilanzsumme": "aktiva",
    "eigenkapital": "eigenkapital",
    "verbindlichkeiten": "verbindlichkeiten",
    "anlagevermoegen": "anlagevermoegen",
    "umlaufvermoegen": "umlaufvermoegen",
    "sachanlagen": "sachanlagen",
    "finanzanlagen": "finanzanlagen",
    "vorraete": "vorraete",
    "forderungen": "forderungen_und_sonstige_vermoegensgegenstaende",
    "cash": "kassenbestand_schecks_guthaben_bei_kreditinstituten",
    "rueckstellungen": "rueckstellungen",
    "stammkapital": "stammkapital",
    "kapitalruecklagen": "kapitalruecklagen",
    "gewinnruecklagen": "gewinnruecklagen",
    "bilanzgewinn_verlust": "bilanzgewinn_bilanzverlust",
}

# ``ebit``/``ebitda`` are mapped/derived in the GuV model:
# ebit = zwischensumme_betriebserfolg; ebitda = ebit - abschreibungen (computed).
# NOTE: ebit here is the UGB *operating result* (Betriebserfolg), which EXCLUDES the
# financial result — a common approximation, NOT strict EBIT (before-interest-and-taxes).
# They diverge for entities with material financial/participation income. Documented in
# FIELD_REFERENCE.md, the site FAQ/felder.html, and the MCP describe_fields catalog.
GUV_FIELD_TO_CANONICAL: dict[str, str] = {
    "umsatzerloese": "umsatzerloese",
    "rohergebnis": "rohergebnis",
    "materialaufwand": "materialaufwand",
    "personalaufwand": "personalaufwand",
    "abschreibungen": "abschreibungen",
    "ebit": "zwischensumme_betriebserfolg",
    "jahresueberschuss": "jahresueberschuss_jahresfehlbetrag",
}

# Other canonical positions surfaced as scalars on ``ParsedFiling``.
EMPLOYEES_CANONICAL = "durchschnittliche_anzahl_arbeitnehmer"
MATERIALAUFWAND_CANONICAL = "materialaufwand"


def codes_for_canonical(name: str) -> list[str]:
    """Legacy ``HGB_*``/``XXX_*`` codes for a canonical name (may be empty)."""
    return load_taxonomy().canonical_codes.get(name, [])


def v4_for_canonical(name: str) -> list[str]:
    """Semantic JAb 4.0 ``v4_elements`` for a canonical name (may be empty)."""
    return load_taxonomy().canonical_v4.get(name, [])


def paragraph_ref(code: str) -> str | None:
    """Human UGB §-reference for an official ``HGB_*``/``XXX_*`` code, or ``None``.

    The code structure mirrors the statute: the first numeric segment is the UGB
    paragraph, the second its Absatz (Ziffer for the §231 GuV), and any further
    segments are the subdivision letters/Roman numerals. Examples::

        HGB_224_2_A_II  -> "§224 Abs 2 A II"
        HGB_224_2       -> "§224 Abs 2"
        HGB_231_19      -> "§231 Z 19"     (§231 GuV positions are numbered by Ziffer)

    Returns ``None`` for codes without a numeric paragraph (e.g. a bare semantic JAb
    4.0 element name) — those carry their §-ref via their canonical instead.
    """
    parts = code.split("_")
    nums = parts[1:]
    if not nums or not nums[0].isdigit():
        return None
    label = f"§{nums[0]}"
    if len(nums) >= 2:
        sep = "Z" if nums[0] == "231" else "Abs"  # §231 GuV uses Ziffer, the Bilanz uses Absatz
        label += f" {sep} {nums[1]}"
    if len(nums) > 2:
        label += " " + " ".join(nums[2:])
    return label


def paragraph_ref_for_canonical(name: str) -> str | None:
    """§-reference for a canonical position, from its primary official HGB code.

    Derived from the appendix (the single source of code↔canonical↔§-label), so a
    position parsed from a semantic JAb 4.0 element still gets the official §-ref via
    its canonical. ``None`` when the canonical has no numeric HGB code (e.g. a
    ``davon``/sub-total line without a statutory paragraph).
    """
    for code in load_taxonomy().canonical_codes.get(name, []):
        ref = paragraph_ref(code)
        if ref is not None:
            return ref
    return None
