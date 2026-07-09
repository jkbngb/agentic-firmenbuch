"""Cross-company register-event feed — ``list_events`` + ``get_event_stats`` (Pro tools).

Serves the flattened ``10_events`` container the pipeline writes (one doc per derived register
event, with denormalized facets: name, Bundesland, ÖNACE, legal form). This is the market-watch /
deal-sourcing surface: "which companies changed management / raised capital, where, since when".

Read-only. Filtering + paging run server-side in Cosmos; the in-memory test store ignores SQL and
returns every doc, so the same predicate is also applied in Python (detected by a real COUNT(1)).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fbl_core.storage import CosmosStoreLike

from ._common import _BL_NAME_TO_CODE, _is_gmbh_filter, _legal_form_label

EVENTS = "10_events"
MAX_PAGE_SIZE = 100
DEFAULT_WINDOW_DAYS = 30

EVENT_TYPES = (
    "name_change",
    "seat_change",
    "legal_form_change",
    "capital_change",
    "management_change",
)


def _default_since(now: datetime | None) -> str:
    return ((now or datetime.now(UTC)) - timedelta(days=DEFAULT_WINDOW_DAYS)).strftime("%Y-%m-%d")


def _norm_bundesland(value: str | None) -> str | None:
    """Accept a full name ("Wien") or the stored code ("W"); return the stored code."""
    if value is None:
        return None
    return _BL_NAME_TO_CODE.get(value, value)


def _build_where(
    *,
    types: list[str] | None,
    since: str,
    until: str | None,
    bundesland: str | None,
    oenace_section: str | None,
    oenace_division: str | None,
    legal_form: str | None,
    fnrs: list[str] | None,
) -> tuple[str, list[dict[str, Any]]]:
    clauses = ["c.date >= @since"]
    params: list[dict[str, Any]] = [{"name": "@since", "value": since}]
    if until:
        clauses.append("c.date <= @until")
        params.append({"name": "@until", "value": until})
    if types:
        names = [f"@t{i}" for i in range(len(types))]
        clauses.append(f"c.type IN ({', '.join(names)})")
        params += [{"name": n, "value": t} for n, t in zip(names, types, strict=True)]
    bl = _norm_bundesland(bundesland)
    if bl:
        clauses.append("c.bundesland = @bl")
        params.append({"name": "@bl", "value": bl})
    if oenace_section:
        clauses.append("c.oenace_section = @sec")
        params.append({"name": "@sec", "value": oenace_section.upper()})
    if oenace_division:
        clauses.append("c.oenace_division = @div")
        params.append({"name": "@div", "value": oenace_division})
    if legal_form is not None:
        if _is_gmbh_filter(legal_form):
            clauses.append("STARTSWITH(c.legal_form, 'GE')")
        else:
            clauses.append("c.legal_form = @lf")
            params.append({"name": "@lf", "value": legal_form})
    if fnrs:
        names = [f"@f{i}" for i in range(len(fnrs))]
        clauses.append(f"c.fnr IN ({', '.join(names)})")
        params += [{"name": n, "value": f} for n, f in zip(names, fnrs, strict=True)]
    return " AND ".join(clauses), params


def _matches(
    doc: dict[str, Any],
    *,
    types: list[str] | None,
    since: str,
    until: str | None,
    bundesland: str | None,
    oenace_section: str | None,
    oenace_division: str | None,
    legal_form: str | None,
    fnrs: list[str] | None,
) -> bool:
    date = doc.get("date") or ""
    if date < since or (until and date > until):
        return False
    if types and doc.get("type") not in types:
        return False
    bl = _norm_bundesland(bundesland)
    if bl and doc.get("bundesland") != bl:
        return False
    if oenace_section and doc.get("oenace_section") != oenace_section.upper():
        return False
    if oenace_division and doc.get("oenace_division") != oenace_division:
        return False
    if legal_form is not None:
        code = doc.get("legal_form") or ""
        ok = code.startswith("GE") if _is_gmbh_filter(legal_form) else code == legal_form
        if not ok:
            return False
    return not (fnrs and doc.get("fnr") not in fnrs)


def _serve(doc: dict[str, Any]) -> dict[str, Any]:
    """Public shape of one event (drop Cosmos system fields; label the legal form)."""
    return {
        "fnr": doc.get("fnr"),
        "name": doc.get("name"),
        "date": doc.get("date"),
        "type": doc.get("type"),
        "description": doc.get("description"),
        "capital_from": doc.get("capital_from"),
        "capital_to": doc.get("capital_to"),
        "managers_added": doc.get("managers_added") or [],
        "managers_removed": doc.get("managers_removed") or [],
        "bundesland": doc.get("bundesland"),
        "legal_form": _legal_form_label(doc.get("legal_form")),
        "industry_section": doc.get("oenace_section"),
        "source": doc.get("source"),
    }


def list_events(
    cosmos: CosmosStoreLike,
    *,
    types: list[str] | None = None,
    since: str | None = None,
    until: str | None = None,
    bundesland: str | None = None,
    oenace_section: str | None = None,
    oenace_division: str | None = None,
    legal_form: str | None = None,
    fnrs: list[str] | None = None,
    page: int = 1,
    page_size: int = 25,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Cross-company register-event feed, newest first, filtered + paginated (§9)."""
    page = max(1, page)
    page_size = max(1, min(page_size, MAX_PAGE_SIZE))
    start = (page - 1) * page_size
    since = since or _default_since(now)
    where, params = _build_where(
        types=types,
        since=since,
        until=until,
        bundesland=bundesland,
        oenace_section=oenace_section,
        oenace_division=oenace_division,
        legal_form=legal_form,
        fnrs=fnrs,
    )
    raw_total = next(
        iter(cosmos.query(EVENTS, f"SELECT VALUE COUNT(1) FROM c WHERE {where}", params)), 0
    )
    if isinstance(raw_total, int):  # real Cosmos: page server-side
        total = raw_total
        sql = f"SELECT * FROM c WHERE {where} ORDER BY c.date DESC OFFSET {start} LIMIT {page_size}"
        rows = list(cosmos.query(EVENTS, sql, params))
    else:  # in-memory test store: SQL ignored -> filter/sort/paginate in Python
        allrows = [
            d
            for d in cosmos.query(EVENTS, f"SELECT * FROM c WHERE {where}", params)
            if isinstance(d, dict)
        ]
        matched = [
            d
            for d in allrows
            if _matches(
                d,
                types=types,
                since=since,
                until=until,
                bundesland=bundesland,
                oenace_section=oenace_section,
                oenace_division=oenace_division,
                legal_form=legal_form,
                fnrs=fnrs,
            )
        ]
        matched.sort(key=lambda d: d.get("date") or "", reverse=True)
        total = len(matched)
        rows = matched[start : start + page_size]
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "since": since,
        "until": until,
        "events": [_serve(d) for d in rows],
        "note": (
            "Registeränderungen werden erst seit dem 1. Juli 2026 von uns aufgezeichnet und sind "
            "daher erst ab diesem Datum abfragbar. Ein leeres Ergebnis bedeutet: keine Änderung im "
            "Zeitraum, keine fehlenden Daten."
        ),
    }


