"""Official ÖNACE 2008 → 2025 crosswalk (issues #14, #34 / industry).

The LLM industry classifier is most accurate against the ÖNACE 2008 (NACE Rev.2) catalogue —
the vintage it knows best — so the pipeline classifies in 2008 and maps the result to the
current ÖNACE 2025 standard with this deterministic crosswalk, extracted from the official
Statistik Austria *ÖNACE08→ÖNACE25 Korrespondenz* by ``scripts/extract_oenace_crosswalk.py``.

The official correspondence is **1:n** (one 2008 code, several 2025 targets with activity
descriptions). v1 collapsed it into a dict at GROUP level, which sent every consultancy to
73.3/PR and every petrol station to 35.15/electricity trade (#34). v2 therefore resolves at
**class level** (4-digit — the level the LLM emits, P1) with audited rules; ``map_class`` is
the only mapping the pipeline uses. ``map_group`` remains solely to read legacy v1 documents.

Pure lookups, no network."""

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


def _c4(code: str) -> str:
    """Normalise to a 4-digit class code ``DD.DD`` (e.g. '7022' / '70.22-0' → '70.22')."""
    c = str(code).strip().replace(",", ".")
    m = re.search(r"(\d{1,2})\.(\d{2})", c) or re.fullmatch(r"(\d{2})(\d{2})", c.replace(".", ""))
    if m:
        return f"{int(m.group(1)):02d}.{m.group(2)}"
    return c


def map_class(code_2008_class: str) -> str | None:
    """Map an ÖNACE 2008 **class** (``DD.DD``) to its ÖNACE 2025 **group** (``DD.D``).

    This is the v2 mapping (P1: the classifier emits classes, so no lossy step follows).
    Returns ``None`` for codes not in the table — callers must treat that as
    "unclassified", never guess."""
    table: dict[str, str] = _tables()["class_2008_to_2025_group"]
    return table.get(_c4(code_2008_class))


def ambiguous_classes() -> dict[str, list[dict[str, str]]]:
    """2008 classes whose official correspondence splits across several 2025 groups,
    with every official target row (group, title, activity description) — the audit
    trail for the resolution rules (R3/R3b/manual) in the extractor."""
    table: dict[str, list[dict[str, str]]] = _tables()["ambiguous_class_2008"]
    return table


def map_group(code_2008: str) -> str:
    """LEGACY (v1 documents only): map a 2008 group (``DD.D``) to a 2025 group.

    Group-level mapping is inherently lossy on split groups — do not use for new
    classification; use :func:`map_class`. Identity fallback when not in the table."""
    g = _g3(code_2008)
    table: dict[str, str] = _tables()["group_2008_to_2025"]
    return table.get(g, g)
