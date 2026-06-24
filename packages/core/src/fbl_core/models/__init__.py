"""Canonical Pydantic data contracts for the whole pipeline (Technische Spezifikation §6)."""

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
from .meta import LineageRef, Meta, Stage
from .metric import MetricSeries, Trend

__all__ = [
    # filing
    "Bilanz",
    # mcp
    "CompanyCard",
    # company
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
    # meta / metric
    "LineageRef",
    "Location",
    "Management",
    "Manager",
    "MasterData",
    "Meta",
    "MetricSeries",
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
    "Stage",
    "Trend",
]
