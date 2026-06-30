"""ÖNACE 2025 tree loads, indexes, and powers constrained candidate lists (issue #14)."""

from fbl_core.classification.taxonomy import load_oenace_tree


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


def test_official_crosswalk_2008_to_2025() -> None:
    from fbl_core.classification.crosswalk import changed_groups, map_group

    # identity for an unchanged group, and a deterministic map for a re-coded one
    assert map_group("68.3") == "68.3"  # real estate group stable across vintages
    changed = changed_groups()
    assert 30 < len(changed) < 60  # 41 groups were re-coded 2008->2025
    # every mapped target is itself a real 2025 group
    t = load_oenace_tree(2025)
    for src, dst in list(changed.items())[:50]:
        assert t.is_valid(dst), f"{src}->{dst} not a valid 2025 code"
