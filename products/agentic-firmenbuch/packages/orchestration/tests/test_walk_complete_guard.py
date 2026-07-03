"""The auto-start guard: backfill-ingest defers until the registry grind has finished."""

from __future__ import annotations

import pytest

from fbl_core.storage import RAW_CONTAINER, InMemoryBlobStore
from fbl_ingest import CHECKPOINT_PATH, WALK_COMPLETE_MARKER
from fbl_orchestration.orchestrator import registry_walk_complete


def test_no_checkpoint_means_grind_has_not_run() -> None:
    assert registry_walk_complete(InMemoryBlobStore()) is False


def test_completion_marker_means_grind_done() -> None:
    blob = InMemoryBlobStore()
    blob.put_json(RAW_CONTAINER, WALK_COMPLETE_MARKER, {"completed_at": "2026-06-19T00:00:00Z"})
    assert registry_walk_complete(blob) is True


def test_pending_frontier_means_grind_still_running() -> None:
    blob = InMemoryBlobStore()
    blob.put_json(RAW_CONTAINER, CHECKPOINT_PATH, {"frontier": [["GES", "a"]], "done": []})
    assert registry_walk_complete(blob) is False


def test_cleared_checkpoint_means_grind_complete() -> None:
    blob = InMemoryBlobStore()
    blob.put_json(RAW_CONTAINER, CHECKPOINT_PATH, {})  # clear() empties it on a full walk
    assert registry_walk_complete(blob) is True


def test_sync_overrides_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from fbl_ingest import DEFAULT_RECHTSFORMEN
    from fbl_orchestration.orchestrator import _sync_overrides

    monkeypatch.delenv("SYNC_RECHTSFORMEN", raising=False)
    monkeypatch.delenv("SYNC_MARK_VANISHED", raising=False)
    assert _sync_overrides() == (DEFAULT_RECHTSFORMEN, True)  # default = full reconcile
    monkeypatch.setenv("SYNC_RECHTSFORMEN", "AKT, EGE")
    monkeypatch.setenv("SYNC_MARK_VANISHED", "false")
    assert _sync_overrides() == (("AKT", "EGE"), False)  # one-off top-up, no deletes
