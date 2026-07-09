"""Tool logic over ``10_presentation`` (§9), decoupled from the FastMCP transport.

Pure read functions taking a ``CosmosStoreLike`` so they unit-test against the in-memory
store. Filtering is applied in Python here; in production the same predicates are pushed to the
Cosmos index (§4.1). Every response carries the §8.9 envelope fields.

This is a package: the tools are grouped into cohesive submodules (search / records / documents /
cohort / stats) over a shared ``_common`` support layer, and re-exported here so the public
surface ``fbl_mcp_server.service.<tool>`` is unchanged for ``app.py``, the orchestrator
(``store_stats``) and the playground.
"""

from __future__ import annotations

from .cohort import find_peers, get_cohort_summary
from .documents import get_document
from .events import get_event_stats, list_events
from .records import describe_fields, get_company_details, get_company_history, get_full_record
from .search import search_companies
from .stats import STATS_ID, coverage, coverage_summary, list_sectors, store_stats

__all__ = [
    "STATS_ID",
    "coverage",
    "coverage_summary",
    "describe_fields",
    "find_peers",
    "get_cohort_summary",
    "get_company_details",
    "get_company_history",
    "get_document",
    "get_event_stats",
    "get_full_record",
    "list_events",
    "list_sectors",
    "search_companies",
    "store_stats",
]
