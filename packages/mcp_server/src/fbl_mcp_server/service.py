"""Tool logic over ``10_presentation`` (§9), decoupled from the FastMCP transport.

Pure read functions taking a ``CosmosStoreLike`` so they unit-test against the in-memory
store. Filtering is applied in Python here; in production the same predicates are pushed
to the Cosmos index (§4.1). Every response carries the §8.9 envelope fields.
"""

from __future__ import annotations

from typing import Any

from fbl_core.directories import load_fi_directory
from fbl_core.financial_institution import classify_financial_institution
from fbl_core.models import CompanyCard, PublicProvenance, SearchFilters, SearchResponse, Sort
from fbl_core.storage import RAW_CONTAINER, BlobStoreLike, CosmosStoreLike

from .errors import NotFound

# Caveat per register kind. Generic fallback for the less common financial-institution types.
_FI_CAVEAT = {
    "bank": "Bank: Rechnungslegung nach BWG (§§43-58), nicht UGB; strukturierte UGB-Kennzahlen "
    "liegen daher nicht vor — der amtliche Jahresabschluss ist als PDF einzusehen.",
    "insurer": "Versicherung: Rechnungslegung nach VAG (§§136-167), nicht UGB; strukturierte "
    "UGB-Kennzahlen liegen daher nicht vor — der amtliche Jahresabschluss ist als PDF einzusehen.",
}


def _fi_caveat(kind: str) -> str:
    return _FI_CAVEAT.get(
        kind,
        f"Reguliertes Finanzinstitut ({kind}): es gelten Sondervorschriften, UGB-Kennzahlen sind "
        "nicht ohne Weiteres vergleichbar.",
    )


def _financial_institution(
    doc: dict[str, Any], directory: dict[str, str] | None = None
) -> dict[str, Any] | None:
    """The served ``financial_institution`` block, or ``None`` for an ordinary company.

    Register-first (ROADMAP P2 / issue #15): if the FN is in *directory* — the authoritative
    OeNB/EIOPA register set (``00_directories``) — use that (``source="register"``, exact kind).
    Only fall back to the name heuristic (``source="heuristic"``) for entries not in any register
    (e.g. foreign branches), so banks the heuristic missed (BAWAG, Oberbank) are still right."""
    fnr = doc.get("fnr")
    kind = (directory or {}).get(str(fnr)) if fnr else None
    if kind is not None:
        return {"kind": kind, "source": "register", "caveat": _fi_caveat(kind)}
    fi = classify_financial_institution(
        _g(doc, "identity", "legal_form"), _g(doc, "identity", "name")
    )
    if fi is None:
        return None
    return {"kind": fi.kind, "source": fi.source, "caveat": fi.caveat}


PRESENTED = "10_presentation"
CONSOLIDATED = "50_consolidated"
MAX_PAGE_SIZE = 100


def _g(doc: dict[str, Any], *path: str) -> Any:
    cur: Any = doc
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _provenance(doc: dict[str, Any]) -> PublicProvenance:
    prov = doc.get("provenance") or {}
    return PublicProvenance(
        data_version=prov.get("data_version"),
        built_at=prov.get("built_at"),
        schema_version=prov.get("schema_version", "1.0"),
    )


def _card(doc: dict[str, Any], directory: dict[str, str] | None = None) -> CompanyCard:
    return CompanyCard(
        fnr=doc["fnr"],
        name=_g(doc, "identity", "name") or doc["fnr"],
        legal_form=_legal_form_label(_g(doc, "identity", "legal_form")),
        bundesland=_BL_CODE_TO_NAME.get(
            _g(doc, "location", "bundesland"), _g(doc, "location", "bundesland")
        ),
        size_gkl=_g(doc, "size", "gkl"),
        bilanzsumme_band=_g(doc, "size", "bilanzsumme_band"),
        bilanzsumme_latest=_g(doc, "financials", "latest", "bilanzsumme"),
        equity_ratio_latest=_g(doc, "ratios", "equity_ratio", "latest"),
        revenue_latest=_g(doc, "financials", "latest", "revenue"),
        growth_profile=_g(doc, "growth", "profile"),
        has_guv_latest=bool(_g(doc, "financials", "has_guv_latest")),
        manager_name=_g(doc, "management", "primary_manager_name"),
        is_financial_institution=_financial_institution(doc, directory) is not None,
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
    ]
    return all(checks)


