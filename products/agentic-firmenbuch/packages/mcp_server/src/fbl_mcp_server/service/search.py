"""``search_companies`` — filtered, sorted, paginated company search (§9)."""

from __future__ import annotations

import contextvars
import math
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from fbl_core.storage import CosmosStoreLike
from fbl_core_at.directories import load_fi_directory_cached
from fbl_core_at.geo import haversine_km, plz_centroid, resolve_place
from fbl_core_at.models import (
    NearFilter,
    PublicProvenance,
    RankSignal,
    Relaxation,
    SearchFilters,
    SearchResponse,
    Sort,
)

from ..errors import BadRequest
from ._common import (
    _BL_NAME_TO_CODE,
    _STATUS_SQL,
    MAX_PAGE_SIZE,
    PRESENTED,
    _card,
    _g,
    _in_range,
    _is_gmbh_filter,
    _legal_form_matches,
)


def _near_anchor(near: NearFilter) -> tuple[float, float, float]:
    """Resolve a ``near`` filter to ``(lat, lng, radius_m)`` or raise ``BadRequest``. Exactly one
    of place/postal_code is required; an ambiguous place lists its candidates, an unknown one asks
    for a postal_code (no silent pick). Radius is clamped to 1..150 km."""
    if (near.place is None) == (near.postal_code is None):
        raise BadRequest("near requires exactly one of 'place' or 'postal_code'")
    radius_km = min(150.0, max(1.0, near.radius_km))
    if near.postal_code is not None:
        centroid = plz_centroid(near.postal_code)
        if centroid is None:
            raise BadRequest(f"unknown postal_code {near.postal_code!r}")
        lat, lng = centroid
    else:
        assert near.place is not None
        match, candidates = resolve_place(near.place)
        if match is None and not candidates:
            raise BadRequest(f"unknown place {near.place!r}; use postal_code instead")
        if match is None:
            listing = "; ".join(f"{m.name} (PLZ {m.plz})" for m in candidates[:10])
            raise BadRequest(
                f"ambiguous place {near.place!r} — matches several towns: {listing}. "
                "Use postal_code to disambiguate."
            )
        lat, lng = match.lat, match.lng
    return lat, lng, radius_km * 1000.0


def _doc_distance_m(doc: dict[str, Any], lat: float, lng: float) -> float | None:
    """Haversine metres from the anchor to a doc's PLZ-centroid, or None if it has no coordinate."""
    dlat = _g(doc, "location", "lat")
    dlng = _g(doc, "location", "lng")
    if not isinstance(dlat, int | float) or not isinstance(dlng, int | float):
        return None
    return haversine_km(lat, lng, float(dlat), float(dlng)) * 1000.0


def _status_matches(doc: dict[str, Any], wanted: str) -> bool:
    status = _g(doc, "identity", "status")
    if wanted == "all":
        return True
    if wanted == "active":
        return bool(status == "active")
    return status in ("historical", "deleted")  # inactive


def _oenace_match(doc: dict[str, Any], level: str, value: str, prefix: str | None) -> bool:
    """In-memory twin of the dual-vintage ÖNACE WHERE clause: match the ÖNACE 2025 block, the
    ÖNACE 2008 twin, or — for division/group — the stored 4-digit ``code_2008`` by prefix, on
    either the v2 ``industry`` or the legacy v1 ``branch`` block."""
    for blk in ("industry", "branch"):
        if value in (_g(doc, blk, "oenace", level), _g(doc, blk, "oenace_2008", level)):
            return True
        if prefix is not None:
            code = _g(doc, blk, "code_2008")
            if isinstance(code, str) and code.startswith(prefix):
                return True
    return False


def _matches(doc: dict[str, Any], f: SearchFilters) -> bool:
    if not _status_matches(doc, f.status):
        return False
    checks: list[bool] = [
        f.name is None or f.name.lower() in (_g(doc, "identity", "name") or "").lower(),
        _legal_form_matches(doc, f.legal_form),
        f.bundesland is None
        or _g(doc, "location", "bundesland") == _BL_NAME_TO_CODE.get(f.bundesland, f.bundesland),
        f.size_gkl is None or _g(doc, "size", "gkl") == f.size_gkl,
        f.has_guv is None or bool(_g(doc, "financials", "has_guv")) == f.has_guv,
        f.has_guv_latest is None
        or bool(_g(doc, "financials", "has_guv_latest")) == f.has_guv_latest,
        f.growth_profile is None or _g(doc, "growth", "profile") == f.growth_profile,
        _in_range(
            _g(doc, "financials", "latest", "bilanzsumme"), f.bilanzsumme_min, f.bilanzsumme_max
        ),
        _in_range(
            _g(doc, "ratios", "equity_ratio", "latest"), f.equity_ratio_min, f.equity_ratio_max
        ),
        _in_range(_g(doc, "financials", "latest", "revenue"), f.revenue_min, f.revenue_max),
        _in_range(_g(doc, "employees", "latest"), f.employees_min, f.employees_max),
        f.last_filing_year_min is None
        or (_g(doc, "company", "last_filing_year") or 0) >= f.last_filing_year_min,
        f.founded_year_min is None
        or (_g(doc, "company", "founded_year") or 0) >= f.founded_year_min,
        f.founded_year_max is None
        or (_g(doc, "company", "founded_year") or 99999) <= f.founded_year_max,
        f.gf_age_min is None
        or (_g(doc, "management", "primary_manager", "age") or 0) >= f.gf_age_min,
        f.manager_name is None
        or f.manager_name.lower() in (_g(doc, "management", "primary_manager_name") or "").lower(),
        f.oenace_section is None or _oenace_match(doc, "section", f.oenace_section, None),
        f.oenace_division is None
        or _oenace_match(doc, "division", f.oenace_division, f"{f.oenace_division}."),
        f.oenace_group is None or _oenace_match(doc, "group", f.oenace_group, f.oenace_group),
        f.geschaeftszweig is None
        or f.geschaeftszweig.lower() in (_g(doc, "company", "description") or "").lower(),
        f.postal_code is None
        or (_g(doc, "location", "postal_code") or "").startswith(f.postal_code),
        f.city is None or f.city.lower() in (_g(doc, "location", "city") or "").lower(),
        f.near is None or _near_matches(doc, f.near),
        f.query is None or _query_matches(doc, f.query),
    ]
    return all(checks)


