"""Daily-delta industry classification (#34, step 6 — "new companies get classified too").

Every company that the daily pipeline (re-)presents gets its ``industry`` block resolved
here, deterministically first and with the LLM only as the last resort (the spec's
P-principles, docs/classification/README.md):

  1. unchanged Geschäftszweig  → carry the previous block forward (free, stable)
  2. lexicon hit (P3)          → deterministic text→class table, source "lexicon"
  3. unknown text + LLM key    → one catalogue-constrained call, class level (P1)
  4. no text, name + LLM key   → abstention-capable name call (``classified_from: name``)
  5. otherwise                 → free text with null codes / no block (honest gap)

Without an ``ANTHROPIC_API_KEY`` the pipeline still works: steps 1+2+5 are fully
deterministic; unknown texts stay code-less until the next grind sweeps them.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from functools import lru_cache
from importlib import resources
from typing import Any

from fbl_core.classification.industry import build_industry_block
from fbl_core.classification.taxonomy import load_oenace_tree
from fbl_core.logging import get_logger

log = get_logger(__name__)

# (text, mode) -> ÖNACE 2008 class or None; mode is "text" or "name"
LlmClassifier = Callable[[str, str], str | None]


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip()).casefold()


@lru_cache(maxsize=1)
def _lexicon() -> dict[str, str]:
    """The frozen head lexicon (P3). Empty when the file has not shipped yet."""
    try:
        res = resources.files("fbl_core.classification").joinpath(
            "data", "oenace", "geschaeftszweig_lexicon.json"
        )
        data = json.loads(res.read_text(encoding="utf-8"))
        table: dict[str, str] = data.get("text_to_class_2008", {})
        return table
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}


def resolve_industry(
    geschaeftszweig: str | None,
    name: str | None,
    prev_doc: dict[str, Any] | None,
    llm: LlmClassifier | None = None,
) -> dict[str, Any] | None:
    """Resolve the ``industry`` block for a freshly presented company document."""
    gz = (geschaeftszweig or "").strip()
    prev = (prev_doc or {}).get("industry")

    if gz:
        # 1) carry-forward: same text, keep the previous (already audited) block
        if (
            isinstance(prev, dict)
            and prev.get("oenace")
            and _norm(str(prev.get("geschaeftszweig") or "")) == _norm(gz)
        ):
            return prev
        # 2) deterministic lexicon
        cls = _lexicon().get(_norm(gz))
        if cls:
            return build_industry_block(gz, cls, "lexicon")
        # 3) LLM long tail
        if llm is not None:
            got = llm(gz, "text")
            if got:
                return build_industry_block(gz, got, "llm")
        # 5) honest gap: text served, codes null
        return build_industry_block(gz, None, "llm")

    # 4) no text: name-based only via the abstention prompt, and only with an LLM
    if llm is not None and (name or "").strip():
        # keep a previous name-based block if one exists (stability)
        if isinstance(prev, dict) and prev.get("oenace") and prev.get("classified_from") == "name":
            return prev
        got = llm(str(name).strip(), "name")
        if got:
            return build_industry_block(None, got, "llm", classified_from="name")
    return None


def make_llm_classifier(
    api_key: str | None, model: str = "claude-sonnet-4-6"
) -> LlmClassifier | None:
    """Catalogue-constrained single-text classifier. Returns None without a key.

    Any API error resolves to "unclassified" — classification must never break the
    pipeline; the next grind sweep picks the company up."""
    if not api_key:
        return None
    from anthropic import Anthropic  # runtime dep of orchestration (daily delta volume: cents)

    client = Anthropic(api_key=api_key)
    t08 = load_oenace_tree(2008)
    catalog = "\n".join(f"{n.code} {n.title_de}" for _, n in t08.nodes.items() if n.level == 4)
    sys_text = [
        {
            "type": "text",
            "text": (
                "Du klassifizierst österreichische Firmen nach ÖNACE 2008 anhand ihres "
                "Geschäftszweigs. Wähle GENAU EINEN Code aus der offiziellen Klassenliste "
                "unten (Format 'DD.DD'), erfinde keine. Bei mehreren Tätigkeiten die "
                'Haupttätigkeit. Antworte NUR mit JSON: {"code":"DD.DD"}.'
                f"\n\nÖNACE-2008-KLASSEN:\n{catalog}"
            ),
            "cache_control": {"type": "ephemeral"},
        }
    ]
    sys_name = [
        {
            "type": "text",
            "text": (
                "Du klassifizierst österreichische Firmen nach ÖNACE 2008 anhand NUR ihres "
                "Firmennamens. Gib einen Code aus der Klassenliste unten (Format 'DD.DD') "
                "NUR, wenn der Name die Tätigkeit EINDEUTIG erkennen lässt; im Zweifel "
                'IMMER null. Antworte NUR mit JSON: {"code":"DD.DD" oder null}.'
                f"\n\nÖNACE-2008-KLASSEN:\n{catalog}"
            ),
            "cache_control": {"type": "ephemeral"},
        }
    ]

    def classify(text: str, mode: str) -> str | None:
        try:
            msg = client.messages.create(
                model=model,
                max_tokens=200,
                system=sys_name if mode == "name" else sys_text,  # type: ignore[arg-type]
                messages=[{"role": "user", "content": f'Klassifiziere: "{text}"'}],
            )
            raw = next((b.text for b in msg.content if b.type == "text"), "")
            start = raw.find("{")
            end = raw.rfind("}")
            if start < 0 or end <= start:
                return None
            code = json.loads(raw[start : end + 1]).get("code")
            if not code:
                return None
            node = t08.get(str(code).strip())
            return node.code if node is not None and node.level == 4 else None
        except Exception as exc:
            log.warning("industry llm classify failed", extra={"context": {"error": str(exc)}})
            return None

    return classify
