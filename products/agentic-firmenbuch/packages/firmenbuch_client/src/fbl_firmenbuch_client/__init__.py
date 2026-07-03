"""fbl_firmenbuch_client — Stage 3: typed adapter over the Firmenbuch HVD SOAP API.

``JustizOnlineClient`` implements the ``RegisterSource`` protocol (six read-only
calls) with ``X-API-KEY`` auth and 429/5xx retry. See docs/API_PROBE_FINDINGS.md
for the live-confirmed behaviour this is built against.
"""

from __future__ import annotations

from .errors import FirmenbuchApiError
from .models import (
    AuszugKurz,
    AuszugPerson,
    DocChange,
    FirmaChange,
    FirmaResult,
    RegistrationEvent,
    UrkundeContent,
    UrkundeRef,
    normalize_fnr,
)
from .soap_client import JustizOnlineClient
from .source import RawCapturingSource, RawResponse, RegisterSource

__all__ = [
    "AuszugKurz",
    "AuszugPerson",
    "DocChange",
    "FirmaChange",
    "FirmaResult",
    "FirmenbuchApiError",
    "JustizOnlineClient",
    "RawCapturingSource",
    "RawResponse",
    "RegisterSource",
    "RegistrationEvent",
    "UrkundeContent",
    "UrkundeRef",
    "normalize_fnr",
]