def _in_range(value: Any, lo: float | None, hi: float | None) -> bool:
    if lo is None and hi is None:
        return True
    if value is None:
        return False
    if lo is not None and value < lo:
        return False
    return not (hi is not None and value > hi)


def _all_presented(cosmos: CosmosStoreLike) -> list[dict[str, Any]]:
    return [d for d in cosmos.iter_all(PRESENTED) if not str(d.get("id", "")).startswith("__")]


_STATUS_SQL = {
    "active": "c.identity.status = 'active'",
    "inactive": "c.identity.status IN ('historical', 'deleted')",
}

# Presentation stores Bundesland as the official one/two-letter code; search filters and the
# UI speak full names. Map both ways so "Wien" matches the stored "W" and cards read "Wien".
_BL_NAME_TO_CODE = {
    "Burgenland": "B",
    "Kärnten": "K",
    "Niederösterreich": "N",
    "Oberösterreich": "O",
    "Salzburg": "S",
    "Steiermark": "St",
    "Tirol": "T",
    "Vorarlberg": "V",
    "Wien": "W",
}
_BL_CODE_TO_NAME = {code: name for name, code in _BL_NAME_TO_CODE.items()}

# Presentation stores the granular Firmenbuch Rechtsform code; the GmbH family is the "GE…"
# prefix (GES is ~99.7% of it). Filters/UI speak "GmbH"; map both directions.
_GMBH_NAMES = {"gmbh", "ges.m.b.h.", "ges.m.b.h", "gesellschaft mit beschränkter haftung"}


def _is_gmbh_filter(value: str | None) -> bool:
    return value is not None and value.strip().lower() in _GMBH_NAMES


def _legal_form_label(code: str | None) -> str | None:
    if code and code.startswith("GE"):
        return "GmbH"
    return code


def _legal_form_matches(doc: dict[str, Any], wanted: str | None) -> bool:
    if wanted is None:
        return True
    code = _g(doc, "identity", "legal_form") or ""
    return code.startswith("GE") if _is_gmbh_filter(wanted) else code == wanted


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
    order_sql = (
        f" ORDER BY c.{'.'.join(order_path)} {'DESC' if descending else 'ASC'}"
        if order_path
        else ""
    )

    where_sql, params = _build_where(filters)
    page_sql = f"SELECT * FROM c WHERE {where_sql}{order_sql} OFFSET {start} LIMIT {page_size}"
    count_sql = f"SELECT VALUE COUNT(1) FROM c WHERE {where_sql}"

    rows = list(cosmos.query(PRESENTED, page_sql, params))
    raw_total = next(iter(cosmos.query(PRESENTED, count_sql, params)), 0)

    # Defensive Python filter/sort: a no-op on a Cosmos page (already filtered/sorted),
    # the real filter on the in-memory store (which returns every doc).
    matched = [
        d for d in rows if not str(d.get("id", "")).startswith("__") and _matches(d, filters)
    ]
    matched.sort(key=lambda d: d["fnr"])  # stable base order
    matched.sort(key=lambda d: _sort_key(d, sort_field), reverse=descending)

    if isinstance(raw_total, int):  # Cosmos: COUNT(1) → real total, page already offset
        total = raw_total
        page_docs = matched[:page_size]
    else:  # in-memory fake: SQL ignored, every doc returned → paginate in Python
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


def _require(cosmos: CosmosStoreLike, fnr: str) -> dict[str, Any]:
    doc = cosmos.get(PRESENTED, fnr)
    if doc is None:
        raise NotFound(f"company {fnr!r} not found")
    return doc


def _strip_internal(doc: dict[str, Any]) -> dict[str, Any]:
    # drop the lineage block, the internal event-derivation baseline (issue #16), and Cosmos
    # system fields (_rid/_self/_etag/_ts/_attachments)
    return {
        k: v
        for k, v in doc.items()
        if k not in ("meta", "event_baseline") and not k.startswith("_")
    }


