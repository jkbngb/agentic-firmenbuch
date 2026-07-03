"""OeNB financial-institution list parser tests (ROADMAP P2 / issue #15)."""

from __future__ import annotations

from pathlib import Path

from fbl_core.directories import parse_oenb_list

_FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "products"
    / "agentic-firmenbuch"
    / "tests"
    / "fixtures"
    / "directories"
    / "oenb_mfi_sample.csv"
)


def test_parses_oenb_mfi_with_firmenbuchnummer() -> None:
    result = parse_oenb_list(_FIXTURE.read_bytes(), source="oenb_mfi")
    assert result.stand == "31.05.2026"  # the Stand date off line 0
    assert result.source == "oenb_mfi"

    by_name = {r.name: r for r in result.records}
    # The change block (Neuzugang/Abgang) before the real header is skipped, not parsed as data.
    assert "Poso Bank AG" not in by_name and "Posojilnica Bank eGen" not in by_name

    # The two banks the NAME heuristic provably missed are now flagged via the FB-Nr join.
    oberbank = by_name["Oberbank AG"]
    assert oberbank.fnr == "79063w" and oberbank.kind == "bank"  # E-VGR 1220A → bank
    assert oberbank.lei == "RRUN0TCQ1K2JDV7MXO75"
    assert oberbank.e_vgr == "1220A"
    assert oberbank.sector_label == "MFIs - CRD - MiRe-pflichtig"  # official ESVG legend
    bawag = by_name["BAWAG P.S.K. Bank fuer Arbeit und Wirtschaft AG"]
    assert bawag.fnr == "205340x" and bawag.lei == "529900ICA8XQYGIKR372"


def test_extracts_every_column_verbatim() -> None:
    # "alles extrahieren" — every CSV column is kept in `fields`, not just the typed ones.
    rec = next(
        r
        for r in parse_oenb_list(_FIXTURE.read_bytes(), source="oenb_mfi").records
        if r.name == "Oberbank AG"
    )
    assert rec.fields["RIAD-Code"] == "AT0000000548014"
    assert rec.fields["OeNB-IdentNr"] == "54801"
    assert rec.fields["Institutsart"] == "C"
    assert rec.fields["MR-Pflichtig"] == "Yes"
    assert set(rec.fields) >= {"Nr.", "Institut", "FB-Nr", "E-VGR", "LEI"}  # all headers present


def test_entity_without_firmenbuch_entry_kept_but_unjoinable() -> None:
    # The OeNB itself has no FB-Nr → fnr None (kept for completeness, just not joinable).
    rec = next(
        r
        for r in parse_oenb_list(_FIXTURE.read_bytes(), source="oenb_mfi").records
        if r.name == "Oesterreichische Nationalbank"
    )
    assert rec.fnr is None and rec.lei is None


def test_robust_to_shifted_header_and_fewer_columns_nmfi_shape() -> None:
    # The real risk the OeNB format poses: the change block shifts the header line, and NMFI has
    # FEWER columns than MFI (no Institutsart/MR-*). Header-by-name must handle both without
    # breaking. Here the header sits on line 3 (NMFI-style) with the 7-column NMFI layout.
    data = (
        "31.05.2026\n"
        ";Keine Veraenderungen zum Vormonat\n"
        "\n"
        "Nr.;Institut;RIAD-Code;OeNB-IdentNr;FB-Nr;E-VGR;LEI\n"
        "1;Valida Plus AG;AT0000055874927;5587492;224730k;1250B;529900NXPRVKL8WT6O60\n"
    ).encode("latin-1")
    result = parse_oenb_list(data, source="oenb_nmfi")
    assert len(result.records) == 1
    rec = result.records[0]
    assert rec.fnr == "224730k" and rec.name == "Valida Plus AG"
    assert rec.institutsart is None  # column absent in NMFI → None, not a crash
    assert rec.fields["E-VGR"] == "1250B"  # still captured verbatim
    # E-VGR drives the kind: 1250B = Mitarbeitervorsorgekasse, NOT a bank (the whole point).
    assert rec.kind == "vorsorgekasse"
    assert rec.sector_label == "Mitarbeitervorsorgekassen"


def test_change_only_file_yields_no_records() -> None:
    # An NMFI-style file with only a date + "Keine Veränderungen" (no data header) → empty list.
    data = "31.05.2026\n;Keine Veraenderungen zum Vormonat\n".encode("latin-1")
    result = parse_oenb_list(data, source="oenb_nmfi")
    assert result.stand == "31.05.2026" and result.records == []
