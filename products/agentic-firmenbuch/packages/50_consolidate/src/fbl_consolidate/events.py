"""Derive register events (Vollzüge) from the daily change-feed delta (issue #16).

The HVD ``auszug`` Kurzinformation on our tier returns a company's *current* master data but
**not** the historical ``VOLLZ`` change log, so ``events[]`` was always empty. Instead we DERIVE
events: every daily delta re-fetches a changed company's master data, and comparing it against the
``event_baseline`` captured at the previous consolidation surfaces exactly what changed — name,
seat, legal form, management, or capital. Each derived event carries ``source="change_feed_delta"``
and the run date.

Two safeguards make this safe to switch on:

* **No baseline yet → emit nothing, just record one.** The first time a company is consolidated
  after this feature ships, there is no prior baseline to diff against, so we establish the
  baseline and emit no event. Only the *next* observed change produces events. This prevents a
  spurious flood on first run (where the prior consolidated identity may differ from the master for
  benign provenance reasons).
* **Start-date floor.** No derived event predates :data:`EVENTS_START` (the documented go-live), so
  the event history has a clean, single origin date regardless of when the code was deployed.
"""

from __future__ import annotations

from typing import cast

from fbl_core_at.models import ConsolidatedCompany, Manager, MasterData, RegisterEvent

# Documented go-live for the derived-events history (issue #16). No event is emitted with a date
# before this, so the dataset has one clean origin date.
EVENTS_START = "2026-07-01"

SOURCE = "change_feed_delta"


def _person_sig(p: Manager) -> str:
    """A stable per-person key for management-change detection (role + identity + birth year)."""
    return "|".join(
        str(x) for x in (p.role_label, p.last_name, p.first_name, p.birth_year, p.vertretung)
    )


def master_signature(master: MasterData | None) -> dict[str, object] | None:
    """The comparable snapshot of the master fields whose change is a register event.

    Returns ``None`` when there is no master to snapshot (so the caller establishes no baseline).
    """
    if master is None:
        return None
    loc = master.location
    return {
        "name": master.name,
        "legal_form": master.legal_form,
        "city": loc.city if loc else None,
        "postal_code": loc.postal_code if loc else None,
        "street": loc.street if loc else None,
        "stammkapital": master.stammkapital.amount if master.stammkapital else None,
        # Order-independent set of signatory keys: add/remove/role change all flip this.
        "signatories": sorted(_person_sig(p) for p in master.persons),
    }


def _fmt_person(sig: str) -> str:
    """Render a signatory signature (``role|last|first|birth|vertretung``) as "ROLE First Last"."""
    parts = [p if p not in ("", "None") else "" for p in sig.split("|")]
    role = parts[0] if parts else ""
    last = parts[1] if len(parts) > 1 else ""
    first = parts[2] if len(parts) > 2 else ""
    name = " ".join(p for p in (first, last) if p)
    return " ".join(p for p in (role, name) if p) or sig


def _num(x: object) -> float | None:
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def _diff_to_events(
    prev: dict[str, object], cur: dict[str, object], today: str
) -> list[RegisterEvent]:
    out: list[RegisterEvent] = []

    def ev(type_: str, desc: str, **extra: object) -> RegisterEvent:
        return RegisterEvent(date=today, type=type_, description=desc, source=SOURCE, **extra)  # type: ignore[arg-type]

    if prev.get("name") != cur.get("name"):
        out.append(ev("name_change", f"vormals: {prev.get('name')}"))
    if prev.get("legal_form") != cur.get("legal_form"):
        out.append(ev("legal_form_change", f"vormals: {prev.get('legal_form')}"))
    if any(prev.get(k) != cur.get(k) for k in ("city", "postal_code", "street")):
        where = ", ".join(str(cur.get(k)) for k in ("postal_code", "city") if cur.get(k))
        out.append(
            ev("seat_change", f"neue Anschrift: {where}" if where else "Sitz/Anschrift geändert")
        )
    if prev.get("stammkapital") != cur.get("stammkapital"):
        cf, ct = prev.get("stammkapital"), cur.get("stammkapital")
        out.append(ev("capital_change", f"{cf} → {ct}", capital_from=_num(cf), capital_to=_num(ct)))
    if prev.get("signatories") != cur.get("signatories"):
        prev_list = cast("list[str]", prev.get("signatories") or [])
        cur_list = cast("list[str]", cur.get("signatories") or [])
        prev_s, cur_s = set(prev_list), set(cur_list)
        added = [_fmt_person(s) for s in cur_list if s not in prev_s]
        removed = [_fmt_person(s) for s in prev_list if s not in cur_s]
        bits = []
        if added:
            bits.append("bestellt: " + "; ".join(added))
        if removed:
            bits.append("gelöscht: " + "; ".join(removed))
        out.append(
            ev(
                "management_change",
                " | ".join(bits) if bits else "Vertretungsbefugte Organe geändert",
                managers_added=added,
                managers_removed=removed,
            )
        )
    return out


def derive_register_events(
    prev: ConsolidatedCompany | None,
    master: MasterData | None,
    *,
    today: str | None,
    start: str = EVENTS_START,
) -> tuple[list[RegisterEvent], dict[str, object] | None]:
    """Return ``(events_for_the_new_doc, new_baseline)``.

    ``events`` is the prior event history with any freshly-derived events appended (deduped by
    date+type+description). ``new_baseline`` is the master snapshot to persist on the new doc.
    ``today is None`` (bulk backfill) derives nothing — it just refreshes the baseline and carries
    the existing history forward.
    """
    new_sig = master_signature(master)
    prior_events = list(prev.events) if prev else []
    prev_sig = prev.event_baseline if prev else None

    # Carry forward the literal auszug VOLLZ entries (rare on this tier) once, tagged.
    if not prior_events and master and master.events:
        prior_events = [
            e.model_copy(update={"source": e.source or "auszug"}) for e in master.events
        ]

    # No baseline to diff against yet, bulk backfill (today is None), before go-live, or nothing
    # changed: establish/refresh the baseline (if we have one) and emit nothing.
    if prev_sig is None or new_sig is None or today is None or today < start or new_sig == prev_sig:
        return prior_events, (new_sig if new_sig is not None else prev_sig)

    derived = _diff_to_events(prev_sig, new_sig, today)
    seen = {(e.date, e.type, e.description) for e in prior_events}
    fresh = [e for e in derived if (e.date, e.type, e.description) not in seen]
    return prior_events + fresh, new_sig
