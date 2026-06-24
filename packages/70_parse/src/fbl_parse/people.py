"""Signatories, employees and the age-at-signing feature (§8.4, §15b-13..16).

GDPR data-minimization: we compute ``age_at_signing`` and derive ``birth_year``,
then DISCARD the day/month — only year + age are ever retained (§5/§8.7). Coverage
is partial: many records have no birth date, so age/birth_year are often None.
"""

from __future__ import annotations

from datetime import date

from lxml import etree

from fbl_core.models.filing import Signatory

from .xml_common import age_at, child_by_local, local_name, parse_int, parse_iso_date, text_of


def extract_employees(root: etree._Element) -> int | None:
    """Average employee count: ``HGB_Form_3_16/ANZAHL`` or the JAb 4.0 element."""
    for elem in root.iter():
        if not isinstance(elem.tag, str):
            continue
        name = local_name(elem)
        if name == "HGB_Form_3_16":
            value = parse_int(text_of(elem, "ANZAHL"))
            if value is not None:
                return value
        elif name == "DURCHSCHNITTLICHE_ANZAHL_ARBEITNEHMER":
            value = parse_int(elem.text)
            if value is not None:
                return value
    return None


def _birth_date(block: etree._Element) -> date | None:
    """Birth date: ``GEB_DAT`` (legacy) / ``GEBURTSDATUM`` (jab40 or §15b-16 child PERSON)."""
    birth = parse_iso_date(text_of(block, "GEB_DAT") or text_of(block, "GEBURTSDATUM"))
    if birth is not None:
        return birth
    person = child_by_local(block, "PERSON")
    if person is not None:
        return parse_iso_date(text_of(person, "GEBURTSDATUM"))
    return None


def _sibling_role_codes(root: etree._Element) -> list[str]:
    """PERS_KENN elements that sit OUTSIDE an UNTER block (positional fallback, §15b-15)."""
    codes: list[str] = []
    for elem in root.iter():
        if not isinstance(elem.tag, str) or local_name(elem) != "PERS_KENN":
            continue
        parent = elem.getparent()
        if parent is not None and local_name(parent) == "UNTER":
            continue
        if elem.text and elem.text.strip():
            codes.append(elem.text.strip())
    return codes


def extract_signatories(root: etree._Element) -> list[Signatory]:
    """Parse signing-officer blocks into :class:`Signatory` objects.

    Legacy/fb_2025 use ``UNTER`` (``V_NAME``/``Z_NAME``/``GEB_DAT``/``DAT_UNT``/
    ``PERS_KENN``); JAb 4.0 uses ``AUFSTELLENDE_PERSONEN/PERSON`` (``VORNAME``/
    ``NACHNAME``/``GEBURTSDATUM``/``DATUM_UNTERSCHRIFT``, no role code).
    """
    blocks = [e for e in root.iter() if isinstance(e.tag, str) and local_name(e) == "UNTER"]
    if not blocks:
        blocks = [e for e in root.iter() if isinstance(e.tag, str) and local_name(e) == "PERSON"]
    sibling_codes = _sibling_role_codes(root)

    signatories: list[Signatory] = []
    for idx, block in enumerate(blocks):
        role = text_of(block, "PERS_KENN")
        if role is None and idx < len(sibling_codes):
            role = sibling_codes[idx]  # positional fallback (§15b-15)

        birth = _birth_date(block)
        signed = parse_iso_date(text_of(block, "DAT_UNT") or text_of(block, "DATUM_UNTERSCHRIFT"))
        age = age_at(birth, signed) if (birth is not None and signed is not None) else None

        signatories.append(
            Signatory(
                first_name=text_of(block, "V_NAME") or text_of(block, "VORNAME"),
                last_name=text_of(block, "Z_NAME") or text_of(block, "NACHNAME"),
                birth_year=birth.year if birth is not None else None,  # YEAR ONLY
                age_at_signing=age,
                signed_at=signed.isoformat() if signed is not None else None,
                role_code=role,
            )
        )
    return signatories


def primary_signatory(signatories: list[Signatory]) -> Signatory | None:
    """Pick the primary signing officer: role ``A`` if present, else the first."""
    if not signatories:
        return None
    for sig in signatories:
        if sig.role_code == "A":
            return sig
    return signatories[0]
