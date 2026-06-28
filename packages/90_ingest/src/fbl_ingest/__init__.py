"""fbl_ingest — Layer ``90_raw``: enumeration, change-feed delta, raw ingestion.

``sync_registry`` seeds/reconciles the universe (preferring the data.gv.at bulk dataset,
prefix-walk as fallback); ``detect_changes`` turns the change feeds into a dirty set;
``run_ingest`` downloads new raw artifacts into ``90-raw``. The company catalog itself
lives in the ``fbl_registry`` package (layer 99_registry).
"""

from __future__ import annotations

from .bulk import BulkCompany, BulkSource, IterableBulkSource
from .checkpoint import (
    CHECKPOINT_PATH,
    INGEST_FI_CHECKPOINT_PATH,
    WALK_COMPLETE_MARKER,
    BlobIngestCheckpoint,
    BlobProcessCheckpoint,
    BlobWalkCheckpoint,
)
from .delta import detect_changes
from .directories import (
    DIRECTORIES_CONTAINER,
    OENB_SOURCES,
    load_fi_directory,
    sync_directories,
)
from .enumerate import (
    DEFAULT_ALPHABET,
    DEFAULT_RECHTSFORMEN,
    MAX_PREFIX_DEPTH,
    PUBLICATION_PRIORITY_RECHTSFORMEN,
    Checkpoint,
    InMemoryCheckpoint,
    WalkResult,
    WalkState,
    prefix_walk,
)
from .ingest import archive_raw_responses, run_ingest
from .models import LAYER, DeltaReport, DriftEntry, IngestReport, SyncReport
from .sync_registry import status_from_result, sync_registry

__all__ = [
    "CHECKPOINT_PATH",
    "DEFAULT_ALPHABET",
    "DEFAULT_RECHTSFORMEN",
    "DIRECTORIES_CONTAINER",
    "INGEST_FI_CHECKPOINT_PATH",
    "LAYER",
    "MAX_PREFIX_DEPTH",
    "OENB_SOURCES",
    "PUBLICATION_PRIORITY_RECHTSFORMEN",
    "WALK_COMPLETE_MARKER",
    "BlobIngestCheckpoint",
    "BlobProcessCheckpoint",
    "BlobWalkCheckpoint",
    "BulkCompany",
    "BulkSource",
    "Checkpoint",
    "DeltaReport",
    "DriftEntry",
    "InMemoryCheckpoint",
    "IngestReport",
    "IterableBulkSource",
    "SyncReport",
    "WalkResult",
    "WalkState",
    "archive_raw_responses",
    "detect_changes",
    "load_fi_directory",
    "prefix_walk",
    "run_ingest",
    "status_from_result",
    "sync_directories",
    "sync_registry",
]
