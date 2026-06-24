"""Typed MCP errors mapped to the §9 error model ``{error: {code, message}}``."""

from __future__ import annotations

from typing import Literal

ErrorCode = Literal["not_found", "unauthorized", "rate_limited", "bad_request", "internal"]


class McpError(Exception):
    code: ErrorCode = "internal"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message

    def to_dict(self) -> dict[str, dict[str, str]]:
        return {"error": {"code": self.code, "message": self.message}}


class NotFound(McpError):
    code: ErrorCode = "not_found"


class Unauthorized(McpError):
    code: ErrorCode = "unauthorized"


class RateLimited(McpError):
    code: ErrorCode = "rate_limited"


class BadRequest(McpError):
    code: ErrorCode = "bad_request"
