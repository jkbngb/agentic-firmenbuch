"""Strict no-loss carry-forward — the automated layer-completeness guarantee (Part B §5.1).

Walks the real leaf fields / positions / codes present at each layer and asserts they
survive to the next: raw → parsed → consolidated → derived → presented. The test FAILS on
any silent loss. Two checks anchor it:

* **raw → parsed:** every value-bearing element in the source XML is either mapped to a
  canonical position or captured in passthrough — zero unaccounted elements.
* **derived → presented:** every derived leaf is present in the served document OR on the
  documented ``PRESENTED_ALLOWLIST`` (and the allowlisted detail is retrievable in full via
  the MCP ``get_full_record`` tool).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from lxml import etree

from fbl_consolidate import consolidate
from fbl_core_at.mapping import canonical_for_hgb, canonical_for_v4
from fbl_derive import derive
from fbl_parse import parse_filing
from fbl_parse.xml_common import local_name
from fbl_present import PRESENTED_ALLOWLIST, present

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "raw"

# Value carriers / structural wrappers — these hold a value but are NOT positions.
_VALUE_TAGS = frozenset({"BETRAG", "BETRAG_GJ"})
_STRUCTURAL = frozenset({"POSTENZEILE", "BETRAG", "BETRAG_GJ", "BETRAG_VJ", "BILANZ_VERSION"})


def _multiyear() -> list[Any]:
    files = sorted((FIXTURES / "490875a_multiyear").glob("*.xml"))
    return [parse_filing(f.read_bytes(), run_id="t") for f in files]


# ---- raw → parsed: every BETRAG-bearing element is mapped or passed through ----------


def _value_owning_codes(root: etree._Element) -> list[str]:
    """Local names of every element that directly owns a current-year value.

    An owner has either a direct ``BETRAG``/``BETRAG_GJ`` child or a ``POSTENZEILE`` child
    that holds one. The value carriers themselves (POSTENZEILE/BETRAG…) are excluded — they
    are the value, not a position.
    """
    owners: list[str] = []
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        name = local_name(el)
        if name in _STRUCTURAL:
            continue
        has_value = False
        for child in el:
            if not isinstance(child.tag, str):
                continue
            cn = local_name(child)
            if cn == "POSTENZEILE":
                has_value = any(
                    isinstance(g.tag, str)
                    and local_name(g) in _VALUE_TAGS
                    and (g.text or "").strip()
                    for g in child
                )
            elif cn in _VALUE_TAGS and (child.text or "").strip():
                has_value = True
            if has_value:
                break
        if has_value:
            owners.append(name)
    return owners


@pytest.mark.parametrize(
    "fixture",
    ["030435h_2020-03-31_jb.xml", "030636d_2023-05-31_jb.xml", "030536g_2025-12-31_jab40.xml"],
)
def test_raw_to_parsed_every_value_element_accounted(fixture: str) -> None:
    raw = (FIXTURES / fixture).read_bytes()
    pf = parse_filing(raw, run_id="t")
    root = etree.fromstring(raw)
    # passthrough keys may carry a "CODE: label" / "CODE #n" suffix — match the bare code.
    passthrough = {k.split(":")[0].split(" #")[0].strip() for k in pf.field_provenance.passthrough}
    unaccounted = [
        code
        for code in _value_owning_codes(root)
        if canonical_for_hgb(code) is None
        and canonical_for_v4(code) is None
        and code not in passthrough
    ]
    assert not unaccounted, (
        f"{fixture}: value elements neither mapped nor in passthrough: {unaccounted}"
    )


# ---- parsed → consolidated: every canonical + passthrough code carried ----------------


def test_parsed_to_consolidated_is_a_superset() -> None:
    filings = _multiyear()
    cons = consolidate("490875a", filings, None, None, run_id="t")
    every_canonical = {c for f in filings for c in f.positions}
    every_passthrough = {c for f in filings for c in f.field_provenance.passthrough}
    assert every_canonical <= set(cons.financials.positions), (
        f"canonicals dropped at consolidate: {every_canonical - set(cons.financials.positions)}"
    )
    assert every_passthrough <= set(cons.financials.passthrough)
    # the typed Bilanz map is an ergonomic VIEW keyed by model field; every one of its
    # canonicals appears in the full positions map (keyed by canonical name).
    from fbl_core_at.mapping import BILANZ_FIELD_TO_CANONICAL

    typed_canonicals = {BILANZ_FIELD_TO_CANONICAL[f] for f in cons.financials.bilanz}
    assert typed_canonicals <= set(cons.financials.positions)


# ---- consolidated → derived: superset, every history preserved ------------------------


def test_consolidated_to_derived_is_a_superset() -> None:
    cons = consolidate("490875a", _multiyear(), None, None, run_id="t")
    der = derive(cons, run_id="t")
    assert set(cons.financials.positions) <= set(der.financials.positions)
    assert set(cons.financials.passthrough) <= set(der.financials.passthrough)
    for canonical, series in cons.financials.positions.items():
        assert der.financials.positions[canonical].history == series.history  # facts unchanged


# ---- derived → presented: leaf walk, nothing dropped that isn't allowlisted ------------


def _leaves(obj: Any, prefix: str = "") -> set[str]:
    """Dotted leaf paths of a JSON-able object; list items collapse to ``[]`` (presence only)."""
    out: set[str] = set()
    if isinstance(obj, dict):
        if not obj:
            out.add(prefix)
        for k, v in obj.items():
            out |= _leaves(v, f"{prefix}.{k}" if prefix else str(k))
    elif isinstance(obj, list):
        if not obj:
            out.add(f"{prefix}[]")
        for item in obj:
            out |= _leaves(item, f"{prefix}[]")
    else:
        out.add(prefix)
    return out


# derived → presented section renames (not a loss, just a different served name).
_RENAMES = {"management.primary_gf": "management.primary_manager"}


def _alias(path: str) -> str:
    for src, dst in _RENAMES.items():
        if path == src or path.startswith(src + "."):
            return dst + path[len(src) :]
    return path


def _allowlisted(path: str) -> bool:
    norm = path[:-2] if path.endswith("[]") else path  # a list leaf collapses to its field
    return any(norm == key or norm.startswith(key + ".") for key in PRESENTED_ALLOWLIST)


def test_derived_to_presented_drops_nothing_off_allowlist() -> None:
    der = derive(consolidate("490875a", _multiyear(), None, None, run_id="t"), run_id="t")
    pres = present(der, current_year=2026, run_id="t")
    derived_leaves = _leaves(der.model_dump(mode="json"))
    # presented presence is checked on the leaf KEY-PATHS (values may legitimately differ).
    presented_leaves = _leaves(pres.model_dump(mode="json"))
    presented_keys = {p.rsplit(".", 1)[0] if p else p for p in presented_leaves} | presented_leaves

    lost = [
        path
        for path in derived_leaves
        if _alias(path) not in presented_leaves
        and _alias(path) not in presented_keys
        and not _allowlisted(path)
    ]
    assert not lost, f"derived leaves dropped from presented (not allowlisted): {sorted(lost)}"


def test_master_data_carries_through_to_presentation() -> None:
    # The master (auszug) path: legal_form, court, location, Geschäftszweig and the
    # signing manager must survive master → consolidated → derived → 10_presentation.
    # (The financial-filing completeness checks above never exercise master, so this is
    # the guard against silently dropping master fields — e.g. the court that was lost.)
    from fbl_core_at.models import Court, Location, Manager, MasterData

    master = MasterData(
        fnr="490875a",
        name="Walter Wagner Transporte GmbH",
        legal_form="GES",
        court=Court(code="818", name="Landesgericht Innsbruck"),
        location=Location(bundesland="T", city="Innsbruck", postal_code="6020", street="Haupt 1"),
        description="Güterbeförderung",
        persons=[Manager(first_name="A", last_name="B", birth_year=1970, role_label="GF")],
    )
    der = derive(consolidate("490875a", _multiyear(), master, None, run_id="t"), run_id="t")
    pres = present(der, current_year=2026, run_id="t").model_dump(mode="json")

    assert pres["identity"]["legal_form"] == "GES"
    assert pres["identity"]["court"] == {"code": "818", "name": "Landesgericht Innsbruck"}
    assert pres["location"]["city"] == "Innsbruck" and pres["location"]["bundesland"] == "T"
    assert pres["company"]["description"] == "Güterbeförderung"
    assert pres["management"] is not None and pres["management"]["primary_manager"] is not None


def test_allowlisted_detail_is_retrievable_via_full_record() -> None:
    # Part B(b/c): the curated-out detail (positions/passthrough/completeness) is retrievable.
    from fbl_core.storage import InMemoryCosmosStore
    from fbl_mcp_server import service

    cons = consolidate("490875a", _multiyear(), None, None, run_id="t")
    der = derive(cons, run_id="t")
    cosmos = InMemoryCosmosStore()
    cosmos.upsert("30_derived", {**der.model_dump(mode="json"), "id": "490875a", "fnr": "490875a"})

    full = service.get_full_record(cosmos, "490875a")["result"]
    assert full["financials"]["positions"], "full record must expose the full positions map"
    assert "completeness" in full["financials"]
    assert "meta" not in full  # internal hash chain still stripped
