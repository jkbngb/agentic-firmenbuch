"""Storage interfaces (Protocols) so stages depend on capability, not Azure (§8.1).

The concrete ``BlobStore``/``CosmosStore`` (Azure) and the ``InMemory*`` fakes both
satisfy these, so pipeline code is identical in production and in tests.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .blob import BlobDownloadLink


class BlobStoreLike(Protocol):
    """Blob storage: immutable raw artifacts + JSON projections."""

    def put_raw(self, fnr: str, stichtag: str, filename: str, data: bytes) -> str: ...

    def download_link(
        self,
        container: str,
        path: str,
        *,
        ttl_minutes: int = ...,
        filename: str | None = ...,
        content_type: str | None = ...,
    ) -> BlobDownloadLink: ...

    def put_bytes(
        self, container: str, path: str, data: bytes, *, overwrite: bool = True
    ) -> str: ...

    def get_bytes(self, container: str, path: str) -> bytes | None: ...

    def put_json(self, container: str, path: str, obj: dict[str, Any]) -> str: ...

    def get_json(self, container: str, path: str) -> dict[str, Any] | None: ...

    def exists(self, container: str, path: str) -> bool: ...

    def list_paths(self, container: str, prefix: str = "") -> list[str]: ...


class CosmosStoreLike(Protocol):
    """Cosmos document storage for the consolidated → presented layers + registry."""

    def upsert(self, container: str, doc: dict[str, Any]) -> None: ...

    def get(self, container: str, fnr: str) -> dict[str, Any] | None: ...

    def query(
        self, container: str, sql: str, params: list[dict[str, Any]] | None = None
    ) -> Iterator[dict[str, Any]]: ...

    def query_by_field(
        self, container: str, field: str, value: Any
    ) -> Iterator[dict[str, Any]]: ...

    def iter_all(self, container: str) -> Iterator[dict[str, Any]]: ...