def _query_matches(doc: dict[str, Any], query: str) -> bool:
    """In-memory twin of the lexical `query` leg: substring in name OR activity/description."""
    q = query.lower()
    name = (_g(doc, "identity", "name") or "").lower()
    desc = (_g(doc, "company", "description") or "").lower()
    return q in name or q in desc


def _match_reason(doc: dict[str, Any], query: str) -> str | None:
    """Which lexical leg(s) a `query` hit matched, for the card's match_reason (T14)."""
    q = query.lower()
    legs = []
    if q in (_g(doc, "identity", "name") or "").lower():
        legs.append("name")
    if q in (_g(doc, "company", "description") or "").lower():
        legs.append("Tätigkeit")
    return "text: " + " + ".join(legs) + " match" if legs else None


def _near_matches(doc: dict[str, Any], near: NearFilter) -> bool:
    """In-memory twin of the radius filter: exact haversine ≤ radius; a doc without a coordinate
    never matches a near query."""
    lat, lng, radius_m = _near_anchor(near)
    dist = _doc_distance_m(doc, lat, lng)
    return dist is not None and dist <= radius_m


def _build_where(f: SearchFilters) -> tuple[str, list[dict[str, Any]]]:
    """Translate SearchFilters into a parameterized Cosmos WHERE clause (server-side filter).

    Field *paths* come from a fixed whitelist (never user input); all *values* are bound as
    query parameters, so this is injection-safe.
    """
    conds: list[str] = ["NOT STARTSWITH(c.id, '__')"]  # skip internal/checkpoint docs
    params: list[dict[str, Any]] = []

    def rng(path: str, lo: float | int | None, hi: float | int | None, key: str) -> None:
        if lo is not None:
            conds.append(f"{path} >= @{key}_min")
            params.append({"name": f"@{key}_min", "value": lo})
        if hi is not None:
            conds.append(f"{path} <= @{key}_max")
            params.append({"name": f"@{key}_max", "value": hi})

    if f.status in _STATUS_SQL:
        conds.append(_STATUS_SQL[f.status])
    if f.name:
        # 3-arg case-insensitive CONTAINS: unlike CONTAINS(LOWER(x), …) it can use the string
        # index on /identity/name (T1), roughly halving RU. Bind the value verbatim — the `true`
        # flag does the case-folding, so lowering it here would defeat the point. (T2)
        conds.append("CONTAINS(c.identity.name, @name, true)")
        params.append({"name": "@name", "value": f.name})
    if f.bundesland is not None:
        conds.append("c.location.bundesland = @bundesland")
        params.append(
            {"name": "@bundesland", "value": _BL_NAME_TO_CODE.get(f.bundesland, f.bundesland)}
        )
    if f.legal_form is not None:
        if _is_gmbh_filter(f.legal_form):
            conds.append("STARTSWITH(c.identity.legal_form, 'GE')")
        else:
            conds.append("c.identity.legal_form = @legal_form")
            params.append({"name": "@legal_form", "value": f.legal_form})
    for path, value, pname in (
        ("c.size.gkl", f.size_gkl, "@size_gkl"),
        ("c.growth.profile", f.growth_profile, "@growth_profile"),
        ("c.financials.has_guv", f.has_guv, "@has_guv"),
        ("c.financials.has_guv_latest", f.has_guv_latest, "@has_guv_latest"),
    ):
        if value is not None:
            conds.append(f"{path} = {pname}")
            params.append({"name": pname, "value": value})
    if f.last_filing_year_min is not None:
        conds.append("c.company.last_filing_year >= @lfy_min")
        params.append({"name": "@lfy_min", "value": f.last_filing_year_min})
    if f.founded_year_min is not None:
        conds.append("c.company.founded_year >= @founded_min")
        params.append({"name": "@founded_min", "value": f.founded_year_min})
    if f.founded_year_max is not None:
        conds.append("c.company.founded_year <= @founded_max")
        params.append({"name": "@founded_max", "value": f.founded_year_max})
    if f.gf_age_min is not None:
        conds.append("c.management.primary_manager.age >= @gf_age_min")
        params.append({"name": "@gf_age_min", "value": f.gf_age_min})
    if f.manager_name:
        conds.append("CONTAINS(c.management.primary_manager_name, @manager_name, true)")
        params.append({"name": "@manager_name", "value": f.manager_name})

    # ÖNACE filters match BOTH classification vintages, transparently, so a caller never hits a
    # dead end for using the "wrong" one: the ÖNACE 2025 block (`oenace`), the ÖNACE 2008 twin
    # (`oenace_2008`, present after the #34 re-grind) and — so the 2008 vintage also works on
    # not-yet-reground docs — a prefix match on the stored 4-digit `code_2008`. Either the v2
    # `industry` or the legacy v1 `branch` block; an undefined path is undefined in Cosmos SQL,
    # so the OR falls through. Motor-vehicle trade (division "45" in ÖNACE 2008, split across
    # 46/47 in 2025) thus resolves whichever code the caller queried. `group` is a reserved SQL
    # keyword → bracket notation. Field paths are a fixed whitelist; values are bound params.
    def oenace_or(paths: list[str], value: str, key: str, prefix_value: str | None) -> None:
        params.append({"name": key, "value": value})
        eq = [f"{p} = {key}" for p in paths]
        if prefix_value is not None:  # code_2008 is the 4-digit class, e.g. "45.11"
            pk = f"{key}_p"
            params.append({"name": pk, "value": prefix_value})
            eq += [
                f"STARTSWITH(c.industry.code_2008, {pk})",
                f"STARTSWITH(c.branch.code_2008, {pk})",
            ]
        conds.append("(" + " OR ".join(eq) + ")")

    if f.oenace_section is not None:
        oenace_or(
            [
                "c.industry.oenace.section",
                "c.branch.oenace.section",
                "c.industry.oenace_2008.section",
                "c.branch.oenace_2008.section",
            ],
            f.oenace_section,
            "@oe_s",
            None,
        )
    if f.oenace_division is not None:  # a 2-digit division prefixes its classes as "45."
        oenace_or(
            [
                "c.industry.oenace.division",
                "c.branch.oenace.division",
                "c.industry.oenace_2008.division",
                "c.branch.oenace_2008.division",
            ],
            f.oenace_division,
            "@oe_d",
            f"{f.oenace_division}.",
        )
    if f.oenace_group is not None:  # a 3-digit group prefixes its classes as "45.1"
        oenace_or(
            [
                'c.industry.oenace["group"]',
                'c.branch.oenace["group"]',
                'c.industry.oenace_2008["group"]',
                'c.branch.oenace_2008["group"]',
            ],
            f.oenace_group,
            "@oe_g",
            f.oenace_group,
        )
    if f.geschaeftszweig:
        conds.append("CONTAINS(c.company.description, @geschaeftszweig, true)")
        params.append({"name": "@geschaeftszweig", "value": f.geschaeftszweig})
    if f.query:
        # Free-text `query` (T14): lexical substring-OR over name + activity, index-friendly via
        # the 3-arg CONTAINS. This is the shippable-now leg; when Cosmos FTS + /embedding vector
        # search are enabled it becomes the lexical half of an RRF hybrid (see SEMANTIC_SEARCH.md).
        conds.append(
            "(CONTAINS(c.identity.name, @query, true) "
            "OR CONTAINS(c.company.description, @query, true))"
        )
        params.append({"name": "@query", "value": f.query})
    if f.postal_code:
        # PLZ is digits — no case folding needed; STARTSWITH already rides the index.
        conds.append("STARTSWITH(c.location.postal_code, @postal_code)")
        params.append({"name": "@postal_code", "value": f.postal_code})
    if f.city:
        conds.append("CONTAINS(c.location.city, @city, true)")
        params.append({"name": "@city", "value": f.city})
    rng("c.financials.latest.bilanzsumme", f.bilanzsumme_min, f.bilanzsumme_max, "bs")
    rng("c.ratios.equity_ratio.latest", f.equity_ratio_min, f.equity_ratio_max, "eq")
    rng("c.financials.latest.revenue", f.revenue_min, f.revenue_max, "rev")
    rng("c.employees.latest", f.employees_min, f.employees_max, "emp")
    if f.near is not None:
        # Indexed bounding-box PRE-filter on lat/lng (a proven cheap range path); the exact circle
        # is applied afterwards in Python via haversine. This avoids depending on ST_DISTANCE's RU
        # cost on serverless (which can't be measured until geo is backfilled); the same lat/lng
        # index also lets ST_DISTANCE be swapped in later with no API change. (T12)
        lat, lng, radius_m = _near_anchor(f.near)
        rkm = radius_m / 1000.0
        dlat = rkm / 111.0
        dlng = rkm / (111.0 * max(0.1, math.cos(math.radians(lat))))
        rng("c.location.lat", lat - dlat, lat + dlat, "lat")
        rng("c.location.lng", lng - dlng, lng + dlng, "lng")
    return " AND ".join(conds), params


