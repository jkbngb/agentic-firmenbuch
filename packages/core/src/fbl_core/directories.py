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

from pydantic import BaseModel, Field

# Header cells (by name) we lift into typed fields; everything is also kept in ``fields``.
_FNR = "FB-Nr"
_NAME = "Institut"
_RIAD = "RIAD-Code"
_IDENT = "OeNB-IdentNr"
_LEI = "LEI"
_ART = "Institutsart"


class FinancialInstitutionRecord(BaseModel):
    """One licensed institution from an OeNB list. ``fnr`` is None for entities without a
    Firmenbuch entry (e.g. the OeNB itself) — kept for completeness, just not joinable."""

    fnr: str | None = None  # from FB-Nr (the join key); None if the entity has no Firmenbuch entry
    name: str
    kind: str = "bank"  # OeNB MFI/NMFI are credit institutions; the caller passes "bank"
    source: str  # "oenb_mfi" | "oenb_nmfi"
    riad_code: str | None = None
    oenb_ident: str | None = None
    lei: str | None = None
    institutsart: str | None = None
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
        out.append(
            FinancialInstitutionRecord(
                fnr=_clean(fields.get(_FNR)),
                name=name,
                kind=kind,
                source=source,
                riad_code=_clean(fields.get(_RIAD)),
                oenb_ident=_clean(fields.get(_IDENT)),
                lei=_clean(fields.get(_LEI)),
                institutsart=_clean(fields.get(_ART)),
                fields=fields,
            )
        )
    return OeNBList(stand=stand, source=source, records=out)
