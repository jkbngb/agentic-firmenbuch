"""fbl_consolidate — Stage 6a: parsed filings + master → ConsolidatedCompany (50_consolidated)."""

from __future__ import annotations

from .consolidate import PRODUCER, consolidate

LAYER = "50_consolidated"

__all__ = ["LAYER", "PRODUCER", "consolidate"]
