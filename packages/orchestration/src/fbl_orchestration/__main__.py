"""Container Apps Job entrypoint: ``fbl-pipeline --mode {sync-registry|...|daily}``.

Builds the production dependencies (Azure Blob/Cosmos + the HVD client) from settings
and runs one pass. Stages are importable/testable standalone; this only wires them.
"""

from __future__ import annotations

import argparse
import sys

from fbl_core.config import get_settings
from fbl_core.logging import get_logger
from fbl_core.storage import BlobStore, CosmosStore
from fbl_firmenbuch_client import JustizOnlineClient
from fbl_registry import Registry

from .orchestrator import MODES, make_run_id, run
from .pipeline import PipelineContext

log = get_logger("orchestration")


def _build_context(
    settings: object,
    *,
    capture_raw: bool = True,
    max_retries: int = 4,
    need_api: bool = True,
) -> PipelineContext:
    s = settings  # pydantic Settings
    if s.cosmos_endpoint is None or s.blob_account_url is None:  # type: ignore[attr-defined]
        raise SystemExit("COSMOS_ENDPOINT and BLOB_ACCOUNT_URL must be set")
    cosmos = CosmosStore(s.cosmos_endpoint, s.cosmos_database)  # type: ignore[attr-defined]
    blob = BlobStore(s.blob_account_url)  # type: ignore[attr-defined]
    # The OeNB directory sync (mode=directories) touches only Blob + Cosmos + the public OeNB
    # CSVs — never the HVD API — so it needs no FIRMENBUCH_API_KEY. We still build a client to
    # satisfy the context type; it is simply never called in that mode (need_api=False).
    if need_api and s.firmenbuch_api_key is None:  # type: ignore[attr-defined]
        raise SystemExit("FIRMENBUCH_API_KEY must be set")
    # capture_raw must be OFF for the parallel backfill — one shared _raw buffer can't
    # attribute interleaved responses across concurrent companies (see run_ingest).
    source = JustizOnlineClient(
        s.justizonline_api_url,  # type: ignore[attr-defined]
        s.firmenbuch_api_key,  # type: ignore[attr-defined]
        capture_raw=capture_raw,
        max_retries=max_retries,
    )
    return PipelineContext(
        blob=blob,
        cosmos=cosmos,
        source=source,
        registry=Registry(cosmos),
        expose_personal_data=s.expose_personal_data,  # type: ignore[attr-defined]
        growth_horizons=list(s.growth_horizons),  # type: ignore[attr-defined]
        delta_lookback_days=s.delta_lookback_days,  # type: ignore[attr-defined]
    )


def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fbl-pipeline")
    parser.add_argument("--mode", required=True, choices=MODES)
    args = parser.parse_args(argv)

    settings = get_settings()
    run_id = make_run_id(args.mode)
    log.info("pipeline start", extra={"context": {"run_id": run_id, "mode": args.mode}})
    # Backfill: fewer retries so a single unresponsive FNR fails fast. The HTTP timeout is now
    # granular (short connect = fast-fail on a dead host; generous read = patience for a large
    # urkunde the server is slow to start streaming), so we no longer cripple it with a flat
    # 20 s that dead-lettered the biggest bank/insurer filings (ROADMAP P1.2). The registry
    # walk keeps the generous defaults (its searches can legitimately be slow).
    # Both fan-out ingest modes run workers>1, so capture_raw MUST be off (one shared _raw
    # buffer can't attribute interleaved responses across concurrent companies — see run_ingest),
    # and fewer retries let a single unresponsive FNR fail fast under the per-run time budget.
    fanout_ingest = args.mode in ("backfill-ingest", "ingest-fi")
    ctx = _build_context(
        settings,
        capture_raw=not fanout_ingest,
        max_retries=2 if fanout_ingest else 4,
        need_api=args.mode != "directories",
    )
    code = run(args.mode, ctx, run_id=run_id)
    log.info("pipeline done", extra={"context": {"run_id": run_id, "exit": code}})
    return code


if __name__ == "__main__":  # pragma: no cover
    sys.exit(cli())
