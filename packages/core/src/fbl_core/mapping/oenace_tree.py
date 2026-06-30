"""Official ÖNACE 2025 classification tree (issue #14 / branch).

Loads the canonical ÖNACE 2025 (= NACE Rev.2.1, Austrian national extension) hierarchy from
the bundled ``mapping/oenace/oenace2025_{de,en}.csv`` (Statistik Austria CTI export). Provides
the deterministic taxonomy the branch classifier needs:

* the valid code set at each level (so a predicted code can be VALIDATED, never hallucinated),
* the children of any node (so a constrained prompt can offer only the codes that can actually
  follow the level above — the hierarchical approach), and
* the official bilingual title for any code (so we serve canonical labels, not free text).

Five levels: section ``A`` → division ``A 01`` → group ``A 01.1`` → class ``A 01.11`` →
national subclass ``A 01.11-0``. The numeric code (``01.1``) is what we serve; the leading
section letter is carried for navigation. Pure data + lookups, no network, no LLM."""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from functools import lru_cache
from importlib import resources

_LEVEL_NAMES = {1: "section", 2: "division", 3: "group", 4: "class", 5: "subclass"}


@dataclass(frozen=True)
class OenaceNode:
    """One node in the ÖNACE 2025 tree."""

    level: int  # 1=section … 5=subclass
    level_name: str
    section: str  # owning section letter, e.g. "L"
    code: str  # numeric/display code without the section letter: "68", "68.3", "68.32"
    title_de: str
    title_en: str


@dataclass(frozen=True)
class OenaceTree:
    """The full ÖNACE 2025 hierarchy with code → node and parent → children indexes."""

    nodes: dict[str, OenaceNode]  # keyed by code (e.g. "68.3"); sections keyed by letter
    _children: dict[str, list[str]] = field(default_factory=dict)

    def get(self, code: str) -> OenaceNode | None:
        return self.nodes.get(_norm(code))

    def is_valid(self, code: str) -> bool:
        """True if *code* is a real ÖNACE 2025 code at any level (guards against hallucination)."""
        return _norm(code) in self.nodes

    def title(self, code: str, lang: str = "de") -> str | None:
        n = self.nodes.get(_norm(code))
        if n is None:
            return None
        return n.title_en if lang == "en" else n.title_de

    def section_of(self, code: str) -> str | None:
        n = self.nodes.get(_norm(code))
        return n.section if n else None

    def children(self, code: str) -> list[OenaceNode]:
        """Direct children of *code* (e.g. the groups under a division) — the candidate list a
        constrained prompt offers at the next level. Pass a section letter for its divisions."""
        return [self.nodes[c] for c in self._children.get(_norm(code), [])]

    def codes_at(self, level: int) -> list[str]:
        """All codes at a level (1=section … 5=subclass), in document order."""
        return [c for c, n in self.nodes.items() if n.level == level]


def _norm(code: str) -> str:
    """Normalize a serve/display code to the tree key: strip a leading section letter and the
    Austrian subclass suffix spacing — ``"L 68.3"`` / ``"L68.3"`` / ``"68.3"`` all → ``"68.3"``;
    a bare section letter (``"L"``) stays as-is."""
    c = code.strip().replace(" ", "")
    if c and c[0].isalpha():
        rest = c[1:]
        # a lone section letter has no digits after it
        if rest and rest[0].isdigit():
            c = rest
    return c


@lru_cache(maxsize=1)
def load_oenace_tree() -> OenaceTree:
    """Parse the bundled CSVs into the tree (cached; parsed once per process)."""
    de_rows = _read("oenace2025_de.csv")
    en_titles = {r["Code"]: r["Titel"] for r in _read("oenace2025_en.csv")}

    nodes: dict[str, OenaceNode] = {}
    children: dict[str, list[str]] = {}
    # stack[level] = the code key of the most recent node at that level, for parent linkage
    last_at: dict[int, str] = {}
    for r in de_rows:
        level = int(r["Ebene"])
        display = r["Code"]  # e.g. "A 01.1"
        section = display[0]
        key = display[0] if level == 1 else _norm(display)
        nodes[key] = OenaceNode(
            level=level,
            level_name=_LEVEL_NAMES[level],
            section=section,
            code=key,
            title_de=r["Titel"],
            title_en=en_titles.get(display, ""),
        )
        last_at[level] = key
        if level > 1:
            parent = last_at.get(level - 1)
            if parent is not None:
                children.setdefault(parent, []).append(key)
    return OenaceTree(nodes=nodes, _children=children)


def _read(filename: str) -> list[dict[str, str]]:
    res = resources.files("fbl_core.mapping").joinpath("oenace", filename)
    raw = res.read_text(encoding="utf-8")
    return list(csv.DictReader(raw.splitlines(), delimiter=";"))