def get_company_details(cosmos: CosmosStoreLike, fnr: str) -> dict[str, Any]:
    """Full served document for one company (internal hash chain omitted, §8.7)."""
    doc = _require(cosmos, fnr)
    result = _strip_internal(doc)
    # Serve-time: stamp each filing with a resolvable document_ref the agent can pass straight to
    # get_document for a signed download link — derived from data already in the record (no
    # re-grind, mirrors the FI flag). One filing per Stichtag, so {fnr}:{stichtag} is unique.
    for f in result.get("filings", []):
        if isinstance(f, dict) and f.get("stichtag"):
            f["document_ref"] = f"{fnr}:{f['stichtag']}"
    fi = _financial_institution(doc, load_fi_directory(cosmos))
    if fi is not None:
        # Surface the flag at the top of the record so an agent reads it before the (absent)
        # UGB figures, and doesn't mistake "no Bilanz" for "no data" (ROADMAP P2.1).
        result["financial_institution"] = fi
    return {
        "schema_version": doc.get("schema_version", "1.0"),
        "data_version": _g(doc, "provenance", "data_version"),
        "result": result,
        "provenance": _provenance(doc).model_dump(mode="json"),
    }


# Card field name -> stored history key, so agents can ask for the name they saw on the card.
_METRIC_ALIASES = {"revenue": "umsatzerloese"}


def get_company_history(
    cosmos: CosmosStoreLike, fnr: str, metrics: list[str] | None = None
) -> dict[str, Any]:
    """Per-metric histories (absolutes + ratios) for one company."""
    doc = _require(cosmos, fnr)
    bilanz = _g(doc, "financials", "bilanz") or {}
    guv = _g(doc, "financials", "guv") or {}
    ratios = doc.get("ratios") or {}
    available = {**bilanz, **guv, **{k: v for k, v in ratios.items() if isinstance(v, dict)}}
    # Accept the search-card metric names as aliases for the stored UGB keys (the eval found
    # `revenue` returned nothing because the stored series is `umsatzerloese`).
    for alias, stored in _METRIC_ALIASES.items():
        if alias not in available and stored in available:
            available[alias] = available[stored]
    wanted = metrics or list(available)
    out = {}
    for name in wanted:
        ms = available.get(name)
        if not isinstance(ms, dict):
            continue
        # Expose the official UGB code + §-ref alongside the series (Part A.3).
        out[name] = {
            "history": ms.get("history", {}),
            "source_codes": ms.get("source_codes", []),
            "source_codes_by_year": ms.get("source_codes_by_year", {}),
            "ugb_paragraph": ms.get("paragraph_ref"),
        }
    return {
        "schema_version": doc.get("schema_version", "1.0"),
        "data_version": _g(doc, "provenance", "data_version"),
        "result": {"fnr": fnr, "metrics": out},
        "provenance": _provenance(doc).model_dump(mode="json"),
    }


DERIVED = "30_derived"


def get_full_record(
    cosmos: CosmosStoreLike, fnr: str, *, expose_personal_data: bool = False
) -> dict[str, Any]:
    """The COMPLETE per-company record (Part B §5.1): the derived layer, a superset of the
    served document — every position's full year history (`financials.positions`), the
    unknown-code `passthrough`, `completeness`, and `guv_years`.

    Falls back to the consolidated layer if derived is absent. The internal hash chain
    (`meta`) is stripped; officer names stay withheld unless ``expose_personal_data`` is
    set (a documented lawful basis, §8.7) — names are the only allowlisted field NOT
    retrievable here.
    """
    doc = cosmos.get(DERIVED, fnr) or cosmos.get(CONSOLIDATED, fnr)
    if doc is None:
        raise NotFound(f"company {fnr!r} not found")
    record = _strip_internal(doc)
    if not expose_personal_data:
        _redact_officer_names(record)
    data_version = _g(doc, "meta", "data_version")
    return {
        "schema_version": _g(doc, "meta", "schema_version") or "1.0",
        "data_version": data_version,
        "result": record,
        "provenance": PublicProvenance(data_version=data_version).model_dump(mode="json"),
    }


