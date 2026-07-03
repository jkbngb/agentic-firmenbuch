"""SOAP envelope + response-helper unit tests (§8.2)."""

from __future__ import annotations

from lxml import etree

from fbl_firmenbuch_client.envelope import build_envelope, child_text, fault_string, iter_named


def test_build_envelope_wraps_body_with_namespace() -> None:
    data = build_envelope("ns://x/Req", "<fb:REQ><fb:A>1</fb:A></fb:REQ>")
    root = etree.fromstring(data)
    assert etree.QName(root).localname == "Envelope"
    assert b'xmlns:fb="ns://x/Req"' in data
    assert iter_named(root, "A")[0].text == "1"


def test_fault_string_detects_fault() -> None:
    fault = (
        '<S:Envelope xmlns:S="http://schemas.xmlsoap.org/soap/envelope/"><S:Body>'
        "<S:Fault><faultstring>Validation error</faultstring></S:Fault>"
        "</S:Body></S:Envelope>"
    )
    assert fault_string(etree.fromstring(fault.encode())) == "Validation error"


def test_fault_string_none_on_success() -> None:
    ok = '<R xmlns="ns://x"><ERGEBNIS><FNR>1</FNR></ERGEBNIS></R>'
    assert fault_string(etree.fromstring(ok.encode())) is None


def test_child_text_by_local_name() -> None:
    xml = '<R xmlns="ns://x"><ERGEBNIS><FNR>433826f</FNR></ERGEBNIS></R>'
    root = etree.fromstring(xml.encode())
    erg = iter_named(root, "ERGEBNIS")[0]
    assert child_text(erg, "FNR") == "433826f"
    assert child_text(erg, "MISSING") is None
