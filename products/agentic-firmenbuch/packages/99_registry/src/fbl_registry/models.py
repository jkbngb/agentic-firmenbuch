"""Registry models — layer ``99_registry`` (§15a.0)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

LAYER = "99_registry"
PipelineState = Literal["clean", "dirty", "failed"]
RegistryStatus = Literal["active", "historical", "deleted"]

WATERMARK_ID = "__watermark__"
REGISTRY_CONTAINER = "99_registry"


class KnownFiling(BaseModel):
    """A filing we know about for a company (from sucheUrkunde / change feed)."""

    stichtag: str | None = None
    doc_key: str
    content_hash: str | None = None
    format: str | None = None
    dateiendung: str | None = None
    downloaded: bool = False


class RegistryDoc(BaseModel):
    """One ``99_registry`` document per FNR (§15a.0)."""

    id: str
    fnr: str
    name: str | None = None  # company name from sucheFirma/bulk (catalog convenience)
    rechtsform: str | None = None  # legal-form code from sucheFirma/bulk, e.g. "GES" (GmbH)
    status: RegistryStatus = "active"
    discovered_at: str | None = None
    source: str | None = None  # hvd_bulk | veraenderungenFirma | sucheFirma_sweep
    last_seen_in_registry: str | None = None
    known_filings: list[KnownFiling] = Field(default_factory=list)
    last_filing_check_at: str | None = None
    pipeline_state: PipelineState = "clean"
    dirty_reason: str | None = None
    data_version: int = 0
    dead_letter: str | None = None


class Watermark(BaseModel):
    """Singleton doc tracking the last processed change-feed date."""

    id: str = WATERMARK_ID
    fnr: str = WATERMARK_ID  # partition key value (registry is partitioned by /fnr)
    last_change_date: str | None = None
    updated_at: str | None = None
