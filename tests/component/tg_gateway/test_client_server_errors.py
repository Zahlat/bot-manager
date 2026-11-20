"""Group D: 5xx and network errors are retried only when that cannot cause a duplicate send.

- Idempotent methods (getMe, editMessageText, ...) retry 5xx / network errors with full jitter.
- sendMessage / sendDocument retry only when the request provably never left (connect errors).
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest
import respx

from tg_gateway.client import BotClient
from tg_gateway.errors import TelegramNetworkError, TelegramServerError

from .conftest import CHAT_ID, MESSAGE, FakeSleep, ok

pytestmark = pytest.mark.component

MakeClient = Callable[..., BotClient]

USER: dict[str, object] = {"id": 1, "is_bot": True, "first_name": "Bot"}


def server_error(code: int = 500) -> httpx.Response:
    return httpx.Response(code, json={"ok": False, "error_code": code, "description": "boom"})


# 19
async def test_5xx_retried_with_full_jitter_for_idempotent_method(
    api: respx.MockRouter, make_client: MakeClient, fake_sleep: FakeSleep
) -> None:
    route = api.post("/getMe").mock(side_effect=[server_error(), server_error(503), ok(USER)])

    me = await make_client(rand=lambda: 0.5).get_me()

    assert me.id == 1
    assert route.call_count == 3
    # 0.5 * min(8, 0.5 * 2**0), then 0.5 * min(8, 0.5 * 2**1)
    assert fake_sleep.calls == [pytest.approx(0.25), pytest.approx(0.5)]


# 20
async def test_5xx_not_retried_for_send_document(
    api: respx.MockRouter, make_client: MakeClient, fake_sleep: FakeSleep
) -> None:
    route = api.post("/sendDocument").mock(side_effect=[server_error(), ok(MESSAGE)])

    with pytest.raises(TelegramServerError):
        await make_client().send_document(CHAT_ID, "FILE_ID")

    assert route.call_count == 1
    assert fake_sleep.calls == []


# 21
async def test_connect_error_retried_even_for_send_message(
    api: respx.MockRouter, make_client: MakeClient, fake_sleep: FakeSleep
) -> None:
    route = api.post("/sendMessage").mock(
        side_effect=[httpx.ConnectError("connection refused"), ok(MESSAGE)]
    )

    msg = await make_client().send_message(CHAT_ID, "hi")

    assert msg.message_id == 1
    assert route.call_count == 2
    assert len(fake_sleep.calls) == 1


# 22
async def test_read_timeout_not_retried_for_send_message(
    api: respx.MockRouter, make_client: MakeClient, fake_sleep: FakeSleep
) -> None:
    route = api.post("/sendMessage").mock(
        side_effect=[httpx.ReadTimeout("read timed out"), ok(MESSAGE)]
    )

    with pytest.raises(TelegramNetworkError) as exc_info:
        await make_client().send_message(CHAT_ID, "hi")

    assert exc_info.value.sent is True
    assert exc_info.value.reason == "ReadTimeout"
    assert route.call_count == 1
    assert fake_sleep.calls == []


async def test_read_timeout_retried_for_idempotent_method(
    api: respx.MockRouter, make_client: MakeClient
) -> None:
    route = api.post("/getMe").mock(side_effect=[httpx.ReadTimeout("read timed out"), ok(USER)])

    await make_client().get_me()

    assert route.call_count == 2


# 23
async def test_5xx_exhausted_raises_server_error(
    api: respx.MockRouter, make_client: MakeClient, fake_sleep: FakeSleep
) -> None:
    route = api.post("/getMe").mock(side_effect=[server_error()] * 4)

    with pytest.raises(TelegramServerError):
        await make_client().get_me()

    assert route.call_count == 4
    assert len(fake_sleep.calls) == 3
