"""fbl_core_at — Austria-specific domain for agentic-firmenbuch.

The source-specific counterpart to the shared ``fbl_core``: the UGB position
taxonomy (``mapping``), the Firmenbuch/UGB domain models (``models`` —
``ParsedFiling``, ``ConsolidatedCompany``, ``CompanyCard`` …), ÖNACE branch
classification (``classification``), the OeNB/EIOPA financial-institution
directories, and Austria helpers (``austria``, ``formats``, ``esvg``).

Depends on ``fbl_core`` (lineage, storage, config, and the source-agnostic
``Meta``/``MetricSeries`` contracts); ``fbl_core`` never depends back on this.
See ``products/agentic-firmenbuch/README.md`` and Technische Spezifikation §3.4.
"""

from __future__ import annotations

__version__ = "1.0.0"
