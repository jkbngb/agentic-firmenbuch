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
    timeout: float = 60.0,
    max_retries: int = 4,
) -> PipelineContext:
    s = settings  # pydantic Settings
    if s.cosmos_endpoint is None or s.blob_account_url is None:  # type: ignore[attr-defined]
        raise SystemExit("COSMOS_ENDPOINT and BLOB_ACCOUNT_URL must be set")
    cosmos = CosmosStore(s.cosmos_endpoint, s.cosmos_database)  # type: ignore[attr-defined]
    blob = BlobStore(s.blob_account_url)  # type: ignore[attr-defined]
    if s.firmenbuch_api_key is None:  # type: ignore[attr-defined]
        raise SystemExit("FIRMENBUCH_API_KEY must be set")
    # capture_raw must be OFF for the parallel backfill — one shared _raw buffer can't
    # attribute interleaved responses across concurrent companies (see run_ingest).
    source = JustizOnlineClient(
        s.justizonline_api_url,  # type: ignore[attr-defined]
        s.firmenbuch_api_key,  # type: ignore[attr-defined]
        capture_raw=capture_raw,
        timeout=timeout,
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
    # Backfill: tighter HTTP timeout + fewer retries so a single unresponsive FNR fails fast
    # (~3×20s) instead of stalling the worker for minutes. The registry walk keeps the
    # generous defaults (its searches can legitimately be slow).
    backfill = args.mode == "backfill-ingest"
    ctx = _build_context(
        settings,
        capture_raw=not backfill,
        timeout=20.0 if backfill else 60.0,
        max_retries=2 if backfill else 4,
    )
    code = run(args.mode, ctx, run_id=run_id)
    log.info("pipeline done", extra={"context": {"run_id": run_id, "exit": code}})
    return code


if __name__ == "__main__":  # pragma: no cover
    sys.exit(cli())
