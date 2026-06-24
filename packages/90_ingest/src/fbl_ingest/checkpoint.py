"""Persistent, resumable checkpoint for the ``sucheFirma`` prefix-walk (§15a.1).

A multi-hour grind that crashes or is killed (e.g. the Container Apps Job
``replicaTimeout``) must **resume**, not restart from scratch. :class:`BlobWalkCheckpoint`
persists the walk's frontier/progress to a single JSON blob in ``90-raw`` and restores it
on the next run.

What is persisted: the ``frontier`` (pending prefixes), the ``done`` set (completed
prefixes — so they are never re-queried), ``incomplete`` (depth-ceiling branches), the
per-Rechtsform ``counts``, and the **FNRs already seen** (keys only). The seen *values*
(full ``FirmaResult``) are deliberately NOT stored — they are large and the companies
themselves are already streamed into ``99_registry``. On load they are rebuilt as
placeholder ``FirmaResult(fnr=...)`` so that:

* dedup still works (a company found before the crash is not re-counted), and
* the post-resume ``WalkResult.found`` is a SUPERSET of everything seen so far, which keeps
  the ``mark_vanished`` reconcile safe (it never marks a pre-resume company ``deleted``).
"""

from __future__ import annotations

from typing import Any

from fbl_core.lineage import now_utc_z
from fbl_core.storage import RAW_CONTAINER, BlobStoreLike
from fbl_firmenbuch_client import FirmaResult

from .enumerate import WalkState

# Single well-known location; the grind is a singleton (run lock), so one checkpoint blob.
CHECKPOINT_PATH = "_checkpoints/sync_registry_walk.json"

# Progress checkpoint for the active-only backfill-ingest (the "second run"). Holds the set
# of FNRs whose download is fully complete, so a killed/timed-out replica resumes where it
# left off instead of re-querying every already-done company against the rate-limited API.
INGEST_CHECKPOINT_PATH = "_checkpoints/backfill_ingest.json"

# Progress checkpoint for the bulk backfill-PROCESS (raw → consolidated → derived → presented).
# Holds two FNR sets — companies fully consolidated, and companies fully presented — so a
# bounded/killed run resumes mid-stream instead of recomputing the whole universe (§15a.1).
PROCESS_CHECKPOINT_PATH = "_checkpoints/backfill_process.json"

# Written once sync_registry finishes a full seed/reconcile (walk drained or bulk done). The
# backfill-ingest auto-start guard reads it to know the grinder is done — works for the bulk
# path too, where there is no prefix-walk checkpoint to inspect.
WALK_COMPLETE_MARKER = "_checkpoints/registry_walk_complete.json"


def _to_dict(state: WalkState) -> dict[str, Any]:
    return {
        "frontier": [[rf, prefix] for rf, prefix in state.frontier],
        "done": sorted(state.done),
        "incomplete": list(state.incomplete),
        "counts_by_rechtsform": dict(state.counts_by_rechtsform),
        "seen_fnrs": sorted(state.seen),  # keys only — values rebuilt as placeholders
    }


def _from_dict(d: dict[str, Any]) -> WalkState:
    return WalkState(
        seen={fnr: FirmaResult(fnr=fnr) for fnr in d.get("seen_fnrs", [])},
        done=set(d.get("done", [])),
        incomplete=list(d.get("incomplete", [])),
        frontier=[(rf, prefix) for rf, prefix in d.get("frontier", [])],
        counts_by_rechtsform=dict(d.get("counts_by_rechtsform", {})),
    )


class BlobWalkCheckpoint:
    """A :class:`~fbl_ingest.enumerate.Checkpoint` backed by a JSON blob in ``90-raw``."""

    def __init__(
        self, blob: BlobStoreLike, *, container: str = RAW_CONTAINER, path: str = CHECKPOINT_PATH
    ) -> None:
        self._blob = blob
        self._container = container
        self._path = path

    def load(self) -> WalkState | None:
        doc = self._blob.get_json(self._container, self._path)
        return _from_dict(doc) if doc else None

    def save(self, state: WalkState) -> None:
        self._blob.put_json(self._container, self._path, _to_dict(state))

    def clear(self) -> None:
        """Drop the checkpoint after a fully-completed walk (next run starts fresh)."""
        if self._blob.exists(self._container, self._path):
            self._blob.put_json(self._container, self._path, {})


class BlobIngestCheckpoint:
    """Resumable progress checkpoint for ``run_ingest`` — a set of completed FNRs in ``90-raw``.

    Mirrors :class:`BlobWalkCheckpoint`: the long backfill persists which companies are fully
    downloaded, so a crash or replica-timeout resumes from the next company rather than
    restarting (losing at most ``save_every`` companies' worth of progress — seconds, not days).
    """

    def __init__(
        self,
        blob: BlobStoreLike,
        *,
        container: str = RAW_CONTAINER,
        path: str = INGEST_CHECKPOINT_PATH,
    ) -> None:
        self._blob = blob
        self._container = container
        self._path = path

    def load_done(self) -> set[str]:
        doc = self._blob.get_json(self._container, self._path)
        return set(doc.get("done_fnrs", [])) if doc else set()

    def save_done(self, done: set[str]) -> None:
        self._blob.put_json(
            self._container,
            self._path,
            {"done_fnrs": sorted(done), "count": len(done), "updated_at": now_utc_z()},
        )


class BlobProcessCheckpoint:
    """Resumable progress for the bulk ``backfill-process`` — two FNR sets in ``90-raw``.

    ``consolidated`` = companies whose ``50_consolidated`` doc is written; ``presented`` =
    companies fully derived + presented into ``10_presentation``. Mirrors
    :class:`BlobIngestCheckpoint`: a bounded run (per-run time budget) or a killed replica
    resumes from where it left off, losing at most ``save_every`` companies of progress.
    """

    def __init__(
        self,
        blob: BlobStoreLike,
        *,
        container: str = RAW_CONTAINER,
        path: str = PROCESS_CHECKPOINT_PATH,
    ) -> None:
        self._blob = blob
        self._container = container
        self._path = path

    def load(self) -> tuple[set[str], set[str]]:
        doc = self._blob.get_json(self._container, self._path)
        if not doc:
            return set(), set()
        return set(doc.get("consolidated_fnrs", [])), set(doc.get("presented_fnrs", []))

    def save(self, consolidated: set[str], presented: set[str]) -> None:
        self._blob.put_json(
            self._container,
            self._path,
            {
                "consolidated_fnrs": sorted(consolidated),
                "presented_fnrs": sorted(presented),
                "consolidated_count": len(consolidated),
                "presented_count": len(presented),
                "updated_at": now_utc_z(),
            },
        )
