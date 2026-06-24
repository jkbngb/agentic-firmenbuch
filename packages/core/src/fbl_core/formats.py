"""Filing-format detection — shared by ``parse`` and ``firmenbuch_client`` (§8.2/§8.4).

Lives in ``core`` so the two stages share one source of truth (siblings never
import each other, §3). Detection is by XML namespace + structural signals:

* ``legacy_finanzonline`` — ``finanzonline.bmf.gv.at`` namespace, ``HGB_*`` codes, ``GJ``.
* ``firmenbuch_2025`` — same namespace but with ``GESCHAEFTSJAHR`` (value at ``BETRAG_GJ``).
* ``jab40_semantic`` — ``justiz.gv.at`` v4.0 namespace, semantic element names.
"""

from __future__ import annotations

from typing import Literal

from lxml import etree

XmlVariant = Literal["legacy_finanzonline", "firmenbuch_2025", "jab40_semantic"]


def _local(elem: etree._Element) -> str:
    name: str = etree.QName(elem).localname
    return name


def _has(root: etree._Element, name: str) -> bool:
    return any(isinstance(e.tag, str) and _local(e) == name for e in root.iter())


def _has_hgb_codes(root: etree._Element) -> bool:
    return any(
        isinstance(e.tag, str) and _local(e).startswith(("HGB_", "XXX_")) for e in root.iter()
    )


def detect_xml_variant(root: etree._Element) -> XmlVariant:
    """Return the XML variant of a parsed filing root element."""
    ns = (etree.QName(root).namespace or "").lower()

    if "justiz.gv.at" in ns and ("bilanzierung" in ns or "v4" in ns or "4.0" in ns):
        return "jab40_semantic"
    if "finanzonline" in ns:
        return "firmenbuch_2025" if _has(root, "GESCHAEFTSJAHR") else "legacy_finanzonline"

    # Namespace absent/unknown — fall back to structural signals.
    if _has(root, "GESCHAEFTSJAHR"):
        return "firmenbuch_2025"
    if _has_hgb_codes(root):
        return "legacy_finanzonline"
    return "jab40_semantic"


def detect_xml_variant_bytes(data: bytes) -> XmlVariant:
    """Parse *data* and return its XML variant."""
    return detect_xml_variant(etree.fromstring(data))