def _redact_officer_names(record: dict[str, Any]) -> None:
    """Strip officer first/last name from a full record (GDPR §8.7); keep birth_year/age."""
    gf = _g(record, "management", "primary_gf")
    if isinstance(gf, dict):
        gf.pop("first_name", None)
        gf.pop("last_name", None)


def _latest_filing_stichtag(doc: dict[str, Any]) -> str | None:
    stichtage = sorted(
        (f.get("stichtag") for f in doc.get("filings", []) if f.get("stichtag")), reverse=True
    )
    return stichtage[0] if stichtage else None


def _resolve_document(
    cosmos: CosmosStoreLike, reference: str
) -> tuple[str, str | None, str | None, dict[str, Any] | None]:
    """Resolve a get_document *reference* to ``(fnr, stichtag, doc_key, served_doc)``.

    Accepts three forms, strongest first: ``"{fnr}:{stichtag}"`` (the ``document_ref`` that
    get_company_details stamps on each filing), a bare ``"{fnr}"`` (→ its latest filing), or a
    legacy/explicit opaque ``doc_key`` (matched against a served filing). Raises
    :class:`NotFound` if nothing matches."""
    if ":" in reference:
        fnr, _, stichtag = reference.partition(":")
        fnr, stichtag = fnr.strip(), stichtag.strip()
        if fnr and stichtag:
            return fnr, stichtag, None, cosmos.get(PRESENTED, fnr)
    served = cosmos.get(PRESENTED, reference)
    if served is not None:
        return reference, _latest_filing_stichtag(served), None, served
    for doc in _all_presented(cosmos):
        for filing in doc.get("filings", []):
            if filing.get("doc_key") == reference or filing.get("document_url") == reference:
                return doc["fnr"], filing.get("stichtag"), reference, doc
    raise NotFound(f"document {reference!r} not found")


def _select_artifact(
    artifacts: list[dict[str, Any]], *, doc_key: str | None, prefer_pdf: bool
) -> dict[str, Any] | None:
    """Pick which raw artifact to hand out for a Stichtag. An explicit *doc_key* wins; else for
    a financial institution prefer the official PDF (banks/insurers file PDF, ROADMAP P2.2);
    else the most recently submitted artifact."""
    if not artifacts:
        return None
    if doc_key is not None:
        for art in artifacts:
            if art.get("doc_key") == doc_key:
                return art
    ranked = sorted(artifacts, key=lambda a: (a.get("eingereicht") or "", a.get("doc_key") or ""))
    if prefer_pdf:
        pdfs = [a for a in ranked if str(a.get("dateiendung", "")).lower() == "pdf"]
        if pdfs:
            return pdfs[-1]
    return ranked[-1]


def get_document(
    cosmos: CosmosStoreLike, reference: str, blob: BlobStoreLike | None = None
) -> dict[str, Any]:
    """Resolve a filing document and return a **time-limited download link** to the official
    artifact in ``90-raw`` (§7.2, ROADMAP P2.2).

    *reference* is a ``document_ref`` (``{fnr}:{stichtag}``) from get_company_details, a bare
    FNR (→ latest filing), or a legacy ``doc_key``. The blob path is read from the per-Stichtag
    ``_manifest.json``; the chosen artifact (the PDF for a bank/insurer) is signed with a
    short-lived User-Delegation SAS — the URL is returned, never the bytes. When *blob* is
    unconfigured or nothing is ingested yet, falls back to metadata only (``download: null``)."""
    fnr, stichtag, doc_key, served = _resolve_document(cosmos, reference)

    result: dict[str, Any] = {"doc_key": reference, "fnr": fnr, "stichtag": stichtag}
    if served is not None:
        for filing in served.get("filings", []):
            if filing.get("stichtag") == stichtag:
                result["filing"] = filing
                break
    fi = _financial_institution(served, load_fi_directory(cosmos)) if served is not None else None
    if fi is not None:
        # An FI's UGB figures are absent by construction — surface the flag + caveat so the agent
        # reads the official PDF instead of treating "no Bilanz" as "no data" (ROADMAP P2.1).
        result["financial_institution"] = fi

    download: dict[str, Any] | None = None
    if blob is not None and stichtag:
        manifest = blob.get_json(RAW_CONTAINER, f"{fnr}/{stichtag}/_manifest.json")
        artifacts = list((manifest or {}).get("artifacts", []))
        artifact = _select_artifact(artifacts, doc_key=doc_key, prefer_pdf=fi is not None)
        if artifact is not None:
            container, _, blob_path = str(artifact.get("blob_path", "")).partition("/")
            ext = str(artifact.get("dateiendung") or "bin")
            link = blob.download_link(
                container or RAW_CONTAINER,
                blob_path,
                filename=f"{fnr}_{stichtag}_jahresabschluss.{ext}",
                content_type=artifact.get("content_type"),
            )
            result["document"] = {
                "dateiendung": artifact.get("dateiendung"),
                "content_type": artifact.get("content_type"),
                "bytes": artifact.get("bytes"),
                "dokumentart": artifact.get("dokumentart"),
                "blob_path": artifact.get("blob_path"),
            }
            download = {
                "url": link.url,
                "expires_at": link.expires_at,
                "expires_in_seconds": link.expires_in_seconds,
            }
    result["download"] = download
    if download is None:
        result["note"] = (
            "Kein abrufbares Originaldokument für diesen Stichtag im Rohspeicher — noch nicht "
            "ingestiert oder Download nicht konfiguriert; nur Metadaten."
        )
    if served is None and download is None:
        raise NotFound(f"document {reference!r} not found")
    provenance = _provenance(served) if served is not None else PublicProvenance()
    return {
        "schema_version": "1.0",
        "result": result,
        "provenance": provenance.model_dump(mode="json"),
    }


