"""Run the tier-1 eval harness against the in-memory fixtures as part of CI (T13).

Deterministic: every golden must pass, and top-1 accuracy + recall@25 must be perfect on the
curated fixtures. This is what gates T14 — a regression in search quality fails the build here.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

from fbl_auth import signup
from fbl_core.config import Settings
from fbl_mcp_server import McpService

_REPO_ROOT = Path(__file__).resolve().parents[4]  # …/agentic-firmenbuch (repo root)
sys.path.insert(0, str(Path(__file__).resolve().parent))  # tests/eval → fixtures
sys.path.insert(0, str(_REPO_ROOT / "scripts"))  # eval_search runner

import fixtures  # noqa: E402
from eval_search import run_tier1  # type: ignore[import-not-found]  # noqa: E402

_GOLDEN = Path(__file__).resolve().parent / "golden_search.yaml"


def _svc() -> tuple[McpService, str]:
    store = fixtures.build_store()
    token = signup("eval@example.test", store, tier="pro").token
    return McpService(store, Settings(rate_limit_per_min=100000, rate_limit_per_day=1000000)), token


def test_all_goldens_pass() -> None:
    goldens = yaml.safe_load(_GOLDEN.read_text(encoding="utf-8"))
    assert len(goldens) >= 30  # coverage floor (T13 acceptance)
    svc, token = _svc()
    report = run_tier1(svc, token, goldens)
    failures = [f"{c.id}: {c.detail}" for c in report.cases if not c.passed]
    assert not failures, "eval goldens failed:\n" + "\n".join(failures)


def test_ranking_quality_is_perfect_on_fixtures() -> None:
    goldens = yaml.safe_load(_GOLDEN.read_text(encoding="utf-8"))
    svc, token = _svc()
    report = run_tier1(svc, token, goldens)
    top1 = [c.top1_ok for c in report.cases if c.top1_ok is not None]
    recalls = [c.recall_at_25 for c in report.cases if c.recall_at_25 is not None]
    assert all(top1) and top1  # 100% top-1 on the curated set
    assert recalls and min(recalls) == 1.0  # 100% recall@25
