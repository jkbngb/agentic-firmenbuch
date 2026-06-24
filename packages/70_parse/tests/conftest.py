"""Test bootstrap: expose the local ``builders`` helper and the fixtures path.

With pytest's ``importlib`` import mode the test directory is not on ``sys.path``,
so we add it here to make ``import builders`` work, and provide the shared
``fixtures_dir`` fixture pointing at the repo-root golden fixtures.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

_FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "raw"


@pytest.fixture
def fixtures_dir() -> Path:
    return _FIXTURES
