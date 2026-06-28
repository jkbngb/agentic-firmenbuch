"""The ``99_registry`` catalog — source of truth for which companies exist + their state.

Wraps a ``CosmosStoreLike`` so it works against Azure Cosmos or the in-memory fake.
Drives every download/rebuild/reconcile (§15a.0). The watermark is a singleton doc.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence

from fbl_core.financial_institution import classify_financial_institution
from fbl_core.lineage import now_utc_z
from fbl_core.storage import CosmosStoreLike

from .models import (
    REGISTRY_CONTAINER,
    KnownFiling,
    PipelineState,
    RegistryDoc,
    RegistryStatus,
    Watermark,
)


class Registry:
    """Catalog operations over the ``99_registry`` Cosmos container."""

    def __init__(self, store: CosmosStoreLike, container: str = REGISTRY_CONTAINER) -> None:
        self._store = store
        self._container = container

    # --- per-company ---------------------------------------------------------

    def get(self, fnr: str) -> RegistryDoc | None:
        doc = self._store.get(self._container, fnr)
        return RegistryDoc.model_validate(doc) if doc is not None else None

    def put(self, doc: RegistryDoc) -> None:
        self._store.upsert(self._container, doc.model_dump(mode="json"))

    def ensure(
        self,
        fnr: str,
        *,
        status: RegistryStatus = "active",
        source: str,
        name: str | None = None,
        rechtsform: str | None = None,
    ) -> RegistryDoc:
        """Insert a brand-new FNR if absent; return the (existing or new) doc."""
        existing = self.get(fnr)
        if existing is not None:
            return existing
        now = now_utc_z()
        doc = RegistryDoc(
            id=fnr,
            fnr=fnr,
            name=name,
            rechtsform=rechtsform,
            status=status,
            discovered_at=now,
            source=source,
            last_seen_in_registry=now,  # full ISO-8601 Z timestamp
        )
        self.put(doc)
        return doc

    def set_status(self, fnr: str, status: RegistryStatus) -> None:
        doc = self.get(fnr)
        if doc is None:
            return
        doc.status = status
        self.put(doc)

    def mark_dirty(self, fnr: str, *, reason: str) -> None:
        doc = self.get(fnr)
        if doc is None:
            return
        doc.pipeline_state = "dirty"
        doc.dirty_reason = reason
        self.put(doc)

    def mark_clean(self, fnr: str) -> None:
        doc = self.get(fnr)
        if doc is None:
            return
        doc.pipeline_state = "clean"
        doc.dirty_reason = None
        doc.dead_letter = None
        self.put(doc)

    def dead_letter(self, fnr: str, error: str) -> None:
        doc = self.get(fnr)
        if doc is None:
            return
        doc.pipeline_state = "failed"
        doc.dead_letter = error
        self.put(doc)

    def record_filing(self, fnr: str, filing: KnownFiling) -> None:
        """Add/replace a known filing (keyed by doc_key) and stamp the check time."""
        doc = self.get(fnr)
        if doc is None:
            doc = self.ensure(fnr, source="sucheUrkunde")
        doc.known_filings = [f for f in doc.known_filings if f.doc_key != filing.doc_key]
        doc.known_filings.append(filing)
        doc.last_filing_check_at = now_utc_z()
        self.put(doc)

    def has_filing(self, fnr: str, doc_key: str) -> bool:
        doc = self.get(fnr)
        return doc is not None and any(f.doc_key == doc_key for f in doc.known_filings)

    # --- sets ----------------------------------------------------------------

    @staticmethod
    def _is_company(doc: dict[str, object]) -> bool:
        # Reserved singletons (watermark, run lock) use "__"-prefixed ids.
        return not str(doc.get("id", "")).startswith("__")

    def all_fnrs(self) -> list[str]:
        return sorted(
            d["fnr"] for d in self._store.iter_all(self._container) if self._is_company(d)
        )

    def active_fnrs(self) -> list[str]:
        """Only currently-registered companies (``status == "active"``) — excludes
        historical/deleted FNRs."""
        return sorted(
            d["fnr"]
            for d in self._store.iter_all(self._container)
            if self._is_company(d) and d.get("status") == "active"
        )

    def active_fnrs_by_rechtsform(self, *rechtsformen: str) -> list[str]:
        """Active companies whose ``rechtsform`` is one of *rechtsformen* (e.g. ``"GES"`` for
        GmbH). Drives a per-form bulk backfill-process — process the highest-value form
        (GmbH) first, widen to the others later (the pipeline is form-agnostic, §15b 20a)."""
        wanted = set(rechtsformen)
        return sorted(
            d["fnr"]
            for d in self._store.iter_all(self._container)
            if self._is_company(d) and d.get("status") == "active" and d.get("rechtsform") in wanted
        )

    def financial_institution_fnrs(self) -> list[str]:
        """Active companies the FI heuristic flags as a bank or insurer (ROADMAP P2.2).

        Drives the FI-targeted PDF ingest (``ingest-fi``): banks (BWG) and insurers (VAG)
        file their Jahresabschluss as a **PDF**, which the general backfill skips
        (``include_pdf=False``) to spare storage across all 340k companies. This narrow
        worklist — the few hundred regulated FIs the shipped classifier
        (:func:`~fbl_core.financial_institution.classify_financial_institution`) recognises
        from legal form + name — is the set whose official PDF abschlüsse we DO want in
        ``90-raw``. Reuses the exact serve-time classifier the MCP applies, so the ingested
        set is identical to the flagged set (no drift). Pure registry read."""
        return sorted(
            d["fnr"]
            for d in self._store.iter_all(self._container)
            if self._is_company(d)
            and d.get("status") == "active"
            and classify_financial_institution(d.get("rechtsform"), d.get("name")) is not None
        )

    def ingestable_active_fnrs(self, priority: Sequence[str] = ()) -> list[str]:
        """The active-backfill worklist: active companies that carry **master data**
        (a known ``name``).

        Bare change-feed stubs (``source == "veraenderungenFirma"`` with ``name is None``)
        are deliberately EXCLUDED — they are just an FNR the delta feed flagged, with no
        register details yet. Calling the API for them resolves slowly or not at all, so
        including them in the bulk backfill made it crawl/stall on a tail of unresolvable
        FNRs (§15a.1). They are enriched + ingested by the daily pipeline instead (which
        looks up their master data by FNR), so nothing is lost — they just don't belong in
        the one-off bulk grind.

        When *priority* names Rechtsform codes, companies of those forms come FIRST (in the
        order listed), then everyone else. The filing-check (``sucheUrkunde``) costs one API
        round-trip per company whether or not the company ever filed, so against a per-run
        time budget the order decides which companies get checked at all. Putting the
        publication-required Kapitalgesellschaften (GmbH/AG …) first is what closes the real
        addressable gap before the long tail of forms that almost never file (Einzelunter-
        nehmer/OG/…). The download-checkpoint is a *set* of done FNRs, so reordering the
        worklist never breaks resume — it only re-prioritises the still-pending companies.
        Within each tier FNRs are sorted, so the order stays deterministic (§15a.1, P1)."""
        rank = {rf: i for i, rf in enumerate(priority)}
        tail = len(priority)
        rows = [
            (rank.get(str(d.get("rechtsform")), tail), d["fnr"])
            for d in self._store.iter_all(self._container)
            if self._is_company(d) and d.get("status") == "active" and d.get("name")
        ]
        rows.sort(key=lambda t: (t[0], t[1]))
        return [fnr for _, fnr in rows]

    def dirty_fnrs(self) -> list[str]:
        return sorted(
            d["fnr"]
            for d in self._store.query_by_field(self._container, "pipeline_state", "dirty")
            if self._is_company(d)
        )

    def iter_docs(self) -> Iterator[RegistryDoc]:
        for d in self._store.iter_all(self._container):
            if self._is_company(d):
                yield RegistryDoc.model_validate(d)

    def count(self) -> int:
        return len(self.all_fnrs())

    # --- watermark -----------------------------------------------------------

    def get_watermark(self) -> Watermark:
        doc = self._store.get(self._container, Watermark().id)
        return Watermark.model_validate(doc) if doc is not None else Watermark()

    def set_watermark(self, last_change_date: str) -> None:
        wm = Watermark(last_change_date=last_change_date, updated_at=now_utc_z())
        self._store.upsert(self._container, wm.model_dump(mode="json"))

    def set_pipeline_state(self, fnr: str, state: PipelineState) -> None:
        doc = self.get(fnr)
        if doc is None:
            return
        doc.pipeline_state = state
        self.put(doc)