# --- coverage dashboard (internal/ops, §11) ------------------------------------

REGISTRY = "99_registry"


def coverage_summary(cosmos: CosmosStoreLike) -> dict[str, Any]:
    """Universe coverage: XML vs PDF-only vs none, formats, status, presented count (§11).

    Answers "how many are PDF-only" directly. Read-only aggregation over the registry
    (`known_filings`) and the served layer.
    """
    total = with_xml = pdf_only = none = 0
    by_format: dict[str, int] = {}
    by_status: dict[str, int] = {}
    for doc in cosmos.iter_all(REGISTRY):
        if str(doc.get("id", "")).startswith("__"):  # skip watermark / run lock
            continue
        total += 1
        by_status[doc.get("status", "unknown")] = by_status.get(doc.get("status", "unknown"), 0) + 1
        filings = doc.get("known_filings", []) or []
        formats = {f.get("format") or f.get("dateiendung") for f in filings}
        for f in filings:
            fmt = f.get("format") or f.get("dateiendung") or "unknown"
            by_format[fmt] = by_format.get(fmt, 0) + 1
        if not filings:
            none += 1
        elif formats <= {"pdf"}:
            pdf_only += 1
        else:
            with_xml += 1
    presented = sum(1 for _ in _all_presented(cosmos))
    parse_success = _parse_success_rates(cosmos)
    return {
        "schema_version": "1.0",
        "result": {
            "total_companies": total,
            "with_xml": with_xml,
            "pdf_only": pdf_only,
            "no_filings": none,
            "filings_by_format": by_format,
            "companies_by_status": by_status,
            "presented_count": presented,
            # Parse-success rate by format/year — the metric that surfaces a silently
            # failing format (e.g. an unhandled schema variant) in production (§11).
            "parse_success_by_format": parse_success["by_format"],
            "parse_success_by_year": parse_success["by_year"],
        },
        "provenance": PublicProvenance().model_dump(mode="json"),
    }


