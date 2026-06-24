"""fbl_present — Stage 7: gated, denormalized public document (10_presentation)."""

from __future__ import annotations

from .present import PRESENTED_ALLOWLIST, PRODUCER, present, present_status_only

LAYER = "10_presentation"

__all__ = ["LAYER", "PRESENTED_ALLOWLIST", "PRODUCER", "present", "present_status_only"]
