"""fbl_parse — Stage 2: raw Jahresabschluss XML → canonical ``ParsedFiling``.

The single public entry point is :func:`parse_filing` (auto-detects the format).
:func:`parse_pdf_only` builds the linked-but-empty record for PDF-only filings.
"""

from __future__ import annotations

from .parser import PRODUCER, parse_filing, parse_pdf_only
from .variant import XmlVariant, detect_variant

LAYER = "70_parsed"

__all__ = ["LAYER", "PRODUCER", "XmlVariant", "detect_variant", "parse_filing", "parse_pdf_only"]
