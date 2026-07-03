"""Deterministic playground backend: intent parsing + guards (Distribution §13)."""

from __future__ import annotations

from datetime import UTC, datetime

from fbl_core.storage import InMemoryCosmosStore
from fbl_mcp_server.playground import parse_intent, playground_request


def test_parse_intent_extracts_german_filters() -> None:
    f, _ = parse_intent("Zeig mir aktive GmbHs in der Steiermark mit Bilanzsumme über 5 Mio. Euro.")
    assert f.bundesland == "Steiermark"
    assert f.legal_form == "GmbH"
    assert f.status == "active"
    assert f.bilanzsumme_min == 5_000_000

    f2, _ = parse_intent("Unternehmen mit hoher Eigenkapitalquote, die seit Jahren wachsen")
    assert f2.equity_ratio_min == 0.40
    assert f2.growth_profile == "growing"

    f3, _ = parse_intent("Firmen mit Eigenkapitalquote über 30%")
    assert f3.equity_ratio_min == 0.30


def test_parse_intent_gf_age_filter() -> None:
    f, _ = parse_intent("Finde Firmen, deren Geschäftsführer über 60 ist")
    assert f.gf_age_min == 60
    f2, _ = parse_intent("GmbHs mit Nachfolge-Thema in Wien")
    assert f2.gf_age_min == 60  # succession default when no explicit age


def test_run_tool_threads_sort_and_name_to_searcher() -> None:
    """The playground LLM tool forwards a ranking sort + name filter to the search layer."""
    from fbl_core_at.models.mcp import SearchFilters, Sort
    from fbl_mcp_server.playground_llm import _run_tool

    seen: dict[str, object] = {}

    def _capture(filters: SearchFilters, sort: Sort | None) -> tuple[int, list[dict[str, object]]]:
        seen["filters"] = filters
        seen["sort"] = sort
        return 1, [{"fnr": "111111a", "name": "Rosenbauer GmbH"}]

    total, rows = _run_tool(
        {"name": "Rosenbauer", "has_guv_latest": True, "sort": {"field": "revenue"}},
        _capture,
        max_results=8,
    )
    assert total == 1 and rows and rows[0]["fnr"] == "111111a"
    assert isinstance(seen["filters"], SearchFilters) and seen["filters"].name == "Rosenbauer"
    assert isinstance(seen["sort"], Sort) and seen["sort"].field == "revenue"
    assert seen["sort"].descending is True  # default: highest first


def test_run_tool_threads_city_and_postal_code_to_searcher() -> None:
    """'größte Firmen aus Graz' must filter by city (a town), not fall back to Bundesland."""
    from fbl_core_at.models.mcp import SearchFilters, Sort
    from fbl_mcp_server.playground_llm import _SEARCH_TOOL, _run_tool

    # The two location filters are advertised to the model (else it can only reach Bundesland).
    props = _SEARCH_TOOL["input_schema"]["properties"]
    assert "city" in props and "postal_code" in props

    seen: dict[str, object] = {}

    def _capture(filters: SearchFilters, sort: Sort | None) -> tuple[int, list[dict[str, object]]]:
        seen["filters"] = filters
        return 1, [{"fnr": "111111a", "name": "Graz GmbH"}]

    _run_tool({"city": "Graz", "postal_code": "80"}, _capture, max_results=8)
    assert isinstance(seen["filters"], SearchFilters)
    assert seen["filters"].city == "Graz"
    assert seen["filters"].postal_code == "80"


def test_run_tool_ignores_malformed_sort() -> None:
    from fbl_core_at.models.mcp import SearchFilters, Sort
    from fbl_mcp_server.playground_llm import _run_tool

    captured: dict[str, object] = {}

    def _capture(filters: SearchFilters, sort: Sort | None) -> tuple[int, list[dict[str, object]]]:
        captured["sort"] = sort
        return 0, []

    # a non-dict / field-less sort is dropped → default ordering (no crash)
    _run_tool({"sort": "revenue"}, _capture, max_results=8)
    assert captured["sort"] is None
    _run_tool({"sort": {}}, _capture, max_results=8)
    assert captured["sort"] is None


