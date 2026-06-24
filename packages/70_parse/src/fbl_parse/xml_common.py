"""Namespace-aware XML helpers shared by the format extractors (§8.4).

Kept format-agnostic: local-name access (the filings are heavily namespaced),
tolerant number parsing, German name hyphen-gluing, and defensive date parsing.
"""

from __future__ import annotations

from datetime import date

from lxml import etree


def local_name(elem: etree._Element) -> str:
    """Return an element's local name, ignoring its XML namespace."""
    name: str = etree.QName(elem).localname
    return name


def child_by_local(parent: etree._Element, name: str) -> etree._Element | None:
    """First direct child of *parent* whose local name is *name*, else None."""
    for child in parent:
        if isinstance(child.tag, str) and local_name(child) == name:
            return child
    return None


def text_of(parent: etree._Element | None, name: str) -> str | None:
    """Trimmed text of the first direct child named *name*, else None."""
    if parent is None:
        return None
    child = child_by_local(parent, name)
    if child is None or child.text is None:
        return None
    text: str = child.text.strip()
    return text or None


def first_descendant(root: etree._Element, name: str) -> etree._Element | None:
    """First descendant (any depth) with local name *name*, else None."""
    for elem in root.iter():
        if isinstance(elem.tag, str) and local_name(elem) == name:
            return elem
    return None


def parse_amount(raw: str | None) -> float | None:
    """Parse a monetary string to float, tolerating blanks/sign; None on failure.

    Source values are plain decimals (e.g. ``-60472.38``). We do not guess
    thousands separators here — scaling is applied explicitly via ``WERT_TSD``.
    """
    if raw is None:
        return None
    cleaned = raw.strip()
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_int(raw: str | None) -> int | None:
    """Parse an integer string (e.g. employee count), None on failure."""
    if raw is None:
        return None
    cleaned = raw.strip()
    if not cleaned:
        return None
    try:
        return int(float(cleaned))
    except ValueError:
        return None


def parse_iso_date(raw: str | None) -> date | None:
    """Parse a ``YYYY-MM-DD`` date defensively; None if malformed (§15b-14)."""
    if raw is None:
        return None
    cleaned = raw.strip()
    if not cleaned:
        return None
    try:
        return date.fromisoformat(cleaned)
    except ValueError:
        return None


def glue_name(segments: list[str]) -> str | None:
    """Join multi-line name segments with hyphen-gluing (§15b-5).

    A segment ending in ``-`` joins to the next without a space (a word split
    across lines, e.g. ``Waren-`` + ``handel`` → ``Warenhandel``); otherwise
    segments join with a single space.
    """
    parts = [s.strip() for s in segments if s and s.strip()]
    if not parts:
        return None
    out = parts[0]
    for seg in parts[1:]:
        out = out[:-1] + seg if out.endswith("-") else f"{out} {seg}"
    return out


def age_at(birth: date, signature: date) -> float:
    """Age in years at signing = (signature - birth)/365.25, rounded to 1 decimal."""
    return round((signature - birth).days / 365.25, 1)
