#!/usr/bin/env python
"""Extract the official Statistik Austria ÖNACE 2008 → 2025 correspondence into the
crosswalk JSON shipped with ``fbl_core.classification`` — correctly this time.

The source Excel (OENACE2008_2025_Korrespondenz.xlsx, sheet ÖNACE08→ÖNACE25) is a **1:n
correspondence**: one 2008 subclass may have several 2025 target rows, each with a
``Korr.Beschreibung`` explaining which activities go where. The first extraction collapsed
this into a dict, so an arbitrary row won per code — which put every 70.2 consultancy into
73.3 (PR) and every petrol station into 35.15 (electricity trade, the EV-charging edge row).
Issue #34 documents the fallout.

Deterministic resolution rules, per 2008 CLASS (4-digit, the level the LLM emits):
  R1  all target rows agree on one 2025 group          → that group (clean 1:1)
  R2  a row says "Alle Tätigkeiten"                    → that row's group wins
  R3  an identity row exists (same class code in 2025) → identity wins (the dominant,
      name-sake case; side rows are explicitly-described edge activities)
  R3b drop rows whose 2025 title starts with "Vermittlungstätigkeiten" (2025 split every
      trade/service class into doing-vs-brokering; the doing row is the dominant case);
      if the survivors agree on one group → that group
  R4  otherwise                                         → the class is AMBIGUOUS: recorded
      with all candidate groups + official descriptions; the build test (P1) forces these
      to be resolved by an explicit entry in MANUAL_RESOLUTIONS below, never silently.

Every manual resolution picks the dominant real-world activity; the full official row set
stays in `ambiguous_class_2008` so every choice is auditable.

Run:  uv run --with openpyxl python scripts/extract_oenace_crosswalk.py
Writes: packages/core/src/fbl_core/classification/data/oenace/oenace2008_2025_crosswalk.json
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "packages/core/src/fbl_core/classification/data/oenace"
XLSX = DATA / "OENACE2008_2025_Korrespondenz.xlsx"
OUT = DATA / "oenace2008_2025_crosswalk.json"

# R4 leftovers: 2008 classes whose official rows split into several 2025 groups with no
# "Alle Tätigkeiten" row, no identity target and no R3b resolution. Resolved to the row
# covering the dominant real-world activity; alternatives stay in `ambiguous_class_2008`.
MANUAL_RESOLUTIONS: dict[str, str] = {
    "14.13": "14.2",  # Oberbekleidung: non-knit outerwear is the bulk (knit sliver -> 14.1)
    "14.14": "14.2",  # Wäsche: non-knit underwear is the bulk
    "14.19": "14.2",  # sonstige Bekleidung/Zubehör (knit baby sliver -> 14.1)
    "16.29": "16.2",  # Holzwaren (wooden shoe parts sliver -> 15.2)
    "22.19": "22.1",  # sonstige Gummiwaren (rubber shoe parts sliver -> 15.2)
    "22.29": "22.2",  # sonstige Kunststoffwaren (Kunstrasen/shoe-part slivers elsewhere)
    "35.14": "35.1",  # Elektrizitätshandel (broker sliver -> 35.4)
    "43.39": "43.3",  # sonstiger Ausbau (boat-fitout + post-construction-cleaning slivers)
    "45.11": "47.8",  # car trade <=3.5t: consumer dealerships dominate (wholesale -> 46.7)
    "45.19": "46.7",  # trucks/trailers: wholesale dominates (camping retail -> 47.8)
    "45.31": "46.7",  # GH Kraftwagenteile (Großhandelsvermittlung sliver -> 46.1)
    "45.40": "47.8",  # motorcycles: retail trade dominates (repair sliver -> 95.3)
    "46.69": "46.6",  # GH sonstige Maschinen (PV split stays inside 46.6; A/C sliver 46.8)
    "47.59": "47.5",  # EH Möbel/Einrichtung dominates (Musikinstrumente sliver -> 47.6)
    "47.81": "47.2",  # market-stall food retail -> food product lines
    "47.82": "47.7",  # market-stall clothing/shoes -> 47.7
    "47.89": "47.7",  # market-stall other goods -> catch-all sonstige Neuwaren
    "47.99": "47.7",  # sonstiger EH a.n.g. (vending/door-to-door) -> sonstige Neuwaren
    "61.20": "61.1",  # drahtlose Telekommunikation (SMS-over-internet sliver -> 61.9)
    "62.01": "62.1",  # Programmierung ohne Verlegen (publish-own-software -> 58.2)
    "63.11": "63.1",  # Datenverarbeitung/Hosting proper (game/video portals slivers)
    "63.12": "60.3",  # Webportale (Web-Suchportale sliver -> 63.9)
    "64.20": "64.2",  # Beteiligungsgesellschaften (own-asset mgmt sliver -> 64.9)
    "74.10": "74.1",  # Design proper (Innenarchitekten sliver -> 71.1)
    "74.90": "74.9",  # sonstige wissenschaftl./techn. Tätigkeiten (security consulting sliver)
    "82.19": "82.1",  # Sekretariats-/Copy-Shops (photocopying sliver -> 18.1)
    "90.02": "90.3",  # Dienstleistungen für darstellende Kunst (rights mgmt sliver -> 74.9)
    "90.03": "90.1",  # künstlerisches Schaffen (restoration sliver -> 91.3)
    "96.09": "96.9",  # sonstige persönliche Dienstleistungen
    "99.00": "99.0",  # exterritoriale Organisationen: not in the Excel; identity in 2025
}

_CODE = re.compile(r"([A-U])\s*(\d{2})(?:\.(\d{2}))?(?:-(\d))?")


def parse_code(cell: str) -> tuple[str | None, str | None, str | None]:
    """'G 47.30-0' -> (class '47.30', subclass '47.30-0', group '47.3')."""
    m = _CODE.search(str(cell or ""))
    if not m or m.group(3) is None:
        return None, None, None
    cls = f"{m.group(2)}.{m.group(3)}"
    sub = f"{cls}-{m.group(4)}" if m.group(4) is not None else None
    grp = cls[: cls.index(".") + 2]
    return cls, sub, grp


def main() -> None:
    ws = openpyxl.load_workbook(XLSX, read_only=True)["OENACE08_OENACE25_Korrespondenz"]
    rows = list(ws.iter_rows(values_only=True))[1:]  # skip header

    # per 2008 class: list of (2025 class, 2025 group, 2025 title, beschreibung)
    targets: dict[str, list[dict[str, str]]] = defaultdict(list)
    sub_pairs: dict[str, list[str]] = defaultdict(list)  # full 1:n at subclass level
    for r in rows:
        src, tgt, besch = r[0], r[2], (r[4] or "")
        cls08, sub08, _ = parse_code(src)
        cls25, sub25, grp25 = parse_code(tgt)
        if not cls08 or not cls25:
            continue
        targets[cls08].append(
            {
                "class_2025": cls25,
                "group_2025": grp25 or "",
                "title_2025": str(r[3] or ""),
                "description": str(besch).strip(),
            }
        )
        if sub08 and sub25:
            sub_pairs[sub08].append(sub25)

    class_map: dict[str, str] = {}
    ambiguous: dict[str, list[dict[str, str]]] = {}
    rule_count = {"R1": 0, "R2": 0, "R3": 0, "R4-manual": 0, "R4-open": 0}

    # classes absent from the Excel entirely (99.00): manual identity entries apply too
    for cls08, g25 in MANUAL_RESOLUTIONS.items():
        if cls08 not in targets:
            class_map[cls08] = g25
            rule_count["R4-manual"] += 1

    for cls08, rows08 in sorted(targets.items()):
        groups = sorted({t["group_2025"] for t in rows08})
        if len(groups) == 1:  # R1
            class_map[cls08] = groups[0]
            rule_count["R1"] += 1
            continue
        alle = [t for t in rows08 if t["description"].lower() == "alle tätigkeiten"]
        if alle:  # R2
            class_map[cls08] = alle[0]["group_2025"]
            rule_count["R2"] += 1
            continue
        identity = [t for t in rows08 if t["class_2025"] == cls08]
        if identity:  # R3
            class_map[cls08] = identity[0]["group_2025"]
            ambiguous[cls08] = rows08  # keep alternatives visible
            rule_count["R3"] += 1
            continue
        doing = [t for t in rows08 if not t["title_2025"].startswith("Vermittlungstätigkeiten")]
        doing_groups = sorted({t["group_2025"] for t in doing})
        if doing and len(doing_groups) == 1:  # R3b
            class_map[cls08] = doing_groups[0]
            ambiguous[cls08] = rows08
            rule_count["R3b"] = rule_count.get("R3b", 0) + 1
            continue
        ambiguous[cls08] = rows08  # R4
        if cls08 in MANUAL_RESOLUTIONS:
            class_map[cls08] = MANUAL_RESOLUTIONS[cls08]
            rule_count["R4-manual"] += 1
        else:
            rule_count["R4-open"] += 1

    # legacy group-level table, re-derived with the same rules (majority of the class map
    # inside each 2008 group; identity tie-break). Kept only for reading OLD stored docs.
    group_map: dict[str, str] = {}
    by_group: dict[str, list[str]] = defaultdict(list)
    for cls08, g25 in class_map.items():
        by_group[cls08[: cls08.index(".") + 2]].append(g25)
    for g08, g25s in sorted(by_group.items()):
        counts = defaultdict(int)
        for g in g25s:
            counts[g] += 1
        best = max(counts.items(), key=lambda kv: (kv[1], kv[0] == g08))
        group_map[g08] = best[0]

    out = {
        "_meta": {
            "source": "Statistik Austria, OENACE2008_2025_Korrespondenz.xlsx (official)",
            "method": (
                "class-level (4-digit) resolution of the 1:n correspondence; rules: "
                "R1 unanimous, R2 'Alle Tätigkeiten' row wins, R3 identity row wins, "
                "R4 manual (audited). Extractor: scripts/extract_oenace_crosswalk.py"
            ),
            "rule_counts": rule_count,
        },
        "class_2008_to_2025_group": class_map,
        "ambiguous_class_2008": ambiguous,
        "group_2008_to_2025": group_map,
        "subclass_2008_to_2025_all": {k: sorted(set(v)) for k, v in sorted(sub_pairs.items())},
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"classes mapped: {len(class_map)}  rules: {rule_count}")
    if rule_count["R4-open"]:
        print("\nR4 OPEN (need MANUAL_RESOLUTIONS entries):")
        for cls08, rows08 in sorted(ambiguous.items()):
            if cls08 in class_map:
                continue
            print(f"  {cls08}:")
            for t in rows08:
                print(
                    f"    -> {t['group_2025']} ({t['title_2025'][:50]}) [{t['description'][:60]}]"
                )
    # sanity spot checks
    for probe, want in [("70.22", "70.2"), ("70.21", "73.3"), ("47.30", "47.3"), ("45.20", "95.3")]:
        got = class_map.get(probe)
        print(f"  probe {probe} -> {got}  {'OK' if got == want else f'EXPECTED {want} ⚠️'}")


if __name__ == "__main__":
    main()
