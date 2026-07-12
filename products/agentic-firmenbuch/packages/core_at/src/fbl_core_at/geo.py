"""PLZ geocoding + place resolution for radius search (T12).

Backed by two committed GeoNames-derived tables (CC-BY 4.0, see NOTICE; regenerate with
``scripts/build_plz_geo.py``):

* ``mapping/plz_geo.json``   — ``{plz: {lat, lng, place}}``, the PLZ centroid used to geo-tag
  every company at present time and to anchor a ``near.postal_code`` query.
* ``mapping/plz_places.json`` — ``{place_casefold: [{plz, lat, lng, name}]}``, the reverse index
  behind ``near.place``: a town name → its location(s), so an ambiguous name (several distinct
  towns, e.g. "Neudorf") can be reported back instead of silently picking one.

Pure + offline (no network at runtime). Distances are haversine kilometres.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Any

# Two GeoNames rows for the same real town cluster tightly; genuinely different towns sharing a
# name sit far apart. 20 km cleanly separates "Wien" (79 identical-coordinate PLZs → one place)
# from "Neudorf" (a dozen towns across several Bundesländer → ambiguous).
_CLUSTER_KM = 20.0


@dataclass(frozen=True)
class PlaceMatch:
    """A resolved anchor: a representative PLZ + coordinate + display name for one location."""

    lat: float
    lng: float
    plz: str
    name: str


@lru_cache(maxsize=1)
def _plz_geo() -> dict[str, dict[str, Any]]:
    raw = resources.files("fbl_core_at.mapping").joinpath("plz_geo.json").read_text("utf-8")
    data: dict[str, dict[str, Any]] = json.loads(raw)
    return data


@lru_cache(maxsize=1)
def _plz_places() -> dict[str, list[dict[str, Any]]]:
    raw = resources.files("fbl_core_at.mapping").joinpath("plz_places.json").read_text("utf-8")
    data: dict[str, list[dict[str, Any]]] = json.loads(raw)
    return data


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in kilometres."""
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def plz_centroid(postal_code: str | None) -> tuple[float, float] | None:
    """``(lat, lng)`` for a PLZ, or ``None`` if unknown. Accepts an exact 4-digit PLZ."""
    if not postal_code:
        return None
    entry = _plz_geo().get(postal_code.strip())
    if entry is None:
        return None
    return float(entry["lat"]), float(entry["lng"])


def _cluster(entries: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Greedily group entries whose coordinates lie within ``_CLUSTER_KM`` of a cluster's first
    member — one cluster per real town, several clusters when a name denotes distinct towns."""
    clusters: list[list[dict[str, Any]]] = []
    for e in entries:
        for cl in clusters:
            head = cl[0]
            if haversine_km(e["lat"], e["lng"], head["lat"], head["lng"]) <= _CLUSTER_KM:
                cl.append(e)
                break
        else:
            clusters.append([e])
    return clusters


def _match_of(cluster: list[dict[str, Any]]) -> PlaceMatch:
    """One PlaceMatch for a cluster: its coordinate centroid + the lowest PLZ as representative."""
    lat = sum(e["lat"] for e in cluster) / len(cluster)
    lng = sum(e["lng"] for e in cluster) / len(cluster)
    rep = min(cluster, key=lambda e: str(e["plz"]))
    return PlaceMatch(
        lat=round(lat, 5), lng=round(lng, 5), plz=str(rep["plz"]), name=str(rep["name"])
    )


def resolve_place(place: str) -> tuple[PlaceMatch | None, list[PlaceMatch]]:
    """Resolve a town name to an anchor. Returns:

    * ``(match, [])``   — unambiguous: one town (possibly many PLZs, one cluster).
    * ``(None, cands)`` — ambiguous: several distinct towns share the name; each candidate lists
      its representative PLZ so the caller can re-issue with ``postal_code``.
    * ``(None, [])``    — unknown place.
    """
    entries = _plz_places().get(place.strip().casefold())
    if not entries:
        return None, []
    clusters = _cluster(entries)
    if len(clusters) == 1:
        return _match_of(clusters[0]), []
    candidates = sorted((_match_of(cl) for cl in clusters), key=lambda m: m.plz)
    return None, candidates
