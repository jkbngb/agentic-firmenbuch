"""OeNB directory sync + reconcile tests (issue #15)."""

from __future__ import annotations

from pathlib import Path

from fbl_core.storage import RAW_CONTAINER, InMemoryBlobStore, InMemoryCosmosStore
from fbl_ingest import DIRECTORIES_CONTAINER, load_fi_directory, sync_directories

_MFI = (
    Path(__file__).resolve().parents[3]
    / "tests"
    / "fixtures"
    / "directories"
    / "oenb_mfi_sample.csv"
)
# A tiny NMFI-shaped list (a Vorsorgekasse) so both sources + the kind mapping are exercised.
_NMFI = (
    b"31.05.2026\n;Keine Veraenderungen zum Vormonat\n\n"
    b"Nr.;Institut;RIAD-Code;OeNB-IdentNr;FB-Nr;E-VGR;LEI\n"
    b"1;Valida Plus AG;AT0000055874927;5587492;224730k;1250B;529900NXPRVKL8WT6O60\n"
)


def _fetch(url: str) -> bytes:
    return _MFI.read_bytes() if "/MFI.csv" in url else _NMFI


def test_sync_archives_raw_and_flags_active_with_kind() -> None:
    blob, cosmos = InMemoryBlobStore(), InMemoryCosmosStore()
    report = sync_directories(blob, cosmos, fetch=_fetch, today="2026-06-28")

    # raw archived verbatim + dated (lossless history) for BOTH sources.
    assert blob.get_bytes(RAW_CONTAINER, "_directories/oenb_mfi/2026-06-28.csv") is not None
    assert blob.get_bytes(RAW_CONTAINER, "_directories/oenb_nmfi/2026-06-28.csv") is not None

    # the served lookup: register flag by Firmenbuchnummer, with the exact kind from E-VGR.
    directory = load_fi_directory(cosmos)
    assert directory["79063w"] == "bank"  # Oberbank (MISSED by the name heuristic) → now flagged
    assert directory["205340x"] == "bank"  # BAWAG
    assert directory["224730k"] == "vorsorgekasse"  # NMFI Vorsorgekasse, not a "bank"
    assert report["active"] == len(directory) and report["new"] == report["active"]

    stored = cosmos.get(DIRECTORIES_CONTAINER, "79063w")
    assert stored is not None and stored["first_seen"] == "2026-06-28" and stored["active"] is True


def test_reconcile_deactivates_a_delisted_institution_but_keeps_history() -> None:
    blob, cosmos = InMemoryBlobStore(), InMemoryCosmosStore()
    sync_directories(blob, cosmos, fetch=_fetch, today="2026-06-28")
    assert "224730k" in load_fi_directory(cosmos)

    # Next month the Vorsorgekasse lost its licence → NMFI returns just the header.
    def _fetch_gone(url: str) -> bytes:
        if "/MFI.csv" in url:
            return _MFI.read_bytes()
        return (
            b"30.06.2026\n;Keine Veraenderungen\n\n"
            b"Nr.;Institut;RIAD-Code;OeNB-IdentNr;FB-Nr;E-VGR;LEI\n"
        )

    report = sync_directories(blob, cosmos, fetch=_fetch_gone, today="2026-06-30")
    assert report["deactivated"] == 1
    # Dropped from the served flag, but the record is KEPT (history), just inactive.
    assert "224730k" not in load_fi_directory(cosmos)
    rec = cosmos.get(DIRECTORIES_CONTAINER, "224730k")
    assert rec is not None and rec["active"] is False and rec["deactivated_at"] == "2026-06-30"
