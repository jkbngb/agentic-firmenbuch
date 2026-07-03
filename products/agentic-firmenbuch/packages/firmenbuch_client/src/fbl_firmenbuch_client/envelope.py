"""SOAP envelope building + response helpers (§8.2).

Requests wrap the body in a plain ``soap:Envelope``/``soap:Body`` with the
request namespace bound to the ``fb`` prefix. Auth is a separate ``X-API-KEY``
HTTP header (confirmed live, not WS-Security — see docs/API_PROBE_FINDINGS.md).
Responses declare every namespace on the root with ``ns2…ns19`` prefixes, so we
always parse by **local name**.
"""

from __future__ import annotations

from lxml import etree

_SOAP_NS = "http://schemas.xmlsoap.org/soap/envelope/"


def build_envelope(request_ns: str, body_inner_xml: str) -> bytes:
    """Wrap pre-rendered body XML (``fb:`` prefixed) in a SOAP envelope."""
    envelope = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<soapenv:Envelope xmlns:soapenv="{_SOAP_NS}" xmlns:fb="{request_ns}">'
        f"<soapenv:Body>{body_inner_xml}</soapenv:Body>"
        "</soapenv:Envelope>"
    )
    return envelope.encode("utf-8")


def local_name(elem: etree._Element) -> str:
    name: str = etree.QName(elem).localname
    return name


def fault_string(root: etree._Element) -> str | None:
    """Return the SOAP ``faultstring`` (+ detail) if the response is a Fault, else None."""
    fault = None
    for elem in root.iter():
        if isinstance(elem.tag, str) and local_name(elem) == "Fault":
            fault = elem
            break
    if fault is None:
        return None
    parts: list[str] = []
    for elem in fault.iter():
        if not isinstance(elem.tag, str):
            continue
        name = local_name(elem)
        if name in ("faultstring", "ValidationError") and elem.text:
            parts.append(elem.text.strip())
    return " | ".join(parts) if parts else "SOAP Fault"


def child_text(elem: etree._Element, name: str) -> str | None:
    """Trimmed text of the first descendant with local name *name*, else None."""
    for child in elem.iter():
        if isinstance(child.tag, str) and local_name(child) == name and child is not elem:
            text = (child.text or "").strip()
            return text or None
    return None


def direct_child(elem: etree._Element, name: str) -> etree._Element | None:
    for child in elem:
        if isinstance(child.tag, str) and local_name(child) == name:
            return child
    return None


def iter_named(root: etree._Element, name: str) -> list[etree._Element]:
    """All descendants with local name *name*."""
    return [e for e in root.iter() if isinstance(e.tag, str) and local_name(e) == name]