def _parse_success_rates(cosmos: CosmosStoreLike) -> dict[str, dict[str, dict[str, float | int]]]:
    """Parse-success counts by filing format and by fiscal year (§11).

    Reads the consolidated layer, where each ``FilingRef`` records ``parsed`` (False for
    dead-lettered / empty-extract filings). A format whose ``rate`` falls toward 0 is the
    early-warning signal a schema variant has stopped extracting — what would have caught
    the JAb 4.0 regression before it reached the served layer.
    """
    by_format: dict[str, dict[str, float | int]] = {}
    by_year: dict[str, dict[str, float | int]] = {}

    def bump(bucket: dict[str, dict[str, float | int]], key: str, ok: bool) -> None:
        slot = bucket.setdefault(key, {"total": 0, "parsed": 0})
        slot["total"] = int(slot["total"]) + 1
        if ok:
            slot["parsed"] = int(slot["parsed"]) + 1

    for doc in cosmos.iter_all(CONSOLIDATED):
        if str(doc.get("id", "")).startswith("__"):
            continue
        for f in doc.get("filings", []) or []:
            fmt = f.get("format") or "unknown"
            stichtag = f.get("stichtag") or ""
            year = stichtag[:4] if stichtag[:4].isdigit() else "unknown"
            ok = bool(f.get("parsed"))
            bump(by_format, fmt, ok)
            bump(by_year, year, ok)

    for bucket in (by_format, by_year):
        for slot in bucket.values():
            total = int(slot["total"])
            slot["rate"] = round(int(slot["parsed"]) / total, 4) if total else 0.0
    return {"by_format": by_format, "by_year": by_year}


# Aggregates over the whole served universe (~340k docs) must never stream every document
# into the request — that blows the timeout and drops the MCP connection (observed live on
# list_sectors). This Cosmos SDK build also rejects GROUP BY ("client does not support
# GroupBy"), so the taxonomy is **precomputed** into a single __stats__ doc by the pipeline
# (store_stats) and served O(1). _LIVE_SCAN_MAX gates the Python fallback so the in-memory
# test store still computes live, while production never scans inline.
STATS_ID = "__stats__"
_LIVE_SCAN_MAX = 5000
_COHORT_MEDIAN_MAX = 20000  # exact median needs the values; skip it above this cohort size


def _scalar(cosmos: CosmosStoreLike, container: str, sql: str, params: list[dict[str, Any]]) -> Any:
    """First row of a query — an int for ``SELECT VALUE COUNT(1)`` on real Cosmos, or a whole
    doc on the in-memory store (which ignores SQL). Callers branch on ``isinstance(_, int)``."""
    return next(iter(cosmos.query(container, sql, params)), 0)


def _count_where(
    cosmos: CosmosStoreLike, container: str, where: str, params: list[dict[str, Any]]
) -> int | None:
    """Server-side ``COUNT(1)``; ``None`` signals the in-memory store (SQL ignored) so the
    caller can fall back to Python over its small dataset."""
    raw = _scalar(cosmos, container, f"SELECT VALUE COUNT(1) FROM c WHERE {where}", params)
    return raw if isinstance(raw, int) else None


def _scalar_floats(
    cosmos: CosmosStoreLike, container: str, sql: str, params: list[dict[str, Any]]
) -> list[float]:
    """Numeric values from a ``SELECT VALUE`` projection (the query Protocol is typed for docs,
    so each value is widened to ``Any`` before the numeric check)."""
    out: list[float] = []
    for v in cosmos.query(container, sql, params):
        val: Any = v
        if isinstance(val, int | float) and not isinstance(val, bool):
            out.append(float(val))
    return out


def _compute_sectors(cosmos: CosmosStoreLike) -> dict[str, dict[str, int]]:
    """Legal-form + size-class counts via a lean two-field projection (no GROUP BY). Heavy
    (touches every served doc) — run offline by store_stats, never in a request."""
    legal_forms: dict[str, int] = {}
    size_classes: dict[str, int] = {}
    sql = (
        "SELECT c.identity.legal_form AS lf, c.size.gkl AS gkl "
        'FROM c WHERE NOT STARTSWITH(c.id, "__")'
    )
    for row in cosmos.query(PRESENTED, sql):
        if str(row.get("id", "")).startswith("__"):  # in-memory store: row is a full doc
            continue
        lf = row.get("lf") if "lf" in row else _g(row, "identity", "legal_form")
        gkl = row.get("gkl") if "gkl" in row else _g(row, "size", "gkl")
        if lf:
            legal_forms[str(lf)] = legal_forms.get(str(lf), 0) + 1
        if gkl:
            size_classes[str(gkl)] = size_classes.get(str(gkl), 0) + 1
    return {"legal_forms": legal_forms, "size_classes": size_classes}


def _load_stats(cosmos: CosmosStoreLike) -> dict[str, Any] | None:
    doc = cosmos.get(PRESENTED, STATS_ID)
    return (doc or {}).get("stats") if doc else None


