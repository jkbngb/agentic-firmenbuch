"""``get_document`` — resolve a filing and hand out a time-limited download link (§7.2)."""

from __future__ import annotations

from typing import Any

from fbl_core.storage import RAW_CONTAINER, BlobStoreLike, CosmosStoreLike
from fbl_core_at.directories import load_fi_directory_cached
from fbl_core_at.models import PublicProvenance

from ..errors import NotFound
from ._common import PRESENTED, _all_presented, _financial_institution, _provenance


def _latest_filing_stichtag(doc: dict[str, Any]) -> str | None:
    stichtage = sorted(
        (f.get("stichtag") for f in doc.get("filings", []) if f.get("stichtag")), reverse=True
    )
    return stichtage[0] if stichtage else None


def _resolve_document(
    cosmos: CosmosStoreLike, reference: str
) -> tuple[str, str | None, str | None, dict[str, Any] | None]:
    """Resolve a get_document *reference* to ``(fnr, stichtag, doc_key, served_doc)``.

    Accepts three forms, strongest first: ``"{fnr}:{stichtag}"`` (the ``document_ref`` that
    get_company_details stamps on each filing), a bare ``"{fnr}"`` (→ its latest filing), or a
    legacy/explicit opaque ``doc_key`` (matched against a served filing). Raises
    :class:`NotFound` if nothing matches."""
    if ":" in reference:
        fnr, _, stichtag = reference.partition(":")
        fnr, stichtag = fnr.strip(), stichtag.strip()
        if fnr and stichtag:
            return fnr, stichtag, None, cosmos.get(PRESENTED, fnr)
    served = cosmos.get(PRESENTED, reference)
    if served is not None:
        return reference, _latest_filing_stichtag(served), None, served
    for doc in _all_presented(cosmos):
        for filing in doc.get("filings", []):
            if filing.get("doc_key") == reference or filing.get("document_url") == reference:
                return doc["fnr"], filing.get("stichtag"), reference, doc
    raise NotFound(f"document {reference!r} not found")


def _select_artifact(
    artifacts: list[dict[str, Any]], *, doc_key: str | None, prefer_pdf: bool
) -> dict[str, Any] | None:
    """Pick which raw artifact to hand out for a Stichtag. An explicit *doc_key* wins; else for
    a financial institution prefer the official PDF (banks/insurers file PDF, ROADMAP P2.2);
    else the most recently submitted artifact."""
    if not artifacts:
        return None
    if doc_key is not None:
        for art in artifacts:
            if art.get("doc_key") == doc_key:
                return art
    ranked = sorted(artifacts, key=lambda a: (a.get("eingereicht") or "", a.get("doc_key") or ""))
    if prefer_pdf:
        pdfs = [a for a in ranked if str(a.get("dateiendung", "")).lower() == "pdf"]
        if pdfs:
            return pdfs[-1]
    return ranked[-1]


def get_document(
    cosmos: CosmosStoreLike, reference: str, blob: BlobStoreLike | None = None
) -> dict[str, Any]:
    """Resolve a filing document and return a **time-limited download link** to the official
    artifact in ``90-raw`` (§7.2, ROADMAP P2.2).

    *reference* is a ``document_ref`` (``{fnr}:{stichtag}``) from get_company_details, a bare
    FNR (→ latest filing), or a legacy ``doc_key``. The blob path is read from the per-Stichtag
    ``_manifest.json``; the chosen artifact (the PDF for a bank/insurer) is signed with a
    short-lived User-Delegation SAS — the URL is returned, never the bytes. When *blob* is
    unconfigured or nothing is ingested yet, falls back to metadata only (``download: null``)."""
    fnr, stichtag, doc_key, served = _resolve_document(cosmos, reference)

    result: dict[str, Any] = {"doc_key": reference, "fnr": fnr, "stichtag": stichtag}
    if served is not None:
        for filing in served.get("filings", []):
            if filing.get("stichtag") == stichtag:
                result["filing"] = filing
                break
    fi = (
        _financial_institution(served, load_fi_directory_cached(cosmos))
        if served is not None
        else None
    )
    if fi is not None:
        # An FI's UGB figures are absent by construction — surface the flag + caveat so the agent
        # reads the official PDF instead of treating "no Bilanz" as "no data" (ROADMAP P2.1).
        result["financial_institution"] = fi

    download: dict[str, Any] | None = None
    if blob is not None and stichtag:
        manifest = blob.get_json(RAW_CONTAINER, f"{fnr}/{stichtag}/_manifest.json")
        artifacts = list((manifest or {}).get("artifacts", []))
        artifact = _select_artifact(artifacts, doc_key=doc_key, prefer_pdf=fi is not None)
        if artifact is not None:
            container, _, blob_path = str(artifact.get("blob_path", "")).partition("/")
            ext = str(artifact.get("dateiendung") or "bin")
            link = blob.download_link(
                container or RAW_CONTAINER,
                blob_path,
                filename=f"{fnr}_{stichtag}_jahresabschluss.{ext}",
                content_type=artifact.get("content_type"),
            )
            result["document"] = {
                "dateiendung": artifact.get("dateiendung"),
                "content_type": artifact.get("content_type"),
                "bytes": artifact.get("bytes"),
                "dokumentart": artifact.get("dokumentart"),
                "blob_path": artifact.get("blob_path"),
            }
            download = {
                "url": link.url,
                "expires_at": link.expires_at,
                "expires_in_seconds": link.expires_in_seconds,
            }
    result["download"] = download
    if download is None:
        result["note"] = (
            "Kein abrufbares Originaldokument für diesen Stichtag im Rohspeicher — noch nicht "
            "ingestiert oder Download nicht konfiguriert; nur Metadaten."
        )
    if served is None and download is None:
        raise NotFound(f"document {reference!r} not found")
    provenance = _provenance(served) if served is not None else PublicProvenance()
    return {
        "schema_version": "1.0",
        "result": result,
        "provenance": provenance.model_dump(mode="json"),
    }
