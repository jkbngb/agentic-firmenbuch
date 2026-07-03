"""Source-agnostic Pydantic data contracts (Technische Spezifikation §6, §7).

Only the contracts shared by every product live here: the lineage/meta block
(:mod:`fbl_core.models.meta`) and the metric series (:mod:`fbl_core.models.metric`).
The Austria-specific Firmenbuch/UGB domain models (filings, companies, the served
MCP card) live in :mod:`fbl_core_at.models`.
"""

from __future__ import annotations

from .meta import LineageRef, Meta, Stage
from .metric import MetricSeries, Trend

__all__ = [
    "LineageRef",
    "Meta",
    "MetricSeries",
    "Stage",
    "Trend",
]
