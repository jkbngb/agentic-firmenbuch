"""Errors raised by the Firmenbuch client (§8.2)."""

from __future__ import annotations


class FirmenbuchApiError(RuntimeError):
    """Any HTTP/SOAP failure from the HVD API.

    Wraps the underlying cause so callers (ingest) can dead-letter a single
    company without crashing the batch.
    """

    def __init__(self, message: str, *, status: int | None = None, endpoint: str | None = None):
        super().__init__(message)
        self.status = status
        self.endpoint = endpoint
