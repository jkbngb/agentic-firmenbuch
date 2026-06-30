"""Official ÖNACE 2008 → 2025 crosswalk (issue #14 / branch).

The LLM branch classifier is most accurate against the ÖNACE 2008 (NACE Rev.2) catalogue —
the vintage it knows best — so the pipeline classifies in 2008 and then maps the result to the
current ÖNACE 2025 standard with this deterministic crosswalk. Derived verbatim from the
official Statistik Austria *ÖNACE08_OENACE25 Korrespondenz* table (shipped alongside as
``oenace2008_2025_crosswalk.json``); 231 of 272 groups are unchanged, 41 were re-coded.

Pure lookups, no network. ``map_group`` falls back to the unchanged code when a 2008 group has
no recorded successor (the common identity case)."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from importlib import resources
from typing import Any


def _g3(code: str) -> str:
    c = str(code).strip().replace(",", ".")
    m = re.search(r"(\d{1,2})\.(\d)", c)
    if m:
        return f"{int(m.group(1)):02d}.{m.group(2)}"
    m2 = re.search(r"(\d{1,2})", c)
    return m2.group(1).zfill(2) if m2 else c


@lru_cache(maxsize=1)
def _tables() -> dict[str, Any]:
    res = resources.files("fbl_core.classification").joinpath(
        "data", "oenace", "oenace2008_2025_crosswalk.json"
    )
    data: dict[str, Any] = json.loads(res.read_text(encoding="utf-8"))
    return data


def map_group(code_2008: str) -> str:
    """Map an ÖNACE 2008 group code (``DD.D``) to its ÖNACE 2025 successor.

    Identity when unchanged (231/272 groups) or when the code is not in the table."""
    g = _g3(code_2008)
    table: dict[str, str] = _tables()["group_2008_to_2025"]
    return table.get(g, g)


def changed_groups() -> dict[str, str]:
    """The 41 groups whose code differs between 2008 and 2025 (for audit/docs)."""
    table: dict[str, str] = _tables()["group_2008_to_2025"]
    return {k: v for k, v in table.items() if k != v}
