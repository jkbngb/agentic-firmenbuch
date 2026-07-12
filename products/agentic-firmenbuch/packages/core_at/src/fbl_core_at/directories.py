"""Parse the OeNB financial-institution lists (MFI / NMFI) into typed records.

The OeNB publishes its register of Austrian credit institutions as free, CC-BY bulk CSVs
(`oenb.at/docroot/downloads_observ/MFI.csv`, `…/NMFI.csv`). These are the **authoritative,
Firmenbuchnummer-keyed** source for the `is_financial_institution` flag (ROADMAP P2 / issue
#15) — replacing the lossy name heuristic, which provably missed acronym/compound names
(BAWAG, Oberbank, VIG).

This module is the pure parser only — no HTTP, no storage — so it unit-tests against a fixture.
The file is quirky: Windows/latin-1 encoding, semicolon-delimited, a `Stand` date on line 0,
then a "Veränderungen zum Vormonat" (Neuzugang/Abgang) change block, then the real header row
(`Nr.;Institut;RIAD-Code;OeNB-IdentNr;FB-Nr;E-VGR;Institutsart;MR-Pflichtig;MR-Ausnahme;LEI`).
We key on the **header NAMES** (not column order) and keep **every** column verbatim in
``fields`` so nothing is lost if OeNB adds columns later (§5.1 lossless spirit).
"""

from __future__ import annotations

import csv
import io
import time
import weakref
from typing import Any

from pydantic import BaseModel, Field

from fbl_core.storage import CosmosStoreLike

from .esvg import esvg_kind, esvg_label

# Cosmos container of register-sourced financial institutions (banks/insurers), keyed by FN.
DIRECTORIES_CONTAINER = "00_directories"

# Header cells (by name) we lift into typed fields; everything is also kept in ``fields``.
_FNR = "FB-Nr"
_NAME = "Institut"
_RIAD = "RIAD-Code"
_IDENT = "OeNB-IdentNr"
_LEI = "LEI"
_ART = "Institutsart"
_EVGR = "E-VGR"


class FinancialInstitutionRecord(BaseModel):
    """One licensed institution from an OeNB list. ``fnr`` is None for entities without a
    Firmenbuch entry (e.g. the OeNB itself) — kept for completeness, just not joinable."""

    fnr: str | None = None  # from FB-Nr (the join key); None if the entity has no Firmenbuch entry
    name: str
    kind: str = "bank"  # from the E-VGR/ESVG sector (bank/insurer/pensionskasse/fund/…), see esvg
    source: str  # "oenb_mfi" | "oenb_nmfi"
    riad_code: str | None = None
    oenb_ident: str | None = None
    lei: str | None = None
    institutsart: str | None = None
    e_vgr: str | None = None  # ESVG sector code (the authoritative sector key, OeNB E-VGR column)
    sector_label: str | None = None  # official Bezeichnung for e_vgr (esvg legend)
    fields: dict[str, str] = Field(default_factory=dict)  # ALL columns verbatim (forward-compat)


class OeNBList(BaseModel):
    """The parsed list: the file's ``Stand`` date + the records."""

    stand: str | None = None  # the "Stand" date (DD.MM.YYYY) from line 0, verbatim
    source: str
    records: list[FinancialInstitutionRecord] = Field(default_factory=list)


def _clean(value: str | None) -> str | None:
    v = (value or "").strip()
    return v or None


def parse_oenb_list(data: bytes, *, source: str, kind: str = "bank") -> OeNBList:
    """Parse an OeNB MFI/NMFI CSV (latin-1, semicolon). ``source`` labels the origin
    (``"oenb_mfi"``/``"oenb_nmfi"``). Robust to the leading date + change block: the data
    header is the first row whose cells include ``FB-Nr``."""
    text = data.decode("latin-1")
    rows = list(csv.reader(io.StringIO(text), delimiter=";"))

    stand = _clean(rows[0][0]) if rows and rows[0] else None

    # Find the data header by its column NAMES, not a fixed line number: the leading "Stand"
    # date + the "Veränderungen zum Vormonat" (Neuzugang/Abgang) block push the header up/down
    # month to month (MFI had it at line 8, NMFI at line 3). Require BOTH FB-Nr and Institut so
    # the change block's mini-header (`;Institut;RIAD-Code;IdentNr`, no FB-Nr) is never mistaken
    # for it. NMFI also has fewer columns (no Institutsart) — keying on names handles that too.
    def _is_header(r: list[str]) -> bool:
        cells = {c.strip() for c in r}
        return _FNR in cells and _NAME in cells

    header_idx = next((i for i, r in enumerate(rows) if _is_header(r)), None)
    if header_idx is None:
        return OeNBList(
            stand=stand, source=source
        )  # no data header (e.g. an empty change-only file)

    cols = [c.strip() for c in rows[header_idx]]
    out: list[FinancialInstitutionRecord] = []
    for r in rows[header_idx + 1 :]:
        if not any(c.strip() for c in r):
            continue  # blank separator line
        fields = {cols[i]: r[i].strip() for i in range(min(len(cols), len(r))) if cols[i]}
        name = _clean(fields.get(_NAME))
        if name is None:
            continue  # a stray section marker, not an institution
        e_vgr = _clean(fields.get(_EVGR))
        # The E-VGR/ESVG sector is the authoritative kind (1220*=bank, 1250B=Vorsorgekasse, …) —
        # far better than calling every NMFI row a "bank". Fall back to the caller default if the
        # column is absent (older files / a list without E-VGR).
        out.append(
            FinancialInstitutionRecord(
                fnr=_clean(fields.get(_FNR)),
                name=name,
                kind=esvg_kind(e_vgr) if e_vgr else kind,
                source=source,
                riad_code=_clean(fields.get(_RIAD)),
                oenb_ident=_clean(fields.get(_IDENT)),
                lei=_clean(fields.get(_LEI)),
                institutsart=_clean(fields.get(_ART)),
                e_vgr=e_vgr,
                sector_label=esvg_label(e_vgr),
                fields=fields,
            )
        )
    return OeNBList(stand=stand, source=source, records=out)


