"""Canonical position extraction (§8.4, §15b-3/4/6/8).

Pulls every recognized financial position out of a filing and maps it to its
canonical name via the taxonomy. Unknown codes/elements are kept in a
``passthrough`` so nothing is silently dropped (§5.1). ``WERT_TSD = j`` scales
HGB values x1000. ``BETRAG_VJ`` (prior-year column) is never used as the value of
record - only the current-year column is authoritative (§15b-8).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from lxml import etree

from fbl_core.mapping import canonical_for_hgb, canonical_for_v4

from .xml_common import child_by_local, local_name, parse_amount

logger = logging.getLogger(__name__)

# The value carriers themselves — never a position, only the amount/label of one.
_VALUE_CARRIERS = frozenset({"POSTENZEILE", "BETRAG", "BETRAG_GJ", "BETRAG_VJ"})

# Local names that are structural containers / scalars, not financial positions.
# Includes the JAb 4.0 value carriers (POSTENZEILE/BETRAG_*) and the version stamp, so a
# parent's own value is never re-counted as a bogus passthrough "position" (§5.1 hygiene).
_NON_POSITION_V4 = frozenset(
    {
        "UEBERMITTLUNG",
        "BILANZ",
        "GUV",
        "GUV_GKV",
        "GUV_UKV",
        "GESCHAEFTSJAHR",
        "ALLGEMEINE_ANGABEN",
        "POSTENZEILE",
        "BETRAG_GJ",
        "BETRAG_VJ",
        "BETRAG",
        "BILANZ_VERSION",
    }
)


@dataclass
class ExtractResult:
    """Outcome of position extraction for one filing."""

    canonical_values: dict[str, float] = field(default_factory=dict)
    # Prior-year column (BETRAG_VJ) per canonical — NOT a value of record (§15b-8);
    # used only to cross-check the previous filing (prior-year reconciliation, §8.5).
    prior_year_values: dict[str, float] = field(default_factory=dict)
    # Official source code(s) each canonical was parsed from — keeps BOTH on a collision
    # (two distinct codes → one canonical), never overwrites the value (§-traceability).
    source_codes: dict[str, list[str]] = field(default_factory=dict)
    passthrough: dict[str, float] = field(default_factory=dict)
    provenance: dict[str, str] = field(default_factory=dict)  # canonical -> source path
    wert_tsd_applied: bool = False

    def record_code(self, canonical: str, code: str) -> None:
        """Append a source code for *canonical*, keeping both on a collision (§15b-6)."""
        codes = self.source_codes.setdefault(canonical, [])
        if code in codes:
            return
        if codes:
            logger.warning(
                "source-code collision: canonical %r already from %s; also %r "
                "(both kept; value stays from the first)",
                canonical,
                codes,
                code,
            )
        codes.append(code)

    def record_passthrough(self, code: str, value: float, label: str | None = None) -> None:
        """Keep an unrecognized value-bearing element, never dropping or overwriting (§5.1).

        Free-text slots (``FREI``/``FREIER_SUB_POSTEN`` …) reuse the same code for different
        positions, so the key carries the ``TEXT`` label when present, and a ``#n`` suffix
        disambiguates any remaining clash — so two ``FREI`` rows both survive.
        """
        key = f"{code}: {label}" if label else code
        base, i = key, 2
        while key in self.passthrough:
            key = f"{base} #{i}"
            i += 1
        self.passthrough[key] = value


def element_path(elem: etree._Element) -> str:
    """Slash-joined local-name path from the document root to *elem*."""
    names = [local_name(a) for a in reversed(list(elem.iterancestors())) if isinstance(a.tag, str)]
    names.append(local_name(elem))
    return "/".join(names)


def _scaling_active(root: etree._Element, year_block: str) -> bool:
    """True if the current-year column is reported in thousands (``WERT_TSD = j``).

    The flag lives in the current fiscal-year block (``GJ`` / ``GESCHAEFTSJAHR``);
    ``VOR_GJ``'s flag governs the prior-year column and is ignored here.
    """
    for elem in root.iter():
        if isinstance(elem.tag, str) and local_name(elem) == year_block:
            flag = child_by_local(elem, "WERT_TSD")
            if flag is not None and (flag.text or "").strip().lower() == "j":
                return True
    return False


def _hgb_value(elem: etree._Element, value_mode: str) -> float | None:
    if value_mode == "betrag_gj":  # firmenbuch_2025: BETRAG_GJ (direct or under POSTENZEILE)
        return _betrag_gj(elem)
    # legacy_finanzonline: POSTENZEILE/BETRAG
    posten = child_by_local(elem, "POSTENZEILE")
    if posten is None:
        return None
    return parse_amount(_direct_text(posten, "BETRAG"))


def _prior_value(elem: etree._Element, value_mode: str) -> float | None:
    """Prior-year column: ``POSTENZEILE/BETRAG_VJ`` (all formats) — cross-check only."""
    posten = child_by_local(elem, "POSTENZEILE")
    if posten is not None:
        vj = parse_amount(_direct_text(posten, "BETRAG_VJ"))
        if vj is not None:
            return vj
    return parse_amount(_direct_text(elem, "BETRAG_VJ"))


def _betrag_gj(elem: etree._Element) -> float | None:
    """Current-year value: ``POSTENZEILE/BETRAG_GJ`` (JAb 4.0 / fb_2025) or a direct child."""
    posten = child_by_local(elem, "POSTENZEILE")
    if posten is not None:
        value = parse_amount(_direct_text(posten, "BETRAG_GJ"))
        if value is not None:
            return value
    return parse_amount(_direct_text(elem, "BETRAG_GJ"))


def _any_value(elem: etree._Element) -> float | None:
    """A current-year value via any carrier: POSTENZEILE/BETRAG[_GJ] or a direct BETRAG[_GJ].

    Used to detect free-slot / unknown positions (``FREI``, ``FREIER_SUB_POSTEN``,
    ``GEB_BEFREIUNG``, …) that carry a real amount but no recognized code, so they are
    captured in passthrough rather than silently dropped (§5.1).
    """
    posten = child_by_local(elem, "POSTENZEILE")
    for carrier in (posten, elem):
        if carrier is None:
            continue
        for tag in ("BETRAG", "BETRAG_GJ"):
            value = parse_amount(_direct_text(carrier, tag))
            if value is not None:
                return value
    return None


def _label(elem: etree._Element) -> str | None:
    """The free-text position label (``TEXT``), direct or under POSTENZEILE, if present."""
    posten = child_by_local(elem, "POSTENZEILE")
    for carrier in (elem, posten):
        if carrier is not None:
            text = _direct_text(carrier, "TEXT")
            if text and text.strip():
                return text.strip()
    return None


def _direct_text(parent: etree._Element, name: str) -> str | None:
    child = child_by_local(parent, name)
    return None if child is None or child.text is None else child.text


def _v4_scale(root: etree._Element) -> tuple[float, bool]:
    """JAb 4.0 reporting unit: EINHEIT 'T' → values in thousands (x1000), else euros."""
    for elem in root.iter():
        if isinstance(elem.tag, str) and local_name(elem) == "DARSTELLUNG_EINGEREICHT":
            einheit = (_direct_text(elem, "EINHEIT") or "").strip().upper()
            if einheit == "T":
                return 1000.0, True
            break
    return 1.0, False


def extract_hgb(root: etree._Element, *, value_mode: str, year_block: str) -> ExtractResult:
    """Extract HGB_/XXX_ positions (legacy and firmenbuch_2025)."""
    result = ExtractResult(wert_tsd_applied=_scaling_active(root, year_block))
    scale = 1000.0 if result.wert_tsd_applied else 1.0

    for elem in root.iter():
        if not isinstance(elem.tag, str):
            continue
        code = local_name(elem)
        if code in _VALUE_CARRIERS:
            continue
        if not code.startswith(("HGB_", "XXX_")):
            # A non-HGB element can still carry a real amount in a free-text slot
            # (FREI/FREIER_SUB_POSTEN/GEB_BEFREIUNG …). Capture it in passthrough so the
            # §5.1 no-loss guarantee holds for legacy filings too — never drop it.
            free = _any_value(elem)
            if free is not None:
                result.record_passthrough(code, free * scale, _label(elem))
            continue
        value = _hgb_value(elem, value_mode)
        if value is None:
            continue  # container element (no own POSTENZEILE/BETRAG_GJ)
        value *= scale
        canonical = canonical_for_hgb(code)
        if canonical is None:
            result.record_passthrough(code, value, _label(elem))  # never dropped (§5.1)
            continue
        result.record_code(canonical, code)  # keep every code; collision-safe (§15b-6)
        if canonical not in result.canonical_values:
            result.canonical_values[canonical] = value
            result.provenance[canonical] = element_path(elem)
            prior = _prior_value(elem, value_mode)
            if prior is not None:
                result.prior_year_values[canonical] = prior * scale
    return result


def extract_v4(root: etree._Element) -> ExtractResult:
    """Extract semantic JAb 4.0 positions (§15b-2).

    The real JAb 4.0 schema carries the canonical identity in the **semantic element
    name** (``AKTIVA``, ``ANLAGEVERMOEGEN``, ``EIGENKAPITAL``, …) with the value in a
    **child ``POSTENZEILE/BETRAG_GJ``** (``BETRAG_VJ`` is the prior year, never used as
    the value of record). Verified against a real filing (tests/fixtures/raw/030536g…).
    ``EINHEIT='T'`` scales values x1000.
    """
    scale, scaled = _v4_scale(root)
    result = ExtractResult(wert_tsd_applied=scaled)
    for elem in root.iter():
        if not isinstance(elem.tag, str):
            continue
        name = local_name(elem)
        if name in _NON_POSITION_V4:
            continue
        # value at POSTENZEILE/BETRAG_GJ (real schema); fall back to own text (flat).
        value = _betrag_gj(elem)
        if value is None:
            value = parse_amount(elem.text)
        if value is None:
            continue  # structural container with no own amount
        value *= scale
        canonical = canonical_for_v4(name)
        if canonical is None:
            result.record_passthrough(name, value, _label(elem))  # never dropped (§5.1)
            continue
        result.record_code(canonical, name)  # the v4 element name is the source code
        if canonical not in result.canonical_values:
            result.canonical_values[canonical] = value
            result.provenance[canonical] = element_path(elem)
            prior = _prior_value(elem, "betrag_gj")
            if prior is not None:
                result.prior_year_values[canonical] = prior * scale
    return result
