"""Long-polling UpdateSource: no public URL or HTTPS needed, so it is the dev transport.

Delivery semantics: Telegram treats an update as delivered once getUpdates is called with an
offset above its id. The offset only advances after `handle` returns, and on stop or crash the
last completed offset is confirmed. A crash mid-update therefore replays that update instead of
losing it (handlers must be idempotent, as they must be for webhooks anyway).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Protocol

from tg_gateway.errors import TelegramError, TelegramUnauthorized
from tg_gateway.transport import UpdateHandler, UpdateSource
from tg_gateway.types import Update

__all__ = ["DEFAULT_ALLOWED_UPDATES", "PollingSource", "UpdatesApi"]

_log = logging.getLogger(__name__)

DEFAULT_ALLOWED_UPDATES = ("message", "callback_query", "my_chat_member")


class UpdatesApi(Protocol):
    """The two BotClient methods polling needs (lets tests use a scripted fake)."""

    async def get_updates(
        self,
        *,
        offset: int | None = None,
        timeout: int = 30,  # noqa: ASYNC109  # mirrors BotClient.get_updates
        limit: int = 100,
        allowed_updates: Sequence[str] | None = None,
    ) -> list[Update]: ...

    async def delete_webhook(self, *, drop_pending_updates: bool = False) -> bool: ...


class PollingSource(UpdateSource):
    def __init__(
        self,
        bot: UpdatesApi,
        *,
        timeout: int = 30,
        allowed_updates: Sequence[str] = DEFAULT_ALLOWED_UPDATES,
        delete_webhook: bool = True,
        error_backoff: float = 5.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._bot = bot
        self._timeout = timeout
        self._allowed = list(allowed_updates)
        self._delete_webhook = delete_webhook
        self._error_backoff = error_backoff
        self._sleep = sleep

    async def run(self, handle: UpdateHandler, *, stop: asyncio.Event) -> None:
        if stop.is_set():
            return
        if self._delete_webhook:
            # getUpdates fails with 409 Conflict while a webhook is set.
            await self._bot.delete_webhook(drop_pending_updates=False)
        offset: int | None = None
        try:
            while not stop.is_set():
                updates = await self._poll(offset, stop)
                if updates is None:
                    break
                for update in updates:
                    try:
                        await handle(update)
                    except Exception:
                        # Skip it: one poison update must not block every later update forever.
                        _log.exception("handler failed for update %s; skipping", update.update_id)
                    offset = update.update_id + 1
        finally:
            if offset is not None:
                await self._confirm(offset)

    async def _poll(self, offset: int | None, stop: asyncio.Event) -> list[Update] | None:
        """One long poll; None if `stop` was set while waiting."""
        poll = asyncio.ensure_future(
            self._bot.get_updates(
                offset=offset, timeout=self._timeout, allowed_updates=self._allowed
            )
        )
        stopped = asyncio.ensure_future(stop.wait())
        try:
            done, _ = await asyncio.wait({poll, stopped}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in (poll, stopped):
                task.cancel()
            await asyncio.gather(poll, stopped, return_exceptions=True)

        if poll not in done:
            return None  # cancelling is safe: nothing is confirmed until the next offset
        try:
            return poll.result()
        except TelegramUnauthorized:
            raise  # wrong or revoked token: retrying can't help
        except TelegramError as exc:
            # The client already retried what was safe to retry; now wait and poll again.
            _log.warning("getUpdates failed with %s; backing off", type(exc).__name__)
            await self._sleep(self._error_backoff)
            return []

    async def _confirm(self, offset: int) -> None:
        try:
            await self._bot.get_updates(offset=offset, timeout=0, limit=1)
        except Exception:  # best effort: worst case the last batch is delivered again
            _log.warning("could not confirm offset %s on shutdown", offset)
