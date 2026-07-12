"""Build the PLZ→centroid table for radius search (T12) from the GeoNames AT postal-code dump.

Source: https://download.geonames.org/export/zip/AT.zip  (CC-BY 4.0 — see NOTICE).
Committed output: core_at/.../mapping/plz_geo.json  {"1010": {"lat":.., "lng":.., "place":".."}}.
The output is checked in (offline, reproducible); this script exists so it can be regenerated
when GeoNames updates. One entry per PLZ: coordinates are the mean of every GeoNames row sharing
the PLZ (its centroid); ``place`` is the most frequent place name for that PLZ (the main town).

Usage:
    uv run python scripts/build_plz_geo.py            # download + build
    uv run python scripts/build_plz_geo.py --txt AT.txt   # from an already-extracted file
"""

from __future__ import annotations

import argparse
import io
import json
import urllib.request
import zipfile
from collections import Counter
from pathlib import Path

GEONAMES_URL = "https://download.geonames.org/export/zip/AT.zip"
_MAPPING = (
    Path(__file__).resolve().parents[1]
    / "products/agentic-firmenbuch/packages/core_at/src/fbl_core_at/mapping"
)
OUT = _MAPPING / "plz_geo.json"
OUT_PLACES = _MAPPING / "plz_places.json"


def _rows_from_txt(text: str) -> list[list[str]]:
    return [line.split("\t") for line in text.splitlines() if line.strip()]


def _download_txt() -> str:
    with urllib.request.urlopen(GEONAMES_URL, timeout=120) as resp:
        data = resp.read()
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return zf.read("AT.txt").decode("utf-8")


def build(rows: list[list[str]]) -> dict[str, dict[str, object]]:
    """GeoNames columns (tab-separated): country, postal_code, place_name, admin1_name,
    admin1_code, admin2_name, admin2_code, admin3_name, admin3_code, lat, lng, accuracy."""
    lats: dict[str, list[float]] = {}
    lngs: dict[str, list[float]] = {}
    places: dict[str, Counter[str]] = {}
    for r in rows:
        if len(r) < 11 or r[0] != "AT":
            continue
        plz, place, lat, lng = r[1].strip(), r[2].strip(), r[9].strip(), r[10].strip()
        if not plz or not lat or not lng:
            continue
        lats.setdefault(plz, []).append(float(lat))
        lngs.setdefault(plz, []).append(float(lng))
        places.setdefault(plz, Counter())[place] += 1
    out: dict[str, dict[str, object]] = {}
    for plz in sorted(lats):
        la, lo = lats[plz], lngs[plz]
        # most common place name for this PLZ (its main town), ties broken alphabetically
        place = min(places[plz].most_common(), key=lambda kv: (-kv[1], kv[0]))[0]
        out[plz] = {
            "lat": round(sum(la) / len(la), 5),
            "lng": round(sum(lo) / len(lo), 5),
            "place": place,
        }
    return out


def build_places(rows: list[list[str]]) -> dict[str, list[dict[str, object]]]:
    """Reverse index for the ``near.place`` anchor: every GeoNames place name (case-folded) →
    the distinct PLZs it names, each with a coordinate. So "Vöcklabruck" resolves even when it's
    not its PLZ's dominant label, and a name in several regions ("Neudorf") surfaces every
    candidate for the runtime disambiguator (fbl_core_at.geo.resolve_place)."""
    seen: dict[tuple[str, str], dict[str, object]] = {}
    for r in rows:
        if len(r) < 11 or r[0] != "AT":
            continue
        plz, place, lat, lng = r[1].strip(), r[2].strip(), r[9].strip(), r[10].strip()
        if not plz or not place or not lat or not lng:
            continue
        key = (place.casefold(), plz)
        if key not in seen:  # first coordinate seen for this (place, plz) pair
            seen[key] = {
                "plz": plz,
                "lat": round(float(lat), 5),
                "lng": round(float(lng), 5),
                "name": place,
            }
    index: dict[str, list[dict[str, object]]] = {}
    for (cf, _plz), entry in seen.items():
        index.setdefault(cf, []).append(entry)
    return {k: sorted(v, key=lambda e: str(e["plz"])) for k, v in sorted(index.items())}


def main() -> None:
    parser = argparse.ArgumentParser(description="Build plz_geo.json from GeoNames AT.")
    parser.add_argument("--txt", help="path to an already-extracted AT.txt (skips download)")
    args = parser.parse_args()

    text = Path(args.txt).read_text(encoding="utf-8") if args.txt else _download_txt()
    rows = _rows_from_txt(text)
    table = build(rows)
    OUT.write_text(json.dumps(table, ensure_ascii=False, sort_keys=True, indent=0) + "\n")
    print(f"wrote {len(table)} PLZ centroids to {OUT}")
    places = build_places(rows)
    OUT_PLACES.write_text(json.dumps(places, ensure_ascii=False, sort_keys=True) + "\n")
    print(f"wrote {len(places)} place names to {OUT_PLACES}")


if __name__ == "__main__":
    main()