def _ok_search(_filters, _sort=None):  # type: ignore[no-untyped-def]
    return 2, [
        {"fnr": "111111a", "name": "Beispiel GmbH"},
        {"fnr": "222222b", "name": "Test GmbH"},
    ]


def _empty_search(_filters, _sort=None):  # type: ignore[no-untyped-def]
    return 0, []


def test_playground_happy_path_returns_results() -> None:
    cosmos = InMemoryCosmosStore()
    status, body = playground_request(
        {"message": "GmbHs in Wien"}, "1.2.3.4", "visitor-1", cosmos, searcher=_ok_search
    )
    assert status == 200
    assert body["mode"] == "deterministic"
    assert len(body["results"]) == 2
    assert body["interpretation"].get("bundesland") == "Wien"


def test_playground_kill_switch_and_validation() -> None:
    cosmos = InMemoryCosmosStore()
    assert playground_request({"message": "x"}, None, "v", cosmos, enabled=False)[0] == 503
    assert playground_request({"message": ""}, None, "v", cosmos)[0] == 400
    assert playground_request({"message": "a" * 600}, None, "v", cosmos)[0] == 400


def test_playground_turnstile_gate() -> None:
    cosmos = InMemoryCosmosStore()
    st, body = playground_request(
        {"message": "GmbHs in Wien", "turnstile_token": "x"},
        "1.1.1.1",
        "v",
        cosmos,
        turnstile_secret="sek",
        turnstile_verifier=lambda tok, ip: False,
        searcher=_ok_search,
    )
    assert st == 400 and body["error"] == "turnstile_failed"


def test_playground_per_visitor_cap() -> None:
    cosmos = InMemoryCosmosStore()
    now = datetime(2026, 6, 18, 12, 0, 0, tzinfo=UTC)
    allowed = 0
    for _ in range(5):
        st, _b = playground_request(
            {"message": "GmbHs in Wien"},
            "1.1.1.1",
            "vcap",
            cosmos,
            per_visitor_day=3,
            searcher=_ok_search,
            now=now,
        )
        if st == 200:
            allowed += 1
    assert allowed == 3  # 4th/5th blocked by the per-visitor daily cap


def test_playground_empty_results_adds_befuellt_note() -> None:
    cosmos = InMemoryCosmosStore()
    st, body = playground_request(
        {"message": "GmbHs in Wien"}, "1.1.1.1", "v", cosmos, searcher=_empty_search
    )
    assert st == 200 and any("befüllt" in n for n in body["notes"])


# --- LLM mode (Distribution §13) -----------------------------------------------------------


def test_playground_llm_no_key_falls_back_to_deterministic() -> None:
    cosmos = InMemoryCosmosStore()
    status, body = playground_request(
        {"message": "GmbHs in Wien"},
        None,
        "v",
        cosmos,
        searcher=_ok_search,
        llm_enabled=True,
        anthropic_api_key=None,  # enabled but no key → deterministic
    )
    assert status == 200 and body["mode"] == "deterministic"