# --- zero-hit relaxation (T6) --------------------------------------------------
# Numeric range filters as (min_field, max_field, cosmos_path, mem_path, kind): a *_min/*_max
# pair is ONE relaxation unit (dropping it clears both bounds) and its suggestion is the nearest
# achievable bound over the OTHER-filters result set. Everything here is filter-agnostic — adding
# a filter to SearchFilters makes it a relaxation candidate automatically (a scalar unit); only
# genuine min/max PAIRS need an entry below so they collapse into one unit.
_RANGE_UNITS: dict[str, tuple[str, str, str, tuple[str, ...], str]] = {
    "bilanzsumme_range": (
        "bilanzsumme_min",
        "bilanzsumme_max",
        "c.financials.latest.bilanzsumme",
        ("financials", "latest", "bilanzsumme"),
        "money",
    ),
    "equity_ratio_range": (
        "equity_ratio_min",
        "equity_ratio_max",
        "c.ratios.equity_ratio.latest",
        ("ratios", "equity_ratio", "latest"),
        "ratio",
    ),
    "revenue_range": (
        "revenue_min",
        "revenue_max",
        "c.financials.latest.revenue",
        ("financials", "latest", "revenue"),
        "money",
    ),
    "employees_range": (
        "employees_min",
        "employees_max",
        "c.employees.latest",
        ("employees", "latest"),
        "count",
    ),
    "founded_year_range": (
        "founded_year_min",
        "founded_year_max",
        "c.company.founded_year",
        ("company", "founded_year"),
        "year",
    ),
}
_MAX_RELAXATIONS = 8


