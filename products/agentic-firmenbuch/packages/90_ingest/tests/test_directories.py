"""Directory sync: OeNB banks + EIOPA/GLEIF insurers, with snapshot fallback + alerts (#15, #17)."""

from __future__ import annotations

from collections.abc import Iterable
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
# Low gates so the trimmed fixtures don't trip the production row-count sanity check.
_GATE = {"oenb_mfi": 1, "oenb_nmfi": 1, "eiopa_at": 1}


def _fetch(url: str) -> bytes:
    return _MFI.read_bytes() if "/MFI.csv" in url else _NMFI


def test_sync_archives_raw_and_flags_active_with_kind() -> None:
    blob, cosmos = InMemoryBlobStore(), InMemoryCosmosStore()
    report = sync_directories(blob, cosmos, fetch=_fetch, today="2026-06-28", min_rows=_GATE)

    assert blob.get_bytes(RAW_CONTAINER, "_directories/oenb_mfi/2026-06-28.csv") is not None
    assert blob.get_bytes(RAW_CONTAINER, "_directories/oenb_nmfi/2026-06-28.csv") is not None

    directory = load_fi_directory(cosmos)
    assert directory["79063w"] == "bank"  # Oberbank (MISSED by the name heuristic) → now flagged
    assert directory["205340x"] == "bank"  # BAWAG
    assert directory["224730k"] == "vorsorgekasse"  # NMFI Vorsorgekasse, not a "bank"
    assert report["active"] == len(directory) and report["new"] == report["active"]
    assert report["errors"] == [] and report["degraded"] == []

    stored = cosmos.get(DIRECTORIES_CONTAINER, "79063w")
    assert stored is not None and stored["first_seen"] == "2026-06-28" and stored["active"] is True


def test_genuine_delisting_deactivates_but_keeps_history() -> None:
    blob, cosmos = InMemoryBlobStore(), InMemoryCosmosStore()
    sync_directories(blob, cosmos, fetch=_fetch, today="2026-06-28", min_rows=_GATE)
    assert "224730k" in load_fi_directory(cosmos)

    # Next month NMFI returns a VALID list (a different institution) — Valida is genuinely gone.
    nmfi_2 = (
        b"30.06.2026\n;Aenderungen\n\n"
        b"Nr.;Institut;RIAD-Code;OeNB-IdentNr;FB-Nr;E-VGR;LEI\n"
        b"1;Andere Vorsorge AG;AT0000099999999;9999999;999999z;1250B;LEI999\n"
    )

    def _fetch_2(url: str) -> bytes:
        return _MFI.read_bytes() if "/MFI.csv" in url else nmfi_2

    report = sync_directories(blob, cosmos, fetch=_fetch_2, today="2026-06-30", min_rows=_GATE)
    assert report["deactivated"] == 1
    assert "224730k" not in load_fi_directory(cosmos)  # delisted
    assert "999999z" in load_fi_directory(cosmos)  # replacement added
    rec = cosmos.get(DIRECTORIES_CONTAINER, "224730k")
    assert rec is not None and rec["active"] is False and rec["deactivated_at"] == "2026-06-30"


def test_bad_fetch_falls_back_to_snapshot_and_alerts() -> None:
    blob, cosmos = InMemoryBlobStore(), InMemoryCosmosStore()
    sync_directories(blob, cosmos, fetch=_fetch, today="2026-06-28", min_rows=_GATE)

    alerts: list[tuple[str, str]] = []

    def _fetch_broken(url: str) -> bytes:
        if "/MFI.csv" in url:
            return _MFI.read_bytes()
        raise RuntimeError("OeNB 503")  # NMFI fetch is down this month

    report = sync_directories(
        blob,
        cosmos,
        fetch=_fetch_broken,
        today="2026-07-31",
        min_rows=_GATE,
        alert=lambda s, b: alerts.append((s, b)),
    )
    # The broken source degraded to the last good snapshot — Valida stays flagged, NOT wiped.
    assert "224730k" in load_fi_directory(cosmos)
    assert "oenb_nmfi" in report["degraded"]  # type: ignore[operator]
    assert report["deactivated"] == 0
    assert any("DEGRADED" in subj for subj, _ in alerts)


