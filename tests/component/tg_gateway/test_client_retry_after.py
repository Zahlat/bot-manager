"""Group A: how BotClient handles HTTP 429 (Too Many Requests) with retry_after.

Rule under test: a 429 means Telegram did NOT process the request, so it is safe to retry
every method (even non-idempotent sends) after waiting retry_after (+ jitter in [0, 0.5)).
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
import respx

from tg_gateway.client import BotClient
from tg_gateway.errors import TelegramRetryAfter
from tg_gateway.types import Message

from .conftest import CHAT_ID, MESSAGE, FakeSleep, ok, too_many_requests

pytestmark = pytest.mark.component

MakeClient = Callable[..., BotClient]


# 1
async def test_429_waits_retry_after_then_succeeds(
    api: respx.MockRouter, make_client: MakeClient, fake_sleep: FakeSleep
) -> None:
    route = api.post("/editMessageText").mock(side_effect=[too_many_requests(2), ok(MESSAGE)])

    msg = await make_client().edit_message_text(CHAT_ID, 1, "hi")

    assert fake_sleep.calls == [2.0]
    assert route.call_count == 2
    assert isinstance(msg, Message)
    assert msg.message_id == 1


# 2
async def test_429_retried_even_for_non_idempotent_send_message(
    api: respx.MockRouter, make_client: MakeClient, fake_sleep: FakeSleep
) -> None:
    route = api.post("/sendMessage").mock(side_effect=[too_many_requests(3), ok(MESSAGE)])

    msg = await make_client().send_message(CHAT_ID, "hi")

    assert fake_sleep.calls == [3.0]
    assert route.call_count == 2
    assert msg.message_id == 1


# 3
async def test_429_jitter_stays_within_bounds(
    api: respx.MockRouter, make_client: MakeClient, fake_sleep: FakeSleep
) -> None:
    api.post("/sendMessage").mock(side_effect=[too_many_requests(2), ok(MESSAGE)])

    await make_client(rand=lambda: 0.999).send_message(CHAT_ID, "hi")

    assert len(fake_sleep.calls) == 1
    assert 2.0 < fake_sleep.calls[0] < 2.5


# 4
async def test_429_retry_after_above_cap_raises_immediately(
    api: respx.MockRouter, make_client: MakeClient, fake_sleep: FakeSleep
) -> None:
    route = api.post("/sendMessage").mock(side_effect=[too_many_requests(120), ok(MESSAGE)])

    with pytest.raises(TelegramRetryAfter) as exc_info:
        await make_client().send_message(CHAT_ID, "hi")

    assert exc_info.value.retry_after == 120
    assert exc_info.value.method == "sendMessage"
    assert fake_sleep.calls == []
    assert route.call_count == 1


# 5
async def test_429_every_attempt_exhausts_then_raises_retry_after(
    api: respx.MockRouter, make_client: MakeClient, fake_sleep: FakeSleep
) -> None:
    route = api.post("/sendMessage").mock(side_effect=[too_many_requests(1)] * 4)

    with pytest.raises(TelegramRetryAfter):
        await make_client().send_message(CHAT_ID, "hi")

    assert route.call_count == 4  # RetryPolicy.max_attempts default
    assert fake_sleep.calls == [1.0, 1.0, 1.0]


# 6
async def test_429_without_retry_after_falls_back_to_backoff(
    api: respx.MockRouter, make_client: MakeClient, fake_sleep: FakeSleep
) -> None:
    api.post("/sendMessage").mock(
        side_effect=[too_many_requests(None), too_many_requests(None), ok(MESSAGE)]
    )

    await make_client(rand=lambda: 0.5).send_message(CHAT_ID, "hi")

    # full jitter: rand * min(cap=8, base=0.5 * 2**(attempt-1))
    #   attempt 1: 0.5 * 0.5 = 0.25    attempt 2: 0.5 * 1.0 = 0.5
    # Two retries are needed: a "missing retry_after == 0, + jitter" bug also gives 0.25 on the
    # first retry (jitter 0.5 == base 0.5), but it would give 0.25 again on the second, not 0.5.
    assert fake_sleep.calls == [pytest.approx(0.25), pytest.approx(0.5)]


# 7
async def test_429_invokes_on_retry_after_hook_with_chat_id(
    api: respx.MockRouter, make_client: MakeClient
) -> None:
    api.post("/sendMessage").mock(side_effect=[too_many_requests(2), ok(MESSAGE)])
    seen: list[tuple[int | str | None, float]] = []

    def hook(chat_id: int | str | None, retry_after: float) -> None:
        seen.append((chat_id, retry_after))

    await make_client(on_retry_after=hook).send_message(CHAT_ID, "hi")

    assert seen == [(CHAT_ID, 2.0)]