def _active_units(f: SearchFilters) -> list[tuple[str, dict[str, Any]]]:
    """``(label, reset)`` per active filter unit; ``reset`` maps each field of the unit to its
    default so ``f.model_copy(update=reset)`` removes exactly that one filter. A ``*_min``/``*_max``
    range pair collapses into a single unit; every other set field is its own scalar unit."""
    defaults = {n: fi.default for n, fi in SearchFilters.model_fields.items()}
    active = {n for n in SearchFilters.model_fields if getattr(f, n) != defaults[n]}
    units: list[tuple[str, dict[str, Any]]] = []
    used: set[str] = set()
    for label, (lo, hi, _cp, _mp, _k) in _RANGE_UNITS.items():
        if {lo, hi} & active:
            units.append((label, {lo: defaults[lo], hi: defaults[hi]}))
            used |= {lo, hi}
    for name in sorted(active - used):
        units.append((name, {name: defaults[name]}))
    return units


def _fmt_range_value(value: float, kind: str) -> str:
    v = float(value)
    if kind == "money":
        if abs(v) >= 1_000_000:
            return f"{v / 1_000_000:.1f}M"
        if abs(v) >= 1_000:
            return f"{v / 1_000:.0f}k"
        return f"{v:.0f}"
    if kind == "ratio":
        return f"{v:.2f}"
    return str(round(v))  # count / year (round → int in py3)


def _suggestion(label: str, lo: float | None, hi: float | None) -> str | None:
    rng = _RANGE_UNITS.get(label)
    if rng is None or lo is None or hi is None:
        return None
    kind = rng[4]
    return f"nearest achievable {label}: {_fmt_range_value(lo, kind)}–{_fmt_range_value(hi, kind)}"


def _relaxations_cosmos(cosmos: CosmosStoreLike, filters: SearchFilters) -> list[Relaxation] | None:
    """Leave-one-out over the active filters, server-side and in parallel: for each unit, COUNT
    with just that filter dropped (and, for a range unit, MIN/MAX of the freed field over the
    remaining result set for a nearest-achievable hint). Only relax when ≥2 filters are active —
    with one filter there's nothing to single out."""
    units = _active_units(filters)
    if len(units) < 2:
        return None
    units = units[:_MAX_RELAXATIONS]

    def work(label: str, reset: dict[str, Any]) -> Relaxation | None:
        where, params = _build_where(filters.model_copy(update=reset))
        rng = _RANGE_UNITS.get(label)
        if rng is not None:
            path = rng[2]
            sql = f"SELECT COUNT(1) AS n, MIN({path}) AS lo, MAX({path}) AS hi FROM c WHERE {where}"
            row = next(iter(cosmos.query(PRESENTED, sql, params)), None)
            if not isinstance(row, dict):
                return None
            cnt = int(row.get("n") or 0)
            if cnt <= 0:
                return None
            hint = _suggestion(label, row.get("lo"), row.get("hi"))
            return Relaxation(dropped=label, total=cnt, suggestion=hint)
        raw = next(
            iter(cosmos.query(PRESENTED, f"SELECT VALUE COUNT(1) FROM c WHERE {where}", params)), 0
        )
        cnt = raw if isinstance(raw, int) else 0
        return Relaxation(dropped=label, total=cnt) if cnt > 0 else None

    with ThreadPoolExecutor(max_workers=min(_MAX_RELAXATIONS, len(units))) as pool:
        futures = [
            pool.submit(contextvars.copy_context().run, work, label, reset)
            for label, reset in units
        ]
        found = [f.result() for f in futures]
    out = sorted((r for r in found if r is not None), key=lambda r: r.total, reverse=True)
    return out or None


