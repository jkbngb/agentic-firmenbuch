"""Filing-format detection for parse (§8.4, §15b-1).

The detection logic is shared with ``firmenbuch_client`` and therefore lives in
``fbl_core.formats`` (siblings never import each other, §3). This module re-exports
it under the names parse uses and widens the variant to the model's ``FilingFormat``.
"""

from __future__ import annotations

from typing import cast

from fbl_core.formats import XmlVariant
from fbl_core.formats import detect_xml_variant as detect_variant
from fbl_core.models.filing import FilingFormat

__all__ = ["XmlVariant", "as_filing_format", "detect_variant"]


def as_filing_format(variant: XmlVariant) -> FilingFormat:
    """Widen an :data:`XmlVariant` to the model's ``FilingFormat`` literal."""
    return cast(FilingFormat, variant)
