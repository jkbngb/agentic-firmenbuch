"""Cloudflare Turnstile server-side verification (Distribution §6).

Kept separate from the pure handlers so it can be injected as a ``TurnstileVerifier``
(real in Azure, a stub in tests). Network failure → treated as NOT verified (fail closed).
"""

from __future__ import annotations

import logging
from collections.abc import Callable

_SITEVERIFY = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
logger = logging.getLogger(__name__)


def make_turnstile_verifier(
    secret: str, *, timeout: float = 5.0
) -> Callable[[str, str | None], bool]:
    """Return a ``(token, remote_ip) -> bool`` verifier backed by Cloudflare siteverify."""

    def verify(token: str, remote_ip: str | None) -> bool:
        if not token:
            return False
        import httpx

        data = {"secret": secret, "response": token}
        if remote_ip:
            data["remoteip"] = remote_ip
        try:
            resp = httpx.post(_SITEVERIFY, data=data, timeout=timeout)
            return bool(resp.json().get("success") is True)
        except Exception:  # network/parse error → fail closed
            logger.warning("Turnstile verification call failed", exc_info=True)
            return False

    return verify
