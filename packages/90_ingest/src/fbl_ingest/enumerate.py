"""Universe enumeration via the ``sucheFirma`` prefix-walk (§15a.1, §15b-17..20).

The completeness-safe seed is the data.gv.at HVD **bulk dataset** (`bulk.py`); this
prefix-walk is the proven fallback that needs no bulk file. Hardened against silently
missing companies:

* **Iterative + resumable** — an explicit frontier with a ``done`` set and a ``seen``
  set, persisted via a :class:`Checkpoint` so a crashed multi-hour sweep resumes
  instead of restarting (ports the prototype's checkpoint pattern).
* **Deep enough** — ``MAX_PREFIX_DEPTH = 20`` (depth 6 truncates dense Austrian
  prefixes like ``immobilien``/``betriebs`` that stay over the 1000 cap for many
  characters).
* **Exhaustive split alphabet** — every character that can appear in an Austrian
  Firmenwortlaut, UNIONed with the characters actually observed at each split point
  (so even an exotic char beyond the static set is still followed). Input is treated
  case-insensitively (Firmenbuch search is case-insensitive; we walk lowercase
  prefixes). A space guard forbids a **leading** space (no prefix starts with " ") and a
  **double** space (no ``"x "`` → ``"x  "``) — the API ignores such spaces, so those
  branches would only re-walk name-spaces already covered.
* **Loud on incompleteness** — a branch still at the cap at ``MAX_PREFIX_DEPTH`` is
  logged as an ERROR (never a silent keep-first-1000) and recorded in ``incomplete``.

``EXAKTESUCHE=true`` is mandatory (phonetic mode collapses repeated letters and
recurses forever); ``SUCHBEREICH=1`` includes gelöscht/historisch; sweep all Rechtsformen.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from fbl_core.logging import get_logger
from fbl_firmenbuch_client import FirmaResult, RegisterSource

log = get_logger("ingest.enumerate")

RESULT_CAP = 1000
MAX_PREFIX_DEPTH = 20  # was 6 — dense prefixes stay over the cap for many characters

# Static split alphabet: every character that can appear in an Austrian Firmenwortlaut.
# Lowercase letters + digits + German umlauts/ß + other accented Latin letters that occur
# in AT company names + space and the punctuation/symbols used in company names.
DEFAULT_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789äöüßáàâéèêíìîóòôúùûñç -.&:,'+/()"
# Sweep per legal form: the API REQUIRES a RECHTSFORM (or ORTNR) whenever the search term is
# "*" or under 3 characters, so we sweep each form code. These codes were **empirically
# verified** via the `diag` probe (the AI-generated reference was wrong — it listed AKT/EGE,
# which return 0; the real codes are AG/GEN). Covers GmbH, AG, KG, OG, KEG, OHG, Genossenschaft,
# Privatstiftung, SE, Einzelunternehmer, Sparkasse and Versicherungsverein.
DEFAULT_RECHTSFORMEN = (
    "GES",
    "AG",
    "KG",
    "OG",
    "KEG",
    "OHG",
    "GEN",
    "PST",
    "SE",
    "EU",
    "SPA",
    "VER",
)


@dataclass
class WalkState:
    """Resumable enumeration state."""

    seen: dict[str, FirmaResult] = field(default_factory=dict)
    done: set[str] = field(default_factory=set)  # "{rechtsform}|{prefix}" completed
    incomplete: list[str] = field(default_factory=list)
    frontier: list[tuple[str, str]] = field(default_factory=list)  # (rechtsform, prefix)
    counts_by_rechtsform: dict[str, int] = field(default_factory=dict)


@dataclass
class WalkResult:
    """Outcome of a sweep."""

    found: dict[str, FirmaResult]
    incomplete: list[str]
    counts_by_rechtsform: dict[str, int]


class Checkpoint(Protocol):
    """Persistence for a resumable sweep (load on start, save periodically, clear when done)."""

    def load(self) -> WalkState | None: ...

    def save(self, state: WalkState) -> None: ...

    def clear(self) -> None: ...


class InMemoryCheckpoint:
    """A non-persistent checkpoint (default; useful for tests/short runs)."""

    def __init__(self) -> None:
        self._state: WalkState | None = None

    def load(self) -> WalkState | None:
        return self._state

    def save(self, state: WalkState) -> None:
        self._state = state

    def clear(self) -> None:
        self._state = None


def _split_chars(prefix: str, results: list[FirmaResult], alphabet: str) -> list[str]:
    """Static alphabet UNION the next-characters actually observed at this split point.

    The observed set keeps the alphabet exhaustive (an exotic char beyond the static set
    is still followed); the static set guarantees coverage despite result truncation.
    """
    pos = len(prefix)
    observed = {name[pos].lower() for r in results if (name := (r.name or "")) and len(name) > pos}
    return sorted(set(alphabet) | observed)


def prefix_walk(
    source: RegisterSource,
    *,
    rechtsformen: tuple[str, ...] = DEFAULT_RECHTSFORMEN,
    suchbereich: int = 1,
    alphabet: str = DEFAULT_ALPHABET,
    max_depth: int = MAX_PREFIX_DEPTH,
    cap: int = RESULT_CAP,
    checkpoint: Checkpoint | None = None,
    on_incomplete: Callable[[str, str], None] | None = None,
    on_found: Callable[[list[FirmaResult]], None] | None = None,
    save_every: int = 500,
    heartbeat: Callable[[], bool] | None = None,
) -> WalkResult:
    """Walk name prefixes across all Rechtsformen; return a :class:`WalkResult`.

    Resumes from *checkpoint* if it holds a prior state. ``on_incomplete(prefix,
    rechtsform)`` overrides the default (a loud ERROR log) for depth-ceiling branches.
    ``heartbeat`` (the run-lock renewal) is called at the ``save_every`` cadence so a
    multi-hour walk renews its lease (never expires mid-walk) and stops cleanly if the lock
    was lost/overtaken — this lets the grind run on the short, self-healing lease (§15a.3).
    """
    state = (checkpoint.load() if checkpoint else None) or WalkState(
        frontier=[(rf, "") for rf in rechtsformen]
    )
    processed = 0
    while state.frontier:
        rechtsform, prefix = state.frontier.pop()
        key = f"{rechtsform}|{prefix}"
        if key in state.done:
            continue
        query = f"{prefix}*" if prefix else "*"
        results = source.suche_firma(
            query, suchbereich=suchbereich, rechtsform=rechtsform, exaktesuche=True
        )
        if len(results) < cap:
            _record(state, rechtsform, results)
            state.done.add(key)
            if on_found and results:
                on_found(results)  # stream: persist as discovered (durable + observable)
        elif len(prefix) >= max_depth:
            _record(state, rechtsform, results)  # keep what we have...
            state.done.add(key)
            if on_found and results:
                on_found(results)
            state.incomplete.append(key)  # ...but flag it — LOUD, never silent
            if on_incomplete is not None:
                on_incomplete(prefix, rechtsform)
            else:
                log.error(
                    "enumeration branch incomplete at depth ceiling",
                    extra={"context": {"prefix": prefix, "rechtsform": rechtsform, "cap": cap}},
                )
        else:
            for ch in _split_chars(prefix, results, alphabet):
                # Space guard: never let a space be the FIRST character of a prefix, and never
                # produce a double space. The API ignores leading/duplicate spaces, so a
                # " *" branch just re-walks the whole namespace already covered by "*" (and
                # no Austrian Firmenwortlaut starts with a space) — pure wasted work.
                if ch == " " and (not prefix or prefix.endswith(" ")):
                    continue
                state.frontier.append((rechtsform, prefix + ch))
            state.done.add(key)
        processed += 1
        # Periodic progress so a multi-hour walk is observable in the logs (Log Analytics).
        if processed % save_every == 0:
            log.info(
                "enumeration progress",
                extra={
                    "context": {
                        "prefixes_processed": processed,
                        "frontier_remaining": len(state.frontier),
                        "companies_found": len(state.seen),
                        "incomplete_branches": len(state.incomplete),
                    }
                },
            )
            if checkpoint is not None:
                checkpoint.save(state)
            if heartbeat is not None and not heartbeat():
                # Lost the run lock (lease expired / overtaken). RAISE rather than return: an
                # early *return* would let the caller run its mark_vanished reconcile on an
                # INCOMPLETE walk and falsely delete unseen companies. Raising mimics a crash —
                # the checkpoint.save above means the next run resumes from here, and the
                # reconcile is correctly skipped (it only runs after a complete walk).
                raise RuntimeError("run lock lost during enumeration — resuming next run")

    # Reaching here means the frontier drained — a COMPLETE walk. Clear the checkpoint so
    # the next run starts fresh (a persisted "all done" state would make it re-walk nothing).
    # A crash never reaches this line, so the last in-loop save survives for resume.
    if checkpoint is not None:
        checkpoint.clear()
    return WalkResult(
        found=state.seen,
        incomplete=state.incomplete,
        counts_by_rechtsform=state.counts_by_rechtsform,
    )


def _record(state: WalkState, rechtsform: str, results: list[FirmaResult]) -> None:
    for r in results:
        if r.fnr not in state.seen:
            state.seen[r.fnr] = r
            state.counts_by_rechtsform[rechtsform] = (
                state.counts_by_rechtsform.get(rechtsform, 0) + 1
            )
