"""Variant-detection tests (§8.4, §15b-1)."""

from __future__ import annotations

from pathlib import Path

from builders import firmenbuch_2025_xml, jab40_xml
from lxml import etree

from fbl_parse import detect_variant


def _root(data: bytes) -> etree._Element:
    return etree.fromstring(data)


def test_real_fixtures_are_legacy(fixtures_dir: Path) -> None:
    for path in [
        fixtures_dir / "030435h_2020-03-31_jb.xml",
        fixtures_dir / "030636d_2023-05-31_jb.xml",
        fixtures_dir / "490875a_multiyear" / "490875a_2024-12-31.xml",
    ]:
        assert detect_variant(_root(path.read_bytes())) == "legacy_finanzonline"


def test_firmenbuch_2025_detected_by_geschaeftsjahr() -> None:
    assert detect_variant(_root(firmenbuch_2025_xml())) == "firmenbuch_2025"


def test_jab40_detected_by_namespace() -> None:
    assert detect_variant(_root(jab40_xml())) == "jab40_semantic"