def _relaxations_memory(
    rows: list[dict[str, Any]], filters: SearchFilters
) -> list[Relaxation] | None:
    """In-memory twin of :func:`_relaxations_cosmos` over the already-fetched docs."""
    units = _active_units(filters)
    if len(units) < 2:
        return None
    valid = [d for d in rows if not str(d.get("id", "")).startswith("__")]
    out: list[Relaxation] = []
    for label, reset in units[:_MAX_RELAXATIONS]:
        relaxed = filters.model_copy(update=reset)
        matched = [d for d in valid if _matches(d, relaxed)]
        if not matched:
            continue
        suggestion = None
        rng = _RANGE_UNITS.get(label)
        if rng is not None:
            vals = [v for d in matched if (v := _g(d, *rng[3])) is not None]
            if vals:
                suggestion = _suggestion(label, min(vals), max(vals))
        out.append(Relaxation(dropped=label, total=len(matched), suggestion=suggestion))
    out.sort(key=lambda r: r.total, reverse=True)
    return out or None


def _applied_filters(f: SearchFilters, page_size: int) -> dict[str, Any] | None:
    """The filters as actually applied, after the same normalization ``_build_where`` performs:
    Bundesland name→code, GmbH-family→``GE*`` prefix, plus the clamped ``page_size``. ``None`` when
    no filter was active (so an unfiltered search stays clean). Response-only (T9)."""
    defaults = {n: fi.default for n, fi in SearchFilters.model_fields.items()}
    out: dict[str, Any] = {
        n: getattr(f, n) for n in SearchFilters.model_fields if getattr(f, n) != defaults[n]
    }
    if not out:
        return None
    if f.bundesland is not None:  # "Wien" → "W" (as bound in the WHERE clause)
        out["bundesland"] = _BL_NAME_TO_CODE.get(f.bundesland, f.bundesland)
    if f.legal_form is not None and _is_gmbh_filter(f.legal_form):
        out["legal_form"] = "GE*"  # the GmbH family is matched by STARTSWITH(legal_form,'GE')
    if f.near is not None:  # a plain dict, not the model object (JSON-clean echo)
        out["near"] = f.near.model_dump(exclude_none=True)
    out["page_size"] = page_size  # the value after clamping to 1..MAX_PAGE_SIZE
    return out


# Bounding-box candidates to exact-filter for a radius query. A local radius yields far fewer;
# a very dense area is pool-capped (the response's total reflects the exact matches within it).
_NEAR_POOL_LIMIT = 3000


def _order_near(
    docs: list[dict[str, Any]],
    anchor: tuple[float, float, float],
    sort_field: str,
    descending: bool,
    rank_by: list[RankSignal] | None,
) -> list[dict[str, Any]]:
    """Order the exact-radius result set: by weighted signals, an explicit metric, or (default)
    ascending distance from the anchor, fnr-stable."""
    lat, lng, _ = anchor
    if rank_by:
        return _rank_by_weighted(docs, rank_by)
    if sort_field != "distance":
        return _sorted_nulls_last(docs, sort_field, descending)

    def key(d: dict[str, Any]) -> tuple[bool, float, str]:
        dist = _doc_distance_m(d, lat, lng)
        return (dist is None, dist if dist is not None else 0.0, str(d.get("fnr") or ""))

    return sorted(docs, key=key)


def _search_near(
    cosmos: CosmosStoreLike,
    filters: SearchFilters,
    anchor: tuple[float, float, float],
    sort_field: str,
    descending: bool,
    rank_by: list[RankSignal] | None,
    page: int,
    page_size: int,
    start: int,
) -> SearchResponse:
    """Radius search: indexed bounding-box pool (built into where_sql) → exact haversine filter in
    Python → distance-sorted page, with distance_km on every card. ``_matches`` (which runs the
    same exact haversine via _near_matches) refines the pool for the Cosmos store and IS the whole
    filter for the in-memory store, so one code path serves both."""
    lat, lng, _radius_m = anchor
    where_sql, params = _build_where(filters)
    pool_sql = f"SELECT * FROM c WHERE {where_sql} OFFSET 0 LIMIT {_NEAR_POOL_LIMIT}"
    pool = [
        d
        for d in cosmos.query(PRESENTED, pool_sql, params)
        if not str(d.get("id", "")).startswith("__") and _matches(d, filters)
    ]
    ordered = _order_near(pool, anchor, sort_field, descending, rank_by)
    total = len(ordered)
    page_docs = ordered[start : start + page_size]

    directory = load_fi_directory_cached(cosmos)
    cards = []
    for d in page_docs:
        card = _card(d, directory)
        dist = _doc_distance_m(d, lat, lng)
        if dist is not None:
            card.distance_km = round(dist / 1000.0, 1)
        if filters.query:
            card.match_reason = _match_reason(d, filters.query)
        cards.append(card)
    data_version_max = max((_g(d, "provenance", "data_version") or 0 for d in page_docs), default=0)
    return SearchResponse(
        data_version_max=data_version_max,
        total=total,
        page=page,
        page_size=page_size,
        results=cards,
        has_more=start + len(cards) < total,
        applied_filters=_applied_filters(filters, page_size),
        provenance=PublicProvenance(),
    )


