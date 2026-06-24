"""Blob (ADLS Gen2) storage client (Technische Spezifikation §8.1).

Holds the immutable raw artifacts (``90-raw``) and parsed JSON (``70-parsed``).
The Azure SDK is imported lazily inside methods so unit tests for the offline
stages (core/parse) run without Azure installed or configured (§3.2).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from azure.storage.blob import BlobServiceClient

RAW_CONTAINER = "90-raw"
PARSED_CONTAINER = "70-parsed"


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
        self._account_url = account_url
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
