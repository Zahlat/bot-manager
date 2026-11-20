"""Slice 6: long-polling UpdateSource (dev transport)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence

import pytest

from tg_gateway.errors import (
    TelegramConflict,
    TelegramServerError,
    TelegramUnauthorized,
)
from tg_gateway.transport.polling import PollingSource
from tg_gateway.types import Update

pytestmark = pytest.mark.unit

BLOCK = object()  # scripted response: hang like a long poll with nothing to deliver


def upd(update_id: int) -> Update:
    return Update(update_id=update_id)


class FakeBot:
    """Scripted getUpdates; records every call. Satisfies the UpdatesApi Protocol."""

    def __init__(self, *script: list[Update] | Exception | object) -> None:
        self.script = list(script)
        self.calls: list[dict[str, object]] = []
        self.webhook_deleted = 0

    async def delete_webhook(self, *, drop_pending_updates: bool = False) -> bool:
        self.webhook_deleted += 1
        return True

    async def get_updates(
        self,
        *,
        offset: int | None = None,
        timeout: int = 30,  # noqa: ASYNC109  # mirrors BotClient.get_updates
        limit: int = 100,
        allowed_updates: Sequence[str] | None = None,
    ) -> list[Update]:
        self.calls.append(
            {"offset": offset, "timeout": timeout, "allowed_updates": allowed_updates}
        )
        step = self.script.pop(0) if self.script else BLOCK
        if step is BLOCK and timeout == 0:
            return []  # like Telegram: timeout=0 answers at once
        if step is BLOCK:
            await asyncio.Event().wait()  # forever, until cancelled
        if isinstance(step, Exception):
            raise step
        assert isinstance(step, list)
        return step


class FakeSleep:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def source(bot: FakeBot, sleep: FakeSleep | None = None, **kwargs: object) -> PollingSource:
    return PollingSource(bot, sleep=sleep or FakeSleep(), **kwargs)  # type: ignore[arg-type]


async def run_until(
    src: PollingSource, stop: asyncio.Event, handled: list[int], *, after: int
) -> None:
    async def handle(update: Update) -> None:
        handled.append(update.update_id)
        if len(handled) >= after:
            stop.set()

    await asyncio.wait_for(src.run(handle, stop=stop), timeout=2)


async def test_deletes_webhook_once_at_start_and_uses_long_poll_settings() -> None:
    bot, stop, handled = FakeBot([upd(1)]), asyncio.Event(), list[int]()

    await run_until(source(bot, timeout=25, allowed_updates=["message"]), stop, handled, after=1)

    assert bot.webhook_deleted == 1
    assert bot.calls[0] == {"offset": None, "timeout": 25, "allowed_updates": ["message"]}


async def test_webhook_kept_when_disabled() -> None:
    bot, stop, handled = FakeBot([upd(1)]), asyncio.Event(), list[int]()

    await run_until(source(bot, delete_webhook=False), stop, handled, after=1)

    assert bot.webhook_deleted == 0


async def test_offset_advances_past_each_handled_batch() -> None:
    bot, stop, handled = FakeBot([upd(10), upd(11)], [upd(12)]), asyncio.Event(), list[int]()

    await run_until(source(bot), stop, handled, after=3)

    assert handled == [10, 11, 12]
    assert [c["offset"] for c in bot.calls[:2]] == [None, 12]


async def test_stop_confirms_the_last_handled_offset() -> None:
    bot, stop, handled = FakeBot([upd(10), upd(11)]), asyncio.Event(), list[int]()

    await run_until(source(bot), stop, handled, after=2)

    final = bot.calls[-1]
    assert final["offset"] == 12  # tells Telegram 10 and 11 are done, so a restart won't replay
    assert final["timeout"] == 0


async def test_crash_mid_batch_confirms_only_completed_updates() -> None:
    bot, stop = FakeBot([upd(10), upd(11)]), asyncio.Event()

    async def handle(update: Update) -> None:
        if update.update_id == 11:
            raise asyncio.CancelledError  # e.g. the process is shutting down mid-update

    with pytest.raises(asyncio.CancelledError):
        await source(bot).run(handle, stop=stop)

    assert bot.calls[-1]["offset"] == 11  # 10 confirmed, 11 will be delivered again


async def test_stop_interrupts_a_waiting_long_poll() -> None:
    bot, stop = FakeBot(BLOCK), asyncio.Event()

    async def handle(update: Update) -> None:
        raise AssertionError("nothing to handle")

    task = asyncio.create_task(source(bot).run(handle, stop=stop))
    await asyncio.sleep(0.01)
    stop.set()

    await asyncio.wait_for(task, timeout=1)  # returns promptly, not after the 30 s poll


async def test_handler_error_is_logged_and_the_update_skipped(
    caplog: pytest.LogCaptureFixture,
) -> None:
    bot, stop, handled = FakeBot([upd(1), upd(2)]), asyncio.Event(), list[int]()

    async def handle(update: Update) -> None:
        handled.append(update.update_id)
        if update.update_id == 1:
            raise ValueError("poison update")
        stop.set()

    with caplog.at_level(logging.ERROR):
        await asyncio.wait_for(source(bot).run(handle, stop=stop), timeout=2)

    assert handled == [1, 2]  # one bad update doesn't block the queue forever
    assert "update 1" in caplog.text


async def test_conflict_backs_off_and_keeps_polling() -> None:
    sleep = FakeSleep()
    bot = FakeBot(TelegramConflict("getUpdates", 409, "Conflict"), [upd(5)])
    stop, handled = asyncio.Event(), list[int]()

    await run_until(source(bot, sleep, error_backoff=7.0), stop, handled, after=1)

    assert handled == [5]
    assert sleep.calls == [7.0]


async def test_server_error_backs_off_and_keeps_polling() -> None:
    sleep = FakeSleep()
    bot = FakeBot(TelegramServerError("getUpdates", 502, "Bad Gateway"), [upd(5)])
    stop, handled = asyncio.Event(), list[int]()

    await run_until(source(bot, sleep), stop, handled, after=1)

    assert handled == [5]
    assert sleep.calls == [5.0]


async def test_unauthorized_stops_polling() -> None:
    bot = FakeBot(TelegramUnauthorized("getUpdates", 401, "Unauthorized"))

    async def handle(update: Update) -> None:
        raise AssertionError("unreachable")

    with pytest.raises(TelegramUnauthorized):
        await source(bot).run(handle, stop=asyncio.Event())


async def test_already_set_stop_returns_without_polling() -> None:
    bot, stop = FakeBot(), asyncio.Event()
    stop.set()

    async def handle(update: Update) -> None:
        raise AssertionError("unreachable")

    await source(bot).run(handle, stop=stop)

    assert bot.calls == []