def search_companies(
    cosmos: CosmosStoreLike,
    filters: SearchFilters | None = None,
    sort: Sort | None = None,
    page: int = 1,
    page_size: int = 25,
) -> SearchResponse:
    """Filtered, sorted, paginated company search (§9).

    Filtering/sorting/paging run **server-side** in Cosmos (WHERE + ORDER BY + OFFSET/LIMIT)
    so a single query touches only one page, never the whole ~160k-doc container. The
    in-memory test store ignores SQL and returns every doc, so the same predicate is also
    applied in Python — detected by whether the COUNT(1) came back as a real integer.
    """
    filters = filters or SearchFilters()
    page = max(1, page)
    page_size = max(1, min(page_size, MAX_PAGE_SIZE))
    start = (page - 1) * page_size

    # Resolve the radius anchor up front so an ambiguous/unknown place fails fast and cleanly.
    near_anchor = _near_anchor(filters.near) if filters.near is not None else None

    rank_by = sort.rank_by if sort else None
    requested_field = sort.field if sort and sort.field else None
    # Default sort: distance for a near query, else Bilanzsumme.
    sort_field = requested_field or ("distance" if near_anchor is not None else "bilanzsumme")
    descending = sort.descending if sort else True
    # Reject an unknown sort field loudly (it used to silently drop ordering): the LLM gets a
    # clear list of valid fields instead of a mystifyingly unordered page. (T11) "distance" is a
    # computed field valid only alongside a near filter (T12).
    if sort_field == "distance":
        if near_anchor is None:
            raise BadRequest("sort field 'distance' requires a 'near' filter")
    elif sort_field not in _SORT_PATHS:
        valid = [*sorted(_SORT_PATHS), "distance"]
        raise BadRequest(f"unknown sort field {sort_field!r}; valid: {', '.join(valid)}")
    order_path = _SORT_PATHS.get(sort_field)

    # Radius search has exact-count / distance-sort semantics that don't fit the two-bucket
    # COUNT+page machinery, so it takes a dedicated pool path (T12).
    if near_anchor is not None:
        return _search_near(
            cosmos, filters, near_anchor, sort_field, descending, rank_by, page, page_size, start
        )

    where_sql, params = _build_where(filters)
    count_sql = f"SELECT VALUE COUNT(1) FROM c WHERE {where_sql}"

    # Fire the total COUNT and the first page CONCURRENTLY. On real Cosmos these are two
    # independent scans and the sync azure-cosmos client is thread-safe for reads, so running
    # them in parallel roughly halves the wall-clock of a full-page search (T4). We can't know
    # a priori whether the store is Cosmos (COUNT→int) or the in-memory twin (SQL ignored, COUNT
    # returns a doc), so both futures always launch; on the in-memory branch the page future's
    # result is simply discarded (its dataset is tiny — negligible waste).
    def _count() -> Any:
        return next(iter(cosmos.query(PRESENTED, count_sql, params)), 0)

    # Each worker runs inside a COPY of the current context so the RU metered by cosmos.query
    # (a ContextVar in fbl_core.metrics) lands in this request's accumulator — ContextVars don't
    # propagate into pool threads on their own. Separate copies per submit: a Context object
    # can't be entered by two threads at once. (T4 + T5)
    with ThreadPoolExecutor(max_workers=2) as pool:
        count_future = pool.submit(contextvars.copy_context().run, _count)
        page_future = pool.submit(
            contextvars.copy_context().run,
            _cosmos_page,
            cosmos,
            where_sql,
            params,
            order_path,
            descending,
            start,
            page_size,
        )
        raw_total = count_future.result()
        relaxations: list[Relaxation] | None = None
        # A NAME lookup over a small result set → rank by match quality, not the numeric sort (T10).
        if isinstance(raw_total, int):  # Cosmos: COUNT(1) → real total; page server-side
            total = raw_total
            if rank_by and total > 0:
                # Weighted multi-signal ranking (T11): pool top-per-signal, re-rank by the mix.
                page_future.result()
                page_docs = _rank_by_signals_cosmos(
                    cosmos, where_sql, params, rank_by, start, page_size
                )
            elif filters.name and 0 < total <= _POOL_LIMIT:
                # Fetch the whole (small) candidate pool unordered and re-rank in Python; total
                # stays exact. The parallel ordered page is discarded — cheap at ≤200 rows.
                page_future.result()
                pool_sql = f"SELECT * FROM c WHERE {where_sql} OFFSET 0 LIMIT {_POOL_LIMIT}"
                candidates = [
                    d
                    for d in cosmos.query(PRESENTED, pool_sql, params)
                    if not str(d.get("id", "")).startswith("__")
                ]
                ranked = _rank_by_name(candidates, filters.name, sort_field)
                page_docs = ranked[start : start + page_size]
            else:
                rows = page_future.result()
                page_docs = [d for d in rows if not str(d.get("id", "")).startswith("__")]
            if total == 0:
                # Zero hits with ≥2 active filters → tell the caller which single filter to drop
                # instead of forcing it into a blind retry spiral (T6). ~0.3 s, parallel.
                relaxations = _relaxations_cosmos(cosmos, filters)
        else:  # in-memory fake: SQL ignored, every doc returned → filter/sort/paginate in Python
            page_future.result()  # drain the (ignored) page future so no thread is left dangling
            rows = list(cosmos.query(PRESENTED, f"SELECT * FROM c WHERE {where_sql}", params))
            matched = [
                d
                for d in rows
                if not str(d.get("id", "")).startswith("__") and _matches(d, filters)
            ]
            total = len(matched)
            if rank_by and total > 0:
                matched = _rank_by_weighted(matched, rank_by)  # same fn the Cosmos pool re-ranks by
            elif filters.name and 0 < total <= _POOL_LIMIT:
                matched = _rank_by_name(matched, filters.name, sort_field)  # same fn as Cosmos path
            else:
                matched = _sorted_nulls_last(matched, sort_field, descending)
            page_docs = matched[start : start + page_size]
            if total == 0:
                relaxations = _relaxations_memory(rows, filters)

    directory = load_fi_directory_cached(cosmos)
    cards = [_card(d, directory) for d in page_docs]
    if filters.query:  # annotate why each hit matched the free-text query (T14)
        for card, doc in zip(cards, page_docs, strict=False):
            card.match_reason = _match_reason(doc, filters.query)
    data_version_max = max((_g(d, "provenance", "data_version") or 0 for d in page_docs), default=0)
    return SearchResponse(
        data_version_max=data_version_max,
        total=total,
        page=page,
        page_size=page_size,
        results=cards,
        has_more=start + len(cards) < total,
        relaxations=relaxations,
        applied_filters=_applied_filters(filters, page_size),
        provenance=PublicProvenance(),
    )


