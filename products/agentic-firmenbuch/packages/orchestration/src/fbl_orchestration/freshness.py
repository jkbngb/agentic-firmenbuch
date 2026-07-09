"""Freshness watchdog — alert when the served data or the change-feed have gone stale.

This is the admin-visibility guard that was missing when the daily pipeline silently froze:
the change-feed watermark stopped advancing (its writer was killed before it ran), so the
served ``10_presentation`` layer aged for weeks while the jobs still reported ``Succeeded``.

It reads two independent signals — cheap, read-only over Cosmos:

* **Change-feed watermark age** (``99_registry/__watermark__.updated_at``): how long ago the
  daily delta last advanced its feed read position. If this stops moving, no new changes are
  being detected — the exact failure we hit.
* **Newest served ``built_at``** (``MAX(c.provenance.built_at)`` over ``10_presentation``): how
  long ago ANY served company was last (re)built. A healthy pipeline rebuilds thousands of docs
  a day, so the newest built_at is always ~today.

If either is older than ``max_age_hours`` it ALERTS by email (reusing the same ACS sender the
directories sync uses) and logs an ERROR. It exits 0 either way: a stale-data signal is not a
job failure, and returning non-zero would just add noise to Azure's job-status view. The alert
is the product of the check, not its exit code.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from fbl_core.logging import get_logger
from fbl_core.storage import CosmosStoreLike

log = get_logger("orchestration.freshness")

REGISTRY = "99_registry"
PRESENTED = "10_presentation"
WATERMARK_ID = "__watermark__"


def _parse_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def check_freshness(
    cosmos: CosmosStoreLike,
    *,
    alert: Callable[[str, str], None],
    max_age_hours: float = 26.0,
    now: datetime | None = None,
) -> int:
    """Check watermark + served-data freshness; email ``alert`` on staleness. Always returns 0.

    ``max_age_hours`` defaults to 26h: the daily job runs every 24h, so >26h means at least one
    run's worth of freshness was missed (with a couple of hours slack for a long run).
    """
    now = now or datetime.now(UTC)
    limit = timedelta(hours=max_age_hours)

    wm = cosmos.get(REGISTRY, WATERMARK_ID) or {}
    wm_updated = _parse_ts(wm.get("updated_at"))
    newest_built = next(
        iter(cosmos.query(PRESENTED, "SELECT VALUE MAX(c.provenance.built_at) FROM c", [])), None
    )
    built_at = _parse_ts(newest_built if isinstance(newest_built, str) else None)

    problems: list[str] = []
    if wm_updated is None or now - wm_updated > limit:
        problems.append(
            f"Der Change-Feed-Watermark wurde zuletzt am {wm.get('updated_at', 'nie')} "
            f"fortgeschrieben (Schwelle: {max_age_hours:.0f}h). Der taegliche Delta-Lauf "
            "erkennt keine Aenderungen mehr."
        )
    if built_at is None or now - built_at > limit:
        problems.append(
            f"Der neueste servierte built_at ist {newest_built} (Schwelle: {max_age_hours:.0f}h). "
            "Der Presentation-Layer wird nicht mehr neu gebaut."
        )

    if not problems:
        log.info(
            "freshness ok",
            extra={
                "context": {
                    "watermark_updated_at": wm.get("updated_at"),
                    "newest_built_at": newest_built,
                }
            },
        )
        return 0

    body = (
        "Automatischer Freshness-Alarm der Firmenbuch-Pipeline.\n\n"
        + "\n".join(f"- {p}" for p in problems)
        + "\n\nBitte den Job job-firmenbuch-daily und den Watermark (99_registry/__watermark__) "
        "pruefen. Diagnose-Runbook: docs/ (Pipeline-Freshness)."
    )
    log.error(
        "freshness check failed",
        extra={
            "context": {
                "problems": problems,
                "watermark_updated_at": wm.get("updated_at"),
                "newest_built_at": newest_built,
            }
        },
    )
    alert("Firmenbuch: Daten veraltet (Freshness-Alarm)", body)
    return 0
