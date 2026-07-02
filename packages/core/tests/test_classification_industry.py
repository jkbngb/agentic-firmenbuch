"""The served industry block (v2, #34): one assigned fact, everything else derived —
including the golden cases that v1 got wrong."""

from typing import Any

from fbl_core.classification.industry import (
    build_industry_block,
    industry_from_legacy_branch,
)


def test_golden_consulting_is_consulting_not_pr() -> None:
    """The BCG failure class: 'Unternehmensberatung' must serve as consulting (70.2),
    never PR (73.3). v1 shipped 8k+ of these wrong."""
    b = build_industry_block("Unternehmensberatung", "70.22", "lexicon")
    assert b is not None and b["oenace"] is not None
    assert b["oenace"]["group"] == "70.2"
    assert b["oenace"]["group_label_de"] == "Unternehmensberatung"
    assert b["oenace"]["section"] == "N"
    assert b["code_2008"] == "70.22"


def test_golden_pr_stays_pr() -> None:
    b = build_industry_block("Public-Relations-Beratung", "70.21", "llm")
    assert b is not None and b["oenace"] is not None
    assert b["oenace"]["group"] == "73.3"


def test_golden_petrol_station_is_fuel_retail_not_electricity() -> None:
    b = build_industry_block("Tankstelle", "47.30", "lexicon")
    assert b is not None and b["oenace"] is not None
    assert b["oenace"]["group"] == "47.3"
    assert "Motorenkraftstoff" in b["oenace"]["group_label_de"]


def test_golden_car_repair_moved_to_95_3() -> None:
    b = build_industry_block("Kraftfahrzeugwerkstätte", "45.20", "llm")
    assert b is not None and b["oenace"] is not None
    assert b["oenace"]["group"] == "95.3"


def test_oenace_and_nace_are_symmetric_and_consistent() -> None:
    b = build_industry_block("Unternehmensberatung", "70.22", "lexicon")
    assert b is not None
    oe, na = b["oenace"], b["nace"]
    # identical codes by construction
    assert (oe["section"], oe["division"], oe["group"]) == (
        na["section"],
        na["division"],
        na["group"],
    )
    # hierarchy consistent by construction: division is the group's prefix
    assert oe["group"].startswith(oe["division"] + ".")
    # labels on every level, DE+EN for oenace, EN for nace; EN labels identical
    for lvl in ("section", "division", "group"):
        assert oe[f"{lvl}_label_de"] and oe[f"{lvl}_label_en"]
        assert na[f"{lvl}_label"] == oe[f"{lvl}_label_en"]
    assert oe["version"] == "OENACE_2025" and na["version"] == "NACE_REV_2.1"


def test_unknown_class_serves_text_but_never_guesses_codes() -> None:
    b = build_industry_block("Sonstiges", "00.99", "llm")
    assert b is not None
    assert b["oenace"] is None and b["nace"] is None and b["code_2008"] is None
    assert b["geschaeftszweig"] == "Sonstiges"


def test_no_signal_no_block() -> None:
    assert build_industry_block(None, None, "llm") is None
    assert build_industry_block("", "", "llm") is None


def test_name_classified_is_flagged() -> None:
    b = build_industry_block(None, "49.41", "llm", classified_from="name")
    assert b is not None and b["classified_from"] == "name"
    assert b["oenace"] is not None and b["oenace"]["group"] == "49.4"


def test_legacy_branch_translates_to_v2_shape() -> None:
    """Transition adapter: old stored v1 branch blocks serve in the v2 shape (labels
    re-derived from the official tables); mapping correctness lands with the re-grind."""
    legacy: dict[str, Any] = {
        "geschaeftszweig": "Immobilienverwaltung",
        "oenace": {"section": "M", "division": "68", "group": "68.3", "label": "x"},
        "nace_rev21_group": "68.3",
        "source": "llm",
        "code_2008": "68.3",
    }
    b = industry_from_legacy_branch(legacy)
    assert b is not None and b["oenace"] is not None
    assert b["oenace"]["group"] == "68.3"
    assert b["oenace"]["group_label_de"]  # canonical label, not the stored one
    assert b["nace"]["version"] == "NACE_REV_2.1"
    assert industry_from_legacy_branch(None) is None
