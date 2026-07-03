"""Ingest run-report models — layer ``90_raw`` (§8.3).

Registry models (``RegistryDoc``/``KnownFiling``/``Watermark``) live in the
``fbl_registry`` package (layer 99_registry).
"""

from __future__ import annotations

from pydantic import BaseModel, Field

LAYER = "90_raw"


class DriftEntry(BaseModel):
    """One company the reconcile had to correct — i.e. the change feed missed it."""

    fnr: str
    name: str | None = None
    rechtsform: str | None = None
    status: str | None = None


class SyncReport(BaseModel):
    seeded: int = 0
    updated: int = 0
    marked_deleted: int = 0
    total_seen: int = 0
    incomplete_branches: list[str] = Field(default_factory=list)
    counts_by_rechtsform: dict[str, int] = Field(default_factory=dict)
    source: str = "sucheFirma_sweep"
    # Drift detection (§15a.1): companies the full reconcile had to add/remove that the
    # daily change feed should have caught. On the very first run the registry is empty,
    # so `was_initial_seed` is true and `seeded_companies` is the whole universe (not drift).
    was_initial_seed: bool = False
    seeded_companies: list[DriftEntry] = Field(default_factory=list)
    deleted_companies: list[DriftEntry] = Field(default_factory=list)


class IngestReport(BaseModel):
    run_id: str
    companies: int = 0
    filings_downloaded: int = 0
    filings_skipped: int = 0
    pdfs_downloaded: int = 0
    responses_archived: int = 0  # verbatim API responses written to 90-raw/_responses (§5.1)
    failures: int = 0
    dead_letters: list[str] = Field(default_factory=list)


class DeltaReport(BaseModel):
    run_id: str
    new_companies: int = 0
    doc_changes: int = 0
    status_changes: int = 0
    dirty_fnrs: list[str] = Field(default_factory=list)