_SORT_PATHS = {
    "bilanzsumme": ("financials", "latest", "bilanzsumme"),
    "revenue": ("financials", "latest", "revenue"),
    "equity_ratio": ("ratios", "equity_ratio", "latest"),
    "employees": ("employees", "latest"),
    "last_filing_year": ("company", "last_filing_year"),
    # Precomputed intent scores (T11): a single-signal sort rides the same two-bucket machinery.
    "score_growth": ("scores", "growth"),
    "score_solidity": ("scores", "solidity"),
    "score_scale": ("scores", "scale"),
}

# --- name-relevance re-ranking (T10) -------------------------------------------
# When a caller is FINDING a company (filters.name set) rather than SCREENING a market, match
# quality must dominate over any numeric sort — otherwise "Novomatic" ranks a micro subsidiary
# above NOVOMATIC AG just because the AG has no Bilanzsumme. Above this pool size the query is
# treated as a screen and keeps the numeric sort (a fixed threshold, documented in the docstring).
_POOL_LIMIT = 200
_WORD_SPLIT = re.compile(r"[^0-9a-zà-ÿ]+")


def _name_match_score(query: str, name: str) -> tuple[int, float]:
    """Fixed, intent-independent text score (higher = better): exact > prefix > word-boundary
    start > substring, tie-broken by how much of the name the query covers. Pure + unit-tested."""
    q = query.casefold().strip()
    n = (name or "").casefold().strip()
    if not q or not n:
        return (0, 0.0)
    if n == q:
        tier = 4  # exact
    elif n.startswith(q):
        tier = 3  # prefix
    elif any(tok.startswith(q) for tok in _WORD_SPLIT.split(n) if tok):
        tier = 2  # a word in the name starts with the query
    elif q in n:
        tier = 1  # substring anywhere
    else:
        tier = 0  # not a name match (the SQL filter shouldn't return these)
    return (tier, len(q) / len(n))


def _rank_by_name(docs: list[dict[str, Any]], query: str, sort_field: str) -> list[dict[str, Any]]:
    """Re-rank a candidate pool by name relevance, then the requested/default sort value (desc),
    then fnr (asc, stable). Used by BOTH the Cosmos and in-memory paths so they stay identical."""
    path = _SORT_PATHS.get(sort_field)

    def key(d: dict[str, Any]) -> tuple[int, float, float]:
        tier, ratio = _name_match_score(query, _g(d, "identity", "name") or "")
        raw = _g(d, *path) if path else None
        if isinstance(raw, int | float) and not isinstance(raw, bool):
            value = float(raw)
        else:
            value = float("-inf")  # nulls sort last within a tier
        return (tier, ratio, value)

    ranked = sorted(docs, key=lambda d: str(d.get("fnr") or ""))  # fnr asc → stable final tiebreak
    ranked.sort(key=key, reverse=True)
    return ranked


# --- weighted multi-signal ranking (T11 rank_by) -------------------------------
def _weighted_score(doc: dict[str, Any], rank_by: list[RankSignal]) -> float:
    """Σ weight·score over the signals present on the doc, renormalized by the present weights
    (a doc missing one signal is scored fairly on the others, never penalized to zero). ``-inf``
    when the doc has none of the requested signals, so those sort last."""
    scores = doc.get("scores") or {}
    num = 0.0
    wsum = 0.0
    for sig in rank_by:
        value = scores.get(sig.signal) if isinstance(scores, dict) else None
        if isinstance(value, int | float) and not isinstance(value, bool):
            num += sig.weight * float(value)
            wsum += sig.weight
    return num / wsum if wsum > 0 else float("-inf")


