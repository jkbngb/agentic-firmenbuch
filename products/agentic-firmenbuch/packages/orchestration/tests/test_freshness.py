"""Unit tests for the freshness watchdog (change-feed / served-data staleness alert)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fbl_orchestration.freshness import check_freshness


class _FakeCosmos:
    """Minimal cosmos double: a fixed watermark doc + a fixed MAX(built_at) for the query."""

    def __init__(self, *, watermark_updated: str | None, newest_built: str | None) -> None:
        self._wm = (
            {"id": "__watermark__", "updated_at": watermark_updated} if watermark_updated else {}
        )
        self._newest = newest_built

    def get(self, container: str, doc_id: str) -> dict[str, Any] | None:
        return self._wm or None

    def query(self, container: str, sql: str, params: list[dict[str, Any]]) -> list[Any]:
        return [self._newest]  # the SELECT VALUE MAX(...) result


NOW = datetime(2026, 7, 9, 12, 0, tzinfo=UTC)


def test_fresh_data_does_not_alert() -> None:
    alerts: list[tuple[str, str]] = []
    cosmos = _FakeCosmos(
        watermark_updated="2026-07-09T03:05:00Z", newest_built="2026-07-09T07:00:00Z"
    )
    rc = check_freshness(cosmos, alert=lambda s, b: alerts.append((s, b)), now=NOW)  # type: ignore[arg-type]
    assert rc == 0
    assert alerts == []  # everything recent -> silent


def test_stale_watermark_alerts() -> None:
    alerts: list[tuple[str, str]] = []
    # watermark 3+ weeks old (the exact prod incident), served data also stale
    cosmos = _FakeCosmos(
        watermark_updated="2026-06-24T03:58:27Z", newest_built="2026-06-22T21:00:00Z"
    )
    rc = check_freshness(cosmos, alert=lambda s, b: alerts.append((s, b)), now=NOW)  # type: ignore[arg-type]
    assert rc == 0  # a staleness signal is not a job failure
    assert len(alerts) == 1
    assert "veraltet" in alerts[0][0].lower()
    assert "Watermark" in alerts[0][1] and "built_at" in alerts[0][1]


def test_missing_watermark_alerts() -> None:
    alerts: list[tuple[str, str]] = []
    cosmos = _FakeCosmos(watermark_updated=None, newest_built="2026-07-09T07:00:00Z")
    check_freshness(cosmos, alert=lambda s, b: alerts.append((s, b)), now=NOW)  # type: ignore[arg-type]
    assert len(alerts) == 1  # no watermark at all -> alert