def store_stats(cosmos: CosmosStoreLike, *, include_coverage: bool = True) -> dict[str, Any]:
    """Materialise the expensive aggregates into the ``__stats__`` doc so the read tools serve
    them O(1). Called by the pipeline (and a one-off populate); never from a request path."""
    from fbl_core.lineage import now_utc_z

    stats: dict[str, Any] = {"sectors": _compute_sectors(cosmos)}
    if include_coverage:
        stats["coverage"] = coverage_summary(cosmos)["result"]
    cosmos.upsert(
        PRESENTED,
        {"id": STATS_ID, "fnr": STATS_ID, "stats": stats, "computed_at": now_utc_z()},
    )
    return stats


def coverage(cosmos: CosmosStoreLike) -> dict[str, Any]:
    """Coverage dashboard served from the precomputed ``__stats__`` doc (O(1)). The live
    computation (``coverage_summary``) triple-scans ~340k docs, so it only runs on a
    small/test store; in production a missing stats doc returns ``pending`` rather than
    stalling the request."""
    stats = _load_stats(cosmos)
    if stats and stats.get("coverage"):
        result: dict[str, Any] = stats["coverage"]
    elif (_count_where(cosmos, REGISTRY, "NOT STARTSWITH(c.id, '__')", []) or 0) <= _LIVE_SCAN_MAX:
        result = coverage_summary(cosmos)["result"]
    else:
        result = {"pending": True}
    return {
        "schema_version": "1.0",
        "result": result,
        "provenance": PublicProvenance().model_dump(mode="json"),
    }


def list_sectors(cosmos: CosmosStoreLike) -> dict[str, Any]:
    """v1 taxonomy: legal forms + size classes with counts. Served from the precomputed
    ``__stats__`` doc (O(1)); falls back to a live scan only on a small/test store."""
    stats = _load_stats(cosmos)
    if stats and stats.get("sectors"):
        sectors = stats["sectors"]
    elif (_count_where(cosmos, PRESENTED, "NOT STARTSWITH(c.id, '__')", []) or 0) <= _LIVE_SCAN_MAX:
        sectors = _compute_sectors(cosmos)
    else:  # production, stats doc missing — never scan inline
        sectors = {"legal_forms": {}, "size_classes": {}, "pending": True}
    return {
        "schema_version": "1.0",
        "result": {
            **sectors,
            "size_class_labels": {"W": "Mikro/Kleinst", "K": "Klein", "M": "Mittel", "G": "Groß"},
        },
        "provenance": PublicProvenance().model_dump(mode="json"),
    }


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
        from .errors import BadRequest

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


FIELD_REFERENCE_URL = "https://www.agentic-firmenbuch.at/felder.html"

# Catalog of every field the server can return, by tool tier. Kept in sync with the served
# Pydantic models (fbl_core.models.mcp) and the prose reference at FIELD_REFERENCE_URL /
# docs/FIELD_REFERENCE.md. Lets an agent discover the full field set programmatically instead
# of guessing from a single search card.
_BILANZ_POSITIONS = [
    "bilanzsumme",
    "eigenkapital",
    "verbindlichkeiten",
    "anlagevermoegen",
    "umlaufvermoegen",
    "sachanlagen",
    "finanzanlagen",
    "vorraete",
    "forderungen",
    "cash",
    "rueckstellungen",
    "stammkapital",
    "kapitalruecklagen",
    "gewinnruecklagen",
    "bilanzgewinn_verlust",
]
_GUV_POSITIONS = [
    "umsatzerloese",
    "materialaufwand",
    "personalaufwand",
    "abschreibungen",
    "ebit",
    "ebitda",
    "jahresueberschuss",
]
_RATIOS = [
    "equity_ratio",
    "debt_ratio",
    "debt_to_equity",
    "working_capital_ratio",
    "anlagedeckungsgrad_1",
    "ebit_margin",
    "ebitda_margin",
    "net_margin",
    "personalkostenquote",
    "materialaufwandsquote",
    "roa",
    "roe",
    "capital_profile",
]