def _rank_by_weighted(
    docs: list[dict[str, Any]], rank_by: list[RankSignal]
) -> list[dict[str, Any]]:
    ranked = sorted(docs, key=lambda d: str(d.get("fnr") or ""))  # stable fnr tiebreak
    ranked.sort(key=lambda d: _weighted_score(d, rank_by), reverse=True)
    return ranked


def _rank_by_signals_cosmos(
    cosmos: CosmosStoreLike,
    where_sql: str,
    params: list[dict[str, Any]],
    rank_by: list[RankSignal],
    start: int,
    page_size: int,
) -> list[dict[str, Any]]:
    """Pool the top candidates per signal via the indexed ORDER BY (union by fnr), then re-rank the
    union by the weighted mix and slice the page. A screening operation — ``total`` (the filter
    match count) is reported separately and stays exact."""
    pool: dict[str, dict[str, Any]] = {}
    for sig in rank_by:
        path = f"c.scores.{sig.signal}"
        sql = (
            f"SELECT * FROM c WHERE {where_sql} AND IS_DEFINED({path}) "
            f"ORDER BY {path} DESC OFFSET 0 LIMIT {_POOL_LIMIT}"
        )
        for d in cosmos.query(PRESENTED, sql, params):
            if not str(d.get("id", "")).startswith("__"):
                pool[str(d.get("fnr"))] = d
    ranked = _rank_by_weighted(list(pool.values()), rank_by)
    return ranked[start : start + page_size]


def _sort_key(doc: dict[str, Any], field: str) -> Any:
    path = _SORT_PATHS.get(field)
    value = _g(doc, *path) if path else doc.get("fnr")
    return value if value is not None else (0 if path else "")


def _cosmos_page(
    cosmos: CosmosStoreLike,
    where_sql: str,
    params: list[dict[str, Any]],
    order_path: tuple[str, ...] | None,
    descending: bool,
    start: int,
    page_size: int,
) -> list[dict[str, Any]]:
    """One server-side page, **without dropping the ~40% of companies that lack the sort field**
    (#32). Cosmos ``ORDER BY`` silently omits docs where the path is undefined, so banks/insurers
    (no UGB Bilanzsumme) and any company without the metric vanished from every list page while
    still being counted. We therefore page in two ordered buckets — ranked docs (field present)
    first, then the rest (field absent) ordered by name — and stitch across the boundary.

    The expensive ranked-bucket COUNT that this used to run on *every* call is now issued **only**
    for the rare deep-page-into-bucket-B case (T4): for a full ranked page, or the boundary page
    that straddles the two buckets, the bucket-A row count alone pins the B offset."""
    if order_path is None:
        sql = f"SELECT * FROM c WHERE {where_sql} OFFSET {start} LIMIT {page_size}"
        return list(cosmos.query(PRESENTED, sql, params))

    path = "c." + ".".join(order_path)
    direction = "DESC" if descending else "ASC"
    ranked = f"{where_sql} AND IS_DEFINED({path}) AND NOT IS_NULL({path})"
    rest = f"{where_sql} AND (NOT IS_DEFINED({path}) OR IS_NULL({path}))"

    sql_a = (
        f"SELECT * FROM c WHERE {ranked} ORDER BY {path} {direction} "
        f"OFFSET {start} LIMIT {page_size}"
    )
    rows = list(cosmos.query(PRESENTED, sql_a, params))
    if len(rows) == page_size:
        return rows  # full page from the ranked bucket → no count needed (the common case)

    # The ranked bucket is exhausted on this page; top up from the field-less "rest" bucket,
    # ordered by c.id (always-present system property → stable order, no custom index path).
    if rows:
        # A short page WITH ranked rows means bucket A ran out exactly at start+len(rows), so the
        # ranked bucket has that many rows total and bucket B has not started → B offset 0.
        b_offset = 0
    elif start == 0:
        b_offset = 0  # first page, ranked bucket entirely empty → all from B, offset 0
    else:
        # Deep page fully past the ranked bucket (bucket A returned nothing at this offset): only
        # NOW pay for the ranked COUNT, to place us within bucket B.
        _cr = next(
            iter(cosmos.query(PRESENTED, f"SELECT VALUE COUNT(1) FROM c WHERE {ranked}", params)), 0
        )
        count_ranked = _cr if isinstance(_cr, int) else 0
        b_offset = max(0, start - count_ranked)

    remaining = page_size - len(rows)
    sql_b = f"SELECT * FROM c WHERE {rest} ORDER BY c.id OFFSET {b_offset} LIMIT {remaining}"
    rows += list(cosmos.query(PRESENTED, sql_b, params))
    return rows


def _sorted_nulls_last(
    docs: list[dict[str, Any]], sort_field: str, descending: bool
) -> list[dict[str, Any]]:
    """In-memory equivalent of :func:`_cosmos_page`'s ordering: docs with the sort value first
    (by value, respecting direction), then the field-less docs by id/fnr — never interleaved."""
    path = _SORT_PATHS.get(sort_field)
    present = [d for d in docs if (_g(d, *path) if path else d.get("fnr")) is not None]
    absent = [d for d in docs if d not in present]
    present.sort(key=lambda d: d["fnr"])
    present.sort(key=lambda d: _sort_key(d, sort_field), reverse=descending)
    absent.sort(key=lambda d: d.get("id") or d.get("fnr") or "")
    return present + absent
