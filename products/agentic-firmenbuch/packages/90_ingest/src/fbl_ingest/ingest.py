"""``run_ingest`` — fetch raw artifacts for the dirty set into ``90-raw`` (§8.3, §5.1).

Preserves everything the API returns, byte-for-byte and immutably: each Jahresabschluss
XML/PDF, a per-Stichtag ``_manifest.json``, the master ``auszug`` extract, AND the verbatim
API responses themselves (``sucheUrkunde``/``auszug`` under ``{fnr}/_responses/{run_id}/``;
change feeds under ``_changes/_responses/{run_id}/``). The ``urkunde`` document payload is
the one response not re-archived as an envelope — it is already byte-preserved decoded.
Idempotent: a filing whose ``doc_key`` is already recorded is skipped (no re-download).
Per-company failures dead-letter; they never crash the batch.
"""

from __future__ import annotations

import hashlib
import itertools
import time
from collections import defaultdict
from collections.abc import Callable
from typing import Protocol

from fbl_core.lineage import hash_bytes, new_doc_id, now_utc_z
from fbl_core.storage import RAW_CONTAINER, BlobStoreLike
from fbl_firmenbuch_client import (
    FirmenbuchApiError,
    RawCapturingSource,
    RegisterSource,
    UrkundeRef,
)
from fbl_registry import KnownFiling, Registry

from .models import IngestReport

PRODUCER = "ingest@1.0.0"


class IngestCheckpoint(Protocol):
    """Persists the set of fully-downloaded FNRs so a killed backfill resumes (§15a)."""

    def load_done(self) -> set[str]: ...

    def save_done(self, done: set[str]) -> None: ...


def _doc_token(doc_key: str) -> str:
    """Short, stable token derived from a document key (unique per filing version)."""
    return hashlib.sha1(doc_key.encode("utf-8")).hexdigest()[:10]


