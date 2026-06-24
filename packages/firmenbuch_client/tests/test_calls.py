"""VCR-style tests for all six RegisterSource calls (§8.2 DoD)."""

from __future__ import annotations

from datetime import date

from helpers import fixed_response, make_client


def test_suche_firma_parses_results() -> None:
    client = make_client(fixed_response("sucheFirma"))
    results = client.suche_firma("Aetos Maschinenbau*", rechtsform="GES")
    assert len(results) == 1
    first = results[0]
    assert first.fnr == "433826f"
    assert first.name == "Aetos Maschinenbau GmbH"
    assert first.rechtsform_code == "GES"
    assert first.gericht_text is not None


def test_suche_urkunde_parses_documents() -> None:
    client = make_client(fixed_response("sucheUrkunde"))
    docs = client.suche_urkunde("030435h")
    assert len(docs) == 4
    assert all(d.is_jahresabschluss for d in docs)
    assert any(d.is_xml for d in docs)
    assert any(not d.is_xml for d in docs)
    # FNR inside the response ("30435 h") is normalized
    assert {d.fnr for d in docs} == {"030435h"}
    xml_doc = next(d for d in docs if d.is_xml)
    assert xml_doc.stichtag is not None
    assert xml_doc.key.endswith("_XML")


def test_urkunde_decodes_and_detects_format() -> None:
    client = make_client(fixed_response("urkunde"))
    content = client.urkunde("030435_8180501900xxx_000___000_30_9999999_XML")
    assert content.content.startswith(b"<?xml")
    assert content.format == "legacy_finanzonline"  # detected from the decoded bytes
    assert content.fnr == "030435h"
    assert content.oeffentlich is True
    # the decoded XML is the real filing bytes -> parseable downstream
    assert b"HGB_224_2" in content.content


def test_auszug_parses_master_data() -> None:
    client = make_client(fixed_response("auszug_v2"))
    a = client.auszug("030435h", stichtag=date(2026, 6, 16))
    assert a.fnr == "030435h"
    assert a.name == "Westerthaler GmbH"
    assert a.city == "Innsbruck" and a.postal_code == "6020"
    assert a.rechtsform_code == "GES"
    # Firmenbuchgericht (HG) is carried, not dropped (§5.1).
    assert a.court_code == "818" and a.court_text == "Landesgericht Innsbruck"
    master = a.to_master_data()
    assert master.court is not None and master.court.name == "Landesgericht Innsbruck"
    assert a.geschaeftszweig is not None
    assert len(a.persons) == 2
    # birth YEAR only — day/month discarded at the client boundary (§8.7)
    p = a.persons[0]
    assert p.birth_year == 1970
    assert p.first_name == "Max"
    assert len(a.events) >= 1
    assert a.events[0].date is not None


# --- master-path completeness audit (every auszug field carried or allowlisted) -------

# Raw auszug elements/attributes that are INTENTIONALLY not carried into MasterData, each
# with its justification. Anything in the response NOT here and NOT captured fails the
# audit below — the guard that caught the dropped court/role/euid.
_AUSZUG_ALLOWLIST = {
    "NAME_FORMATIERT": "redundant — equals VORNAME + NACHNAME",
    "ZUSTELLBAR": "internal mail-deliverability flag",
    "DATVON": "function valid-from date; the role itself IS carried",
    "ZNR": "EUID sub-number; the EUID value IS carried",
    # query-echo / integrity metadata on the response envelope:
    "ABFRAGEZEITPUNKT": "query echo (request timestamp)",
    "PRUEFSUMME": "response checksum (integrity, not company data)",
    "STICHTAG": "query echo (requested Stichtag)",
    "UMFANG": "query echo (requested scope)",
    "AUFRECHT": "per-block 'currently-in-force' flag",
    "PNR": "internal person-number join key (used to attach roles, not data itself)",
}
# Elements whose data reaches AuszugKurz/MasterData (directly or via a code/text pair).
_AUSZUG_CARRIED = {
    "BEZEICHNUNG",
    "STRASSE",
    "HAUSNUMMER",
    "PLZ",
    "ORT",
    "STAAT",
    "SITZ",
    "EUID",
    "VORNAME",
    "NACHNAME",
    "GEBURTSDATUM",
    "VNR",
    "VOLLZUGSDATUM",
    "AZ",
    "ANTRAGSTEXT",
    "EINGELANGTAM",
    "CODE",
    "TEXT",
    "FKEN",
    "FKENTEXT",
    "FNR",
}


def test_auszug_master_path_accounts_for_every_field() -> None:
    # §5.1 master path: every data-bearing element/attribute in the raw auszug must be
    # captured into MasterData OR be on the documented allowlist — zero silently dropped.
    from pathlib import Path

    from lxml import etree

    from fbl_parse.xml_common import local_name

    raw = (Path(__file__).resolve().parent / "recorded" / "auszug_v2.xml").read_bytes()
    root = etree.fromstring(raw)
    els = [e for e in root.iter() if isinstance(e.tag, str)]
    names = {local_name(e) for e in els if (e.text or "").strip()}
    names |= {k.split("}")[-1] for e in els for k in e.attrib}

    accounted = _AUSZUG_CARRIED | set(_AUSZUG_ALLOWLIST)
    unaccounted = sorted(n for n in names if n not in accounted)
    assert not unaccounted, f"auszug fields neither captured nor allowlisted: {unaccounted}"

    # And prove "carried" is real, not hand-waved (court, euid, person role all populate).
    client = make_client(fixed_response("auszug_v2"))
    a = client.auszug("030435h")
    assert a.court_text == "Landesgericht Innsbruck"
    assert a.euid and a.euid.startswith("ATBRA")
    assert a.persons and a.persons[0].function_text  # role joined from FUN
    assert a.persons[0].vertretung == "Einzelvertretung"  # Vertretungsart carried (VART)
    m = a.to_master_data()
    assert m.persons[0].role_label == a.persons[0].function_text  # → reaches MasterData
    assert m.persons[0].vertretung == "Einzelvertretung"


def test_raw_responses_captured_for_archival() -> None:
    # §5.1: metadata responses are retained verbatim for archival; the urkunde
    # document payload is excluded (already byte-preserved decoded by ingest).
    client = make_client(fixed_response("auszug_v2"))
    client.auszug("030435h", stichtag=date(2026, 6, 16))
    captured = client.drain_raw()
    assert len(captured) == 1
    assert captured[0].endpoint == "auszug_v2"
    assert b"AUSZUG" in captured[0].content.upper()
    assert client.drain_raw() == []  # drain clears the buffer


def test_urkunde_response_not_captured() -> None:
    client = make_client(fixed_response("urkunde"))
    client.urkunde("030435_8180501900xxx_000___000_30_9999999_XML")
    assert client.drain_raw() == []


def test_veraenderungen_urkunden_parses_changes() -> None:
    client = make_client(fixed_response("veraenderungenUrkunden"))
    changes = client.veraenderungen_urkunden(date(2026, 6, 16), date(2026, 6, 16))
    assert len(changes) == 3
    assert changes[0].key
    assert changes[0].fnr is not None  # derived from the KEY prefix


def test_veraenderungen_firma_classifies_kinds() -> None:
    client = make_client(fixed_response("veraenderungenFirma"))
    changes = client.veraenderungen_firma(date(2026, 6, 16), date(2026, 6, 16), rechtsform="GES")
    assert changes
    kinds = {c.kind for c in changes}
    # the trimmed fixture covers the meaningful register-change kinds
    assert "Neueintragung" in kinds
    assert "Löschung" in kinds
    assert all(c.fnr for c in changes)
