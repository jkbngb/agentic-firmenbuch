"""Change-feed delta detection (§8.3, §15a.2).

The change feeds are live-confirmed (§16), so this is the active delta branch. A
status change alone (e.g. Löschung) marks a company dirty for a cheap re-`present`,
even without a new filing.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import date, timedelta

from fbl_core.logging import get_logger
from fbl_firmenbuch_client import RegisterSource
from fbl_registry import Registry

from .enumerate import DEFAULT_RECHTSFORMEN
from .models import DeltaReport

logger = get_logger("ingest.delta")

# One registry write per UNIQUE company, not per feed entry: a single firm can appear many
# times in a day's feeds (several amendments + several new documents). We collapse to one
# action per FNR, strongest reason wins, so a "full rebuild" change is never downgraded to a
# cheap status refresh. Higher number = higher priority.
_REASON_PRIORITY = {
    "status_change": 0,  # Löschung only → cheap re-present
    "register_change": 1,
    "new_filing": 2,
    "new_registration": 3,
}
_HEARTBEAT_EVERY = 100  # renew the run lock + log progress every N companies written


def detect_changes(
    source: RegisterSource,
    registry: Registry,
    von: date,
    bis: date,
    *,
    run_id: str,
    rechtsformen: tuple[str, ...] = DEFAULT_RECHTSFORMEN,
    heartbeat: Callable[[], bool] | None = None,
) -> DeltaReport:
    """Read both change feeds, update the registry, and return the dirty set.

    The firma feed is queried **per Rechtsform** (GES, AG, KG, …): an empty-Rechtsform request
    tries to return the whole register's changes in one response and never comes back (the same
    limit the search API has). The urkunden feed is form-agnostic, so it stays a single call.
    We collapse to one write per unique FNR (a firm appears in many entries), and heartbeat the
    run lock + log progress throughout — otherwise a long detection phase silently outlives the
    30-min lease (the grind/ingest loops already heartbeat; detection did not, which hung the
    first live runs).
    """
    report = DeltaReport(run_id=run_id)
    reasons: dict[str, str] = {}
    deleted: set[str] = set()
    # The change-feed API rejects any window > 7 days ("Der Zeitraum darf 7 Tage nicht
    # überschreiten"), so a catch-up after an outage MUST be sliced. 7-calendar-day windows.
    windows = list(_date_windows(von, bis, max_days=7))

    def _bump(fnr: str, reason: str) -> None:
        cur = reasons.get(fnr)
        if cur is None or _REASON_PRIORITY[reason] > _REASON_PRIORITY[cur]:
            reasons[fnr] = reason

    # --- 1) Register changes, ONE call per Rechtsform per ≤7-day window (never empty-form) ----
    for rf in rechtsformen:
        for w_von, w_bis in windows:
            changes = source.veraenderungen_firma(w_von, w_bis, rechtsform=rf)
            logger.info(
                "change feed firma",
                extra={
                    "context": {
                        "run_id": run_id,
                        "rechtsform": rf,
                        "von": w_von.isoformat(),
                        "count": len(changes),
                    }
                },
            )
            for ch in changes:
                if not ch.fnr:
                    continue
                if ch.kind == "Neueintragung":
                    _bump(ch.fnr, "new_registration")
                elif ch.kind == "Löschung":
                    deleted.add(ch.fnr)
                    _bump(ch.fnr, "status_change")
                else:  # Änderung / other register change
                    _bump(ch.fnr, "register_change")
            if heartbeat is not None:
                heartbeat()

    # --- 2) Document changes, one form-agnostic call per ≤7-day window ----------------------
    for w_von, w_bis in windows:
        docs = source.veraenderungen_urkunden(w_von, w_bis)
        logger.info(
            "change feed urkunden",
            extra={"context": {"run_id": run_id, "count": len(docs), "von": w_von.isoformat()}},
        )
        for dc in docs:
            if not dc.fnr:
                continue
            _bump(dc.fnr, "new_filing")

    # Counts derived from the deduped set (so per-form duplicates don't inflate them).
    report.new_companies = sum(1 for r in reasons.values() if r == "new_registration")
    report.doc_changes = sum(1 for r in reasons.values() if r == "new_filing")
    report.status_changes = len(deleted)

    # --- 3) Apply once per FNR, heartbeating + logging progress ----------------------------
    total = len(reasons)
    logger.info("applying change set", extra={"context": {"run_id": run_id, "unique_fnrs": total}})
    for i, fnr in enumerate(sorted(reasons), start=1):
        registry.ensure(fnr, source="change_feed")
        if fnr in deleted:
            registry.set_status(fnr, "deleted")
        registry.mark_dirty(fnr, reason=reasons[fnr])
        if i % _HEARTBEAT_EVERY == 0:
            if heartbeat is not None:
                heartbeat()
            logger.info(
                "detect_changes progress",
                extra={"context": {"run_id": run_id, "written": i, "total": total}},
            )

    report.dirty_fnrs = sorted(reasons)
    return report


def _date_windows(von: date, bis: date, *, max_days: int = 7) -> Iterator[tuple[date, date]]:
    """Slice ``[von, bis]`` (inclusive) into consecutive windows of at most ``max_days`` calendar
    days, because the change-feed API rejects longer spans. A normal daily run (≤3-day lookback)
    yields one window; a post-outage catch-up yields several."""
    if bis < von:
        return
    cur = von
    while cur <= bis:
        end = min(cur + timedelta(days=max_days - 1), bis)
        yield cur, end
        cur = end + timedelta(days=1)
