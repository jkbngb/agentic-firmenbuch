"""Put the test dir on sys.path so ``import orch_fakes`` works under importlib mode."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
