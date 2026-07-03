"""Fixtures path + example loaders for derive tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

_EXAMPLES = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "consolidated_examples"


@pytest.fixture
def grama() -> dict[str, Any]:
    return json.loads((_EXAMPLES / "grama_trade_032616s.json").read_text())


@pytest.fixture
def schubert() -> dict[str, Any]:
    return json.loads((_EXAMPLES / "schubert_093450b.json").read_text())
