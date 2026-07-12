"""Search-quality eval harness (T13). Replays golden search intents and scores the results.

Tier 1 (default, ~free): direct ``search_companies`` tool-call replay, no LLM.
  --ci        against the deterministic in-memory fixtures (fast, reproducible; CI uses this).
  --cosmos-endpoint URL   in-process against the LIVE Cosmos (RU cents).
Reports top-1 accuracy, recall@25, and p50 latency as a markdown table.

Tier 2 (optional, budget-capped): end-to-end LLM loop measuring ROUNDS per intent.
  --tier2 --model claude-haiku-4-5-20251001 --max-queries 20
  Needs ANTHROPIC_API_KEY; prints the token bill. Haiku over ~20 intents ≈ <1-2 €.

Usage:
    uv run python scripts/eval_search.py --ci
    COSMOS_ENDPOINT=... uv run python scripts/eval_search.py --cosmos-endpoint $COSMOS_ENDPOINT
    uv run python scripts/eval_search.py --ci --tier2 --max-queries 10
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from fbl_core_at.models import SearchFilters, Sort

_GOLDEN = (
    Path(__file__).resolve().parents[1]
    / "products/agentic-firmenbuch/tests/eval/golden_search.yaml"
)


@dataclass
class CaseResult:
    id: str
    intent: str
    passed: bool
    latency_ms: float
    detail: str = ""
    # accuracy signals (None when the case doesn't test that dimension)
    top1_ok: bool | None = None
    recall_at_25: float | None = None


@dataclass
class Report:
    cases: list[CaseResult] = field(default_factory=list)

    def markdown(self) -> str:
        lines = ["| case | pass | top1 | recall@25 | ms |", "|---|---|---|---|---|"]
        for c in self.cases:
            t1 = "" if c.top1_ok is None else ("✓" if c.top1_ok else "✗")
            rc = "" if c.recall_at_25 is None else f"{c.recall_at_25:.2f}"
            lines.append(
                f"| {c.id} | {'✓' if c.passed else '✗'} | {t1} | {rc} | {c.latency_ms:.1f} |"
            )
        top1 = [c.top1_ok for c in self.cases if c.top1_ok is not None]
        recalls = [c.recall_at_25 for c in self.cases if c.recall_at_25 is not None]
        lat = sorted(c.latency_ms for c in self.cases)
        p50 = lat[len(lat) // 2] if lat else 0.0
        passed = sum(c.passed for c in self.cases)
        lines += [
            "",
            f"**{passed}/{len(self.cases)} cases pass** · "
            f"top-1 accuracy {sum(top1) / len(top1) * 100:.0f}% ({len(top1)} cases) · "
            f"recall@25 {sum(recalls) / len(recalls) * 100:.0f}% ({len(recalls)} cases) · "
            f"p50 {p50:.1f} ms",
        ]
        return "\n".join(lines)

    @property
    def all_passed(self) -> bool:
        return all(c.passed for c in self.cases)


def _call_args(call: dict[str, Any]) -> tuple[SearchFilters, Sort | None]:
    filters = SearchFilters(**call.get("filters", {}))
    sort = Sort(**call["sort"]) if "sort" in call else None
    return filters, sort


def _check(resp: dict[str, Any], g: dict[str, Any]) -> tuple[bool, str, bool | None, float | None]:
    results = resp.get("results", [])
    fnrs = [r.get("fnr") for r in results]
    problems: list[str] = []
    top1_ok: bool | None = None
    recall: float | None = None

    if "expect_total" in g and resp.get("total") != g["expect_total"]:
        problems.append(f"total {resp.get('total')} != {g['expect_total']}")
    if "expect_top1_fnr" in g:
        top1_ok = bool(fnrs) and fnrs[0] == g["expect_top1_fnr"]
        if not top1_ok:
            problems.append(f"top1 {fnrs[:1]} != [{g['expect_top1_fnr']}]")
    if "expect_fnrs_in_top25" in g:
        want = g["expect_fnrs_in_top25"]
        found = [f for f in want if f in fnrs]
        recall = len(found) / len(want) if want else 1.0
        if recall < 1.0:
            problems.append(f"missing {[f for f in want if f not in fnrs]}")
    if "expect_fnrs_not_present" in g:
        leaked = [f for f in g["expect_fnrs_not_present"] if f in fnrs]
        if leaked:
            problems.append(f"should be absent: {leaked}")
    if "expect_relaxation_for" in g:
        dropped = {r.get("dropped") for r in (resp.get("relaxations") or [])}
        missing = [d for d in g["expect_relaxation_for"] if d not in dropped]
        if missing:
            problems.append(f"no relaxation for {missing} (got {sorted(dropped)})")
    return (not problems), "; ".join(problems), top1_ok, recall


def run_tier1(svc: Any, token: str, goldens: list[dict[str, Any]]) -> Report:
    report = Report()
    for g in goldens:
        filters, sort = _call_args(g["call"])
        t0 = time.perf_counter()
        try:
            resp = svc.search_companies(token, filters, sort, page_size=25)
            dt = (time.perf_counter() - t0) * 1000
            passed, detail, top1, recall = _check(resp, g)
        except Exception as exc:  # a raised BadRequest etc. is a failed expectation here
            dt = (time.perf_counter() - t0) * 1000
            passed, detail, top1, recall = False, f"error: {exc}", None, None
        report.cases.append(CaseResult(g["id"], g["intent"], passed, dt, detail, top1, recall))
    return report


def _ci_service() -> tuple[Any, str]:
    from fbl_auth import signup
    from fbl_core.config import Settings
    from fbl_mcp_server import McpService

    sys.path.insert(0, str(_GOLDEN.parent))
    import fixtures  # type: ignore[import-not-found]

    store = fixtures.build_store()
    token = signup("eval@example.test", store, tier="pro").token
    return McpService(store, Settings(rate_limit_per_min=100000, rate_limit_per_day=1000000)), token


def _live_service(endpoint: str) -> tuple[Any, str]:
    from azure.identity import DefaultAzureCredential

    from fbl_auth import signup
    from fbl_core.config import Settings
    from fbl_core.storage import CosmosStore
    from fbl_mcp_server import McpService

    store = CosmosStore(endpoint, credential=DefaultAzureCredential())
    token = signup("eval-live@example.test", store, tier="pro").token
    return McpService(store, Settings(rate_limit_per_min=100000, rate_limit_per_day=1000000)), token


def main() -> None:
    parser = argparse.ArgumentParser(description="Search-quality eval harness (T13).")
    parser.add_argument("--ci", action="store_true", help="run against in-memory fixtures")
    parser.add_argument("--cosmos-endpoint", help="run in-process against a live Cosmos endpoint")
    parser.add_argument(
        "--tier2", action="store_true", help="also run the LLM rounds-per-intent loop"
    )
    parser.add_argument("--model", default="claude-haiku-4-5-20251001")
    parser.add_argument("--max-queries", type=int, default=20)
    args = parser.parse_args()

    goldens = yaml.safe_load(_GOLDEN.read_text(encoding="utf-8"))

    if args.cosmos_endpoint:
        svc, token = _live_service(args.cosmos_endpoint)
        mode = "live"
    else:
        svc, token = _ci_service()
        mode = "ci"
    report = run_tier1(svc, token, goldens)
    print(f"## Tier 1 ({mode}) — {len(goldens)} goldens\n")
    print(report.markdown())

    if args.tier2:
        from eval_tier2 import run_tier2  # type: ignore[import-not-found]

        sys.path.insert(0, str(Path(__file__).resolve().parent))
        print("\n## Tier 2 (LLM rounds per intent)\n")
        print(run_tier2(svc, token, goldens[: args.max_queries], model=args.model))

    if mode == "ci" and not report.all_passed:
        sys.exit(1)  # CI gate: fixtures are deterministic, every golden must pass


if __name__ == "__main__":
    main()
