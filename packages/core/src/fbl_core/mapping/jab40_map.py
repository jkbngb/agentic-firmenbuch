"""Semantic JAb 4.0 mapping: ``v4_elements`` → canonical (Appendix B).

Thin accessor over the canonical taxonomy for the fully-semantic JAb 4.0 format
(element names like ``EIGENKAPITAL``, ``UMSATZERLOESE``). Unknown elements return
``None`` and are carried by the parser's passthrough (§5.1, §15b-2).
"""

from __future__ import annotations

from .canonical import load_taxonomy


def canonical_for_v4(element: str) -> str | None:
    """Return the canonical name for a JAb 4.0 ``v4_element``, or ``None``."""
    return load_taxonomy().v4_to_canonical.get(element)


def is_known_v4(element: str) -> bool:
    return element in load_taxonomy().v4_to_canonical