# EIOPA register columns (the AT export; semicolon-delimited, UTF-8 BOM). We key by name.
_EIOPA_COUNTRY = "Home Country"
_EIOPA_LEI = "LEI"
_EIOPA_NAME = "Official name of the entity"
_EIOPA_INTL_NAME = "International Name"
_EIOPA_IDCODE = "Identification code"
# ESVG/ESA-2010 sector for insurance corporations (S.128); the OeNB E-VGR equivalent is 1280.
_INSURER_EVGR = "1280"


def parse_eiopa_at(data: bytes, *, source: str = "eiopa") -> OeNBList:
    """Parse the EIOPA register of insurance undertakings (AT export) into insurer records.

    The export is semicolon-delimited with a UTF-8 BOM; the header carries ``Home Country``,
    ``LEI``, ``Official name of the entity``, ``Identification code``. We keep only Home
    Country = AT rows (defensive even though the export is pre-filtered), dedupe by LEI (rows
    repeat per cross-border status), and set ``kind="insurer"`` (E-VGR 1280). ``fnr`` stays
    ``None`` here — the Firmenbuchnummer is resolved separately via the GLEIF LEI bridge."""
    text = data.decode("utf-8-sig", errors="replace")
    rows = list(csv.reader(io.StringIO(text), delimiter=";"))
    if not rows:
        return OeNBList(source=source)
    cols = [c.strip() for c in rows[0]]
    idx = {name: i for i, name in enumerate(cols)}

    def cell(r: list[str], name: str) -> str | None:
        i = idx.get(name)
        return _clean(r[i]) if i is not None and i < len(r) else None

    out: list[FinancialInstitutionRecord] = []
    seen_lei: set[str] = set()
    for r in rows[1:]:
        if not any(c.strip() for c in r):
            continue
        country = (cell(r, _EIOPA_COUNTRY) or "").upper()
        if country not in ("AT", "AUSTRIA", "ÖSTERREICH"):
            continue
        name = cell(r, _EIOPA_NAME) or cell(r, _EIOPA_INTL_NAME)
        if name is None:
            continue
        lei = cell(r, _EIOPA_LEI)
        if lei is not None:
            if lei in seen_lei:
                continue  # one undertaking, many cross-border rows
            seen_lei.add(lei)
        fields = {cols[i]: r[i].strip() for i in range(min(len(cols), len(r))) if cols[i]}
        out.append(
            FinancialInstitutionRecord(
                fnr=None,  # resolved via GLEIF LEI→FN downstream
                name=name,
                kind="insurer",
                source=source,
                lei=lei,
                e_vgr=_INSURER_EVGR,
                sector_label=esvg_label(_INSURER_EVGR),
                fields=fields,
            )
        )
    return OeNBList(source=source, records=out)


def load_fi_directory(cosmos: CosmosStoreLike) -> dict[str, str]:
    """The served lookup: ``{fnr: kind}`` for every **active** institution in ``00_directories``.
    Small (~450 rows) → cheap to load + cache. The MCP joins this by FN at serve time so the flag
    is register-based (authoritative), with the name heuristic only as a fallback for entries not
    in the list (e.g. foreign branches outside the OeNB/EIOPA registers)."""
    out: dict[str, str] = {}
    try:
        items = list(cosmos.iter_all(DIRECTORIES_CONTAINER))
    except Exception:
        return out  # container not provisioned yet (e.g. before the first directories run)
    for d in items:
        fnr = d.get("fnr")
        if fnr and d.get("active"):
            out[str(fnr)] = str(d.get("kind") or "bank")
    return out


# Module-level TTL cache for the served FI directory. Without it every ``search_companies``
# call (and each get_document / find_peers / cohort card builder) does a full ~450-row read of
# ``00_directories`` — ~0.2 s of pure overhead on EVERY request. The register changes at most
# daily, so 15 min of staleness is invisible to callers (T3).
#
# Keyed by the store OBJECT via a WeakKeyDictionary, deliberately not by ``id(cosmos)``: an int
# id is recycled by CPython once the store is garbage-collected, so a fresh in-memory store in a
# later test could collide with a dead store's id and get a stale hit. A weak key auto-evicts
# when the store dies, so a new store is always a clean miss while a long-lived production store
# keeps its warm entry.
_FI_CACHE: weakref.WeakKeyDictionary[Any, tuple[float, dict[str, str]]] = (
    weakref.WeakKeyDictionary()
)
_FI_TTL_SECONDS = 900.0


def load_fi_directory_cached(cosmos: CosmosStoreLike) -> dict[str, str]:
    """TTL-cached :func:`load_fi_directory`, keyed weakly by the store so a fresh store is always
    a miss and a long-lived process keeps a warm cache. Returns the SAME dict object on a hit;
    callers treat it as read-only (they do — it's only passed to ``_card``)."""
    now = time.monotonic()
    hit = _FI_CACHE.get(cosmos)
    if hit is not None and now - hit[0] < _FI_TTL_SECONDS:
        return hit[1]
    data = load_fi_directory(cosmos)
    _FI_CACHE[cosmos] = (now, data)
    return data
