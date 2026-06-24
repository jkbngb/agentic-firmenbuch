"""fbl_core — shared models, mappings, lineage, config and storage clients.

The foundation package for agentic-firmenbuch. Contains no business logic; every
pipeline stage serializes through the contracts defined here (Technische
Spezifikation §8.1). See ``models`` (§6), ``lineage`` (§7) and ``mapping`` (Appendix C/D).
"""

from __future__ import annotations

__version__ = "1.0.0"
