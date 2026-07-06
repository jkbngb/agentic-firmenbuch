"""Build the served ``industry`` block (v2 contract, #34).

One assigned fact goes in — the ÖNACE 2008 **class** (4-digit) that the lexicon or the LLM
picked for the Geschäftszweig — and everything else is a deterministic lookup: the class is
mapped to its ÖNACE 2025 group via the official class-level crosswalk (:mod:`.crosswalk`),
and section/division/labels on every level come from the official bilingual tables
(:mod:`.taxonomy`). ``oenace`` and ``nace`` are structurally symmetric blocks with identical
codes (ÖNACE 2025 == NACE Rev. 2.1 down to the class level; Austria only adds 5-digit
national subclasses): ÖNACE carries the official German and English titles, NACE the
official English ones.

Hierarchy consistency (section ⊇ division ⊇ group) is guaranteed by construction — nothing
is assigned independently. Companies without a Geschäftszweig get no block at all
(``industry: null`` in the served document): a missing signal is served as a gap, never
guessed. See docs/classification/README.md (authoritative spec).
"""

from __future__ import annotations

from typing import Any

from .crosswalk import map_class
from .taxonomy import OenaceTree, load_oenace_tree

OENACE_VERSION = "OENACE_2025"
OENACE_2008_VERSION = "OENACE_2008"
NACE_VERSION = "NACE_REV_2.1"


def _labels(tree: OenaceTree, code: str) -> tuple[str | None, str | None]:
    n = tree.get(code)
    return (n.title_de if n else None, n.title_en if n else None)


def oenace_2008_block(code_2008: str | None) -> dict[str, Any] | None:
    """Expand a stored ÖNACE **2008** code into the same section/division/group shape as the
    2025 block, with official DE/EN labels from the bundled 2008 tree.

    This is a pure, deterministic lookup (no crosswalk, no guessing): the 2008 vintage the
    classifier actually predicted in, served symmetrically to ``oenace`` so a caller can filter
    or read either vintage. ``group``/``class`` are set only at/below their level; an
    invalid/unknown code yields ``None`` (nothing honest to say)."""
    if not code_2008:
        return None
    t08 = load_oenace_tree(2008)
    code = code_2008.strip()
    node = t08.get(code)
    if node is None:
        return None
    division = code.split(".")[0]
    group = code[:4] if node.level >= 3 and "." in code else None
    klass = code if node.level >= 4 else None
    section = t08.section_of(code)
    sec_de, sec_en = _labels(t08, section) if section else (None, None)
    div_de, div_en = _labels(t08, division)
    grp_de, grp_en = _labels(t08, group) if group else (None, None)
    cls_de, cls_en = (node.title_de, node.title_en) if klass else (None, None)
    return {
        "section": section,
        "section_label_de": sec_de,
        "section_label_en": sec_en,
        "division": division,
        "division_label_de": div_de,
        "division_label_en": div_en,
        "group": group,
        "group_label_de": grp_de,
        "group_label_en": grp_en,
        "class": klass,
        "class_label_de": cls_de,
        "class_label_en": cls_en,
        "version": OENACE_2008_VERSION,
    }


def build_industry_block(
    geschaeftszweig: str | None,
    code_2008_class: str | None,
    source: str,
    classified_from: str = "geschaeftszweig",
) -> dict[str, Any] | None:
    """The full served ``industry`` block, or ``None`` when there is nothing honest to say.

    ``source`` is ``"lexicon"`` (verified head table) or ``"llm"`` (long-tail
    classification); ``classified_from`` is ``"geschaeftszweig"`` or ``"name"``.
    An invalid/unknown class yields a block with ``oenace``/``nace`` = None (the free
    text is still served) — codes are never guessed past the crosswalk."""
    if not geschaeftszweig and not code_2008_class:
        return None

    t08 = load_oenace_tree(2008)
    t25 = load_oenace_tree(2025)

    cls08 = (code_2008_class or "").strip()
    group25 = map_class(cls08) if cls08 and t08.is_valid(cls08) else None
    if group25 is None:
        return {
            "geschaeftszweig": geschaeftszweig,
            "oenace": None,
            "nace": None,
            "oenace_2008": oenace_2008_block(cls08) if cls08 else None,
            "code_2008": None,
            "source": source,
            "classified_from": classified_from,
        }

    division = group25.split(".")[0]
    section = t25.section_of(group25)
    sec_de, sec_en = _labels(t25, section) if section else (None, None)
    div_de, div_en = _labels(t25, division)
    grp_de, grp_en = _labels(t25, group25)

    return {
        "geschaeftszweig": geschaeftszweig,
        "oenace": {
            "section": section,
            "section_label_de": sec_de,
            "section_label_en": sec_en,
            "division": division,
            "division_label_de": div_de,
            "division_label_en": div_en,
            "group": group25,
            "group_label_de": grp_de,
            "group_label_en": grp_en,
            "version": OENACE_VERSION,
        },
        # same codes by construction (national version == EU NACE at these levels);
        # English titles only — the official German NACE titles ARE the ÖNACE titles.
        "nace": {
            "section": section,
            "section_label": sec_en,
            "division": division,
            "division_label": div_en,
            "group": group25,
            "group_label": grp_en,
            "version": NACE_VERSION,
        },
        # The ÖNACE 2008 vintage the classifier actually predicted in, expanded to the same
        # shape as `oenace` — so a filter/read against either vintage is honest and symmetric.
        "oenace_2008": oenace_2008_block(node08.code if (node08 := t08.get(cls08)) else cls08),
        "code_2008": (node08.code if node08 else cls08),
        "source": source,
        "classified_from": classified_from,
    }


def industry_from_legacy_branch(branch: dict[str, Any] | None) -> dict[str, Any] | None:
    """Serve-time adapter: translate a stored v1 ``branch`` block into the v2 ``industry``
    shape (transition period until the re-grind replaces every document).

    v1 stored only a GROUP-level 2008 code, so the class-level crosswalk cannot repair
    its mapping here — the stored 2025 group is served as-is (labels re-derived from the
    official tables), and correctness lands with the re-grind (#34)."""
    if not branch:
        return None
    oenace = branch.get("oenace") or {}
    group25 = oenace.get("group")
    gz = branch.get("geschaeftszweig")
    if not group25:
        return {
            "geschaeftszweig": gz,
            "oenace": None,
            "nace": None,
            "oenace_2008": oenace_2008_block(branch.get("code_2008")),
            "code_2008": branch.get("code_2008"),
            "source": branch.get("source") or "llm",
            "classified_from": "geschaeftszweig",
        }
    t25 = load_oenace_tree(2025)
    division = str(group25).split(".")[0]
    section = t25.section_of(str(group25))
    sec_de, sec_en = _labels(t25, section) if section else (None, None)
    div_de, div_en = _labels(t25, division)
    grp_de, grp_en = _labels(t25, str(group25))
    return {
        "geschaeftszweig": gz,
        "oenace": {
            "section": section,
            "section_label_de": sec_de,
            "section_label_en": sec_en,
            "division": division,
            "division_label_de": div_de,
            "division_label_en": div_en,
            "group": group25,
            "group_label_de": grp_de,
            "group_label_en": grp_en,
            "version": OENACE_VERSION,
        },
        "nace": {
            "section": section,
            "section_label": sec_en,
            "division": division,
            "division_label": div_en,
            "group": group25,
            "group_label": grp_en,
            "version": NACE_VERSION,
        },
        "oenace_2008": oenace_2008_block(branch.get("code_2008")),
        "code_2008": branch.get("code_2008"),
        "source": branch.get("source") or "llm",
        "classified_from": "geschaeftszweig",
    }
