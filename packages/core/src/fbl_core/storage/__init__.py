"""Storage clients for Blob (raw/parsed) and Cosmos (consolidated → presented).

Production: ``BlobStore`` / ``CosmosStore`` (Azure). Tests/offline: ``InMemoryBlobStore``
/ ``InMemoryCosmosStore``. Both satisfy the ``BlobStoreLike`` / ``CosmosStoreLike``
Protocols, so pipeline code is identical either way.
"""

from __future__ import annotations

from .base import BlobStoreLike, CosmosStoreLike
from .blob import PARSED_CONTAINER, RAW_CONTAINER, BlobStore
from .cosmos import PARTITION_KEYS, CosmosStore
from .memory import InMemoryBlobStore, InMemoryCosmosStore

__all__ = [
    "PARSED_CONTAINER",
    "PARTITION_KEYS",
    "RAW_CONTAINER",
    "BlobStore",
    "BlobStoreLike",
    "CosmosStore",
    "CosmosStoreLike",
    "InMemoryBlobStore",
    "InMemoryCosmosStore",
]
