"""Cohort + peer tools: ``get_cohort_summary`` and ``find_peers`` (§9)."""

from __future__ import annotations

from typing import Any

from fbl_core.storage import CosmosStoreLike
from fbl_core_at.directories import load_fi_directory
from fbl_core_at.models import PublicProvenance

from ..errors import BadRequest
from ._common import (
    _BL_NAME_TO_CODE,
    PRESENTED,
    _all_presented,
    _card,
    _count_where,
    _g,
    _median,
    _require,
    _scalar_floats,
)

_COHORT_MEDIAN_MAX = 20000  # exact median needs the values; skip it above this cohort size

_COHORT_PATHS = {
    "gkl": ("size", "gkl"),
    "bundesland": ("location", "bundesland"),
    "legal_form": ("identity", "legal_form"),
}
_COHORT_SQL = {
    "gkl": "c.size.gkl",
    "bundesland": "c.location.bundesland",
    "legal_form": "c.identity.legal_form",
}


def get_cohort_summary(cosmos: CosmosStoreLike, dimension: str, value: str) -> dict[str, Any]:
    """Aggregate for companies where ``dimension == value`` (gkl/bundesland/legal_form).

    Server-side counts; the exact Bilanzsumme median is fetched only for tractable cohorts
    (``<= _COHORT_MEDIAN_MAX``) so a 100k-member cohort can't stall the request.
    """
    dimension = {"size_gkl": "gkl"}.get(dimension, dimension)  # accept the search-filter name
    path = _COHORT_PATHS.get(dimension)
    if path is None:
        raise BadRequest(f"unknown dimension {dimension!r}")
    # Bundesland arrives as a full name ("Wien") but is stored as a code ("W").
    stored = _BL_NAME_TO_CODE.get(value, value) if dimension == "bundesland" else value
    where = f'NOT STARTSWITH(c.id, "__") AND {_COHORT_SQL[dimension]} = @v'
    params = [{"name": "@v", "value": stored}]

    count = _count_where(cosmos, PRESENTED, where, params)
    median_capped = False
    if count is None:  # in-memory test store: SQL ignored → compute over the small dataset
        members = [d for d in _all_presented(cosmos) if _g(d, *path) == stored]
        count = len(members)
        with_guv = sum(1 for d in members if _g(d, "financials", "has_guv_latest"))
        values = sorted(
            v for d in members if (v := _g(d, "financials", "latest", "bilanzsumme")) is not None
        )
        median = _median(values)
    else:
        wg = _count_where(
            cosmos, PRESENTED, f"{where} AND c.financials.has_guv_latest = true", params
        )
        with_guv = wg or 0
        median = None
        if 0 < count <= _COHORT_MEDIAN_MAX:
            sql = (
                f"SELECT VALUE c.financials.latest.bilanzsumme FROM c "
                f"WHERE {where} AND IS_DEFINED(c.financials.latest.bilanzsumme)"
            )
            median = _median(sorted(_scalar_floats(cosmos, PRESENTED, sql, params)))
        elif count > _COHORT_MEDIAN_MAX:
            median_capped = True

    result: dict[str, Any] = {
        "dimension": dimension,
        "value": value,
        "count": count,
        "bilanzsumme_median": median,
        "with_guv": with_guv,
    }
    if median_capped:
        result["bilanzsumme_median_note"] = (
            f"Kohorte > {_COHORT_MEDIAN_MAX} Firmen — exakter Median nicht berechnet."
        )
    return {
        "schema_version": "1.0",
        "result": result,
        "provenance": PublicProvenance().model_dump(mode="json"),
    }


def find_peers(cosmos: CosmosStoreLike, fnr: str, n: int = 10) -> dict[str, Any]:
    """Nearest companies by Bilanzsumme within the same size class (§9, optional v1).

    On real Cosmos this is two small index-ordered windows around the target (never a full
    scan); on the in-memory store it filters the small dataset in Python.
    """
    n = max(1, min(n, 50))
    target = _require(cosmos, fnr)
    gkl = _g(target, "size", "gkl")
    target_bs = _g(target, "financials", "latest", "bilanzsumme")
    where = (
        'NOT STARTSWITH(c.id, "__") AND c.fnr != @fnr AND c.size.gkl = @gkl '
        "AND IS_DEFINED(c.financials.latest.bilanzsumme)"
    )
    params = [{"name": "@fnr", "value": fnr}, {"name": "@gkl", "value": gkl}]

    probe = _count_where(cosmos, PRESENTED, where, params)
    if probe is None:  # in-memory store: filter the small dataset directly
        candidates = [
            d
            for d in _all_presented(cosmos)
            if d["fnr"] != fnr
            and _g(d, "size", "gkl") == gkl
            and _g(d, "financials", "latest", "bilanzsumme") is not None
        ]
    elif target_bs is None:
        candidates = []  # no Bilanzsumme to rank against — don't scan the universe
    else:
        bs = "c.financials.latest.bilanzsumme"
        bs_params = [*params, {"name": "@bs", "value": target_bs}]

        def _window(cmp: str, direction: str) -> list[dict[str, Any]]:
            sql = (
                f"SELECT * FROM c WHERE {where} AND {bs} {cmp} @bs "
                f"ORDER BY {bs} {direction} OFFSET 0 LIMIT {n}"
            )
            return list(cosmos.query(PRESENTED, sql, bs_params))

        candidates = _window(">=", "ASC") + _window("<", "DESC")

    if target_bs is not None:
        candidates.sort(key=lambda d: abs(_g(d, "financials", "latest", "bilanzsumme") - target_bs))
    directory = load_fi_directory(cosmos)
    return {
        "schema_version": "1.0",
        "result": {
            "fnr": fnr,
            "gkl": gkl,
            "peers": [_card(d, directory).model_dump(mode="json") for d in candidates[:n]],
        },
        "provenance": PublicProvenance().model_dump(mode="json"),
    }
