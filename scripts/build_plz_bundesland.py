#!/usr/bin/env python3
"""Regenerate ``mapping/plz_bundesland.json`` — the exact PLZ→Bundesland lookup.

Source of truth: the Austrian municipality list "oe_gemeinden" (bresu, CC BY-SA 4.0), which
carries each municipality's Gemeindekennziffer (GKZ) and PLZ. The GKZ's leading digit encodes
the Bundesland per Statistik Austria (1 Burgenland … 9 Wien). We:

  1. map every PLZ in the Gemeinde list to its Bundesland via the GKZ leading digit,
  2. fill Wien (which owns leading PLZ digit 1 exclusively but is absent from the seat list),
  3. for the PLZ that appear in our plz_geo.json but carry no municipal seat (city sub-districts
     etc.), take the majority Bundesland of Gemeinde-PLZ sharing the same 2-digit postal region
     (falling back to the 1-digit region) — postal regions do not cross Bundesland borders.

Run: ``uv run python scripts/build_plz_bundesland.py``. It is deterministic; the committed
JSON is the product of this script and is validated against known anchor PLZ before writing.
"""

from __future__ import annotations

import csv
import io
import json
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

CSV_URL = "https://raw.githubusercontent.com/bresu/oe_gemeinden/master/gemeinden_CSV.csv"
MAPPING = Path(__file__).resolve().parents[1] / (
    "products/agentic-firmenbuch/packages/core_at/src/fbl_core_at/mapping"
)
GKZ2BL = {"1": "B", "2": "K", "3": "N", "4": "O", "5": "S", "6": "St", "7": "T", "8": "V", "9": "W"}

# Anchors that pin the tricky boundaries — Vorarlberg (67–69xx), Osttirol (99xx = Tirol),
# Innviertel (52xx = OÖ), Südburgenland (838x = Bgld), Wien specials (>1300), a Steiermark/
# Kärnten border PLZ, etc. The build refuses to write if any anchor is wrong.
ANCHORS = {
    "1010": "W", "1300": "W", "1400": "W", "1610": "W",
    "6900": "V", "6971": "V", "6767": "V", "6710": "V", "6991": "V",
    "6020": "T", "6460": "T", "9900": "T", "9992": "T",
    "5280": "O", "4780": "O", "5020": "S", "5453": "S",
    "8380": "B", "7000": "B", "7540": "B",
    "8020": "St", "8554": "St", "8563": "St",
    "9020": "K", "9433": "K", "3100": "N", "2870": "N",
}


def _fetch_csv() -> dict[str, str]:
    with urllib.request.urlopen(CSV_URL) as resp:
        text = resp.read().decode("utf-8-sig")
    out: dict[str, str] = {}
    for row in csv.DictReader(io.StringIO(text), delimiter=";"):
        plz = (row.get("PLZ") or "").strip()
        gkz = (row.get("Gemeindekennziffer") or "").strip()
        if plz and gkz and gkz[0] in GKZ2BL:
            out[plz] = GKZ2BL[gkz[0]]
    return out


def build() -> dict[str, str]:
    csv_map = _fetch_csv()
    geo = json.loads((MAPPING / "plz_geo.json").read_text("utf-8"))
    by2: dict[str, Counter[str]] = defaultdict(Counter)
    by1: dict[str, Counter[str]] = defaultdict(Counter)
    for plz, bl in csv_map.items():
        if plz.isdigit():
            by2[plz[:2]][bl] += 1
            by1[plz[:1]][bl] += 1

    def prefix_bl(plz: str) -> str | None:
        if plz[:2] in by2:
            return by2[plz[:2]].most_common(1)[0][0]
        if plz[:1] in by1:
            return by1[plz[:1]].most_common(1)[0][0]
        return None

    final: dict[str, str] = {}
    for plz in sorted(geo):
        if plz.isdigit() and plz[0] == "1":  # leading digit 1 = Wien, exclusively
            final[plz] = "W"
        elif plz in csv_map:
            final[plz] = csv_map[plz]
        else:
            bl = prefix_bl(plz)
            if bl is not None:
                final[plz] = bl
    missing = [p for p in geo if p not in final]
    if missing:
        raise SystemExit(f"unassigned PLZ: {missing[:20]}")
    for plz, expected in ANCHORS.items():
        got = final.get(plz)
        if got != expected:
            raise SystemExit(f"anchor {plz}: expected {expected}, got {got}")
    return final


def main() -> None:
    table = build()
    out = MAPPING / "plz_bundesland.json"
    out.write_text(json.dumps(table, ensure_ascii=False, sort_keys=True), "utf-8")
    dist = dict(sorted(Counter(table.values()).items()))
    print(f"wrote {len(table)} PLZ → {out}")
    print(f"Bundesland distribution: {dist}")


if __name__ == "__main__":
    main()