def test_playground_llm_mode_uses_llm_answer(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import fbl_mcp_server.playground_llm as pll

    def fake_llm_answer(message, searcher, **kw):  # type: ignore[no-untyped-def]
        _total, rows = searcher(None)
        return {
            "mode": "llm",
            "summary": "2 Firmen.",
            "results": rows,
            "interpretation": {},
        }

    monkeypatch.setattr(pll, "llm_answer", fake_llm_answer)
    cosmos = InMemoryCosmosStore()
    status, body = playground_request(
        {"message": "x"},
        None,
        "v",
        cosmos,
        searcher=_ok_search,
        llm_enabled=True,
        anthropic_api_key="sk-test",
    )
    assert status == 200 and body["mode"] == "llm"
    assert body["summary"] == "2 Firmen." and len(body["results"]) == 2


def test_playground_llm_failure_falls_back(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import fbl_mcp_server.playground_llm as pll

    def boom(*a, **k):  # type: ignore[no-untyped-def]
        raise RuntimeError("api down")

    monkeypatch.setattr(pll, "llm_answer", boom)
    cosmos = InMemoryCosmosStore()
    status, body = playground_request(
        {"message": "GmbHs in Wien"},
        None,
        "v",
        cosmos,
        searcher=_ok_search,
        llm_enabled=True,
        anthropic_api_key="sk-test",
    )
    assert status == 200 and body["mode"] == "deterministic"


def test_llm_answer_runs_tool_loop_and_summarizes(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import anthropic

    from fbl_mcp_server import playground_llm

    class _Block:
        def __init__(self, **kw: object) -> None:
            self.__dict__.update(kw)

    class _Resp:
        def __init__(self, stop_reason: str, content: list[_Block]) -> None:
            self.stop_reason = stop_reason
            self.content = content

    calls = {"n": 0}

    class _Messages:
        def create(self, **kw: object) -> _Resp:
            calls["n"] += 1
            if calls["n"] == 1:
                tool = _Block(type="tool_use", id="t1", input={"bundesland": "Wien"})
                return _Resp("tool_use", [tool])
            return _Resp("end_turn", [_Block(type="text", text="2 Firmen in Wien.")])

    class _Client:
        def __init__(self, **kw: object) -> None:
            self.messages = _Messages()

    monkeypatch.setattr(anthropic, "Anthropic", _Client)
    out = playground_llm.llm_answer(
        "GmbHs in Wien", _ok_search, api_key="k", model="m", max_tokens=100, max_results=8
    )
    assert out["mode"] == "llm" and out["summary"] == "2 Firmen in Wien."
    assert len(out["results"]) == 2 and out["interpretation"]["bundesland"] == "Wien"


def test_llm_tool_result_includes_total_matches_so_summary_can_be_accurate(  # type: ignore[no-untyped-def]
    monkeypatch,
) -> None:
    """Regression: 1.437 matches must not summarise as '8 GmbHs' just because we cap rows.

    The tool_result fed back to Claude carries ``total_matches`` (full count) AND ``shown``
    (capped preview length) so the model reports the real number, not the preview length.
    """
    import json as _json
    from typing import cast

    import anthropic

    from fbl_mcp_server import playground_llm

    def _searcher(_f, _s=None):  # type: ignore[no-untyped-def]
        return 1437, [{"fnr": f"{i:06d}a", "name": f"GmbH {i}"} for i in range(8)]

    class _B:
        def __init__(self, **kw: object) -> None:
            self.__dict__.update(kw)

    captured_tool_content: dict[str, object] = {}

    class _Msgs:
        n = 0

        def create(self, **kw: object):  # type: ignore[no-untyped-def]
            self.n += 1
            if self.n == 1:
                return _B(stop_reason="tool_use", content=[_B(type="tool_use", id="t", input={})])
            # capture the tool_result content the model now sees on round 2
            msgs = cast("list[dict[str, object]]", kw.get("messages") or [])
            for m in reversed(msgs):
                if m.get("role") == "user":
                    for blk in cast("list[dict[str, object]]", m.get("content", [])):
                        if isinstance(blk, dict) and blk.get("type") == "tool_result":
                            captured_tool_content.update(_json.loads(str(blk["content"])))
                    break
            return _B(stop_reason="end_turn", content=[_B(type="text", text="rund 1.437 GmbHs.")])

    class _Client:
        def __init__(self, **kw: object) -> None:
            self.messages = _Msgs()

    monkeypatch.setattr(anthropic, "Anthropic", _Client)
    out = playground_llm.llm_answer(
        "GmbHs in Steiermark > 5M, EK > 30%",
        _searcher,
        api_key="k",
        model="m",
        max_tokens=100,
        max_results=8,
    )
    assert captured_tool_content.get("total_matches") == 1437
    assert captured_tool_content.get("shown") == 8
    assert out["summary"] == "rund 1.437 GmbHs."
    # The UI also needs the total to render "Top 8 von 1.437 Treffern" honestly.
    assert out["total_matches"] == 1437
