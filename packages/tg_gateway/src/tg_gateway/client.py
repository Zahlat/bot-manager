"""BotClient: a thin, typed, async Telegram Bot API client with a safe retry loop.

Security notes:
- The token lives only in a SecretStr and in the URL built per request. URLs are never logged,
  never stored on exceptions, and httpx/httpcore loggers are capped at WARNING (they log URLs).
- httpx exceptions are converted to TelegramNetworkError *without* raising inside the `except`
  block, so the original exception (which holds the request URL) is not chained as __context__.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import random
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass

import httpx
from pydantic import BaseModel, SecretStr, TypeAdapter, ValidationError

from tg_gateway.errors import (
    TelegramDecodeError,
    TelegramError,
    TelegramNetworkError,
    TelegramRetryAfter,
    map_error,
)
from tg_gateway.retry import RetryPolicy
from tg_gateway.types import (
    BotCommand,
    InlineKeyboardMarkup,
    InputFile,
    Message,
    ParseMode,
    Update,
    User,
    WebhookInfo,
)

__all__ = ["BotClient", "RetryAfterHook", "Timeouts"]

_log = logging.getLogger(__name__)

# Called on every 429 that carries retry_after: (chat_id or None, seconds). Lets a shared rate
# limiter back off globally. Wired up in the rate-limiter slice.
RetryAfterHook = Callable[[int | str | None, float], None]

# Errors raised before any byte of the request could reach Telegram -> safe to retry any method.
_NOT_SENT: tuple[type[httpx.TransportError], ...] = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.PoolTimeout,
)

_USER = TypeAdapter(User)
_MESSAGE = TypeAdapter(Message)
_BOOL = TypeAdapter(bool)
_UPDATES = TypeAdapter(list[Update])
_WEBHOOK_INFO = TypeAdapter(WebhookInfo)


@dataclass(frozen=True, slots=True)
class Timeouts:
    """Seconds. No call may wait forever (docs/PLAN.md, Robustness)."""

    connect: float = 5.0
    read: float = 10.0
    write: float = 10.0
    pool: float = 5.0
    upload: float = 60.0

    def default(self) -> httpx.Timeout:
        return httpx.Timeout(connect=self.connect, read=self.read, write=self.write, pool=self.pool)

    def for_upload(self) -> httpx.Timeout:
        return httpx.Timeout(
            connect=self.connect, read=self.upload, write=self.upload, pool=self.pool
        )

    def for_long_poll(self, poll_seconds: int) -> httpx.Timeout:
        # Telegram holds getUpdates open for poll_seconds; the read timeout must outlast that.
        return httpx.Timeout(
            connect=self.connect, read=poll_seconds + self.read, write=self.write, pool=self.pool
        )


def _silence_url_logging() -> None:
    """httpx logs every request URL at INFO (and httpcore at DEBUG); the URL holds the token."""
    for name in ("httpx", "httpcore"):
        logger = logging.getLogger(name)
        if logger.level < logging.WARNING:  # includes NOTSET, which would inherit root's level
            logger.setLevel(logging.WARNING)


def _jsonable(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", by_alias=True, exclude_none=True)
    if isinstance(value, list | tuple):
        return [_jsonable(item) for item in value]
    return value


def _payload(**fields: object) -> dict[str, object]:
    """Build a request body: drop None values, serialize models."""
    return {key: _jsonable(value) for key, value in fields.items() if value is not None}


def _form_value(value: object) -> str:
    """Multipart fields are strings; nested objects (e.g. reply_markup) are compact JSON."""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


class BotClient:
    def __init__(
        self,
        token: SecretStr,
        *,
        http: httpx.AsyncClient,
        base_url: str = "https://api.telegram.org",
        retry: RetryPolicy | None = None,
        timeouts: Timeouts | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rand: Callable[[], float] = random.random,  # jitter only, not security-sensitive
        on_retry_after: RetryAfterHook | None = None,
    ) -> None:
        self._token = token
        self._http = http
        self._base_url = base_url.rstrip("/")
        self._retry = retry or RetryPolicy()
        self._timeouts = timeouts or Timeouts()
        self._sleep = sleep
        self._rand = rand
        self._on_retry_after = on_retry_after
        _silence_url_logging()

    def __repr__(self) -> str:
        return f"{type(self).__name__}(base_url={self._base_url!r})"

    # ------------------------------------------------------------------ Bot API methods

    async def get_me(self) -> User:
        return await self._call("getMe", {}, _USER, idempotent=True)

    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        parse_mode: ParseMode | None = "HTML",
        reply_markup: InlineKeyboardMarkup | None = None,
        disable_notification: bool = False,
        link_preview: bool = False,
    ) -> Message:
        payload = _payload(
            chat_id=chat_id,
            text=text,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
            disable_notification=disable_notification,
            link_preview_options={"is_disabled": not link_preview},
        )
        return await self._call("sendMessage", payload, _MESSAGE, idempotent=False, chat_id=chat_id)

    async def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        *,
        parse_mode: ParseMode | None = "HTML",
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> Message:
        payload = _payload(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
        )
        # Re-sending the same edit is harmless (Telegram answers "message is not modified").
        return await self._call(
            "editMessageText", payload, _MESSAGE, idempotent=True, chat_id=chat_id
        )

    async def send_document(
        self,
        chat_id: int,
        document: str | InputFile,
        *,
        caption: str | None = None,
        parse_mode: ParseMode | None = "HTML",
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> Message:
        """`document` is a cached file_id (str) or an InputFile to upload."""
        upload = document if isinstance(document, InputFile) else None
        payload = _payload(
            chat_id=chat_id,
            document=None if upload else document,
            caption=caption,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
        )
        return await self._call(
            "sendDocument",
            payload,
            _MESSAGE,
            idempotent=False,
            chat_id=chat_id,
            files={"document": upload} if upload else None,
            http_timeout=self._timeouts.for_upload() if upload else None,
        )

    async def answer_callback_query(
        self, callback_query_id: str, *, text: str | None = None, show_alert: bool = False
    ) -> bool:
        payload = _payload(callback_query_id=callback_query_id, text=text, show_alert=show_alert)
        return await self._call("answerCallbackQuery", payload, _BOOL, idempotent=True)

    async def set_webhook(
        self,
        url: str,
        *,
        secret_token: SecretStr,
        allowed_updates: Sequence[str],
        drop_pending_updates: bool = False,
        max_connections: int | None = None,
    ) -> bool:
        payload = _payload(
            url=url,
            secret_token=secret_token.get_secret_value(),
            allowed_updates=list(allowed_updates),
            drop_pending_updates=drop_pending_updates,
            max_connections=max_connections,
        )
        return await self._call("setWebhook", payload, _BOOL, idempotent=True)

    async def get_webhook_info(self) -> WebhookInfo:
        return await self._call("getWebhookInfo", {}, _WEBHOOK_INFO, idempotent=True)

    async def delete_webhook(self, *, drop_pending_updates: bool = False) -> bool:
        payload = _payload(drop_pending_updates=drop_pending_updates)
        return await self._call("deleteWebhook", payload, _BOOL, idempotent=True)

    async def get_updates(
        self,
        *,
        offset: int | None = None,
        timeout: int = 30,  # noqa: ASYNC109  # Telegram's long-poll parameter, not ours
        limit: int = 100,
        allowed_updates: Sequence[str] | None = None,
    ) -> list[Update]:
        payload = _payload(
            offset=offset,
            timeout=timeout,
            limit=limit,
            allowed_updates=list(allowed_updates) if allowed_updates is not None else None,
        )
        return await self._call(
            "getUpdates",
            payload,
            _UPDATES,
            idempotent=True,
            http_timeout=self._timeouts.for_long_poll(timeout),
        )

    async def set_my_commands(
        self, commands: Sequence[BotCommand], *, language_code: str | None = None
    ) -> bool:
        payload = _payload(commands=list(commands), language_code=language_code)
        return await self._call("setMyCommands", payload, _BOOL, idempotent=True)

    # ------------------------------------------------------------------ core

    async def _call[T](
        self,
        method: str,
        payload: Mapping[str, object],
        result: TypeAdapter[T],
        *,
        idempotent: bool,
        chat_id: int | str | None = None,
        files: Mapping[str, InputFile] | None = None,
        http_timeout: httpx.Timeout | None = None,
    ) -> T:
        for attempt in itertools.count(1):
            response = await self._send(
                method, payload, files, http_timeout or self._timeouts.default()
            )
            outcome: T | TelegramError
            if isinstance(response, TelegramError):
                outcome = response
            else:
                outcome = _parse(method, response, result)
            if not isinstance(outcome, TelegramError):
                return outcome

            error = outcome
            if isinstance(error, TelegramRetryAfter) and error.retry_after is not None:
                self._notify_retry_after(chat_id, float(error.retry_after))
            delay = self._retry.decide(
                attempt=attempt, error=error, idempotent=idempotent, rand=self._rand()
            )
            if delay is None:
                raise error  # outside any `except`, so no httpx exception is chained
            _log.warning(
                "Bot API %s failed with %s; retry %d/%d in %.2fs",
                method,
                type(error).__name__,
                attempt,
                self._retry.max_attempts - 1,
                delay,
            )
            await self._sleep(delay)
        raise AssertionError("unreachable")  # pragma: no cover  # itertools.count never ends

    async def _send(
        self,
        method: str,
        payload: Mapping[str, object],
        files: Mapping[str, InputFile] | None,
        http_timeout: httpx.Timeout,
    ) -> httpx.Response | TelegramNetworkError:
        url = f"{self._base_url}/bot{self._token.get_secret_value()}/{method}"
        try:
            if files:
                return await self._http.post(
                    url,
                    data={key: _form_value(value) for key, value in payload.items()},
                    files={
                        name: (file.filename, file.content, file.mime)
                        for name, file in files.items()
                    },
                    timeout=http_timeout,
                )
            return await self._http.post(url, json=payload, timeout=http_timeout)
        except httpx.TransportError as exc:
            # Return (not raise) so the caller raises outside this block: no __context__ chain.
            return TelegramNetworkError(
                method, sent=not isinstance(exc, _NOT_SENT), reason=type(exc).__name__
            )

    def _notify_retry_after(self, chat_id: int | str | None, seconds: float) -> None:
        if self._on_retry_after is not None:
            self._on_retry_after(chat_id, seconds)


def _parse[T](method: str, response: httpx.Response, result: TypeAdapter[T]) -> T | TelegramError:
    """Decode the Bot API envelope into the result, or into the matching error (returned)."""
    try:
        envelope: object = response.json()
    except ValueError:
        envelope = None

    if not isinstance(envelope, dict):
        if response.is_success:
            return TelegramDecodeError(method, "response was not a JSON object")
        return map_error(method, response.status_code, None)

    if response.is_success and envelope.get("ok") is True:
        try:
            return result.validate_python(envelope.get("result"))
        except ValidationError:
            # Don't echo pydantic's message: it may contain user content from the payload.
            return TelegramDecodeError(method, "result did not match the expected type")
    return map_error(method, response.status_code, envelope)
