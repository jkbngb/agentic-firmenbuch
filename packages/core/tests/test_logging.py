"""Structured JSON logging tests (Technische Spezifikation §11).

The ``ts`` field carries a ``Z`` suffix, so it must actually be UTC — the default
``logging.Formatter`` converter is localtime, which silently shifts every timestamp
by the host's UTC offset (observed: a 13:49 UTC event logged as 15:49 CEST "Z").
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from fbl_core.logging import JsonFormatter


def _record() -> logging.LogRecord:
    return logging.LogRecord(
        name="orchestration.run",
        level=logging.WARNING,
        pathname=__file__,
        lineno=1,
        msg="present/store failed",
        args=None,
        exc_info=None,
    )


def test_ts_is_utc() -> None:
    record = _record()
    payload = json.loads(JsonFormatter().format(record))
    expected = datetime.fromtimestamp(record.created, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert payload["ts"] == expected


def test_ts_roundtrips_to_record_created() -> None:
    """Parsing ``ts`` as UTC must land on the record's epoch time (within the truncated
    sub-second part) — this fails on a localtime formatter for any host not on UTC."""
    record = _record()
    payload = json.loads(JsonFormatter().format(record))
    parsed = datetime.strptime(payload["ts"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    assert 0 <= record.created - parsed.timestamp() < 1
