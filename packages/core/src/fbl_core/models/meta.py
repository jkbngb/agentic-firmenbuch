"""Lineage / provenance models (Technische Spezifikation §6, §7).

Every produced document carries a ``Meta`` block so the full chain
raw → parsed → consolidated → derived → presented is walkable by ``doc_id``
and verifiable by ``content_hash``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Stage = Literal["raw", "parsed", "consolidated", "derived", "presented"]


class LineageRef(BaseModel):
    """One upstream provenance entry copied into a downstream document."""

    stage: str
    doc_id: str
    content_hash: str
    created_at: str  # ISO-8601 Z
    producer: str | None = None
    entity_id: str | None = None
    source: str | None = None  # used by fan-in inputs (e.g. "auszug" master extract)


class Meta(BaseModel):
    """The ``_meta`` block attached to every document at every stage."""

    doc_id: str  # uuid4 of THIS document
    entity_id: str  # "093450b" or "093450b/2025-12-31"
    stage: Stage
    producer: str  # "parse@1.0.0"
    source: str = "justizonline_firmenbuch_hvd"
    license: str = "CC-BY-4.0"
    schema_version: str = "1.0"
    metrics_version: str | None = None
    run_id: str
    data_version: int | None = None
    content_hash: str | None = None  # filled last; see §7
    timestamps: dict[str, str] = Field(default_factory=dict)  # {"ingested_at": "...Z", ...}
    checks: dict[str, bool] = Field(default_factory=dict)
    lineage: list[LineageRef] = Field(default_factory=list)  # linear upstream chain
    inputs: list[LineageRef] = Field(default_factory=list)  # fan-in (consolidate)
    supersedes: LineageRef | None = None  # previous version of this entity's doc