def test_mass_deactivation_guard_refuses_and_alerts() -> None:
    # Seed many MFI banks; then a fetch that (wrongly) returns almost none must NOT wipe them.
    rows = "".join(
        f"{i};Bank {i} AG;RIAD{i};ID{i};fn{i:04d}x;1220A;Bank;J;;LEI{i}\n" for i in range(60)
    )
    full = (
        "01.06.2026\n;x\n\nNr.;Institut;RIAD-Code;OeNB-IdentNr;FB-Nr;E-VGR;Institutsart;"
        "MR-Pflichtig;MR-Ausnahme;LEI\n" + rows
    ).encode("latin-1")
    one = (
        "01.07.2026\n;x\n\nNr.;Institut;RIAD-Code;OeNB-IdentNr;FB-Nr;E-VGR;Institutsart;"
        "MR-Pflichtig;MR-Ausnahme;LEI\n1;Bank 0 AG;RIAD0;ID0;fn0000x;1220A;Bank;J;;LEI0\n"
    ).encode("latin-1")

    blob, cosmos = InMemoryBlobStore(), InMemoryCosmosStore()
    sync_directories(
        blob,
        cosmos,
        fetch=lambda u: full,
        sources=(("oenb_mfi", "u"),),
        today="2026-06-01",
        min_rows=_GATE,
    )
    assert len(load_fi_directory(cosmos)) == 60

    alerts: list[tuple[str, str]] = []
    report = sync_directories(
        blob,
        cosmos,
        fetch=lambda u: one,
        sources=(("oenb_mfi", "u"),),
        today="2026-07-01",
        min_rows=_GATE,
        alert=lambda s, b: alerts.append((s, b)),
    )
    # 59/60 would be deactivated (>10%) → refused; the 60 stay active, an alert fires.
    assert report["deactivated"] == 0
    assert len(load_fi_directory(cosmos)) == 60
    assert report["errors"] and any("FAILED" in subj for subj, _ in alerts)


def test_insurers_via_eiopa_and_gleif() -> None:
    blob, cosmos = InMemoryBlobStore(), InMemoryCosmosStore()
    eiopa_csv = (
        '﻿"International Name";"Home Country";"LEI";"Official name of the entity";'
        '"Identification code"\n'
        '"UNIQA";"AT";"529900OOW8ELHOXWZP82";"UNIQA Insurance Group AG";"X1"\n'
        '"VIG";"AT";"549300JCRU23I1THU176";"Vienna Insurance Group AG";"X2"\n'
        '"Foreign";"DE";"DELEI";"Ein deutscher Versicherer";"X3"\n'  # not AT → excluded
    ).encode()

    def _gleif(leis: Iterable[str]) -> dict[str, str]:
        table = {"529900OOW8ELHOXWZP82": "92933t", "549300JCRU23I1THU176": "75687f"}
        return {lei: table[lei] for lei in leis if lei in table}

    report = sync_directories(
        blob,
        cosmos,
        fetch=_fetch,
        eiopa_fetch=lambda: eiopa_csv,
        gleif=_gleif,
        today="2026-06-28",
        min_rows=_GATE,
    )
    directory = load_fi_directory(cosmos)
    assert directory["92933t"] == "insurer"  # UNIQA, FN from the GLEIF bridge
    assert directory["75687f"] == "insurer"  # VIG
    assert report["insurers"] == 2
    assert isinstance(report["banks"], int) and report["banks"] >= 2
    assert blob.get_bytes(RAW_CONTAINER, "_directories/eiopa_at/2026-06-28.csv") is not None
    assert report["errors"] == []
