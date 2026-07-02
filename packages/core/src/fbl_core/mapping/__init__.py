"""Canonical position mappings (Appendix C/D).

The authoritative lookup table ``appendix_position_mapping.json`` (317 entries) is
copied verbatim from the prototype; everything else here is a typed accessor over it.
"""

from __future__ import annotations

from .canonical import (
    BILANZ_FIELD_TO_CANONICAL,
    EBT_CANONICAL,
    EMPLOYEES_CANONICAL,
    GUV_FIELD_TO_CANONICAL,
    INTEREST_EXPENSE_CANONICAL,
    MAPPING_VERSION,
    MATERIALAUFWAND_CANONICAL,
    CanonicalPosition,
    Taxonomy,
    codes_for_canonical,
    load_taxonomy,
    paragraph_ref,
    paragraph_ref_for_canonical,
    v4_for_canonical,
)
from .jab40_map import canonical_for_v4, is_known_v4
from .legacy_map import canonical_for_hgb, is_known_hgb

__all__ = [
    "BILANZ_FIELD_TO_CANONICAL",
    "EBT_CANONICAL",
    "EMPLOYEES_CANONICAL",
    "GUV_FIELD_TO_CANONICAL",
    "INTEREST_EXPENSE_CANONICAL",
    "MAPPING_VERSION",
    "MATERIALAUFWAND_CANONICAL",
    "CanonicalPosition",
    "Taxonomy",
    "canonical_for_hgb",
    "canonical_for_v4",
    "codes_for_canonical",
    "is_known_hgb",
    "is_known_v4",
    "load_taxonomy",
    "paragraph_ref",
    "paragraph_ref_for_canonical",
    "v4_for_canonical",
]
