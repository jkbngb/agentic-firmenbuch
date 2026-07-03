"""data.gv.at HVD bulk-dataset seed (§16 #2) — the PREFERRED, completeness-safe seed.

A bulk file with the full FNR list is the only true completeness guarantee, so when one
is available it is preferred over the prefix-walk. As of the probe (see
docs/API_PROBE_FINDINGS.md) the public data.gv.at portal surfaces the HVD **API** and
per-document access, but no single downloadable full-FNR file could be confirmed — so
this is a pluggable hook: provide a ``BulkSource`` (file/URL parser) and ``sync_registry``
uses it; otherwise it falls back to the prefix-walk.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Protocol

from pydantic import BaseModel


class BulkCompany(BaseModel):
    """One company row from the bulk dataset."""

    fnr: str
    status: str | None = None  # "", "historisch", "gelöscht"
    name: str | None = None
    rechtsform: str | None = None


class BulkSource(Protocol):
    """A source of the full company universe (e.g. a parsed data.gv.at bulk file)."""

    def iter_companies(self) -> Iterable[BulkCompany]: ...


class IterableBulkSource:
    """Wrap an in-memory iterable of :class:`BulkCompany` as a ``BulkSource`` (tests/CLI)."""

    def __init__(self, companies: Iterable[BulkCompany]) -> None:
        self._companies = list(companies)

    def iter_companies(self) -> Iterator[BulkCompany]:
        yield from self._companies
