"""Exceptions raised by BotClient, and the pure mapping from Bot API error responses to them.

No exception here ever stores a URL or a wrapped httpx exception: Bot API URLs contain the bot
token, and httpx exceptions carry the request (and so the URL).
"""

from __future__ import annotations

from collections.abc import Mapping
from http import HTTPStatus

__all__ = [
    "InvalidFileId",
    "MessageNotModified",
    "TelegramAPIError",
    "TelegramBadRequest",
    "TelegramConflict",
    "TelegramDecodeError",
    "TelegramError",
    "TelegramForbidden",
    "TelegramNetworkError",
    "TelegramNotFound",
    "TelegramRetryAfter",
    "TelegramServerError",
    "TelegramUnauthorized",
    "map_error",
]


class TelegramError(Exception):
    """Base class for every error BotClient raises. `method` is the Bot API method name."""

    def __init__(self, method: str, message: str) -> None:
        super().__init__(f"{method}: {message}")
        self.method = method


class TelegramAPIError(TelegramError):
    """Telegram answered with `ok: false` (or a non-2xx status)."""

    def __init__(self, method: str, error_code: int, description: str) -> None:
        super().__init__(method, f"[{error_code}] {description}")
        self.error_code = error_code
        self.description = description


class TelegramBadRequest(TelegramAPIError):
    """400: the request itself is wrong."""


class MessageNotModified(TelegramBadRequest):
    """400 on edit when the new content equals the old one. Callers usually treat it as success."""


class InvalidFileId(TelegramBadRequest):
    """400 when a stored file_id no longer works; the caller should re-upload the file."""


class TelegramUnauthorized(TelegramAPIError):
    """401: the bot token is wrong or revoked."""


class TelegramForbidden(TelegramAPIError):
    """403: typically the user blocked the bot."""


class TelegramNotFound(TelegramAPIError):
    """404: unknown method (or a malformed token)."""


class TelegramConflict(TelegramAPIError):
    """409: e.g. getUpdates while a webhook is set, or two pollers at once."""


class TelegramRetryAfter(TelegramAPIError):
    """429: flood control. `retry_after` is seconds to wait, or None if Telegram omitted it."""

    def __init__(
        self, method: str, error_code: int, description: str, *, retry_after: int | None
    ) -> None:
        super().__init__(method, error_code, description)
        self.retry_after = retry_after


class TelegramServerError(TelegramAPIError):
    """5xx from Telegram or a proxy in front of it."""


class TelegramNetworkError(TelegramError):
    """Transport failure. `sent` says whether the request may have reached Telegram."""

    def __init__(self, method: str, *, sent: bool, reason: str) -> None:
        super().__init__(method, f"network error ({reason}, sent={sent})")
        self.sent = sent
        self.reason = reason


class TelegramDecodeError(TelegramError):
    """A 2xx response that isn't valid JSON or doesn't match the expected result type."""


_BY_CODE: dict[int, type[TelegramAPIError]] = {
    HTTPStatus.BAD_REQUEST: TelegramBadRequest,
    HTTPStatus.UNAUTHORIZED: TelegramUnauthorized,
    HTTPStatus.FORBIDDEN: TelegramForbidden,
    HTTPStatus.NOT_FOUND: TelegramNotFound,
    HTTPStatus.CONFLICT: TelegramConflict,
}

_NOT_MODIFIED = ("message is not modified",)
_BAD_FILE_ID = ("wrong file identifier", "wrong remote file", "file reference")


def map_error(method: str, status: int, envelope: Mapping[str, object] | None) -> TelegramAPIError:
    """Turn an error response into the most specific exception. Pure; never raises."""
    envelope = envelope or {}
    code = envelope.get("error_code")
    error_code = code if isinstance(code, int) else status
    raw_description = envelope.get("description")
    description = raw_description if isinstance(raw_description, str) else f"HTTP {status}"

    if error_code == HTTPStatus.TOO_MANY_REQUESTS:
        params = envelope.get("parameters")
        retry_after = params.get("retry_after") if isinstance(params, Mapping) else None
        return TelegramRetryAfter(
            method,
            error_code,
            description,
            retry_after=retry_after if isinstance(retry_after, int) else None,
        )
    if error_code == HTTPStatus.BAD_REQUEST:
        lowered = description.lower()
        if any(marker in lowered for marker in _NOT_MODIFIED):
            return MessageNotModified(method, error_code, description)
        if any(marker in lowered for marker in _BAD_FILE_ID):
            return InvalidFileId(method, error_code, description)
    if 500 <= error_code <= 599:  # noqa: PLR2004  # the HTTP 5xx range
        return TelegramServerError(method, error_code, description)
    return _BY_CODE.get(error_code, TelegramAPIError)(method, error_code, description)
