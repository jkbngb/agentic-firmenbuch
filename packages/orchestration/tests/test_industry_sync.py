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


def test_changed_text_reclassifies_via_llm() -> None:
    def llm(text: str, mode: str) -> str | None:
        assert mode == "text"
        return "47.30"

    got = resolve_industry("Tankstelle", None, _prev("Unternehmensberatung"), llm)
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
