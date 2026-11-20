"""Slice 3: BotClient asks the SendLimiter before each chat-bound send and reports 429s to it."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest
import respx

from tg_gateway.client import BotClient
from tg_gateway.ratelimit import SendLimiter

from .conftest import CHAT_ID, MESSAGE, ok, too_many_requests

pytestmark = pytest.mark.component

MakeClient = Callable[..., BotClient]


class FakeLimiter(SendLimiter):
    def __init__(self) -> None:
        self.acquired: list[int | str | None] = []
        self.penalized: list[tuple[int | str | None, float]] = []

    async def acquire(self, chat_id: int | str | None) -> None:
        self.acquired.append(chat_id)

    def penalize(self, chat_id: int | str | None, seconds: float) -> None:
        self.penalized.append((chat_id, seconds))


@pytest.fixture
def fake_limiter() -> FakeLimiter:
    return FakeLimiter()


def chat_sends(client: BotClient) -> dict[str, tuple[str, Callable[[], Awaitable[object]]]]:
    return {
        "send_message": ("/sendMessage", lambda: client.send_message(CHAT_ID, "hi")),
        "edit_message_text": (
            "/editMessageText",
            lambda: client.edit_message_text(CHAT_ID, 1, "x"),
        ),
        "send_document": ("/sendDocument", lambda: client.send_document(CHAT_ID, "FILE")),
    }


@pytest.mark.parametrize("name", ["send_message", "edit_message_text", "send_document"])
async def test_acquire_before_every_attempt_including_retries(
    api: respx.MockRouter, make_client: MakeClient, fake_limiter: FakeLimiter, name: str
) -> None:
    client = make_client(limiter=fake_limiter)
    path, call = chat_sends(client)[name]
    api.post(path).mock(side_effect=[too_many_requests(2), ok(MESSAGE)])

    await call()

    assert fake_limiter.acquired == [CHAT_ID, CHAT_ID]


async def test_429_penalizes_the_chat_that_hit_it(
    api: respx.MockRouter, make_client: MakeClient, fake_limiter: FakeLimiter
) -> None:
    api.post("/sendMessage").mock(side_effect=[too_many_requests(2), ok(MESSAGE)])

    await make_client(limiter=fake_limiter).send_message(CHAT_ID, "hi")

    assert fake_limiter.penalized == [(CHAT_ID, 2.0)]


async def test_429_on_a_chatless_method_penalizes_globally(
    api: respx.MockRouter, make_client: MakeClient, fake_limiter: FakeLimiter
) -> None:
    user = {"id": 1, "is_bot": True, "first_name": "Bot"}
    api.post("/getMe").mock(side_effect=[too_many_requests(3), ok(user)])

    await make_client(limiter=fake_limiter).get_me()

    assert fake_limiter.penalized == [(None, 3.0)]
    assert fake_limiter.acquired == []  # getMe isn't a chat send


async def test_answer_callback_query_is_not_rate_limited(
    api: respx.MockRouter, make_client: MakeClient, fake_limiter: FakeLimiter
) -> None:
    api.post("/answerCallbackQuery").mock(return_value=ok(True))

    await make_client(limiter=fake_limiter).answer_callback_query("cbq")

    assert fake_limiter.acquired == []


async def test_on_retry_after_hook_still_fires_with_a_limiter(
    api: respx.MockRouter, make_client: MakeClient, fake_limiter: FakeLimiter
) -> None:
    api.post("/sendMessage").mock(side_effect=[too_many_requests(2), ok(MESSAGE)])
    seen: list[tuple[int | str | None, float]] = []

    await make_client(
        limiter=fake_limiter, on_retry_after=lambda c, s: seen.append((c, s))
    ).send_message(CHAT_ID, "hi")

    assert seen == [(CHAT_ID, 2.0)]
    assert fake_limiter.penalized == [(CHAT_ID, 2.0)]
