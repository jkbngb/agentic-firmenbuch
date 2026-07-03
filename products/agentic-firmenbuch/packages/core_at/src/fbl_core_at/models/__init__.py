"""Austria-specific Pydantic data contracts (Firmenbuch/UGB domain).

The source-agnostic ``Meta``/``LineageRef``/``Stage`` and ``MetricSeries``/``Trend``
contracts stay in :mod:`fbl_core.models`; everything modelling a Firmenbuch filing,
a consolidated/derived company, or the served MCP projection lives here.
"""

from __future__ import annotations

from .company import (
    CompanyMaster,
    ConsolidatedCompany,
    Court,
    Derivations,
    DerivedCompany,
    FilingRef,
    Financials,
    Growth,
    Identity,
    Location,
    Management,
    Manager,
    MasterData,
    Money,
    Ratios,
    RegisterEvent,
    Size,
)
from .filing import (
    Bilanz,
    FieldProvenance,
    FilingFormat,
    GuV,
    ParsedFiling,
    RevenueBasis,
    Signatory,
)
from .mcp import (
    CompanyCard,
    ErrorBody,
    ErrorResponse,
    PresentedCompany,
    PresentedFinancials,
    PresentedManagement,
    PresentedManager,
    PublicProvenance,
    SearchFilters,
    SearchResponse,
    Sort,
)

__all__ = [
    "Bilanz",
    "CompanyCard",
    "CompanyMaster",
    "ConsolidatedCompany",
    "Court",
    "Derivations",
    "DerivedCompany",
    "ErrorBody",
    "ErrorResponse",
    "FieldProvenance",
    "FilingFormat",
    "FilingRef",
    "Financials",
    "Growth",
    "GuV",
    "Identity",
    "Location",
    "Management",
    "Manager",
    "MasterData",
    "Money",
    "ParsedFiling",
    "PresentedCompany",
    "PresentedFinancials",
    "PresentedManagement",
    "PresentedManager",
    "PublicProvenance",
    "Ratios",
    "RegisterEvent",
    "RevenueBasis",
    "SearchFilters",
    "SearchResponse",
    "Signatory",
    "Size",
    "Sort",
]
