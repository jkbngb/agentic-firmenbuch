"""Format detection on real sample XMLs + FNR normalization (§8.2 DoD)."""

from __future__ import annotations

from pathlib import Path

import pytest

from fbl_core_at.formats import detect_xml_variant_bytes
from fbl_firmenbuch_client import normalize_fnr

FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "raw"


def test_format_detection_on_real_legacy_fixtures() -> None:
    for path in [
        FIXTURES / "030435h_2020-03-31_jb.xml",
        FIXTURES / "030636d_2023-05-31_jb.xml",
        FIXTURES / "490875a_multiyear" / "490875a_2024-12-31.xml",
    ]:
        assert detect_xml_variant_bytes(path.read_bytes()) == "legacy_finanzonline"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("030435h", "030435h"),
        ("30435 h", "030435h"),
        ("30435h", "030435h"),
        ("433826f", "433826f"),
        ("093450 b", "093450b"),
        ("  490875a ", "490875a"),
    ],
)
def test_normalize_fnr(raw: str, expected: str) -> None:
    assert normalize_fnr(raw) == expected
