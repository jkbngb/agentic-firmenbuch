"""Legacy FinanzOnline mapping: ``HGB_*``/``XXX_*`` codes → canonical (Appendix A).

Thin accessor over the canonical taxonomy so the parser does not need to know the
JSON shape. ``XXX_*`` and any unrecognized codes return ``None`` and are carried by
the parser's passthrough — never silently dropped (§5.1, §15b-3).
"""

from __future__ import annotations

from .canonical import load_taxonomy


def canonical_for_hgb(code: str) -> str | None:
    """Return the canonical name for an ``HGB_*``/``XXX_*`` code, or ``None``."""
    return load_taxonomy().hgb_to_canonical.get(code)


def is_known_hgb(code: str) -> bool:
    return code in load_taxonomy().hgb_to_canonical
