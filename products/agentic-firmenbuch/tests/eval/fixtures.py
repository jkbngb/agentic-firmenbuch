"""Deterministic fixture universe for the tier-1 eval harness (T13).

A small, hand-curated set of presented docs that exercises every search intent the goldens
cover — name lookup + relevance, ÖNACE concept industry, region, radius, financial screen,
and a zero-hit relaxation case. Built into an in-memory store so `--ci` runs are
fast and reproducible (no live DB, RU cents = 0). Keep the fnrs stable — the goldens key on them.
"""

from __future__ import annotations

from typing import Any

from fbl_core.storage import InMemoryCosmosStore
from fbl_core_at.geo import plz_centroid

PRESENTED = "10_presentation"

# Presentation stores the Bundesland CODE (search maps "Wien"→"W"); mirror that here so the
# fixtures behave exactly like live docs.
_BL_CODE = {
    "Burgenland": "B",
    "Kärnten": "K",
    "Niederösterreich": "N",
    "Oberösterreich": "O",
    "Salzburg": "S",
    "Steiermark": "St",
    "Tirol": "T",
    "Vorarlberg": "V",
    "Wien": "W",
}


def _doc(
    fnr: str,
    *,
    name: str,
    bundesland: str,
    plz: str,
    bilanzsumme: float | None = None,
    equity_ratio: float | None = None,
    profile: str | None = None,
    oenace_division: str | None = None,
    oenace_group: str | None = None,
    geschaeftszweig: str | None = None,
    legal_form: str = "GES",
) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "id": fnr,
        "fnr": fnr,
        "identity": {"fnr": fnr, "name": name, "legal_form": legal_form, "status": "active"},
        "location": {"bundesland": _BL_CODE.get(bundesland, bundesland), "postal_code": plz},
        "company": {"description": geschaeftszweig, "last_filing_year": 2024},
        "financials": {
            "latest": {"bilanzsumme": bilanzsumme},
            "has_guv_latest": bilanzsumme is not None,
        },
        "ratios": {"equity_ratio": {"latest": equity_ratio}},
        "growth": {"profile": profile},
        "provenance": {"data_version": 7},
    }
    centroid = plz_centroid(plz)
    if centroid is not None:
        lat, lng = centroid
        doc["location"].update(lat=lat, lng=lng, geo={"type": "Point", "coordinates": [lng, lat]})
    if oenace_division is not None:
        doc["industry"] = {
            "geschaeftszweig": geschaeftszweig,
            "oenace": {
                "division": oenace_division,
                "group": oenace_group,
                "section": "C",
                "division_label_de": "Maschinenbau",
                "group_label_de": "Maschinenbau",
            },
        }
    return doc


# fnr → doc. Curated so each golden has an unambiguous expected winner.
_DOCS: list[dict[str, Any]] = [
    # Name lookup + relevance: the AG (no Bilanzsumme) must beat the bigger subsidiary.
    _doc("069548b", name="NOVOMATIC AG", bundesland="Niederösterreich", plz="2352"),
    _doc(
        "111111a",
        name="NOVOMATIC Sports Betting Solutions GmbH",
        bundesland="Wien",
        plz="1010",
        bilanzsumme=114_000.0,
    ),
    _doc(
        "222222b",
        name="Red Bull GmbH",
        bundesland="Salzburg",
        plz="5330",
        bilanzsumme=2_000_000_000.0,
    ),
    # OÖ Anlagenbau (division 28).
    _doc(
        "300001a",
        name="Oberösterreich Anlagenbau GmbH",
        bundesland="Oberösterreich",
        plz="4020",
        oenace_division="28",
        oenace_group="28.9",
        geschaeftszweig="Anlagenbau und Maschinenbau",
        bilanzsumme=8_000_000.0,
        equity_ratio=0.55,
        profile="fast_growing",
    ),
    _doc(
        "300002b",
        name="Linzer Maschinen GmbH",
        bundesland="Oberösterreich",
        plz="4030",
        oenace_division="28",
        oenace_group="28.1",
        geschaeftszweig="Maschinenbau, Anlagenbau",
        bilanzsumme=5_000_000.0,
        equity_ratio=0.75,
        profile="stable",
    ),
    _doc(
        "300003c",
        name="Welser Anlagentechnik GmbH",
        bundesland="Oberösterreich",
        plz="4600",
        oenace_division="28",
        oenace_group="28.2",
        geschaeftszweig="Anlagenbau",
        bilanzsumme=3_000_000.0,
        equity_ratio=0.40,
        profile="growing",
    ),
    # Vienna region companies (financial screen).
    _doc(
        "400001a",
        name="Wiener Handels GmbH",
        bundesland="Wien",
        plz="1020",
        bilanzsumme=12_000_000.0,
        equity_ratio=0.30,
    ),
    _doc(
        "400002b",
        name="Donau Immobilien GmbH",
        bundesland="Wien",
        plz="1030",
        bilanzsumme=45_000_000.0,
        equity_ratio=0.20,
        geschaeftszweig="Immobilienverwaltung",
    ),
    # Radius cluster around Gmunden / Vöcklabruck (Oberösterreich).
    _doc(
        "500001a",
        name="Gmundner Keramik Handel GmbH",
        bundesland="Oberösterreich",
        plz="4810",
        bilanzsumme=1_500_000.0,
    ),
    _doc(
        "500002b",
        name="Vöcklabrucker Bau GmbH",
        bundesland="Oberösterreich",
        plz="4840",
        bilanzsumme=2_500_000.0,
        geschaeftszweig="Baugewerbe",
    ),
    _doc(
        "500003c",
        name="Attnang Logistik GmbH",
        bundesland="Oberösterreich",
        plz="4800",
        bilanzsumme=900_000.0,
    ),
    # Steiermark, for a region-negative check.
    _doc(
        "600001a",
        name="Grazer Software GmbH",
        bundesland="Steiermark",
        plz="8010",
        bilanzsumme=4_000_000.0,
        geschaeftszweig="Softwareentwicklung",
    ),
]


def build_store() -> InMemoryCosmosStore:
    store = InMemoryCosmosStore()
    for d in _DOCS:
        store.upsert(PRESENTED, d)
    return store


def all_fnrs() -> list[str]:
    return [d["fnr"] for d in _DOCS]
