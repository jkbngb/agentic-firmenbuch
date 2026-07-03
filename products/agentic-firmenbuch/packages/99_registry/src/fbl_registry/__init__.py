"""fbl_registry — Layer ``99_registry``: the authoritative company catalog (§15a.0).

One document per FNR plus a watermark singleton; the source of truth for *which
companies exist and their processing state*. Drives every download/rebuild/reconcile.
"""

from __future__ import annotations

from .models import (
    LAYER,
    REGISTRY_CONTAINER,
    WATERMARK_ID,
    KnownFiling,
    PipelineState,
    RegistryDoc,
    RegistryStatus,
    Watermark,
)
from .registry import Registry

__all__ = [
    "LAYER",
    "REGISTRY_CONTAINER",
    "WATERMARK_ID",
    "KnownFiling",
    "PipelineState",
    "Registry",
    "RegistryDoc",
    "RegistryStatus",
    "Watermark",
]
