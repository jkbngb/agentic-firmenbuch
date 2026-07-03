"""fbl_consolidate — Stage 6a: parsed filings + master → ConsolidatedCompany (50_consolidated)."""

from __future__ import annotations

from .consolidate import PRODUCER, consolidate
from .events import EVENTS_START, derive_register_events, master_signature

LAYER = "50_consolidated"

__all__ = [
    "EVENTS_START",
    "LAYER",
    "PRODUCER",
    "consolidate",
    "derive_register_events",
    "master_signature",
]
