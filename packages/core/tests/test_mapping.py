"""Canonical mapping tests (Technische Spezifikation §8.1 DoD, Appendix C/D).

DoD for `core`: mappings cover every canonical Bilanz/GuV field for BOTH formats
(legacy ``HGB_*`` and semantic ``v4_elements``).
"""

from __future__ import annotations

import pytest

from fbl_core.mapping import (
    BILANZ_FIELD_TO_CANONICAL,
    EMPLOYEES_CANONICAL,
    GUV_FIELD_TO_CANONICAL,
    canonical_for_hgb,
    canonical_for_v4,
    codes_for_canonical,
    is_known_hgb,
    is_known_v4,
    load_taxonomy,
    paragraph_ref,
    paragraph_ref_for_canonical,
    v4_for_canonical,
)
from fbl_core.models.filing import Bilanz, GuV

# ebitda and ebit_strict are COMPUTED (ebitda = ebit + D&A; ebit_strict = EBT + interest),
# not 1:1 single positions, and revenue_basis is a label not a position. ebit and
# operating_result both map 1:1 to zwischensumme_betriebserfolg and stay tested.
_GUV_NON_POSITION = {"ebitda", "ebit_strict", "revenue_basis"}


def test_taxonomy_has_317_entries() -> None:
    tax = load_taxonomy()
    assert len(tax.positions) == 317


def test_taxonomy_matches_declared_entry_count() -> None:
    # The appendix _meta declares its own entry_count; copy must be verbatim.
    from fbl_core.mapping.canonical import _raw_mapping

    raw = _raw_mapping()
    assert raw["_meta"]["entry_count"] == len(load_taxonomy().positions)


@pytest.mark.parametrize("field", [f for f in Bilanz.model_fields])
def test_every_bilanz_field_maps_both_formats(field: str) -> None:
    canonical = BILANZ_FIELD_TO_CANONICAL[field]
    assert codes_for_canonical(canonical), f"{field} -> {canonical} has no HGB codes"
    assert v4_for_canonical(canonical), f"{field} -> {canonical} has no v4 elements"


@pytest.mark.parametrize("field", [f for f in GuV.model_fields if f not in _GUV_NON_POSITION])
def test_every_guv_position_field_maps_both_formats(field: str) -> None:
    canonical = GUV_FIELD_TO_CANONICAL[field]
    assert codes_for_canonical(canonical), f"{field} -> {canonical} has no HGB codes"
    assert v4_for_canonical(canonical), f"{field} -> {canonical} has no v4 elements"


def test_employees_maps_both_formats() -> None:
    assert codes_for_canonical(EMPLOYEES_CANONICAL)
    assert v4_for_canonical(EMPLOYEES_CANONICAL)


def test_known_codes_resolve() -> None:
    assert canonical_for_hgb("HGB_224_2") == "aktiva"
    assert canonical_for_hgb("HGB_224_3_A") == "eigenkapital"
    assert canonical_for_v4("EIGENKAPITAL") == "eigenkapital"
    assert is_known_hgb("HGB_224_3_D")
    assert is_known_v4("UMSATZERLOESE")


def test_some_xxx_codes_are_in_taxonomy() -> None:
    # The appendix actually catalogs some XXX_* non-standard items (e.g. hybride
    # Finanzinstrumente) — those are recognized, not passthrough.
    assert canonical_for_hgb("XXX_224_3_D_X") == "hybride_finanzinstrumente"
    assert is_known_hgb("XXX_224_3_D_X")


def test_unknown_codes_return_none() -> None:
    # Codes absent from the taxonomy resolve to None -> carried by parse passthrough.
    assert canonical_for_hgb("XXX_999_TOTALLY_MADE_UP") is None
    assert canonical_for_hgb("HGB_NONEXISTENT") is None
    assert canonical_for_v4("NOT_A_REAL_ELEMENT") is None
    assert not is_known_hgb("XXX_999_TOTALLY_MADE_UP")


def test_field_map_keys_match_models() -> None:
    assert set(BILANZ_FIELD_TO_CANONICAL) == set(Bilanz.model_fields)
    assert set(GUV_FIELD_TO_CANONICAL) | _GUV_NON_POSITION == set(GuV.model_fields)


def test_paragraph_ref_from_code() -> None:
    # Part A.2: human §-reference derived from the official code structure.
    assert paragraph_ref("HGB_224_2_A_II") == "§224 Abs 2 A II"
    assert paragraph_ref("HGB_224_2") == "§224 Abs 2"
    assert paragraph_ref("XXX_224_3_D_X") == "§224 Abs 3 D X"
    assert paragraph_ref("HGB_231_19") == "§231 Z 19"  # §231 GuV uses Ziffer, not Absatz
    # No numeric paragraph (bare v4 element name) -> None.
    assert paragraph_ref("AKTIVA") is None
    assert paragraph_ref("UMSATZERLOESE") is None


def test_paragraph_ref_for_canonical_uses_appendix() -> None:
    # The §-ref for a canonical comes from its official HGB code in the appendix,
    # so a position parsed from a JAb 4.0 element still resolves the same §-ref.
    assert paragraph_ref_for_canonical("aktiva") == "§224 Abs 2"
    assert paragraph_ref_for_canonical("eigenkapital") == "§224 Abs 3 A"


def test_by_canonical_resolves_without_crashing() -> None:
    # Regression: by_canonical was an @lru_cache over the Taxonomy model (unhashable),
    # so the first call raised TypeError. It must resolve a known entry and return None
    # for an unknown one.
    tax = load_taxonomy()
    pos = tax.by_canonical("aktiva")
    assert pos is not None and pos.canonical == "aktiva"
    assert tax.by_canonical("not-a-real-canonical") is None


def test_code_collisions_are_pinned_to_the_two_known_latent_ones() -> None:
    # Two appendix codes map to >1 canonical; resolution is first-by-appendix-order
    # (the appendix is byte-identical to docs and must not be edited to disambiguate).
    # Neither appears in any live filing, so impact is nil — but pin them so any NEW
    # collision (a real ambiguity) fails CI instead of silently picking by order.
    tax = load_taxonomy()
    assert set(tax.hgb_collisions) == {"HGB_Form_2", "HGB_231_2_4"}
    assert tax.v4_collisions == {}