def run_ingest(
    source: RegisterSource,
    registry: Registry,
    blob: BlobStoreLike,
    *,
    run_id: str,
    fnrs: list[str],
    fetch_master: bool = True,
    include_pdf: bool = True,
    checkpoint: IngestCheckpoint | None = None,
    heartbeat: Callable[[], bool] | None = None,
    save_every: int = 200,
    workers: int = 1,
    max_seconds: float | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> IngestReport:
    """Download new Jahresabschluss artifacts for *fnrs* into ``90-raw``.

    ``include_pdf=False`` fetches only the structured XML filings (skips the large PDF
    siblings entirely — never even downloaded). ``checkpoint`` makes the run resumable:
    already-completed companies are skipped on restart, and progress is persisted every
    ``save_every`` companies. ``heartbeat`` (the run-lock renewal) is called between
    companies so a multi-day backfill can't outlive its lease; if it reports the lock was
    lost, the run stops cleanly so it never races a takeover.

    ``workers > 1`` fans the per-company work out across a thread pool (the bottleneck is
    per-request latency, so concurrency ≈ linear speed-up; the client already retries 429/5xx
    with backoff, so it self-throttles). Thread-safety: each company is a distinct FNR →
    distinct Cosmos partition + distinct blob paths, so registry/blob writes never collide;
    the shared ``httpx`` client is thread-safe; the checkpoint/done-set updates happen on the
    main thread between batches. NOTE: with ``workers > 1`` the source's raw-response capture
    must be OFF (one shared ``_raw`` buffer can't attribute interleaved responses) — the
    orchestrator builds the backfill source with ``capture_raw=False``.
    """
    report = IngestReport(run_id=run_id)
    done: set[str] = checkpoint.load_done() if checkpoint else set()
    pending = [f for f in fnrs if f not in done]
    deadline = (clock() + max_seconds) if max_seconds is not None else None

    def _account(fnr: str, err: Exception | None) -> None:
        report.companies += 1
        if err is not None:
            registry.dead_letter(fnr, str(err))
            report.failures += 1
            report.dead_letters.append(fnr)
        else:
            done.add(fnr)

    def _must_stop() -> bool:
        # Stop scheduling new work if we lost the run-lock OR hit the per-run time budget.
        # Bounding the run is what keeps the recurring schedule "never stuck": each run ends
        # cleanly with a saved checkpoint, and the next run resumes the remaining FNRs.
        if heartbeat is not None and not heartbeat():
            return True
        return deadline is not None and clock() >= deadline

    if workers > 1:
        from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

        def _work(fnr: str) -> tuple[str, Exception | None]:
            try:
                _ingest_company(
                    source, registry, blob, run_id, fnr, fetch_master, include_pdf, report
                )
                return fnr, None
            except Exception as exc:  # returned to the main thread, never crashes the pool
                return fnr, exc

        # Bounded sliding window: keep ~workers*4 companies in flight, submit a new one each
        # time one finishes. This (a) caps memory (never materialises a future per pending
        # FNR — a fresh run can have hundreds of thousands), (b) avoids head-of-line blocking
        # (a single slow/unresponsive FNR no longer stalls a whole batch — fast ones complete
        # and are replaced immediately), and (c) checkpoints per-company progress so a kill
        # mid-run loses at most ``save_every`` companies, not a whole batch.
        src = iter(pending)
        window = max(workers * 4, workers)
        since_save = 0
        stopping = False
        with ThreadPoolExecutor(max_workers=workers) as ex:
            inflight = {ex.submit(_work, f): f for f in itertools.islice(src, window)}
            while inflight:
                finished, _ = wait(set(inflight), return_when=FIRST_COMPLETED)
                for fut in finished:
                    del inflight[fut]
                    fnr, err = fut.result()
                    _account(fnr, err)
                    since_save += 1
                if checkpoint is not None and since_save >= save_every:
                    checkpoint.save_done(done)  # durable → resumable
                    since_save = 0
                if not stopping and _must_stop():
                    stopping = True  # drain in-flight, but submit no more
                if not stopping:
                    for f in itertools.islice(src, len(finished)):
                        inflight[ex.submit(_work, f)] = f
        if checkpoint is not None:
            checkpoint.save_done(done)
        return report

    # --- sequential (workers == 1) ---
    since_save = 0
    for fnr in pending:
        try:
            _ingest_company(source, registry, blob, run_id, fnr, fetch_master, include_pdf, report)
            _account(fnr, None)
        except Exception as exc:
            _account(fnr, exc)
        since_save += 1
        if checkpoint is not None and since_save >= save_every:
            checkpoint.save_done(done)
            since_save = 0
        if _must_stop():
            break  # lost the run lock, or hit the per-run time budget — stop cleanly
    if checkpoint is not None:
        checkpoint.save_done(done)
    return report


def _ingest_company(
    source: RegisterSource,
    registry: Registry,
    blob: BlobStoreLike,
    run_id: str,
    fnr: str,
    fetch_master: bool,
    include_pdf: bool,
    report: IngestReport,
) -> None:
    # Clear any responses captured before this company (seeding/prior company) so the
    # archive attributes only this company's verbatim responses to it (§5.1).
    if isinstance(source, RawCapturingSource):
        source.drain_raw()

    # XML-only mode: keep just the structured XML filings — the large PDF siblings are
    # filtered out here, before download, so they cost neither an API call nor storage.
    refs = [
        r for r in source.suche_urkunde(fnr) if r.is_jahresabschluss and (include_pdf or r.is_xml)
    ]
    by_stichtag: dict[str, list[UrkundeRef]] = defaultdict(list)
    for ref in refs:
        by_stichtag[ref.stichtag or "unknown"].append(ref)

    for stichtag, group in by_stichtag.items():
        artifacts: list[dict[str, object]] = []
        for ref in group:
            if registry.has_filing(fnr, ref.key):
                report.filings_skipped += 1
                continue
            content = source.urkunde(ref.key)
            ext = (content.dateiendung or "bin").lower()
            # Disambiguate by doc_key: a company can have multiple documents for the
            # same Stichtag + extension (resubmissions, §15b-11) — without this they
            # would collide on one immutable raw path.
            token = _doc_token(ref.key)
            filename = f"{fnr}_{stichtag}_{token}_jb.{ext}"
            blob_path = blob.put_raw(fnr, stichtag, filename, content.content)
            digest = hash_bytes(content.content)
            artifacts.append(
                {
                    "blob_path": blob_path,
                    "doc_key": ref.key,
                    "dokumentart": {"code": ref.dokumentart_code, "text": ref.dokumentart_text},
                    "content_type": content.content_type,
                    "format": content.format,
                    "dateiendung": ext,
                    "gkl": ref.gkl,
                    "stichtag": stichtag,
                    "eingereicht": ref.eingereicht,
                    "bytes": len(content.content),
                    "content_hash": digest,
                }
            )
            registry.record_filing(
                fnr,
                KnownFiling(
                    stichtag=ref.stichtag,
                    doc_key=ref.key,
                    content_hash=digest,
                    format=content.format,
                    dateiendung=ext,
                    downloaded=True,
                ),
            )
            if ext == "pdf":
                report.pdfs_downloaded += 1
            else:
                report.filings_downloaded += 1

        if artifacts:
            _write_manifest(blob, run_id, fnr, stichtag, artifacts)

    if fetch_master:
        _archive_master(source, blob, run_id, fnr)

    # §5.1 lossless: every verbatim API response for this company (sucheUrkunde +
    # auszug) is archived byte-for-byte. The urkunde document payload is excluded from
    # capture — it is already preserved decoded above — so this never doubles storage.
    report.responses_archived += archive_raw_responses(source, blob, run_id=run_id, prefix=fnr)


def _write_manifest(
    blob: BlobStoreLike,
    run_id: str,
    fnr: str,
    stichtag: str,
    artifacts: list[dict[str, object]],
) -> None:
    """Write/merge the per-Stichtag raw manifest (Stage 0 sample shape)."""
    path = f"{fnr}/{stichtag}/_manifest.json"
    existing = blob.get_json(RAW_CONTAINER, path)
    merged: dict[str, dict[str, object]] = {}
    if existing is not None:
        for art in existing.get("artifacts", []):
            merged[str(art.get("doc_key"))] = art
    for art in artifacts:
        merged[str(art["doc_key"])] = art
    manifest = {
        "entity_id": f"{fnr}/{stichtag}",
        "artifacts": list(merged.values()),
        "_meta": {
            "doc_id": new_doc_id(),
            "entity_id": f"{fnr}/{stichtag}",
            "stage": "raw",
            "producer": PRODUCER,
            "source": "justizonline_firmenbuch_hvd",
            "source_endpoint": "urkunde",
            "license": "CC-BY-4.0",
            "run_id": run_id,
            "timestamps": {"ingested_at": now_utc_z()},
            "lineage": [],
        },
    }
    blob.put_json(RAW_CONTAINER, path, manifest)


def archive_raw_responses(
    source: RegisterSource,
    blob: BlobStoreLike,
    *,
    run_id: str,
    prefix: str,
) -> int:
    """Write each captured verbatim response under ``90-raw/{prefix}/_responses/{run_id}/`` (§5.1).

    Returns the number of responses archived. No-op (returns 0) when the source does
    not retain raw bytes (test doubles), so the pipeline is identical in production
    and in tests. ``prefix`` is the company FNR for filing ingestion, or a synthetic
    key (e.g. ``_changes``) for the non-company change-feed responses.
    """
    if not isinstance(source, RawCapturingSource):
        return 0
    count = 0
    for idx, raw in enumerate(source.drain_raw()):
        path = f"{prefix}/_responses/{run_id}/{idx:02d}_{raw.endpoint}.xml"
        blob.put_bytes(RAW_CONTAINER, path, raw.content)
        count += 1
    return count


def _archive_master(source: RegisterSource, blob: BlobStoreLike, run_id: str, fnr: str) -> None:
    """Archive the master ``auszug`` extract verbatim under ``90-raw/{fnr}/master/`` (§5.1)."""
    try:
        auszug = source.auszug(fnr)
    except FirmenbuchApiError:
        return  # master is best-effort; absence never fails ingestion
    today = now_utc_z()[:10]
    blob.put_json(
        RAW_CONTAINER, f"{fnr}/master/auszug_{today}.json", auszug.model_dump(mode="json")
    )