def get_event_stats(
    cosmos: CosmosStoreLike,
    *,
    since: str | None = None,
    until: str | None = None,
    bundesland: str | None = None,
    oenace_section: str | None = None,
    oenace_division: str | None = None,
    legal_form: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Counts by type and by Bundesland over the filter window (market-watch dashboard)."""
    since = since or _default_since(now)
    filt: dict[str, Any] = dict(
        types=None,
        since=since,
        until=until,
        bundesland=bundesland,
        oenace_section=oenace_section,
        oenace_division=oenace_division,
        legal_form=legal_form,
        fnrs=None,
    )
    where, params = _build_where(**filt)
    rows = [
        d
        for d in cosmos.query(EVENTS, f"SELECT * FROM c WHERE {where}", params)
        if isinstance(d, dict)
    ]
    # In-memory safety (the fake store ignores SQL); a no-op over Cosmos-filtered rows.
    rows = [d for d in rows if _matches(d, **filt)]
    by_type: dict[str, int] = {}
    by_bundesland: dict[str, int] = {}
    for d in rows:
        by_type[d.get("type", "?")] = by_type.get(d.get("type", "?"), 0) + 1
        bl = d.get("bundesland") or "?"
        by_bundesland[bl] = by_bundesland.get(bl, 0) + 1
    return {
        "since": since,
        "until": until,
        "total": len(rows),
        "by_type": dict(sorted(by_type.items(), key=lambda x: -x[1])),
        "by_bundesland": dict(sorted(by_bundesland.items(), key=lambda x: -x[1])),
    }
