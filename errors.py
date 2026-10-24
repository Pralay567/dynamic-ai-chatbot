"""Exception types that map cleanly onto HTTP error responses.

``message`` is always safe to show to end users. ``detail`` is for server logs
only and is never sent to clients.
"""

from __future__ import annotations

from typing import Mapping, Optional


class ApiError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        status: int,
        *,
        headers: Optional[Mapping[str, str]] = None,
        detail: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.headers = dict(headers or {})
        self.detail = detail


class AIError(ApiError):
    """The AI provider could not produce a usable answer."""
