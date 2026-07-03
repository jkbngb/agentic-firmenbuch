"""Daily-delta industry resolution (#34 step 6): deterministic first, LLM last,
never a wiped classification on re-present."""

from typing import Any

from fbl_orchestration.industry_sync import resolve_industry


def _prev(gz: str, group: str = "70.2") -> dict[str, Any]:
    return {
        "industry": {
            "geschaeftszweig": gz,
            "oenace": {"group": group},
            "code_2008": "70.22",
            "source": "lexicon",
            "classified_from": "geschaeftszweig",
        }
    }


def test_carry_forward_when_text_unchanged() -> None:
    """Re-presenting an unchanged company must NOT lose or re-buy the classification."""
    calls: list[str] = []

    def llm(text: str, mode: str) -> str | None:
        calls.append(text)
        return "70.22"

    got = resolve_industry("Unternehmensberatung", "X GmbH", _prev("Unternehmensberatung"), llm)
    assert got == _prev("Unternehmensberatung")["industry"]
    assert calls == []  # no LLM spend on carry-forward


def test_carry_forward_is_case_and_whitespace_insensitive() -> None:
    got = resolve_industry("  unternehmensberatung ", None, _prev("Unternehmensberatung"), None)
    assert got is not None and got["code_2008"] == "70.22"


def test_changed_frequent_text_resolves_from_frozen_lexicon() -> None:
    """A changed text that is in the frozen head lexicon never reaches the LLM."""
    calls: list[str] = []

    def llm(text: str, mode: str) -> str | None:
        calls.append(text)
        return "47.30"

    got = resolve_industry("Tankstelle", None, _prev("Unternehmensberatung"), llm)
    assert got is not None and got["oenace"]["group"] == "47.3"
    assert got["source"] == "lexicon" and calls == []


def test_changed_long_tail_text_reclassifies_via_llm() -> None:
    def llm(text: str, mode: str) -> str | None:
        assert mode == "text"
        return "47.30"

    got = resolve_industry(
        "Betrieb einer völlig neuartigen Zapfsäulen-Anlage",
        None,
        _prev("Unternehmensberatung"),
        llm,
    )
    assert got is not None and got["oenace"]["group"] == "47.3" and got["source"] == "llm"


def test_unknown_text_without_llm_serves_honest_gap() -> None:
    got = resolve_industry("Völlig neuartige Tätigkeit", None, None, None)
    assert got is not None
    assert got["geschaeftszweig"] == "Völlig neuartige Tätigkeit"
    assert got["oenace"] is None  # no guess, next grind sweep picks it up


def test_no_text_name_pass_with_abstention() -> None:
    def yes(text: str, mode: str) -> str | None:
        assert mode == "name"
        return "49.41"

    got = resolve_industry(None, "Müller Transporte GmbH", None, yes)
    assert got is not None
    assert got["classified_from"] == "name" and got["oenace"]["group"] == "49.4"

    def abstain(text: str, mode: str) -> str | None:
        return None

    assert resolve_industry(None, "Huber GmbH", None, abstain) is None
    assert resolve_industry(None, "Huber GmbH", None, None) is None


def test_learned_lexicon_classifies_each_text_exactly_once() -> None:
    """P2 across companies and runs: the LLM is called at most once per unique text;
    every later company with the same text resolves deterministically."""
    from fbl_core.storage import InMemoryCosmosStore
    from fbl_orchestration.industry_sync import LearnedLexicon

    cosmos = InMemoryCosmosStore()
    learned = LearnedLexicon(cosmos)
    calls: list[str] = []

    def llm(text: str, mode: str) -> str | None:
        calls.append(text)
        return "62.01"

    a = resolve_industry("Individualsoftware-Schmiede", None, None, llm, learned)
    b = resolve_industry("individualsoftware-schmiede  ", None, None, llm, learned)
    assert calls == ["Individualsoftware-Schmiede"]  # exactly ONE call for both companies
    assert a is not None and b is not None
    assert a["oenace"]["group"] == b["oenace"]["group"] == "62.1"
    assert b["source"] == "lexicon"  # second hit is served deterministically

    # a fresh run (new memo, same Cosmos) still needs no LLM
    learned2 = LearnedLexicon(cosmos)
    c = resolve_industry("Individualsoftware-Schmiede", None, None, None, learned2)
    assert c is not None and c["oenace"]["group"] == "62.1" and c["source"] == "lexicon"
