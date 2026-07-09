"""Blob (ADLS Gen2) storage client (Technische Spezifikation §8.1).

Holds the immutable raw artifacts (``90-raw``) and parsed JSON (``70-parsed``).
The Azure SDK is imported lazily inside methods so unit tests for the offline
stages (core/parse) run without Azure installed or configured (§3.2).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

if TYPE_CHECKING:  # pragma: no cover - typing only
    from azure.storage.blob import BlobServiceClient

RAW_CONTAINER = "90-raw"
PARSED_CONTAINER = "70-parsed"

# Default lifetime of a download link. Short by design: the link is a bearer capability over a
# public-data document, so it expires quickly; an agent re-asks get_document for a fresh one.
DOWNLOAD_TTL_MINUTES = 15
# Allow for client/server clock skew so a link is valid immediately (Azure rejects a SAS whose
# signed start time is in the future relative to the storage service's clock).
_SKEW_MINUTES = 5


@dataclass(frozen=True)
class BlobDownloadLink:
    """A time-limited, read-only download URL for one blob (a User-Delegation SAS in prod).

    ``url`` carries the signature inline — hand it straight to the caller; never stream the
    blob's bytes through a tool response. ``expires_at`` is an ISO-8601 ``Z`` timestamp;
    ``expires_in_seconds`` is the same horizon relative to issue time, for display."""

    url: str
    expires_at: str
    expires_in_seconds: int


class BlobStore:
    """Thin wrapper over a ``BlobServiceClient`` using Managed Identity.

    Parameters
    ----------
    account_url:
        e.g. ``https://<account>.blob.core.windows.net``.
    credential:
        a ``DefaultAzureCredential`` (Managed Identity in Azure). If omitted,
        one is created lazily on first use.
    """

    def __init__(self, account_url: str, credential: Any | None = None) -> None:
        # Strip any trailing slash: download_link builds "{account_url}/{container}/{path}",
        # so a trailing slash here yields "…net//90-raw/…" — a malformed blob name that Azure
        # rejects with HTTP 400 on the SAS download (the container becomes empty). The env var
        # BLOB_ACCOUNT_URL is commonly stored with a trailing slash, so normalize it here.
        self._account_url = account_url.rstrip("/")
        self._credential = credential
        self._client: BlobServiceClient | None = None

    def _service(self) -> BlobServiceClient:
        if self._client is None:
            from azure.identity import DefaultAzureCredential
            from azure.storage.blob import BlobServiceClient

            cred = self._credential or DefaultAzureCredential()
            self._client = BlobServiceClient(self._account_url, credential=cred)
        return self._client

    @staticmethod
    def raw_path(fnr: str, stichtag: str, filename: str) -> str:
        """Canonical raw blob path ``{fnr}/{stichtag}/{filename}`` (§5)."""
        return f"{fnr}/{stichtag}/{filename}"

    def put_raw(self, fnr: str, stichtag: str, filename: str, data: bytes) -> str:
        """Store a raw artifact (idempotent); returns the blob path.

        ``overwrite=True`` is safe because the path is content-keyed — the filename carries a
        token derived from the document key, so the same path always means the same bytes.
        Re-writing identical content keeps the archive immutable in practice (§5.1) AND lets a
        killed/resumed ingest re-process a company without crashing on an already-written blob
        (the checkpoint saves in batches, so some blobs land before the checkpoint advances).
        """
        path = self.raw_path(fnr, stichtag, filename)
        client = self._service().get_blob_client(RAW_CONTAINER, path)
        client.upload_blob(data, overwrite=True)
        return f"{RAW_CONTAINER}/{path}"

    def put_bytes(self, container: str, path: str, data: bytes, *, overwrite: bool = True) -> str:
        """Store arbitrary bytes at ``container/path``."""
        client = self._service().get_blob_client(container, path)
        client.upload_blob(data, overwrite=overwrite)
        return f"{container}/{path}"

    def get_bytes(self, container: str, path: str) -> bytes | None:
        """Read raw bytes, or ``None`` if the blob does not exist."""
        from azure.core.exceptions import ResourceNotFoundError

        client = self._service().get_blob_client(container, path)
        try:
            data: bytes = client.download_blob().readall()
        except ResourceNotFoundError:
            return None
        return data

    def exists(self, container: str, path: str) -> bool:
        """True if a blob exists at ``container/path``."""
        return bool(self._service().get_blob_client(container, path).exists())

    def list_paths(self, container: str, prefix: str = "") -> list[str]:
        """List blob paths in *container* under *prefix*."""
        client = self._service().get_container_client(container)
        return [b.name for b in client.list_blobs(name_starts_with=prefix or None)]

    def download_link(
        self,
        container: str,
        path: str,
        *,
        ttl_minutes: int = DOWNLOAD_TTL_MINUTES,
        filename: str | None = None,
        content_type: str | None = None,
    ) -> BlobDownloadLink:
        """Mint a time-limited, read-only **User-Delegation SAS** URL for one blob (§7.2).

        User-delegation (not an account key) means the SAS is signed with a key the Managed
        Identity requests from Entra ID — so it works key-free and is auditable. The MI needs
        the ``Storage Blob Delegator`` role IN ADDITION to ``Storage Blob Data Contributor``
        (see ``infra/modules/rbac.bicep``); without it ``get_user_delegation_key`` 403s.

        ``filename`` sets a ``Content-Disposition: attachment`` so a browser saves the official
        document under a sensible name; ``content_type`` overrides the response MIME. The URL
        embeds the signature — return it directly, never the bytes."""
        from azure.storage.blob import BlobSasPermissions, generate_blob_sas

        service = self._service()
        account_name = service.account_name
        if account_name is None:  # pragma: no cover - always set for a real BlobServiceClient
            raise RuntimeError("blob service client has no account name; cannot sign a SAS")
        now = datetime.now(UTC)
        start = now - timedelta(minutes=_SKEW_MINUTES)
        expiry = now + timedelta(minutes=ttl_minutes)
        key = service.get_user_delegation_key(key_start_time=start, key_expiry_time=expiry)
        disposition = f'attachment; filename="{filename}"' if filename else None
        sas = generate_blob_sas(
            account_name=account_name,
            container_name=container,
            blob_name=path,
            user_delegation_key=key,
            permission=BlobSasPermissions(read=True),
            start=start,
            expiry=expiry,
            content_disposition=disposition,
            content_type=content_type,
        )
        url = f"{self._account_url}/{container}/{quote(path)}?{sas}"
        return BlobDownloadLink(
            url=url,
            expires_at=expiry.strftime("%Y-%m-%dT%H:%M:%SZ"),
            expires_in_seconds=ttl_minutes * 60,
        )

    def put_json(self, container: str, path: str, obj: dict[str, Any]) -> str:
        """Store a JSON document (overwrite-safe; deterministic content)."""
        data = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return self.put_bytes(container, path, data, overwrite=True)

    def get_json(self, container: str, path: str) -> dict[str, Any] | None:
        """Read a JSON document, or ``None`` if it does not exist."""
        from azure.core.exceptions import ResourceNotFoundError

        client = self._service().get_blob_client(container, path)
        try:
            data = client.download_blob().readall()
        except ResourceNotFoundError:
            return None
        result: dict[str, Any] = json.loads(data)
        return result
