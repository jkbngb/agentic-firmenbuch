"""``search_companies`` — filtered, sorted, paginated company search (§9)."""

from __future__ import annotations

import contextvars
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from fbl_core.storage import CosmosStoreLike
from fbl_core_at.directories import load_fi_directory_cached
from fbl_core_at.models import (
    PublicProvenance,
    Relaxation,
    SearchFilters,
    SearchResponse,
    Sort,
)

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
    return " AND ".join(conds), params


# --- zero-hit relaxation (T6) --------------------------------------------------
# Numeric range filters as (min_field, max_field, cosmos_path, mem_path, kind): a *_min/*_max
# pair is ONE relaxation unit (dropping it clears both bounds) and its suggestion is the nearest
# achievable bound over the OTHER-filters result set. Everything here is filter-agnostic — adding
# a filter to SearchFilters makes it a relaxation candidate automatically (a scalar unit); only
# genuine min/max PAIRS need an entry below so they collapse into one unit.
_RANGE_UNITS: dict[str, tuple[str, str, str, tuple[str, ...], str]] = {
    "bilanzsumme_range": (
        "bilanzsumme_min", "bilanzsumme_max",
        "c.financials.latest.bilanzsumme", ("financials", "latest", "bilanzsumme"), "money",
    ),
    "equity_ratio_range": (
        "equity_ratio_min", "equity_ratio_max",
        "c.ratios.equity_ratio.latest", ("ratios", "equity_ratio", "latest"), "ratio",
    ),
    "revenue_range": (
        "revenue_min", "revenue_max",
        "c.financials.latest.revenue", ("financials", "latest", "revenue"), "money",
    ),
    "employees_range": (
        "employees_min", "employees_max",
        "c.employees.latest", ("employees", "latest"), "count",
    ),
    "founded_year_range": (
        "founded_year_min", "founded_year_max",
        "c.company.founded_year", ("company", "founded_year"), "year",
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


def _relaxations_cosmos(
    cosmos: CosmosStoreLike, filters: SearchFilters
) -> list[Relaxation] | None:
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
    out["page_size"] = page_size  # the value after clamping to 1..MAX_PAGE_SIZE
    return out


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
        if isinstance(raw_total, int):  # Cosmos: COUNT(1) → real total; page server-side
            total = raw_total
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
            matched = _sorted_nulls_last(matched, sort_field, descending)
            total = len(matched)
            page_docs = matched[start : start + page_size]
            if total == 0:
                relaxations = _relaxations_memory(rows, filters)

    directory = load_fi_directory_cached(cosmos)
    cards = [_card(d, directory) for d in page_docs]
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
