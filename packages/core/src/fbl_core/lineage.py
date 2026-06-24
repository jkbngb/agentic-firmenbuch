"""Lineage helpers (Technische Spezifikation §7).

The one rule that makes the pipeline idempotent: ``content_hash`` is computed over
the document's *content* with the volatile meta excluded (the hash itself,
timestamps, lineage, and inputs). So "same inputs ⇒ same hash" and unchanged
companies can be skipped cheaply.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from .models.meta import LineageRef, Meta

# The content hash covers the business payload only; the entire meta block is
# excluded. §7 names the minimum excludes (content_hash, timestamps, lineage,
# inputs), but its explicit GOAL is "same inputs ⇒ same hash" for skip-unchanged.
# doc_id, run_id, data_version and supersedes are also per-run volatile, so we
# exclude the whole ``meta``/``_meta`` block rather than an incomplete subset.
_META_KEYS = ("meta", "_meta")


def new_doc_id() -> str:
    """Return a fresh uuid4 string. Never reused across stages."""
    return str(uuid.uuid4())


def now_utc_z() -> str:
    """Current UTC time as an ISO-8601 string ending in ``Z`` (second precision)."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _strip_volatile(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of *payload* with the volatile fields removed, for hashing.

    Drops the top-level meta block (``meta`` for internal models, ``_meta`` for
    serialized docs) AND the ``provenance.built_at`` timestamp — which is
    populated post-hash for the served record but otherwise turns identical
    content into different hashes when re-computed after a stage boundary.
    """
    cleaned: dict[str, Any] = {k: v for k, v in payload.items() if k not in _META_KEYS}
    prov = cleaned.get("provenance")
    if isinstance(prov, dict) and "built_at" in prov:
        cleaned["provenance"] = {k: v for k, v in prov.items() if k != "built_at"}
    return cleaned


def content_hash(payload: dict[str, Any]) -> str:
    """SHA-256 over canonical JSON of the document content, excluding volatile meta.

    Canonical form: ``json.dumps(obj, sort_keys=True, separators=(',',':'),
    ensure_ascii=False)``. Returns ``"sha256:<hex>"``. Stable: identical content
    yields an identical hash (the basis for change detection / skip-unchanged).
    """
    cleaned = _strip_volatile(payload)
    canonical = json.dumps(cleaned, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def hash_bytes(data: bytes) -> str:
    """SHA-256 of raw bytes (used for the immutable raw artifacts), ``"sha256:<hex>"``."""
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def stamp(meta: Meta, payload: dict[str, Any], *, stage_time_key: str) -> Meta:
    """Finalize *meta*: set the stage timestamp, then the content hash.

    The hash is computed *after* the data is final and excludes timestamps/hash/
    lineage so re-running unchanged input yields the same hash. The supplied
    *payload* should be the full document dict (its ``meta``/``_meta`` is ignored
    for hashing via the volatile-key exclusion).
    """
    meta.timestamps[stage_time_key] = now_utc_z()
    meta.content_hash = content_hash(payload)
    return meta


def lineage_ref(meta: Meta) -> LineageRef:
    """Build a ``LineageRef`` pointing at the document described by *meta*.

    The ``created_at`` is taken from the producing stage's own timestamp when
    available, falling back to the earliest recorded timestamp.
    """
    created_at = _stage_created_at(meta)
    return LineageRef(
        stage=meta.stage,
        doc_id=meta.doc_id,
        content_hash=meta.content_hash or "",
        created_at=created_at,
        producer=meta.producer,
        entity_id=meta.entity_id,
    )


_STAGE_TIME_KEY = {
    "raw": "ingested_at",
    "parsed": "parsed_at",
    "consolidated": "consolidated_at",
    "derived": "derived_at",
    "presented": "presented_at",
}


def _stage_created_at(meta: Meta) -> str:
    key = _STAGE_TIME_KEY.get(meta.stage)
    if key and key in meta.timestamps:
        return meta.timestamps[key]
    if meta.timestamps:
        return next(iter(meta.timestamps.values()))
    return now_utc_z()
