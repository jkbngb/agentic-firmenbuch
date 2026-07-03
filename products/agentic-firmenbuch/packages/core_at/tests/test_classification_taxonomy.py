"""ÖNACE 2025 tree loads, indexes, and powers constrained candidate lists (issue #14)."""

from fbl_core_at.classification.taxonomy import load_oenace_tree


def test_levels_have_expected_counts() -> None:
    t = load_oenace_tree()  # 2025 default
    assert len(t.codes_at(1)) == 22  # sections
    assert len(t.codes_at(2)) == 87  # divisions
    assert len(t.codes_at(3)) == 287  # groups
    assert len(t.codes_at(4)) == 651  # classes
    assert len(t.codes_at(5)) == 711  # national subclasses


def test_2008_vintage_loads() -> None:
    t = load_oenace_tree(2008)  # NACE Rev.2 — the LLM's strongest vintage
    assert len(t.codes_at(1)) == 21  # 2008 had 21 sections (J not yet split)
    assert len(t.codes_at(2)) == 88
    assert len(t.codes_at(3)) == 272
    assert t.section_of("68.3") == "L"  # real estate was section L in 2008


def test_lookup_validation_and_labels() -> None:
    t = load_oenace_tree()
    # ÖNACE 2025 lettering: real estate (68) sits in section M (2008 had it in L).
    assert t.is_valid("68.3") and t.is_valid("M 68.3") and t.is_valid("68")
    assert not t.is_valid("99.9")  # not a real code
    assert t.section_of("68.3") == "M"
    assert "Grundstücks" in (t.title("68.3", "de") or "")
    assert (t.title("68.3", "en") or "").lower().startswith("real estate")


def test_children_give_the_constrained_candidate_list() -> None:
    t = load_oenace_tree()
    # the divisions a constrained prompt would offer once section M (real estate) is fixed
    div = [n.code for n in t.children("M")]
    assert div == ["68"]
    # the groups under division 68 — the next-level candidate list
    groups = {n.code for n in t.children("68")}
    assert {"68.1", "68.2", "68.3"} <= groups
    # normalization: a serve-style code with section letter resolves the same node
    assert t.get("M 68.32") is t.get("68.32")


def test_map_class_is_total_and_unambiguous() -> None:
    """P1 build gate (#34): every 2008 class the LLM may emit maps to exactly one valid
    2025 group. If this fails, the question to the model is too coarse — fix the
    crosswalk extraction, never ship."""
    from fbl_core_at.classification.crosswalk import map_class

    t08, t25 = load_oenace_tree(2008), load_oenace_tree(2025)
    for code in t08.codes_at(4):
        g25 = map_class(code)
        assert g25 is not None, f"2008 class {code} has no 2025 mapping"
        assert t25.is_valid(g25), f"{code}->{g25} is not a valid 2025 group"


def test_map_class_golden_cases() -> None:
    """The v1 failure class (#34): split groups must resolve to the right branch."""
    from fbl_core_at.classification.crosswalk import map_class

    assert map_class("70.22") == "70.2"  # Unternehmensberatung stays consulting (BCG bug)
    assert map_class("70.21") == "73.3"  # PR goes to PR
    assert map_class("47.30") == "47.3"  # petrol stations stay fuel retail (not 35.15!)
    assert map_class("45.20") == "95.3"  # car repair genuinely moved to 95.3
    assert map_class("64.20") == "64.2"  # holdings stay Beteiligungsgesellschaften
    # normalisation: bare digits and subclass suffixes resolve to the same class
    assert map_class("7022") == "70.2"
    assert map_class("70.22-0") == "70.2"
    # unknown code -> None (caller must treat as unclassified, never guess)
    assert map_class("00.00") is None


def test_ambiguous_classes_are_documented() -> None:
    from fbl_core_at.classification.crosswalk import ambiguous_classes, map_class

    amb = ambiguous_classes()
    assert len(amb) > 50  # the official correspondence is genuinely 1:n
    # every ambiguous class is still resolved (rule or manual) — nothing silently open
    for code in amb:
        assert map_class(code) is not None


def test_map_group_legacy_reads_v1_docs() -> None:
    from fbl_core_at.classification.crosswalk import map_group

    assert map_group("68.3") == "68.3"  # identity for stable groups (v1 docs only)
