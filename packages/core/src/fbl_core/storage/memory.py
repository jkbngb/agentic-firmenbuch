"""In-memory storage fakes — used by every stage's tests and offline runs (§12).

Faithful enough for the pipeline: byte-exact blob storage and document upsert/get/
query-by-field. Not for production (no durability, naive query).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from .blob import PARSED_CONTAINER, RAW_CONTAINER


class InMemoryBlobStore:
    """Dict-backed BlobStore satisfying ``BlobStoreLike``."""

    def __init__(self) -> None:
        # {container: {path: bytes}}
        self._data: dict[str, dict[str, bytes]] = {RAW_CONTAINER: {}, PARSED_CONTAINER: {}}

    def _bucket(self, container: str) -> dict[str, bytes]:
        return self._data.setdefault(container, {})

    @staticmethod
    def raw_path(fnr: str, stichtag: str, filename: str) -> str:
        return f"{fnr}/{stichtag}/{filename}"

    def put_raw(self, fnr: str, stichtag: str, filename: str, data: bytes) -> str:
        path = self.raw_path(fnr, stichtag, filename)
        bucket = self._bucket(RAW_CONTAINER)
        if path in bucket:  # immutable: never overwrite an existing raw artifact (§5.1)
            return f"{RAW_CONTAINER}/{path}"
        bucket[path] = data
        return f"{RAW_CONTAINER}/{path}"

    def put_bytes(self, container: str, path: str, data: bytes, *, overwrite: bool = True) -> str:
        bucket = self._bucket(container)
        if path in bucket and not overwrite:
            return f"{container}/{path}"
        bucket[path] = data
        return f"{container}/{path}"

    def get_bytes(self, container: str, path: str) -> bytes | None:
        return self._bucket(container).get(path)

    def put_json(self, container: str, path: str, obj: dict[str, Any]) -> str:
        data = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return self.put_bytes(container, path, data, overwrite=True)

    def get_json(self, container: str, path: str) -> dict[str, Any] | None:
        data = self.get_bytes(container, path)
        if data is None:
            return None
        result: dict[str, Any] = json.loads(data)
        return result

    def exists(self, container: str, path: str) -> bool:
        return path in self._bucket(container)

    def list_paths(self, container: str, prefix: str = "") -> list[str]:
        return sorted(p for p in self._bucket(container) if p.startswith(prefix))


class InMemoryCosmosStore:
    """Dict-backed CosmosStore satisfying ``CosmosStoreLike``."""

    def __init__(self) -> None:
        # {container: {id: doc}}
        self._data: dict[str, dict[str, dict[str, Any]]] = {}

    def _bucket(self, container: str) -> dict[str, dict[str, Any]]:
        return self._data.setdefault(container, {})

    def upsert(self, container: str, doc: dict[str, Any]) -> None:
        if "id" not in doc:
            raise ValueError("Cosmos document must include 'id'")
        # Store a deep copy so callers can't mutate stored state by reference.
        self._bucket(container)[doc["id"]] = json.loads(json.dumps(doc))

    def get(self, container: str, fnr: str) -> dict[str, Any] | None:
        doc = self._bucket(container).get(fnr)
        return json.loads(json.dumps(doc)) if doc is not None else None

    def iter_all(self, container: str) -> Iterator[dict[str, Any]]:
        for doc in list(self._bucket(container).values()):
            yield json.loads(json.dumps(doc))

    def query_by_field(self, container: str, field: str, value: Any) -> Iterator[dict[str, Any]]:
        for doc in self.iter_all(container):
            if doc.get(field) == value:
                yield doc

    def query(
        self, container: str, sql: str, params: list[dict[str, Any]] | None = None
    ) -> Iterator[dict[str, Any]]:
        # Minimal support: callers in tests use query_by_field / iter_all. A raw SQL
        # query returns everything (the fake does not implement a SQL engine).
        yield from self.iter_all(container)

    def count(self, container: str) -> int:
        return len(self._bucket(container))
