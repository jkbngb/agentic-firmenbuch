"""Cosmos DB storage client (Technische Spezifikation §8.1).

Serves the Cosmos layers (``50_consolidated`` … ``00_accounts``). The Azure SDK
is imported lazily so the offline stages run without Azure (§3.2). Every document
upserted must carry ``id`` and its partition-key field (``fnr`` or ``token_hash``).
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from azure.cosmos import CosmosClient, DatabaseProxy

# Cosmos containers and their partition keys (§4.0).
PARTITION_KEYS = {
    "50_consolidated": "/fnr",
    "30_derived": "/fnr",
    "10_presentation": "/fnr",
    "99_registry": "/fnr",
    "00_accounts": "/token_hash",
    "40_enriched": "/fnr",
    "20_scored": "/fnr",
}


class CosmosStore:
    """Thin wrapper over a Cosmos ``DatabaseProxy`` using Managed Identity."""

    def __init__(
        self,
        endpoint: str,
        database: str = "firmenbuch",
        credential: Any | None = None,
    ) -> None:
        self._endpoint = endpoint
        self._database_name = database
        self._credential = credential
        self._client: CosmosClient | None = None
        self._db: DatabaseProxy | None = None
        # Guards the lazy client init so concurrent first-use (search_companies fires COUNT +
        # page on two threads) can't race two CosmosClient constructions.
        self._init_lock = threading.Lock()

    def _database(self) -> DatabaseProxy:
        if self._db is None:
            with self._init_lock:
                if self._db is None:  # double-checked: another thread may have won the lock
                    from azure.cosmos import CosmosClient
                    from azure.identity import DefaultAzureCredential

                    cred = self._credential or DefaultAzureCredential()
                    # Serverless Cosmos throttles (429) under concurrent load; give the SDK a
                    # generous throttle-retry budget before it surfaces the error to the caller.
                    self._client = CosmosClient(
                        self._endpoint, credential=cred, retry_total=30, retry_backoff_max=60
                    )
                    self._db = self._client.get_database_client(self._database_name)
        return self._db

    def upsert(self, container: str, doc: dict[str, Any]) -> None:
        """Upsert a document. ``doc`` must include ``id`` and its partition key."""
        if "id" not in doc:
            raise ValueError("Cosmos document must include 'id'")
        self._database().get_container_client(container).upsert_item(doc)

    def get(self, container: str, fnr: str) -> dict[str, Any] | None:
        """Point-read by ``fnr`` (which equals ``id`` for pipeline layers)."""
        from azure.cosmos.exceptions import CosmosResourceNotFoundError

        client = self._database().get_container_client(container)
        try:
            result: dict[str, Any] = client.read_item(item=fnr, partition_key=fnr)
        except CosmosResourceNotFoundError:
            return None
        return result

    def query(
        self, container: str, sql: str, params: list[dict[str, Any]] | None = None
    ) -> Iterator[dict[str, Any]]:
        """Run a parameterized SQL query across partitions."""
        client = self._database().get_container_client(container)
        yield from client.query_items(
            query=sql,
            parameters=params or [],
            enable_cross_partition_query=True,
        )

    def query_by_field(self, container: str, field: str, value: Any) -> Iterator[dict[str, Any]]:
        """Yield documents where ``c.<field> == value`` (field is an identifier)."""
        sql = f"SELECT * FROM c WHERE c.{field} = @value"
        yield from self.query(container, sql, [{"name": "@value", "value": value}])

    def iter_all(self, container: str) -> Iterator[dict[str, Any]]:
        """Yield every document in *container* (use sparingly; full scan)."""
        yield from self.query(container, "SELECT * FROM c", [])
