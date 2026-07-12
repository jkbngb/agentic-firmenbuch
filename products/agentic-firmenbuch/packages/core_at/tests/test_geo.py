"""T12 — PLZ geocoding + place resolution over the committed GeoNames tables."""

from __future__ import annotations

from fbl_core_at.geo import haversine_km, plz_centroid, resolve_place


def test_plz_centroid_known_and_unknown() -> None:
    lat, lng = plz_centroid("1010")  # type: ignore[misc]
    assert abs(lat - 48.21) < 0.05 and abs(lng - 16.37) < 0.05  # Vienna, inner city
    assert plz_centroid("0000") is None and plz_centroid(None) is None


def test_place_resolves_single_town() -> None:
    match, cands = resolve_place("Gmunden")
    assert match is not None and cands == []
    assert match.plz == "4810"


def test_place_resolution_is_case_insensitive() -> None:
    a, _ = resolve_place("vöcklabruck")
    b, _ = resolve_place("VÖCKLABRUCK")
    assert a is not None and b is not None and a.plz == b.plz


def test_ambiguous_place_lists_candidates() -> None:
    # "Neudorf" names distinct towns across several Bundesländer.
    match, cands = resolve_place("Neudorf")
    assert match is None and len(cands) > 1
    assert all(c.plz for c in cands)


def test_unknown_place_returns_nothing() -> None:
    assert resolve_place("Definitelynotatown") == (None, [])


def test_acceptance_4865_within_30km_of_voecklabruck_not_5km() -> None:
    lat, lng = plz_centroid("4865")  # type: ignore[misc]
    vm, _ = resolve_place("Vöcklabruck")
    assert vm is not None
    dist = haversine_km(lat, lng, vm.lat, vm.lng)
    assert dist <= 30.0 and dist > 5.0  # the exact-circle boundary the radius filter must honor
