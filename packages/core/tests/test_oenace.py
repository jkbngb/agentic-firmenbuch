"""Deterministic ÖNACE-section classifier tests (issue #14)."""

from __future__ import annotations

import pytest

from fbl_core.oenace import OENACE_SECTIONS, classify_oenace


def test_sections_are_the_21_oenace_sections() -> None:
    assert len(OENACE_SECTIONS) == 21
    assert set(OENACE_SECTIONS) == set("ABCDEFGHIJKLMNOPQRSTU")


@pytest.mark.parametrize(
    ("text", "section"),
    [
        ("Immobilienverwaltung", "L"),
        ("Vermögensverwaltung", "K"),
        ("Holdinggesellschaft", "K"),
        ("Beteiligungen an Unternehmen", "K"),
        ("Gastgewerbe", "I"),
        ("Steuerberatung", "M"),
        ("Unternehmensberatung", "M"),
        ("Entwicklung, Verkauf v. Soft-/Hardware", "J"),
        ("Video- und Fernsehfilmproduktion", "J"),
        ("Tennishallenbetrieb", "R"),
        ("Friseur und Perückenmacher", "S"),
        ("Baumeistergewerbe", "F"),
        ("Lebensmitteleinzelhandel", "G"),
        ("Handel mit Waren aller Art", "G"),
        ("Fleisch und Wurstproduktion und Vertrieb", "C"),
        ("Transport und Spedition", "H"),
        ("Erwerb und Verwaltung von Grundstücken und Baulichkeiten", "L"),
    ],
)
def test_high_confidence_head_maps_correctly(text: str, section: str) -> None:
    g = classify_oenace(text)
    assert g is not None and g.section == section


def test_unclassifiable_returns_none() -> None:
    # No keyword → left for the LLM tail, never a forced guess.
    assert classify_oenace("Helicopter Transporte") is None or classify_oenace("xyzzy") is None
    assert classify_oenace("") is None
    assert classify_oenace(None) is None


def test_multi_activity_is_flagged_medium() -> None:
    g = classify_oenace("Bilanzbuchhaltung, Gastronomie, Handel mit Waren aller Art")
    assert g is not None and g.confidence == "medium"  # spans M + I + G


def test_single_clear_match_is_high() -> None:
    g = classify_oenace("Immobilienverwaltung")
    assert g is not None and g.confidence == "high"
