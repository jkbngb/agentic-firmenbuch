"""``search_companies`` — filtered, sorted, paginated company search (§9)."""

from __future__ import annotations

from typing import Any

from fbl_core.storage import CosmosStoreLike
from fbl_core_at.directories import load_fi_directory
from fbl_core_at.models import PublicProvenance, SearchFilters, SearchResponse, Sort

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


def _status_matches(doc: dict[str, Any], wanted: str) -> bool:
    status = _g(doc, "identity", "status")
    if wanted == "all":
        return True
    if wanted == "active":
        return bool(status == "active")
    return status in ("historical", "deleted")  # inactive


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
        f.oenace_section is None
        or f.oenace_section
        in (_g(doc, "industry", "oenace", "section"), _g(doc, "branch", "oenace", "section")),
        f.oenace_division is None
        or f.oenace_division
        in (_g(doc, "industry", "oenace", "division"), _g(doc, "branch", "oenace", "division")),
        f.oenace_group is None
        or f.oenace_group
        in (_g(doc, "industry", "oenace", "group"), _g(doc, "branch", "oenace", "group")),
        f.geschaeftszweig is None
        or f.geschaeftszweig.lower() in (_g(doc, "company", "description") or "").lower(),
        f.postal_code is None
        or (_g(doc, "location", "postal_code") or "").startswith(f.postal_code),
        f.city is None or f.city.lower() in (_g(doc, "location", "city") or "").lower(),
    ]
    return all(checks)


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
        conds.append("CONTAINS(LOWER(c.identity.name), @name)")
        params.append({"name": "@name", "value": f.name.lower()})
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
        conds.append("CONTAINS(LOWER(c.management.primary_manager_name), @manager_name)")
        params.append({"name": "@manager_name", "value": f.manager_name.lower()})
    # ÖNACE filters match the v2 `industry` block OR the legacy v1 `branch` block, so search
    # works both before and after the re-grind replaces the stored docs (#34). A comparison on
    # an undefined path is undefined in Cosmos SQL, so the OR simply falls through.
    for bpath, lpath, bval, bkey in (
        ("c.industry.oenace.section", "c.branch.oenace.section", f.oenace_section, "@oe_s"),
        ("c.industry.oenace.division", "c.branch.oenace.division", f.oenace_division, "@oe_d"),
        # `group` is a reserved SQL keyword — must use bracket notation, not dot access.
        ('c.industry.oenace["group"]', 'c.branch.oenace["group"]', f.oenace_group, "@oe_g"),
    ):
        if bval is not None:
            conds.append(f"({bpath} = {bkey} OR {lpath} = {bkey})")
            params.append({"name": bkey, "value": bval})
    if f.geschaeftszweig:
        conds.append("CONTAINS(LOWER(c.company.description), @geschaeftszweig)")
        params.append({"name": "@geschaeftszweig", "value": f.geschaeftszweig.lower()})
    if f.postal_code:
        conds.append("STARTSWITH(c.location.postal_code, @postal_code)")
        params.append({"name": "@postal_code", "value": f.postal_code})
    if f.city:
        conds.append("CONTAINS(LOWER(c.location.city), @city)")
        params.append({"name": "@city", "value": f.city.lower()})
    rng("c.financials.latest.bilanzsumme", f.bilanzsumme_min, f.bilanzsumme_max, "bs")
    rng("c.ratios.equity_ratio.latest", f.equity_ratio_min, f.equity_ratio_max, "eq")
    rng("c.financials.latest.revenue", f.revenue_min, f.revenue_max, "rev")
    rng("c.employees.latest", f.employees_min, f.employees_max, "emp")
    return " AND ".join(conds), params


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

    sort_field = sort.field if sort else "bilanzsumme"
    descending = sort.descending if sort else True
    order_path = _SORT_PATHS.get(sort_field)

    where_sql, params = _build_where(filters)
    count_sql = f"SELECT VALUE COUNT(1) FROM c WHERE {where_sql}"
    raw_total = next(iter(cosmos.query(PRESENTED, count_sql, params)), 0)

    if isinstance(raw_total, int):  # Cosmos: COUNT(1) → real total; page server-side
        total = raw_total
        rows = _cosmos_page(cosmos, where_sql, params, order_path, descending, start, page_size)
        page_docs = [d for d in rows if not str(d.get("id", "")).startswith("__")]
    else:  # in-memory fake: SQL ignored, every doc returned → filter/sort/paginate in Python
        rows = list(cosmos.query(PRESENTED, f"SELECT * FROM c WHERE {where_sql}", params))
        matched = [
            d for d in rows if not str(d.get("id", "")).startswith("__") and _matches(d, filters)
        ]
        matched = _sorted_nulls_last(matched, sort_field, descending)
        total = len(matched)
        page_docs = matched[start : start + page_size]

    directory = load_fi_directory(cosmos)
    cards = [_card(d, directory) for d in page_docs]
    data_version_max = max((_g(d, "provenance", "data_version") or 0 for d in page_docs), default=0)
    return SearchResponse(
        data_version_max=data_version_max,
        total=total,
        page=page,
        page_size=page_size,
        results=cards,
        provenance=PublicProvenance(),
    )


_SORT_PATHS = {
    "bilanzsumme": ("financials", "latest", "bilanzsumme"),
    "revenue": ("financials", "latest", "revenue"),
    "equity_ratio": ("ratios", "equity_ratio", "latest"),
    "employees": ("employees", "latest"),
    "last_filing_year": ("company", "last_filing_year"),
}


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
    first, then the rest (field absent) ordered by name — and stitch across the boundary."""
    if order_path is None:
        sql = f"SELECT * FROM c WHERE {where_sql} OFFSET {start} LIMIT {page_size}"
        return list(cosmos.query(PRESENTED, sql, params))

    path = "c." + ".".join(order_path)
    direction = "DESC" if descending else "ASC"
    ranked = f"{where_sql} AND IS_DEFINED({path}) AND NOT IS_NULL({path})"
    rest = f"{where_sql} AND (NOT IS_DEFINED({path}) OR IS_NULL({path}))"
    _cr = next(
        iter(cosmos.query(PRESENTED, f"SELECT VALUE COUNT(1) FROM c WHERE {ranked}", params)), 0
    )
    count_ranked = _cr if isinstance(_cr, int) else 0

    rows: list[dict[str, Any]] = []
    if start < count_ranked:
        sql_a = (
            f"SELECT * FROM c WHERE {ranked} ORDER BY {path} {direction} "
            f"OFFSET {start} LIMIT {page_size}"
        )
        rows = list(cosmos.query(PRESENTED, sql_a, params))
    remaining = page_size - len(rows)
    if remaining > 0:  # boundary page or fully past the ranked bucket → draw from the rest
        b_offset = max(0, start - count_ranked)
        # c.id (= fnr) is a system property, always indexed + present → stable order for the
        # field-less docs without needing a custom index path.
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
