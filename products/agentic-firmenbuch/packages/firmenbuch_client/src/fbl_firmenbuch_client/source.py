"""The ``RegisterSource`` interface (§8.2).

A stable Protocol so the rest of the pipeline depends on the capability, not the
concrete JustizOnline SOAP implementation (swappable / mockable).
"""

from __future__ import annotations

from datetime import date
from typing import NamedTuple, Protocol, runtime_checkable

from .models import AuszugKurz, DocChange, FirmaChange, FirmaResult, UrkundeContent, UrkundeRef


class RawResponse(NamedTuple):
    """A verbatim API response held for lossless archival (§5.1)."""

    endpoint: str  # the SOAP operation that produced it (e.g. "auszug_v2")
    content: bytes  # the raw response body, byte-for-byte


@runtime_checkable
class RawCapturingSource(Protocol):
    """A source that retains raw response bytes so ingest can archive them (§5.1).

    Optional capability: ``isinstance(source, RawCapturingSource)`` lets ingest
    archive verbatim responses when the concrete client supports it, while plain
    test doubles (which don't) skip archival transparently.
    """

    def drain_raw(self) -> list[RawResponse]:
        """Return all captured responses since the last drain and clear the buffer."""
        ...


class RegisterSource(Protocol):
    """Read-only access to the Austrian Firmenbuch register."""

    def suche_firma(
        self,
        firmenwortlaut: str,
        *,
        suchbereich: int = 1,
        rechtsform: str = "",
        exaktesuche: bool = True,
        gericht: str = "",
        ortnr: str = "",
    ) -> list[FirmaResult]: ...

    def suche_urkunde(self, fnr: str) -> list[UrkundeRef]: ...

    def urkunde(self, key: str) -> UrkundeContent: ...

    def auszug(self, fnr: str, *, stichtag: date | None = None) -> AuszugKurz: ...

    def veraenderungen_urkunden(self, von: date, bis: date) -> list[DocChange]: ...

    def veraenderungen_firma(
        self, von: date, bis: date, *, rechtsform: str = ""
    ) -> list[FirmaChange]: ...