def describe_fields() -> dict[str, Any]:
    """Self-describing catalog of every field the server can return (§9).

    Static schema doc — no per-company lookup. Tells an agent which fields exist at each
    tier (search card → full profile → full record), the code tables, and the availability
    rules (when a field is null). Per-company availability is exposed on the records
    themselves: ``has_guv_latest``, ``employees`` (null when unknown), ``filing_years_available``.
    """
    return {
        "schema_version": "1.0",
        "reference_url": FIELD_REFERENCE_URL,
        "tiers": {
            "search_companies": {
                "returns": "compact summary card per hit — NOT the full record",
                "fields": list(CompanyCard.model_fields.keys()),
            },
            "get_company_details": {
                "returns": "full served profile for one company",
                "sections": {
                    "identity": ["fnr", "register_id", "name", "legal_form", "status", "court"],
                    "location": ["country", "bundesland", "city", "postal_code", "street"],
                    "company": [
                        "stammkapital",
                        "first_filing_year",
                        "last_filing_year",
                        "filing_years_available",
                        "founded_year",
                        "founded_source",
                        "description",
                    ],
                    "size": ["gkl", "bilanzsumme_band", "peer_percentiles"],
                    "financials": {
                        "scalars": ["latest_year", "has_guv_latest", "revenue_basis", "latest"],
                        "bilanz_positions": _BILANZ_POSITIONS,
                        "guv_positions": _GUV_POSITIONS,
                    },
                    "ratios": _RATIOS,
                    "growth": ["profile", "method"],
                    "employees": ["latest", "latest_year", "history"],
                    "filings[]": ["stichtag", "format", "parsed", "gkl", "eingereicht", "doc_key"],
                    "events[]": ["registered events (V1: usually empty)"],
                    "management": [
                        "n_signatories_latest",
                        "signatories_stable_years",
                        "primary_manager.age",
                        "primary_manager.birth_year",
                        "primary_manager.role_label",
                        "primary_manager.vertretung",
                    ],
                },
            },
            "get_full_record": {
                "returns": "superset of the profile",
                "adds": [
                    "financials.positions (full 317-position UGB taxonomy)",
                    "financials.passthrough (unknown source codes + history)",
                    "financials.completeness (per-year QA metric)",
                    "financials.guv_years",
                    "management.signatories_history",
                    "derivations (metrics_version + formula registry)",
                ],
            },
        },
        "codes": {
            "bundesland": _BL_CODE_TO_NAME,
            "gkl": {"W": "Kleinst/Mikro", "K": "Klein", "M": "Mittel", "G": "Groß"},
            "legal_form": "profile carries the raw Firmenbuch code; GmbH family = 'GE…' prefix "
            "(GES ≈ 99.7%); the search card labels it 'GmbH'",
        },
        "metric_definitions": {
            "ebit": "Operating result (Betriebserfolg, the §231 Abs 2 UGB operating subtotal) "
            "BEFORE financial result and taxes. The UGB GuV reports no EBIT line, so this is a "
            "simplified approximation; it is NOT strict EBIT (earnings before interest and taxes, "
            "which includes the financial result). For entities with material financial / "
            "participation income (e.g. holdings) the two can differ materially.",
            "ebitda": "ebit (= Betriebserfolg) plus depreciation & amortisation (abschreibungen). "
            "Same operating-result basis and caveat as ebit.",
            "ebit_margin": "ebit / umsatzerloese (operating-result basis, see ebit).",
            "ebitda_margin": "ebitda / umsatzerloese (operating-result basis, see ebit).",
        },
        "availability_rules": [
            "search_companies returns a summary card, not all data — escalate to "
            "get_company_details / get_full_record for the full field set.",
            "guv positions + revenue are present only when has_guv is true (small companies "
            "often file a Bilanz only).",
            "employees is frequently null — the Firmenbuch reports headcount only sparsely.",
            "growth.profile is null until at least 2 comparable years exist.",
            "GDPR: officer names ARE served (public Firmenbuch data, per-query lookup); birth "
            "data is year only (birth_year / age) — never month or day.",
        ],
        "provenance": PublicProvenance().model_dump(mode="json"),
    }


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    mid = len(values) // 2
    if len(values) % 2:
        return values[mid]
    return (values[mid - 1] + values[mid]) / 2
